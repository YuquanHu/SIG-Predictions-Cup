"""Hold book (structural mispricings and big edges): up to HOLD_SLOTS positions per probability bucket, picked by z (edge per unit of
risk), each sized at an equal slot of the bucket's share of the book (an empty slot stays cash). Pure logic: it turns opportunities,
baselines, the ledger and the portfolio into Wants for trading.size_buys."""
import math
from dataclasses import dataclass, replace

import config
from books import book_value
from races import needs_rules_check
from strategy import EPS, keep_best, z_score
from trading import Want, bet_room, entry_minutes, patient_bid, rest_want, sell_fill

BUCKET_SHARE = {name: share for name, _, _, share in config.BUCKETS}
BUCKET_BOUNDS = {name: (low, high) for name, low, high, _ in config.BUCKETS}


def bucket_of(prob):
    """(name, share of the hold book) of the bucket that fair probability `prob` falls in; 90% and up is a sure bet."""
    for name, low, high, share in config.BUCKETS:
        if low <= prob < high or high >= 1.0:
            return name, share
    raise ValueError(f"no bucket for probability {prob}")


def bucket_for(prob, previous=None):
    """The bucket for `prob`, except that a position already placed in `previous` stays there until its probability is
    BUCKET_BAND outside it, so a fair value wobbling around 90% does not move a position between buckets."""
    name = bucket_of(prob)[0]
    if previous and previous != name:
        low, high = BUCKET_BOUNDS[previous]
        if low - config.BUCKET_BAND <= prob < high + config.BUCKET_BAND:
            return previous
    return name


@dataclass
class BucketStatus:
    """One row of the hold report: a pick (what it holds, what it would buy and why), a held bet that keeps its slot without a top-up,
    or a bucket's empty slots."""
    bucket: str
    share: float             # the row's share of total equity: one slot of the bucket (hold_book_pct x bucket share / slots), or its empty slots
    pick: object = None      # the chosen Opportunity, or None
    held: float = 0.0        # value the hold book already holds in the bet
    want: float = 0.0        # value still to buy
    note: str = ""
    holding: object = None   # a held bet with no pick this loop (portfolio.Holding): it keeps its slot, without a top-up


def spreads(races):
    """{market_id: SIG yes spread} for every contract with a two-sided quote."""
    return {c["market_id"]: c["yes"]["ask"] - c["yes"]["bid"] for race in races.values() for c in race["contracts"]
            if c["yes"]["bid"] is not None and c["yes"]["ask"] is not None}


def eligible(candidates, baselines, races, cooldown):
    """The candidates the hold book may buy. Every other candidate gets the reason in `decision`.
    Structural (or an executable edge of HOLD_BIG_EDGE, which is held to resolution whether or not the gap lasts) on the side its
    gap favours, enough edge, a tight spread, rules checked, not sold a moment ago, and of two equivalent bets (Dem Yes / Rep No)
    only the cheaper. A market with no classified gap is never eligible."""
    spread_of = spreads(races)
    keep = []
    for c in candidates:
        b = baselines.get(c.market_id)
        if b is None or b.side is None or not (b.structural or c.edge >= config.HOLD_BIG_EDGE - EPS):
            c.decision = "not structural"
        elif c.side != b.side:
            c.decision = "the other side of the usual gap"
        elif c.edge < config.HOLD_MIN_EDGE - EPS:
            c.decision = f"edge {c.edge * 100:.1f}c is below {config.HOLD_MIN_EDGE * 100:.0f}c"
        elif needs_rules_check(races[c.race]):
            c.decision = "rules not checked"
        elif spread_of.get(c.market_id, 0) > config.HOLD_MAX_SPREAD + EPS:
            c.decision = f"spread {spread_of[c.market_id] * 100:.1f}c is too wide"
        elif cooldown.blocks(c.market_id, "buy"):
            c.decision = "sold within the cooldown"
        else:
            c.decision = None
            keep.append(c)
    return keep_best(keep, lambda c: (c.race, c.wins_when), lambda c: -c.price,
                     lambda w: f"same bet as {w.side.upper()} {w.market_id}, which is cheaper")


def held_z(h):
    """The z of keeping a held bet to resolution, at its sell price (what selling it now gives up); None without a fair value or a bid."""
    return None if h.fair is None or not h.sell_price else z_score(h.fair, h.sell_price)


def swappable(h, ledger):
    """True when a held bet may be sold for a better one: it has a z, and no top-up freeze (a fall of 10c+ is never sold at the bottom)."""
    return held_z(h) is not None and (h.race, h.wins_when) not in ledger.frozen


