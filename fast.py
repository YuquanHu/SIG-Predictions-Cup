"""Fast book (SIG prices that lag a real market): buy when the edge is at least FAST_MIN_EDGE beyond the market's usual gap,
sell when SIG has caught up to that usual gap. Pure logic: it turns opportunities, baselines, the ledger and the portfolio
into Wants (buys) and sells for the fast book only. The hold book's own exit (negative edge) still sells a whole position.

    entry   fast part = executable edge - the usual gap on that side (gapmodel.fast_part), at least FAST_MIN_EDGE
    rank    fast part, biggest first (in price, not divided by it: the noise of a fair value is about the same in cents at any price,
            so a 4c part on a 0.20 longshot is mostly noise even though its ROI is high)
    size    one slice = FAST_POSITION_PCT of the fast book (MEDIUM_SLICE_FACTOR of it for a market with middling volume, THIN_SLICES
            of it and only for a big fast part when the volume is low: liquidity.py), at most FAST_MAX_POSITIONS positions
    unwind  (fair - sell price) - usual gap is down to NOISE_TOLERANCE; or FAST_MAX_HOURS have passed; or a better
            candidate needs the slot (swap). A position resting for a patient exit keeps its slot and can be swapped out too;
            with patient exits on, a swap sells by steps: a limit at the best bid resting SWAP_STEP_MINUTES, then the next
            bid level down for what is left (PatientBook keeps the step state), each loop only while the best fast entry still
            has FAST_SWAP_Z more z (of the fast part) than the position does at the step's price (ended_steps); else the steps end and it is kept
    patient entry  with PATIENT_ENTRIES (and patient exits) on, a buy (a new position or a top-up; the hold book's too, trading.patient_bid) on a spread wider than one tick first rests its buy
            one tick above the best bid for PATIENT_ENTRY_MINUTES (PATIENT_ENTRY_SHORT_MINUTES for a fast part above
            PATIENT_ENTRY_SHORT_PART, none above PATIENT_ENTRY_MAX_PART), then buys at the ask (a bid is repeated on a spread over PATIENT_REBID_SPREAD, up to PATIENT_MAX_BIDS in a row: trading.patient_bid); a candidate that stops qualifying,
            or whose fast part rises above PATIENT_ENTRY_MAX_PART, cancels its bid
    early   with EARLY_EXITS (and patient exits) on, the unwind is by age instead: from the first entry all fast shares are listed at
            the structural value (fair - usual gap) for EARLY_EXIT_HOURS, re-listed when it moves more than EARLY_EXIT_BAND; then a
            patient exit rests until the time stop plus PATIENT_MAX_MINUTES; then the sale at the bid. One resting sell per position.
            Held back while the fast part left is still FAST_MIN_EDGE or more (held_back): the position is a buy then, so a resting
            listing is cancelled and the bot buys or sells a position, never both.
"""
import math
from dataclasses import dataclass, field, replace

import config
from bigorder import MAX_PRICE, TICK, big_order_size, rest_price, wall_price
from books import book_value
from gapmodel import UNCLASSIFIED, fast_part
from hold import spreads
from liquidity import liquidity_blocker, millions, slice_factor
from orders import Order
from strategy import EPS, z_score
from trading import Want, bet_room, entry_minutes, patient_bid, rest_want, sell_fill, sell_order, swap_out

ROWS_SHOWN = 5  # candidates in the report besides the held positions
SWAP_READS = 2  # with a full book, the best new candidates whose order book is read each loop so a swap knows the shares on offer
ROW_KIND = {"early": "early", "caught_up": "patient", "time_stop": "patient", "patient": "patient", "swap": "swap", "step": "swap",
            "entry": "entry"}  # Exit or rest kind -> report status
WAITING_NOTE = {"caught_up": " - cannot rest yet (7-day volume or book levels not known), waiting", "early": " - no fair value or book levels to list at, waiting"}


