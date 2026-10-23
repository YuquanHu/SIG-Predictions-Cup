"""Sizing, exits and swaps. Pure logic: turns opportunities and a portfolio into orders."""
import math
from dataclasses import dataclass

import config
from orders import Order
from strategy import EPS, Opportunity


@dataclass
class Want:
    """How much more of one opportunity we are willing to hold, in currency."""
    opp: Opportunity
    target: float  # half Kelly x bankroll, scaled so all targets together fit the investment cap
    held: float    # value already held in the same bet (Dem Yes and Rep No count as one bet)
    want: float    # target - held, limited by what the race cap leaves in this race


def compute_wants(chosen, portfolio):
    total_half_kelly = sum(c.half_kelly for c in chosen)
    scale = min(1.0, config.INVEST_CAP / total_half_kelly) if total_half_kelly else 1.0
    wants = []
    for c in chosen:
        target = c.half_kelly * scale * portfolio.bankroll
        held = portfolio.held_in_bet(c.race, c.wins_when)
        cap = config.RACE_CAP if c.sources >= 2 else config.SINGLE_SOURCE_RACE_CAP
        race_room = cap * portfolio.bankroll - portfolio.held_in_race(c.race)
        wants.append(Want(c, target, held, max(0.0, min(target - held, race_room))))
    return wants


def _sell_order(h, reason):
    """Sell what the book will take at the sell price, or nothing when it takes less than one share."""
    shares = math.floor(min(h.shares, h.sell_depth or 0))
    return [Order("sell", h.side, h.race, h.market_id, shares, h.sell_price, h.fair, reason)] if shares else []


def sell_decisions(portfolio, wants):
    """[(holding, reason)] to sell: any position whose fair value fell below its sell price; and, when no
    investable cash is left, the worst held position for a new bet that beats it by SWAP_MARGIN."""
    exiting = [h for h in portfolio.holdings if h.negative_edge]
    decisions = [(h, f"exit: fair {h.fair:.3f} is below sell price {h.sell_price:.3f}") for h in exiting]

    if portfolio.investable >= config.MIN_ORDER_VALUE:
        return decisions
    worst_first = sorted((h for h in portfolio.holdings if h.exp_return is not None and h not in exiting),
                         key=lambda h: h.exp_return)
    for w in wants:
        if w.want < config.MIN_ORDER_VALUE:
            continue
        rivals = [h for h in worst_first if not (h.race == w.opp.race and h.wins_when == w.opp.wins_when)]
        if rivals and w.opp.expected_return - rivals[0].exp_return >= config.SWAP_MARGIN - EPS:
            worst = rivals[0]
            worst_first.remove(worst)
            decisions.append((worst, f"swap: {w.opp.race} {w.opp.side.upper()} {w.opp.market_id} returns "
                                     f"{w.opp.expected_return:.1%} vs {worst.exp_return:.1%} held"))
    return decisions


def sell_orders(decisions):
    return [order for h, reason in decisions for order in _sell_order(h, reason)]


def decide_sells(portfolio, wants):
    return sell_orders(sell_decisions(portfolio, wants))


def size_buys(wants, investable):
    """Buy orders in Kelly order, limited by the investable cash and the shares on offer at the price.
    Returns (orders, one explanatory note per want)."""
    buys, notes = [], []
    for w in wants:
        o = w.opp
        label = f"{o.race} {o.side.upper()} {o.market_id}"
        shares = math.floor(min(w.want, investable) / o.price)
        shares = min(shares, math.floor(o.size or 0))
        if shares * o.price < config.MIN_ORDER_VALUE:
            if w.want < config.MIN_ORDER_VALUE:
                why = f"target {w.target:,.0f}, already held {w.held:,.0f}: nothing to add"
            elif investable < config.MIN_ORDER_VALUE:
                why = "no investable cash left"
            else:
                why = f"only {o.size or 0:,.0f} shares on offer at {o.price:.3f}"
            notes.append(f"{label}: no buy ({why})")
            continue
        investable -= shares * o.price
        buys.append(Order("buy", o.side, o.race, o.market_id, shares, o.price, o.fair,
                          f"edge {o.edge * 100:.1f}c, half Kelly {o.half_kelly:.1%}"))
        notes.append(f"{label}: buy {shares:,} (target {w.target:,.0f}, held {w.held:,.0f}, want {w.want:,.0f})")
    return buys, notes