def held_bets(holdings, ledger):
    """{bucket: [Holding, best z first]} of the bets the hold book holds, one per bet (race and the outcome it wins on, so an equivalent
    contract is the same bet), each in the bucket its lots were placed in (inside BUCKET_BAND), else its fair value's. A hand buy counts
    once the ledger adopts it. The best HOLD_SLOTS of a bucket keep its slots (and are topped up when they qualify); the rest are over
    its slots: held, never topped up, the first a better new bet takes the place of."""
    out, seen = {}, set()
    for h in holdings:
        previous = ledger.bucket_of(h.market_id, h.side)
        if (h.race, h.wins_when) in seen or ledger.shares(h.market_id, h.side, "hold") < 1 or (h.fair is None and previous is None):
            continue
        seen.add((h.race, h.wins_when))
        out.setdefault(previous if h.fair is None else bucket_for(h.fair, previous), []).append(h)
    return {name: sorted(hs, key=lambda h: -math.inf if held_z(h) is None else held_z(h), reverse=True) for name, hs in out.items()}


def fill_slots(beats, slots, last):
    """Up to `slots` picks from `beats` (bets the hold book does not hold yet): last loop's picks still being bought first, then the
    best z. When they are all taken a challenger replaces the weakest pick only with HOLD_SWITCH_Z more z."""
    if slots <= 0:
        return []
    by_z = sorted(beats, key=lambda c: c.z, reverse=True)
    picks = [c for c in by_z if (c.market_id, c.side) in last][:slots]
    for c in by_z:
        if any(c is p for p in picks):
            continue
        if len(picks) < slots:
            picks.append(c)
            continue
        weakest = min(picks, key=lambda p: p.z)
        if c.z - weakest.z <= config.HOLD_SWITCH_Z + EPS:
            break  # best first: no later challenger beats it by more
        picks = [c if p is weakest else p for p in picks]
    return sorted(picks, key=lambda c: c.z, reverse=True)


def roi_floor(prob):
    """The smallest ROI a hold pick with fair probability `prob` can have: the ROI where z is HOLD_MIN_Z (or at HOLD_MIN_EDGE, when that
    is more). The edge then is HOLD_MIN_Z x sqrt(p (1 - p)), so the floor climbs steeply as `prob` falls (infinite once no price is low enough)."""
    edge = max(config.HOLD_MIN_Z * math.sqrt(prob * (1 - prob)), config.HOLD_MIN_EDGE)
    return edge / (prob - edge) if prob > edge else math.inf


def choose_picks(candidates, ledger, held=None, first_loop=False):
    """({bucket: [Opportunity]}, {bucket: why it has no pick}). Buckets are taken from the most certain down. A candidate qualifies
    with a z (edge per unit of its own risk, Opportunity.z) of at least HOLD_MIN_Z, the same in every bucket. The bets the hold book
    holds (`held`, from held_bets) take their bucket's HOLD_SLOTS first; a qualifying candidate of one of them is its pick (a top-up).
    The slots left go to new bets by z (fill_slots), and a new bet still left over takes the place of the weakest held bet (those over
    the slots first) when it has HOLD_SWAP_Z more z than that bet at its sell price (`displaced` names the bets that lose their slot).
    With `first_loop` the book is built from the held bets alone: no new bet is considered. A riskier bucket's picks also need an ROI
    above the best pick of every more certain bucket: taking more risk for the same or less return is pointless. The ledger
    remembers the picks."""
    held = held or {}
    by_bucket = {}
    for c in candidates:
        previous = ledger.bucket_of(c.market_id, c.side)
        by_bucket.setdefault(bucket_for(c.fair, previous), []).append(c)
    picks, skipped = {}, {}
    hurdle, hurdle_pick = 0.0, None  # the best ROI picked so far, and the pick that set it
    for name, *_ in reversed(config.BUCKETS):
        group = by_bucket.get(name, [])
        qualified = [c for c in group if c.z >= config.HOLD_MIN_Z - EPS]
        beats = [c for c in qualified if c.expected_return > hurdle + EPS]
        for c in group:
            if c.z < config.HOLD_MIN_Z - EPS:
                c.decision = f"z {c.z:.2f} is under the {config.HOLD_MIN_Z:.2f} minimum (ROI {c.expected_return:.0%}, {roi_floor(c.fair):.0%} needed)"
            elif c.expected_return <= hurdle + EPS:
                c.decision = f"ROI {c.expected_return:.1%} does not beat the {hurdle_pick[0]} pick's {hurdle:.1%}"
        if group and not beats:
            skipped[name] = (f"best z {max(c.z for c in group):.2f} is under the {config.HOLD_MIN_Z:.2f} minimum" if not qualified else
                             f"best ROI {max(c.expected_return for c in qualified):.1%} does not beat the {hurdle_pick[0]} pick's {hurdle:.1%}")
        if not beats:
            continue
        slots, mine = config.HOLD_SLOTS[name], held.get(name, [])
        keep = {(h.race, h.wins_when) for h in mine[:slots]}
        free = max(slots - len(keep), 0)
        newbets = [c for c in beats if (c.race, c.wins_when) not in {(h.race, h.wins_when) for h in mine}]
        new = [] if first_loop else fill_slots(newbets, free, set(ledger.picks.get(name, ())))
        if not first_loop:  # a new bet still left over takes the weakest held bet's place, margin permitting
            victims = [h for h in reversed(mine) if swappable(h, ledger)]
            for c in sorted((c for c in newbets if not any(c is n for n in new)), key=lambda c: c.z, reverse=True):
                if not victims or c.z - held_z(victims[0]) < config.HOLD_SWAP_Z - EPS:
                    break
                new.append(c)
                keep.discard((victims[0].race, victims[0].wins_when))   # it is sold: no top-up
                victims.pop(0)
        chosen = [c for c in beats if (c.race, c.wins_when) in keep] + new
        for c in beats:
            c.decision = "PICK" if any(c is p for p in chosen) else (
                "loop 1: held bets only" if first_loop and c in newbets else
                f"not in the top {free} new bets by z, or under {config.HOLD_SWAP_Z:.2f} z above the weakest held")
        if not chosen:
            continue
        picks[name] = chosen
        top = max(chosen, key=lambda c: c.expected_return)
        hurdle, hurdle_pick = top.expected_return, (name, top)
    ledger.picks = {b: [(c.market_id, c.side) for c in ps] for b, ps in picks.items()}
    return picks, skipped


