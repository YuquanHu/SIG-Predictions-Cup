"""One planning pass: opportunities -> picks -> sizing -> exits -> orders. Pure logic. make_plan is strategy v1 (top 4 by
Kelly, with swaps); make_plan_v2 is the two-book strategy: the hold book and the fast book (plan.md, end)."""
from dataclasses import dataclass, field

import config
from fast import fast_plan
from hold import choose_picks, displaced, eligible, held_bets, hold_bids, hold_swaps, hold_wants, spreads, stale_bids
from strategy import find_opportunities, keep_top
from trading import compute_wants, exit_decisions, limit_orders, sell_decisions, sell_fill, sell_order, sell_orders, size_buys


@dataclass
class Plan:
    candidates: list
    chosen: list
    wants: list
    decisions: list  # [(holding, reason)] marked to sell
    sells: list
    buys: list
    notes: list
    skipped: list  # orders left for the next loop because of MAX_ORDERS_PER_LOOP
    buckets: list = field(default_factory=list)    # v2: one BucketStatus per hold bucket
    fast_exits: list = field(default_factory=list)  # v2: [(fast share of a holding, reason)] the fast book sells
    fast_rows: list = field(default_factory=list)   # v2: FastRow per held fast position and top candidate
    fast_depth: list = field(default_factory=list)  # v2: markets whose book a swap needs read (new candidates first, then the held ones)
    fast_swaps: list = field(default_factory=list)  # v2: (market id, side) swapped out this loop, sold by steps from the next (patient exits on)
    stale_entries: dict = field(default_factory=dict)  # v2: (market id, side) -> why its patient-entry bid is cancelled
    ended_steps: dict = field(default_factory=dict)  # v2: (market id, side) -> why its swap's step sale stops (the position is kept)

    @property
    def orders(self):
        return self.sells + self.buys


def make_plan(races, fair_values, portfolio, cooldown):
    candidates = find_opportunities(races, fair_values)
    chosen = keep_top(candidates, config.TOP_N)
    all_wants = compute_wants(chosen, portfolio)
    cooled = [w for w in all_wants if cooldown.blocks(w.opp.market_id, "buy")]
    wants = [w for w in all_wants if w not in cooled]
    decisions = sell_decisions(portfolio, wants, cooldown)
    buys, notes = size_buys(wants, portfolio.investable)
    notes += [f"{w.opp.race} {w.opp.side.upper()} {w.opp.market_id}: no buy (sold within the last "
              f"{config.COOLDOWN_SECONDS}s: cooldown)" for w in cooled]
    sells, buys, skipped = limit_orders(sell_orders(decisions), buys, config.MAX_ORDERS_PER_LOOP)
    notes += [f"{o.race} {o.side.upper()} {o.market_id}: {o.action} x{o.shares:,.0f} skipped "
              f"(limit of {config.MAX_ORDERS_PER_LOOP} orders per loop)" for o in skipped]
    return Plan(candidates, chosen, wants, decisions, sells, buys, notes, skipped)


def make_plan_v2(races, fair_values, portfolio, cooldown, baselines, ledger, volumes, now, patient=None, first_loop=False):
    """Both books. Hold: up to HOLD_SLOTS picks per bucket from the structural markets, the bets it holds first, each one slot of the
    bucket's share of the book; a held bet is swapped for a new one with HOLD_SWAP_Z more z. In the first loop of a run (`first_loop`) the
    book is built from the held bets alone, new bets come from loop 2. Fast:
    markets lagging beyond their usual gap that trade enough (`volumes`), and the unwinds. Fast buys come first (a fast gap closes, a structural one stays).
    An exit for any position whose edge turned negative sells the whole position. Records the hold picks and the top-up freezes in `ledger`."""
    candidates = find_opportunities(races, fair_values)
    freezes = ledger.freeze_falls(portfolio.holdings, now)
    held = held_bets(portfolio.holdings, ledger)
    picks, skipped = choose_picks(eligible(candidates, baselines, races, cooldown), ledger, held, first_loop)
    fast = fast_plan(candidates, baselines, races, portfolio, ledger, cooldown, volumes, now, patient)
    decisions = [(h, why) for h, why in exit_decisions(portfolio) if not (patient and patient.reserved(h.market_id, h.side))]
    leaving = {(h.market_id, h.side) for h, _ in decisions} | {(o.market_id, o.side) for o in fast.sells}   # already being sold this loop
    swaps = [(v, why) for v, why in hold_swaps(held, picks, ledger, portfolio, cooldown)
             if (v.market_id, v.side) not in leaving and not (patient and patient.reserved(v.market_id, v.side))]
    rows = hold_wants(picks, skipped, portfolio, ledger, held, candidates, {(v.race, v.wins_when): why for v, why in swaps}, patient)
    rows = hold_bids(rows, spreads(races), patient, {(w.opp.market_id, w.opp.side) for w in fast.wants if w.rest_until}, now)
    wants = fast.wants + [w for w, _ in rows if w is not None]
    investable = max(0.0, portfolio.investable - (patient.entry_value() if patient else 0.0))  # resting bids may still spend it
    buys, notes = size_buys([w for w in wants if w.want >= config.MIN_ORDER_VALUE], investable)
    notes = freezes + notes
    hold_sells = [o for v, why in swaps for o in sell_order(v, why, "hold", sell_fill(v))]   # a swap's bids are read (fast_depth) before it sells
    sells, buys, skipped = limit_orders(sell_orders(decisions) + fast.sells + hold_sells, buys, config.MAX_ORDERS_PER_LOOP)
    notes += [f"{o.race} {o.side.upper()} {o.market_id}: {o.action} x{o.shares:,.0f} skipped "
              f"(limit of {config.MAX_ORDERS_PER_LOOP} orders per loop)" for o in skipped]
    return Plan(candidates, [w.opp for w in wants], wants, decisions, sells, buys, notes, skipped,
                [status for _, status in rows], [(e.view, e.reason) for e in fast.exits], fast.rows,
                fast.swap_markets + [h.market_id for h, _ in displaced(held, picks, ledger)], fast.swaps, {**fast.stale_entries, **stale_bids(picks, ledger, patient)},
                fast.ended_steps)


def depth_targets(plan, limit):
    """SIG markets worth an order-book read: positions to sell first, then wanted buys in plan order."""
    markets = [h.market_id for h, _ in plan.decisions + plan.fast_exits]
    markets += [w.opp.market_id for w in plan.wants if w.want >= config.MIN_ORDER_VALUE]
    markets += plan.fast_depth
    return list(dict.fromkeys(markets))[:limit]
