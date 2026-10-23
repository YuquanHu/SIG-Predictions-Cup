"""One planning pass: opportunities -> top trades -> sizing -> exits and swaps -> orders. Pure logic."""
from dataclasses import dataclass

import config
from strategy import find_opportunities, keep_top
from trading import compute_wants, sell_decisions, sell_orders, size_buys


@dataclass
class Plan:
    candidates: list
    chosen: list
    wants: list
    decisions: list  # [(holding, reason)] marked to sell
    sells: list
    buys: list
    notes: list

    @property
    def orders(self):
        return self.sells + self.buys


def make_plan(races, fair_values, portfolio):
    candidates = find_opportunities(races, fair_values)
    chosen = keep_top(candidates, config.TOP_N)
    wants = compute_wants(chosen, portfolio)
    decisions = sell_decisions(portfolio, wants)
    buys, notes = size_buys(wants, portfolio.investable)
    return Plan(candidates, chosen, wants, decisions, sell_orders(decisions), buys, notes)


def depth_targets(plan, limit):
    """SIG markets worth an order-book read: positions to sell first, then wanted buys in Kelly order."""
    markets = [h.market_id for h, _ in plan.decisions]
    markets += [w.opp.market_id for w in plan.wants if w.want >= config.MIN_ORDER_VALUE]
    return list(dict.fromkeys(markets))[:limit]