def displaced(held, picks, ledger):
    """[(Holding, Opportunity)] the held bets whose slot a new pick took, weakest first, each with the pick that replaced it: a bucket's new
    picks beyond its free slots (choose_picks)."""
    owned = {(h.race, h.wins_when) for hs in held.values() for h in hs}
    out = []
    for name, hs in held.items():
        free = max(config.HOLD_SLOTS[name] - len(hs[:config.HOLD_SLOTS[name]]), 0)
        new = sorted((c for c in picks.get(name, []) if (c.race, c.wins_when) not in owned), key=lambda c: c.z, reverse=True)[free:]
        out += list(zip([h for h in reversed(hs) if swappable(h, ledger)], new))
    return out


def hold_view(h, ledger):
    """The hold book's share of a holding: its shares and value cut to the hold lots (a fast position in the same bet is not sold)."""
    owned = min(ledger.shares(h.market_id, h.side, "hold"), h.shares)
    return replace(h, shares=owned, value=h.value * owned / h.shares)


def hold_swaps(held, picks, ledger, portfolio, cooldown):
    """[(hold view, reason)] the held bets to sell for the new picks that took their slots (displaced), unless the sale is dust
    (MIN_SWAP_PCT of equity, as for any swap) or the market was bought a moment ago (cooldown)."""
    out = []
    for h, c in displaced(held, picks, ledger):
        view = hold_view(h, ledger)
        shares, average, _ = sell_fill(view)
        if cooldown.blocks(h.market_id, "sell") or shares * (average or 0) < config.MIN_SWAP_PCT * portfolio.bankroll:
            continue
        out.append((view, f"swap: {c.race} {c.side.upper()} {c.market_id} z {c.z:.2f} vs {held_z(h):.2f} held"))
    return out


