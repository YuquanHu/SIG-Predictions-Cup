"""Hold book (structural mispricings): one position per probability bucket, picked by ROI, sized from the bucket's share
of the book. Pure logic: it turns opportunities, baselines, the ledger and the portfolio into Wants for trading.size_buys."""
from dataclasses import dataclass

import config
from books import book_value
from races import needs_rules_check
from strategy import EPS, keep_best
from trading import Want, bet_room

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
    """One row of the hold report: what the bucket holds, what it would pick and why it does or does not buy."""
    bucket: str
    share: float             # the bucket's share of total equity (hold_book_pct x bucket share)
    pick: object = None      # the chosen Opportunity, or None when no structural candidate qualifies
    held: float = 0.0        # value the hold book already holds in the picked bet
    want: float = 0.0        # value still to buy
    note: str = ""


def spreads(races):
    """{market_id: SIG yes spread} for every contract with a two-sided quote."""
    return {c["market_id"]: c["yes"]["ask"] - c["yes"]["bid"] for race in races.values() for c in race["contracts"]
            if c["yes"]["bid"] is not None and c["yes"]["ask"] is not None}


def eligible(candidates, baselines, races, cooldown):
    """The candidates the hold book may buy. Every other candidate gets the reason in `decision`.
    Structural on the side its gap favours, enough edge, a tight spread, rules checked, not sold a moment ago,
    and of two equivalent bets (Dem Yes / Rep No) only the cheaper."""
    spread_of = spreads(races)
    keep = []
    for c in candidates:
        b = baselines.get(c.market_id)
        if b is None or not b.structural:
            c.decision = "not structural"
        elif c.side != b.side:
            c.decision = "the other side of the usual gap"
        elif c.edge < config.HOLD_MIN_EDGE - EPS:
            c.decision = f"edge {c.edge * 100:.1f}c is below {config.HOLD_MIN_EDGE * 100:.0f}c"
        elif needs_rules_check(races[c.race]):
            c.decision = "rules not checked (race has an independent candidate)"
        elif spread_of.get(c.market_id, 0) > config.HOLD_MAX_SPREAD + EPS:
            c.decision = f"spread {spread_of[c.market_id] * 100:.1f}c is too wide"
        elif cooldown.blocks(c.market_id, "buy"):
            c.decision = "sold within the cooldown"
        else:
            c.decision = None
            keep.append(c)
    return keep_best(keep, lambda c: (c.race, c.wins_when), lambda c: -c.price,
                     lambda w: f"same bet as {w.side.upper()} {w.market_id}, which is cheaper")


def choose_picks(candidates, ledger):
    """({bucket: Opportunity}, {bucket: why it has no pick}). Buckets are taken from the most certain down. A bucket's pick
    is its best ROI, but a riskier bucket only picks a candidate whose ROI is above the best pick of every more certain
    bucket: taking more risk for the same or less return is pointless. The previous pick keeps its place unless the
    best candidate has HOLD_SWITCH_MARGIN more ROI. The ledger remembers the picks."""
    by_bucket = {}
    for c in candidates:
        previous = ledger.bucket_of(c.market_id, c.side)
        by_bucket.setdefault(bucket_for(c.fair, previous), []).append(c)
    picks, skipped = {}, {}
    hurdle, hurdle_pick = 0.0, None  # the best ROI picked so far, and the pick that set it
    for name, *_ in reversed(config.BUCKETS):
        group = by_bucket.get(name, [])
        beats = [c for c in group if c.expected_return > hurdle + EPS]
        for c in group:
            if c not in beats:
                c.decision = (f"{name}: ROI {c.expected_return:.1%} does not beat the {hurdle_pick[0]} pick's {hurdle:.1%}")
        if group and not beats:
            skipped[name] = (f"best ROI {max(c.expected_return for c in group):.1%} does not beat the "
                             f"{hurdle_pick[0]} pick's {hurdle:.1%}: more risk for no more return")
        if not beats:
            continue
        best = max(beats, key=lambda c: c.expected_return)
        last = ledger.picks.get(name)
        current = next((c for c in beats if (c.market_id, c.side) == tuple(last or ())), None)
        keep_current = current is not None and best.expected_return - current.expected_return <= config.HOLD_SWITCH_MARGIN + EPS
        picks[name] = current if keep_current else best
        for c in beats:
            c.decision = "PICK" if c is picks[name] else (
                f"{name}: {picks[name].race} {picks[name].side.upper()} {picks[name].market_id} is the pick")
        hurdle, hurdle_pick = picks[name].expected_return, (name, picks[name])
    ledger.picks = {b: (c.market_id, c.side) for b, c in picks.items()}
    return picks, skipped


def hold_wants(picks, skipped, portfolio, ledger):
    """[(Want, BucketStatus)] one per bucket, in bucket order. `skipped` says why a bucket with candidates has no pick. A position already above its target gets no buy and no sale:
    the invest caps block new buys only."""
    equity = portfolio.bankroll
    budget_room = config.HOLD_BOOK_PCT * equity - book_value(portfolio.holdings, ledger, "hold")
    out = []
    for name, _, _, share in config.BUCKETS:
        status = BucketStatus(name, config.HOLD_BOOK_PCT * share)
        c = picks.get(name)
        if c is None:
            status.note = f"{skipped[name]}: stays cash" if name in skipped else "no structural candidate: stays cash"
            out.append((None, status))
            continue
        target = status.share * equity
        held = book_value((h for h in portfolio.holdings if h.race == c.race and h.wins_when == c.wins_when), ledger, "hold")
        want, limit = bet_room(c, portfolio, target - held, budget_room, "the bucket target", "the hold book budget")
        status.pick, status.held, status.want = c, held, want
        status.note = (f"buy up to {want:,.0f}" if want >= config.MIN_ORDER_VALUE else
                       f"holds {held:,.0f} against a target of {target:,.0f}: no buy (limit: {limit})")
        budget_room -= want
        out.append((Want(c, target, held, want, "hold", f"hold book, {name}: edge {c.edge * 100:.1f}c, ROI {c.expected_return:.1%}"),
                    status))
    return out