@dataclass
class FastRow:
    """One line of the fast report: a held fast position or a candidate."""
    label: str
    usual: float | None  # the usual gap on this side
    part: float          # fast part now (a held position: still ahead of the usual gap when sold now)
    held: float
    want: float
    note: str
    age: float | None = None  # seconds since the position's first fast entry (a held position only)
    order_kind: str | None = None       # how it is being sold: "early", "patient" or "swap" (None: it is not)
    resting_left: float | None = None  # seconds until its resting sell is cancelled (None: no sell is resting)
    waiting: bool = False              # an early or patient exit that cannot rest yet (nothing is resting)
    fair: float | None = None          # fair probability of the side (None: no fair value)
    price: float | None = None         # the price its ROI and z are taken at: the ask for a candidate, the sell price for a held position


@dataclass
class Exit:
    """A fast position the book sells: `view` is its fast shares, `kind` is caught_up, time_stop, swap (picked this loop to make
    room), step (a swap being sold by steps) or early (listed at the structural value)."""
    view: object
    kind: str
    reason: str
    at_bid_ok: bool = True  # False: a patient exit that may only rest, not be sold at the bid yet (it is not caught up by the 1c rule)


@dataclass
class FastPlan:
    wants: list = field(default_factory=list)  # Wants for the fast buys
    exits: list = field(default_factory=list)  # Exits to sell
    sells: list = field(default_factory=list)  # their sell Orders
    rows: list = field(default_factory=list)   # FastRow for the report
    swap_markets: list = field(default_factory=list)  # markets whose book a swap needs read: the new candidates' (the shares on offer) and the held ones' (the bids)
    swaps: list = field(default_factory=list)  # (market id, side) swapped out this loop, sold by steps from the next loop (patient exits on)
    stale_entries: dict = field(default_factory=dict)  # (market id, side) -> why its resting patient-entry bid is cancelled
    ended_steps: dict = field(default_factory=dict)  # (market id, side) -> why the swap selling it by steps stopped paying (it is kept)


def caught_up(baseline, exit_edge):
    """True when what selling now would still earn beyond the usual gap (exit_edge = fair - sell price) is down to noise."""
    part = fast_part(baseline, exit_edge)
    return part is not None and part <= config.NOISE_TOLERANCE + EPS


def first_entry(ledger, view):
    """When the fast book first bought this position (its oldest fast lot)."""
    return min(l.entry_time for l in ledger.lots_of(view.market_id, view.side, "fast"))


def time_up(entry_time, now):
    return now - entry_time >= config.FAST_MAX_HOURS * 3600


def fast_views(portfolio, ledger, patient=None):
    """The fast book's share of each holding: the holding with shares and value cut to the fast lots, less the shares a resting
    sell already holds (`patient`), so nothing is sold twice."""
    views = []
    for h in portfolio.holdings:
        owned = min(ledger.shares(h.market_id, h.side, "fast"), h.shares)
        if patient:
            owned -= patient.reserved(h.market_id, h.side)
        if owned >= 1:  # less than a share cannot be sold
            views.append(replace(h, shares=owned, value=h.value * owned / h.shares))
    return views


def bet_key(x):
    return x.market_id, x.side


def structural_price(view, baseline):
    """The price where the position's fast part is used up (fair - usual gap) on the tick, or None without a fair value or a usual gap."""
    usual = baseline.usual()
    if view.fair is None or usual is None:
        return None
    return round(min(max(round((view.fair - usual) / TICK) * TICK, TICK), MAX_PRICE), 3)


def early_until(entered):
    """When the early-exit listing of a position first bought at `entered` ends (never after its time stop)."""
    return entered + min(config.EARLY_EXIT_HOURS, config.FAST_MAX_HOURS) * 3600


def listed_early(ledger, view, now):
    """True while a position is in its early-exit phase (early exits on): listed at its structural value once its fast part left is under FAST_MIN_EDGE."""
    return now < early_until(first_entry(ledger, view))


def leftover(view, baseline):
    """The fast part a held position still has if sold now, or None when there is no fair value or sell price."""
    if view.fair is None or view.sell_price is None:
        return None
    return fast_part(baseline, view.fair - view.sell_price)