def hold_wants(picks, skipped, portfolio, ledger, held=None, candidates=(), swapped=None, patient=None):
    """[(Want or None, BucketStatus)] in bucket order: one row per pick, sized at one slot of its bucket (hold_book_pct x bucket share
    / HOLD_SLOTS); one per held bet (`held`, from held_bets) with no pick, which keeps its slot without a top-up (why: its candidate's
    decision in `candidates`; over the bucket's slots, or being swapped out: `swapped` {(race, wins): reason}); then a row for the bucket's
    empty slots, which stay cash. `skipped` says why a bucket with candidates has no pick. A position already above its slot gets no
    buy: the invest caps block new buys only (it is sold only by a swap or a negative edge). A pick whose bet is frozen
    (Ledger.freeze_falls) gets no buy, nor does one with a patient entry resting (the bid counts against the budget)."""
    equity = portfolio.bankroll
    bids = patient.entries("hold") if patient else {}
    budget_room = config.HOLD_BOOK_PCT * equity - book_value(portfolio.holdings, ledger, "hold") - (patient.entry_value("hold") if patient else 0.0)
    held_in = lambda race, wins: book_value((h for h in portfolio.holdings if (h.race, h.wins_when) == (race, wins)), ledger, "hold")
    out = []
    for name, _, _, share in config.BUCKETS:
        slots, chosen = config.HOLD_SLOTS[name], picks.get(name, [])
        slot = config.HOLD_BOOK_PCT * share / slots
        for c in chosen:
            target, value = slot * equity, held_in(c.race, c.wins_when)
            want, limit = bet_room(c, portfolio, target - value, budget_room, "the slot target", "the hold book budget")
            frozen = ledger.frozen.get((c.race, c.wins_when))
            resting = bids.get((c.market_id, c.side))
            if frozen or resting:  # the pick keeps its slot, so the bucket does not fill up with another market meanwhile
                want = 0.0
            note = (frozen.why if frozen else
                    f"patient entry: {resting.remaining:,.0f} bid at {resting.order.price:.3f} (ask {c.price:.3f})" if resting else
                    "buy" if want >= config.MIN_ORDER_VALUE else
                    f"no buy: {value:,.0f} of {target:,.0f}, {limit}")
            budget_room -= want
            out.append((Want(c, target, value, want, "hold", f"hold book, {name}: edge {c.edge * 100:.1f}c, ROI {c.expected_return:.1%}, z {c.z:.2f}"),
                        BucketStatus(name, slot, c, value, want, note)))
        picked = {(c.race, c.wins_when) for c in chosen}
        mine = (held or {}).get(name, [])
        idle = [h for h in mine if (h.race, h.wins_when) not in picked]
        for h in idle:
            key, over = (h.race, h.wins_when), "over the slots, " if mine.index(h) >= slots else ""
            note = (f"{swapped[key]}: sold" if swapped and key in swapped else
                    f"held, {over}no top-up: {held_reason(h, candidates)}")
            out.append((None, BucketStatus(name, slot, None, held_in(h.race, h.wins_when), note=note, holding=h)))
        empty = slots - len(chosen) - len(idle)
        if not chosen and not idle:
            why = f"{skipped[name]}: cash" if name in skipped else "no candidate: cash"
            out.append((None, BucketStatus(name, slot * slots, note=why)))
        elif empty > 0:
            out.append((None, BucketStatus(name, slot * empty, note=f"{empty} of {slots} slots empty: cash")))
    return out


def hold_bids(rows, spread_of, patient, taken, now):
    """The hold_wants rows with patient entries applied: a buy on a spread wider than one tick rests one tick above the best bid for
    entry_minutes of its edge instead of buying at the ask (`taken`: the markets where a fast entry rests or is planned)."""
    out = []
    for w, status in rows:
        entry = w and w.want >= config.MIN_ORDER_VALUE and patient_bid(
            w.opp, w.opp.edge, spread_of.get(w.opp.market_id, 0.0), (w.opp.market_id, w.opp.side), patient, taken)
        if entry:
            status = replace(status, note=f"patient entry: bid {entry[0]:.3f} (ask {w.opp.price:.3f}) for {entry[1]} min")
            w = rest_want(w, entry, now)
        out.append((w, status))
    return out


def stale_bids(picks, ledger, patient):
    """{(market id, side): why} of the hold book's resting bids to cancel: the bet is no longer a pick, is frozen, or has an edge too big to wait."""
    chosen = {(c.market_id, c.side): c for cs in picks.values() for c in cs}
    out = {}
    for key in patient.entries("hold") if patient else ():
        c = chosen.get(key)
        if c is None:
            out[key] = "no longer a hold pick"
        elif (c.race, c.wins_when) in ledger.frozen:
            out[key] = "frozen"
        elif entry_minutes(c.edge) is None:
            out[key] = f"edge {c.edge * 100:.1f}c: buying at the ask"
    return out


def held_reason(h, candidates):
    """Why a held bet gets no top-up this loop: the decision on its own contract, else on an equivalent one, else no candidate."""
    mine = [c for c in candidates if (c.race, c.wins_when) == (h.race, h.wins_when) and c.decision]
    own = next((c for c in mine if (c.market_id, c.side) == (h.market_id, h.side)), None)
    return (own or mine[0]).decision if mine else "not a candidate (no edge, quote or fair)"
