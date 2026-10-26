"""One planning pass: opportunities -> picks -> sizing -> exits -> orders. Pure logic. make_plan is strategy v1 (top 4 by
Kelly, with swaps); make_plan_v2 is the two-book strategy: the hold book and the fast book (plan.md, end)."""
from dataclasses import dataclass, field

import config
from fast import fast_plan
from hold import choose_picks, eligible, hold_wants
from strategy import find_opportunities, keep_top
from trading import compute_wants, exit_decisions, limit_orders, sell_decisions, sell_orders, size_buys


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


def make_plan_v2(races, fair_values, portfolio, cooldown, baselines, ledger, volumes, now):
    """Both books. Hold: one pick per bucket from the structural markets, sized from the bucket's share of the book. Fast:
    markets lagging beyond their usual gap that trade enough (`volumes`), and the unwinds. Fast buys come first (a fast gap closes, a structural one stays).
    An exit for any position whose edge turned negative sells the whole position. Records the hold picks in `ledger`."""
    candidates = find_opportunities(races, fair_values)
    picks, skipped = choose_picks(eligible(candidates, baselines, races, cooldown), ledger)
    rows = hold_wants(picks, skipped, portfolio, ledger)
    fast = fast_plan(candidates, baselines, races, portfolio, ledger, cooldown, volumes, now)
    wants = fast.wants + [w for w, _ in rows if w is not None]
    decisions = exit_decisions(portfolio)
    buys, notes = size_buys([w for w in wants if w.want >= config.MIN_ORDER_VALUE], portfolio.investable)
    sells, buys, skipped = limit_orders(sell_orders(decisions) + fast.sells, buys, config.MAX_ORDERS_PER_LOOP)
    notes += [f"{o.race} {o.side.upper()} {o.market_id}: {o.action} x{o.shares:,.0f} skipped "
              f"(limit of {config.MAX_ORDERS_PER_LOOP} orders per loop)" for o in skipped]
    return Plan(candidates, [w.opp for w in wants], wants, decisions, sells, buys, notes, skipped,
                [status for _, status in rows], fast.exits, fast.rows)


def depth_targets(plan, limit):
    """SIG markets worth an order-book read: positions to sell first, then wanted buys in plan order."""
    markets = [h.market_id for h, _ in plan.decisions + plan.fast_exits]
    markets += [w.opp.market_id for w in plan.wants if w.want >= config.MIN_ORDER_VALUE]
    return list(dict.fromkeys(markets))[:limit]