def held_back(left):
    """True when a position still has the fast part of an entry (`left`, from `leftover`): it is a buy, so no early exit is listed
    and a resting one is cancelled. The bot buys or sells a position, never both."""
    return left is not None and left >= config.FAST_MIN_EDGE - EPS


def held_part(view, baseline):
    """The fast part a held position still has if sold at once at the average price its sale gets (see sell_fill): its leftover
    at that price, None when that cannot be told."""
    _, average, _ = sell_fill(view)
    return leftover(replace(view, sell_price=average or view.sell_price), baseline)


def part_z(fair, part):
    """A fast part as z, the edge per unit of the bet's own risk (strategy.z_score): what swaps compare, so a cent of fast part
    on a coinflip counts for less than on a near-certain bet."""
    return z_score(fair, fair - part)


def exit_due(left):
    """True when a position with `left` of fast part still ahead should be sold: 1c or less, or with patient exits below
    PATIENT_TRIGGER (the sell then rests above the bid first)."""
    if left is None:
        return False
    return left <= config.PATIENT_TRIGGER - EPS if config.PATIENT_EXITS else left <= config.NOISE_TOLERANCE + EPS


def holding_to_resolution(now):
    """True inside the election window (FAST_HOLD_UNTIL): the fast book sells only on a negative edge or a swap."""
    return config.FAST_HOLD_UNTIL is not None and now < config.FAST_HOLD_UNTIL


def unwinds(views, baselines, ledger, now, early=False):
    """[Exit] for the fast positions to sell: time is up, or SIG has caught up. With `early` (early exits on) the exit goes by age
    instead: listed early, then the patient exit until the time stop. A position with a negative edge is left to the hold book's exit,
    which sells the whole position. Nothing while holding to resolution."""
    if holding_to_resolution(now):
        return []
    out = []
    for v in views:
        if v.negative_edge:
            continue
        entered = first_entry(ledger, v)
        left = leftover(v, baselines.get(v.market_id, UNCLASSIFIED))
        if time_up(entered, now):
            out.append(Exit(v, "time_stop", f"time stop: held {(now - entered) / 3600:.0f}h"))
        elif early and listed_early(ledger, v, now):
            if held_back(left):
                continue
            out.append(Exit(v, "early", f"early exit: listed up to the structural value, in front of any wall, for {config.EARLY_EXIT_HOURS}h from entry"))
        elif early:
            out.append(Exit(v, "caught_up", "patient exit: the early exit is over", at_bid_ok=False))
        elif exit_due(left):
            label = "patient exit" if config.PATIENT_EXITS else "caught up"
            out.append(Exit(v, "caught_up", f"{label}: {left * 100:+.1f}c of fast part left", left <= config.NOISE_TOLERANCE + EPS))
    return out


def patient_deadline(ledger, view, now):
    """When a patient exit of this position is given up: PATIENT_MAX_MINUTES after the time stop (FAST_MAX_HOURS from its first entry),
    or after now when the time stop is already due. The order rests the whole time until then."""
    return max(first_entry(ledger, view) + config.FAST_MAX_HOURS * 3600, now) + config.PATIENT_MAX_MINUTES * 60


def resting_order(view, reason, volume, rest_until):
    """One sell of all the view's shares resting until `rest_until` where a big order would fill all of it (bigorder.rest_price), or
    None when the book levels or the market's week volume are not known."""
    shares = math.floor(view.shares)
    if not (shares and view.rest_ladder is not None and view.sell_price is not None and volume and volume.week):
        return None
    price = rest_price(view.rest_ladder, shares, view.sell_price, big_order_size(volume.week))
    return Order("sell", view.side, view.race, view.market_id, shares, price, view.fair, reason, "fast", rest_until=rest_until, rest_kind="patient")


