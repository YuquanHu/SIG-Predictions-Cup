"""Sizing, exits and swaps. Pure logic: turns opportunities and a portfolio into orders."""
import math
from dataclasses import dataclass, replace

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
    book: str = "hold"        # which book the buy belongs to (v1 plans are all "hold")
    reason: str | None = None  # the order's reason; None: the v1 text with the half Kelly


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


def _sell_shares(h):
    """What the book will take of this holding at its sell price (0 when it takes less than one share)."""
    return math.floor(min(h.shares, h.sell_depth or 0))


def buy_shares(opp, value):
    """Whole shares of `opp` that `value` buys, limited by the shares on offer at its price."""
    return min(math.floor(value / opp.price), math.floor(opp.size or 0))


def sell_order(h, reason, book="hold"):
    """Sell what the book will take at the sell price, or nothing when it takes less than one share."""
    shares = _sell_shares(h)
    return [Order("sell", h.side, h.race, h.market_id, shares, h.sell_price, h.fair, reason, book)] if shares else []


def swap_out(w, new_roi, worst_first, portfolio, cooldown):
    """The (holding, roi) from `worst_first` (sorted by roi, lowest first) worth selling to fund buying `w`, or None. It
    must be a different bet, not bought a moment ago, beaten by SWAP_MARGIN, big enough to be worth two spread crossings
    (MIN_SWAP_PCT of equity), and its proceeds must really buy at least MIN_ORDER_VALUE of `w`, so the bot never sells
    and then fails to buy."""
    o = w.opp
    for pair in worst_first:
        h, roi = pair
        if (h.race, h.wins_when) == (o.race, o.wins_when) or cooldown.blocks(h.market_id, "sell"):
            continue
        if new_roi - roi < config.SWAP_MARGIN - EPS:
            return None  # worst_first is sorted, so no later holding is beaten by enough either
        proceeds = _sell_shares(h) * h.sell_price
        if proceeds < config.MIN_SWAP_PCT * portfolio.bankroll:
            continue
        buy_value = min(w.want, proceeds, max_order_value(portfolio.bankroll))
        if buy_shares(o, buy_value) * o.price >= config.MIN_ORDER_VALUE:
            return pair
    return None


def bet_room(opp, portfolio, target_room, budget_room, target_name, budget_name):
    """(value still allowed, name of the limit that binds) for buying more of `opp`: the book's target, the position cap,
    the race cap and the book's budget each leave some room, and the tightest wins."""
    equity = portfolio.bankroll
    race_cap = config.RACE_CAP if opp.sources >= 2 else config.SINGLE_SOURCE_RACE_CAP
    room = {target_name: target_room,
            "the position cap": config.POSITION_CAP * equity - portfolio.held_in_bet(opp.race, opp.wins_when),
            "the race cap": race_cap * equity - portfolio.held_in_race(opp.race),
            budget_name: budget_room}
    limit = min(room, key=room.get)
    return max(0.0, room[limit]), limit


def exit_decisions(portfolio):
    """[(holding, reason)] for every position whose fair value fell below its sell price: sell whatever else is going on."""
    return [(h, f"exit: fair {h.fair:.3f} is below sell price {h.sell_price:.3f}")
            for h in portfolio.holdings if h.negative_edge]


def sell_decisions(portfolio, wants, cooldown):
    """[(holding, reason)] to sell: any position whose fair value fell below its sell price; and, when no
    investable cash is left, the worst held position for a new bet that beats it by SWAP_MARGIN (see swap_out).
    Exits ignore the cooldown: getting out of a bad position always wins."""
    decisions = exit_decisions(portfolio)
    exiting = [h for h, _ in decisions]

    if portfolio.investable >= config.MIN_ORDER_VALUE:
        return decisions
    worst_first = sorted(((h, h.exp_return) for h in portfolio.holdings if h.exp_return is not None and h not in exiting),
                         key=lambda pair: pair[1])
    for w in wants:
        if w.want < config.MIN_ORDER_VALUE:
            continue
        pair = swap_out(w, w.opp.expected_return, worst_first, portfolio, cooldown)
        if pair:
            worst_first.remove(pair)
            decisions.append((pair[0], f"swap: {w.opp.race} {w.opp.side.upper()} {w.opp.market_id} returns "
                                       f"{w.opp.expected_return:.1%} vs {pair[1]:.1%} held"))
    return decisions


def sell_orders(decisions):
    return [order for h, reason in decisions for order in sell_order(h, reason)]


def size_buys(wants, investable):
    """Buy orders in Kelly order, limited by the investable cash and the shares on offer at the price.
    Returns (orders, one explanatory note per want)."""
    buys, notes = [], []
    for w in wants:
        o = w.opp
        label = f"{o.race} {o.side.upper()} {o.market_id}"
        shares = buy_shares(o, min(w.want, investable))
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
                          w.reason or f"edge {o.edge * 100:.1f}c, half Kelly {o.half_kelly:.1%}", w.book))
        notes.append(f"{label}: buy {shares:,} (target {w.target:,.0f}, held {w.held:,.0f}, want {w.want:,.0f})")
    return buys, notes


def limit_orders(sells, buys, limit):
    """At most `limit` orders: sells first (they cut risk), then buys in Kelly order. Returns (sells, buys, skipped)."""
    kept_sells = sells[:limit]
    kept_buys = buys[:limit - len(kept_sells)]
    return kept_sells, kept_buys, sells[len(kept_sells):] + buys[len(kept_buys):]


def max_order_value(equity):
    """The most one buy may cost: ORDER_CAP_PCT of total equity (cash + positions)."""
    return config.ORDER_CAP_PCT * equity


def cap_order_sizes(orders, equity):
    """Hard cap on any single buy (see max_order_value). A bigger buy is cut down to the cap (or dropped if what is left
    is below MIN_ORDER_VALUE). Sells are exempt, so the bot can always exit in full. Returns (orders, notes)."""
    max_value = max_order_value(equity)
    kept, notes = [], []
    for o in orders:
        label = f"{o.race} {o.side.upper()} {o.market_id}"
        if o.action == "sell" or o.value <= max_value:
            kept.append(o)
            continue
        shares = math.floor(max_value / o.price)
        if shares * o.price < config.MIN_ORDER_VALUE:
            notes.append(f"{label}: buy dropped (single-order cap is {max_value:,.0f})")
            continue
        notes.append(f"{label}: buy cut from {o.shares:,.0f} to {shares:,} shares (single-order cap {max_value:,.0f})")
        kept.append(replace(o, shares=shares))
    return kept, notes