def early_order(view, baseline, reason, rest_until, entry_price):
    """One sell of all the view's shares resting until `rest_until` at the structural value, held to EARLY_EXIT_MAX_TICKS above the best
    ask and placed in front of a liquidity wall when that clears the entry price by EARLY_EXIT_MIN_PROFIT (bigorder.wall_price). None
    without a structural value or book levels. A price at or under the bid fills at once."""
    shares, structural = math.floor(view.shares), structural_price(view, baseline)
    if not shares or structural is None or view.rest_ladder is None:
        return None
    price = wall_price(view.rest_ladder, structural, entry_price + config.EARLY_EXIT_MIN_PROFIT)
    return Order("sell", view.side, view.race, view.market_id, shares, price, view.fair, reason, "fast", rest_until=rest_until, rest_kind="early")


def next_step_price(view, last_price):
    """Where a swap's next step sells: the best bid for the first step (`last_price` None), else the highest bid level below the last
    step's price (one tick below it when the levels are not known). None without a bid."""
    if view.sell_price is None:
        return None
    if last_price is None:
        return round(view.sell_price, 3)
    return round(max((p for p, _ in view.sell_ladder or () if p < last_price - EPS), default=max(last_price - TICK, TICK)), 3)


def step_order(view, last_price, reason, rest_until):
    """One step of a swap's sale: all the view's shares resting until `rest_until` at next_step_price. None when there is nothing
    to sell or no bid."""
    shares, price = math.floor(view.shares), next_step_price(view, last_price)
    if not shares or price is None:
        return None
    return Order("sell", view.side, view.race, view.market_id, shares, price, view.fair, reason, "fast", rest_until=rest_until,
                 rest_kind="step")


def ended_steps(owned, stepping, resting, entering, parts, baselines, patient):
    """{(market id, side): why} of the swapped-out positions whose sale stops this loop: the best fast entry of another bet (`entering`,
    best first) no longer has FAST_SWAP_Z more z (of the fast part) than the position still has at the price its step sells at (a
    resting step's own price, else the next step's). Each step down raises the position's z, so the steps can end the swap. The
    position is kept."""
    out = {}
    for key in stepping & set(owned):
        v = owned[key]
        last = patient.step_price(*key)
        price = last if key in resting else next_step_price(v, last)
        part = None if price is None or v.fair is None else fast_part(baselines.get(v.market_id, UNCLASSIFIED), v.fair - price)
        if part is None:
            out[key] = "no fair value or bid to weigh the swap"
            continue
        held = part_z(v.fair, part)
        rival = next((c for c in entering if (c.race, c.wins_when) != (v.race, v.wins_when)), None)
        best = part_z(rival.fair, parts[bet_key(rival)]) if rival else None
        if best is None or best - held < config.FAST_SWAP_Z - EPS:
            against = f"z {best:.2f} for {rival.race} {rival.side.upper()} {rival.market_id}" if rival else "no fast entry"
            out[key] = (f"swap no longer pays: z {held:.2f} held at {price:.3f} vs {against}, {config.FAST_SWAP_Z:.2f} more needed")
    return out


def exit_orders(e, baselines, volumes, patient, ledger, now):
    """The orders that carry out one Exit. With patient exits on, a caught-up or time-stop exit rests above the bid until its patient
    deadline while its patience is not used up, an early exit rests at the structural value until the early phase ends, a swap sells
    nothing this loop and each step rests SWAP_STEP_MINUTES (step_order).
    Without them a swap sells into the bids near the best at their average price. Everything else sells at the best bid. A patient
    exit that cannot rest and is not caught up by the 1c rule waits; a time stop that cannot rest sells at the bid."""
    v = e.view
    if patient is not None:
        if e.kind == "swap":
            return []
        if e.kind == "step":
            order = step_order(v, patient.step_price(v.market_id, v.side), e.reason, now + config.SWAP_STEP_MINUTES * 60)
            return [order] if order else []
        if e.kind == "early":
            order = early_order(v, baselines.get(v.market_id, UNCLASSIFIED), e.reason, early_until(first_entry(ledger, v)),
                                ledger.entry_price(v.market_id, v.side, "fast"))
            return [order] if order else []
        if not patient.exhausted(v.market_id, v.side):
            order = resting_order(v, e.reason, volumes.get(v.market_id), patient_deadline(ledger, v, now))
            if order:
                return [order]
            if not e.at_bid_ok:
                return []
    return sell_order(v, e.reason, "fast", sell_fill(v) if e.kind == "swap" else None)


def entry_blocker(c, part, spread, volume, ledger, cooldown, leaving):
    """Why the fast book may not enter `c`, or None."""
    if part < config.FAST_MIN_EDGE - EPS:
        return f"fast part {part * 100:+.1f}c is below {config.FAST_MIN_EDGE * 100:.0f}c"
    if why := liquidity_blocker(volume, part):
        return why
    if bet_key(c) in leaving:
        return "being sold this loop"
    if ledger.shares(c.market_id, "no" if c.side == "yes" else "yes") > 0:
        return "the other side of this market is held"
    if frozen := ledger.frozen.get((c.race, c.wins_when)):
        return frozen.why
    if spread > config.FAST_MAX_SPREAD + EPS:
        return f"spread {spread * 100:.1f}c is too wide"
    if part - spread <= config.NOISE_TOLERANCE + EPS:  # the way out pays the spread: it would count as caught up at once
        return f"fast part {part * 100:+.1f}c does not survive the {spread * 100:.1f}c spread"
    if cooldown.blocks(c.market_id, "buy"):
        return "sold within the cooldown"
    return None


def rank_entries(candidates, baselines, races, ledger, cooldown, volumes, leaving):
    """(candidates the fast book may enter, best first, one per equivalent bet; {key: fast part}; {key: why not}). Unclassified markets have no fast
    part and are left out of all three."""
    spread_of = spreads(races)
    parts, blocked, entering = {}, {}, []
    for c in candidates:
        part = fast_part(baselines.get(c.market_id, UNCLASSIFIED), c.adj_edge)   # an uncertain fair value needs a bigger edge to enter
        if part is None:
            continue
        parts[bet_key(c)] = part
        why = entry_blocker(c, part, spread_of.get(c.market_id, 0.0), volumes.get(c.market_id), ledger, cooldown, leaving)
        if why:
            blocked[bet_key(c)] = why
        else:
            entering.append(c)
    entering.sort(key=lambda c: parts[bet_key(c)], reverse=True)
    best = {}
    for c in entering:  # of two equivalent bets (Dem Yes / Rep No) that both qualify, buy the cheaper one
        group = (c.race, c.wins_when)
        if group not in best or c.price < best[group].price:
            best[group] = c
    for c in entering:
        winner = best[(c.race, c.wins_when)]
        if c is not winner:
            blocked[bet_key(c)] = f"same bet as {winner.side.upper()} {winner.market_id}, which is cheaper ({winner.price:.3f} vs {c.price:.3f})"
    return [c for c in entering if best[(c.race, c.wins_when)] is c], parts, blocked


def step_exit(view, patient):
    """The Exit of a swapped-out position being sold by steps; its reason says where the next step goes."""
    last = patient.step_price(view.market_id, view.side)
    where = "at the best bid" if last is None else f"at the next bid below {last:.3f}"
    return Exit(view, "step", f"swap exit: selling {where}, {config.SWAP_STEP_MINUTES} min a step")


def fast_plan(candidates, baselines, races, portfolio, ledger, cooldown, volumes, now, patient=None):
    """The fast book's buys, sells and report rows for one loop. A position keeps its slot until it is being sold now: one resting for
    a patient or early exit (or waiting to rest) still counts, so a candidate for a full book must swap out the worst position, resting
    or not. A position has at most one resting sell: while one rests (or its cancel failed) nothing else sells it. A position in its
    early phase is listed only while its fast part left is under FAST_MIN_EDGE (held_back), so it is not bought and sold at once."""
    equity = portfolio.bankroll
    resting = patient.keys() if patient else set()
    views = [v for v in fast_views(portfolio, ledger, patient) if bet_key(v) not in resting]   # the fast shares free to sell
    owned = {bet_key(v): v for v in fast_views(portfolio, ledger)}   # every fast position, resting shares included
    stepping = patient.step_keys() if patient else set()
    early = bool(patient) and config.EARLY_EXITS
    listing = {k for k, v in owned.items() if early and listed_early(ledger, v, now)} - stepping
    exits = unwinds([v for v in views if bet_key(v) not in stepping], baselines, ledger, now, early)
    planned = {bet_key(e.view): (e, exit_orders(e, baselines, volumes, patient, ledger, now)) for e in exits}
    urgent = {k for k, v in owned.items() if v.negative_edge}
    leaving = ((set(planned) | resting) - listing) | urgent | stepping
    entering, parts, blocked = rank_entries(candidates, baselines, races, ledger, cooldown, volumes, leaving)
    ended = ended_steps(owned, stepping, resting, entering, parts, baselines, patient)
    stepping -= set(ended)
    steps = [step_exit(v, patient) for v in views if bet_key(v) in stepping and not v.negative_edge]  # a negative edge: the hold exit
    planned |= {bet_key(e.view): (e, exit_orders(e, baselines, volumes, patient, ledger, now)) for e in steps}
    sold_now = {k for k, (_, orders) in planned.items() if orders and not orders[0].resting} | urgent | stepping
    bids = patient.entries("fast") if patient else {}   # patient entries resting: their slot, budget and market are taken
    spread_of = spreads(races)

    occupied = (set(owned) - sold_now) | set(bids)
    held_parts = ((owned[k], held_part(owned[k], baselines.get(k[0], UNCLASSIFIED))) for k in occupied if k in owned)
    worst_first = sorted(((v, part_z(v.fair, part)) for v, part in held_parts if part is not None), key=lambda pair: pair[1])
    free = free_before = config.FAST_MAX_POSITIONS - len(occupied)
    budget = config.FAST_BOOK_PCT * equity - book_value(portfolio.holdings, ledger, "fast") - (patient.entry_value("fast") if patient else 0.0)
    full_slice = config.FAST_BOOK_PCT * config.FAST_POSITION_PCT * equity
    wants, outcome = [], {}  # outcome: key -> (value to buy, note)
    stale = {k: "no longer a fast entry" for k in set(bids) - {bet_key(c) for c in entering}}
    for c in entering:
        key, part = bet_key(c), parts[bet_key(c)]
        if key in bids:
            r = bids[key]
            if entry_minutes(part) is None:
                stale[key] = f"fast part {part * 100:.1f}c: buying at the ask"
            outcome[key] = (0.0, f"patient entry: {r.remaining:,.0f} bid at {r.order.price:.3f} (ask {c.price:.3f})"
                            + (", cancelled to buy at the ask" if key in stale else ""))
            continue
        volume = volumes.get(c.market_id)
        slice_ = full_slice * slice_factor(volume, part)
        held = book_value([h for h in portfolio.holdings if (h.race, h.wins_when) == (c.race, c.wins_when)], ledger, "fast")
        value, limit = bet_room(c, portfolio, slice_ - held, budget, "the position slice", "the fast book budget")
        new = key not in owned
        if value < config.MIN_ORDER_VALUE:
            outcome[key] = (0.0, f"no room to open a position (limit: {limit})" if new else
                            f"holds {held:,.0f} against a slice of {slice_:,.0f}: no buy (limit: {limit})")
            continue
        w = Want(c, slice_, held, value, "fast", f"fast book: edge {c.edge * 100:.1f}c, {part * 100:.1f}c beyond the usual gap")
        entry = patient_bid(c, part, spread_of.get(c.market_id, 0.0), key, patient)
        if new and free <= 0:
            pair = swap_out(w, part_z(c.fair, part), worst_first, portfolio, cooldown, config.FAST_SWAP_Z)
            if pair is None:
                outcome[key] = (0.0, f"book full ({config.FAST_MAX_POSITIONS} positions), none worth swapping out")
                continue
            worst_first.remove(pair)
            out = Exit(pair[0], "swap", f"swap: {c.race} {c.side.upper()} {c.market_id} has fast-part z {part_z(c.fair, part):.2f} vs {pair[1]:.2f} held"
                       + (", sold by steps from the next loop" if patient else ""))
            planned[bet_key(out.view)] = (out, exit_orders(out, baselines, volumes, patient, ledger, now))  # replaces an exit planned for it
            free += 1
        if new:
            free -= 1
        budget -= value
        c.decision = f"FAST: {part * 100:.1f}c beyond the usual gap"
        note = ""
        if entry:
            note = f"patient entry: bid {entry[0]:.3f} (ask {c.price:.3f}) for {entry[1]} min, "
            w = rest_want(w, entry, now)
        wants.append(w)
        outcome[key] = (value, note + f"buy up to {value:,.0f}" + ("" if slice_ == full_slice else
                                                     f" (slice x{slice_ / full_slice:g}: volume {millions(volume.total)})"))

    exits = [e for e, _ in planned.values()]
    sells = [o for _, orders in planned.values() for o in orders]
    waiting = {k for k, (e, orders) in planned.items() if not orders and e.kind in WAITING_NOTE}
    for k in waiting:  # an exit that can not rest yet: say so in the report instead of staying silent
        planned[k][0].reason += WAITING_NOTE[planned[k][0].kind]
    swaps = [bet_key(e.view) for e in exits if e.kind == "swap"] if patient else []
    newcomers = [c.market_id for c in entering if bet_key(c) not in owned][:SWAP_READS]   # a full book: a swap must know what both sides can trade
    swap_markets = newcomers + [owned[k].market_id for k in occupied if k in owned] if free_before <= 0 and newcomers else []
    shown = list(owned.values())  # the report also lists the positions resting for a patient exit or a step
    kinds = {bet_key(e.view): ROW_KIND.get(e.kind) for e in exits} | {k: "swap" for k in stepping | set(swaps)}
    rows = report_rows(shown, exits, candidates, baselines, parts, {**blocked, **{k: n for k, (_, n) in outcome.items()}, **ended},
                       {k: v for k, (v, _) in outcome.items()}, {bet_key(v): now - first_entry(ledger, v) for v in shown}, patient, now,
                       waiting, kinds)
    return FastPlan(wants, exits, sells, rows, swap_markets, swaps, stale, ended)


def report_rows(views, exits, candidates, baselines, parts, notes, bought, ages, patient, now, waiting, kinds):
    """The held fast positions (all their fast shares, the ones resting for a patient exit too), then the best ROWS_SHOWN other
    classified candidates by fast part. `kinds`: {key: row exit kind} of this loop's exits; a resting sell names its own."""
    reasons = {bet_key(e.view): e.reason for e in exits}
    held = {bet_key(v) for v in views}
    bids = patient.entries("fast") if patient else {}
    rows = []
    for v in views:
        b = baselines.get(v.market_id, UNCLASSIFIED)
        left = leftover(v, b)
        resting = patient.resting_of(v.market_id, v.side) if patient else None
        note = (f"{resting.remaining:,.0f} resting at {resting.order.price:.3f}" if resting else
                reasons.get(bet_key(v)) or notes.get(bet_key(v)) or "holds")
        resting = resting or bids.get(bet_key(v))   # a partly filled patient entry still bidding
        kind = kinds.get(bet_key(v)) or (ROW_KIND[resting.kind] if resting else None)
        rows.append(FastRow(f"{v.race} {v.side.upper()} {v.market_id}", b.usual(), 0.0 if left is None else left,
                            v.value, bought.get(bet_key(v), 0.0), note, ages.get(bet_key(v)), kind,
                            resting.deadline - now if resting else None, bet_key(v) in waiting, v.fair, v.sell_price))
    others = sorted((c for c in candidates if bet_key(c) in parts and bet_key(c) not in held), key=lambda c: parts[bet_key(c)], reverse=True)
    for c in others[:ROWS_SHOWN]:
        k = bet_key(c)
        bid = bids.get(k)
        rows.append(FastRow(f"{c.race} {c.side.upper()} {c.market_id}", baselines[c.market_id].usual(), parts[k],
                            0.0, bought.get(k, 0.0), notes.get(k, ""), order_kind="entry" if bid else None,
                            resting_left=bid.deadline - now if bid else None, fair=c.fair, price=c.price))
    return rows
