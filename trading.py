"""Sizing, exits and swaps. Pure logic: turns opportunities and a portfolio into orders."""
import math
from dataclasses import dataclass, replace

import config
from bigorder import TICK, exit_fill
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
    rest_until: float | None = None  # a patient entry: the buy rests at opp.price (a bid, not the ask) until this time


def entry_minutes(gap):
    """How long a patient entry bids for a buy whose gap (the fast part, or a hold bet's edge) is this big, or None for a gap too big to
    risk SIG catching up before a bid fills."""
    if gap > config.PATIENT_ENTRY_MAX_PART + EPS:
        return None
    return config.PATIENT_ENTRY_SHORT_MINUTES if gap > config.PATIENT_ENTRY_SHORT_PART + EPS else config.PATIENT_ENTRY_MINUTES


def patient_bid(c, gap, spread, key, patient, taken=()):
    """(price, minutes) of a buy's patient entry (one tick above the best bid, for entry_minutes; a new position or a top-up), or None to
    buy at the ask: patient entries off, a spread of one tick, a bid already resting or planned in this market (`taken`: keys), a big gap,
    or a bet whose last bid ended unfilled within the hour unless the spread is over PATIENT_REBID_SPREAD and it has had fewer than
    PATIENT_MAX_BIDS bids in a row (then it is bid again)."""
    minutes = entry_minutes(gap)
    if (not (patient and config.PATIENT_ENTRIES) or spread <= TICK + EPS or minutes is None or key in taken or key in patient.entries()):
        return None
    ended = patient.ended_bids(*key)
    if ended and not (spread > config.PATIENT_REBID_SPREAD + EPS and ended < config.PATIENT_MAX_BIDS):
        return None
    return round(round((c.price - spread) / TICK + 1) * TICK, 3), minutes


def rest_want(w, entry, now):
    """The want as a patient entry: its buy rests at the bid for the entry's minutes instead of buying at the ask."""
    bid, minutes = entry
    return replace(w, opp=replace(w.opp, price=bid), rest_until=now + minutes * 60,
                   reason=w.reason + f"; patient entry: bid {bid:.3f} for {minutes} min")


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


def sell_fill(h):
    """(shares, average price, worst price) of selling h into the bids at once: the bids within SWAP_BID_BAND of the best when the
    book levels are known, else what the best bid takes. (0, None, None) when nothing can be sold."""
    if h.sell_ladder:
        return exit_fill(h.sell_ladder, h.shares, config.SWAP_BID_BAND)
    shares = _sell_shares(h)
    return (shares, h.sell_price, h.sell_price) if shares else (0, None, None)


def sell_order(h, reason, book="hold", fill=None):
    """Sell what the book will take at the sell price, or nothing when it takes less than one share. With `fill` (from sell_fill)
    the order is that many shares limited at the worst price of the fill, so the whole size trades at the average price."""
    shares, _, price = fill if fill else (_sell_shares(h), None, h.sell_price)
    return [Order("sell", h.side, h.race, h.market_id, shares, price, h.fair, reason, book)] if shares else []


def swap_out(w, new_score, worst_first, portfolio, cooldown, margin):
    """The (holding, score) from `worst_first` (sorted by score, lowest first) worth selling to fund buying `w`, or None. The score
    is the expected return in v1 and the fast part in the fast book. It
    must be a different bet, not bought a moment ago, beaten by `margin`, big enough to be worth two spread crossings
    (MIN_SWAP_PCT of equity), and its proceeds must really buy at least MIN_ORDER_VALUE of `w`, so the bot never sells
    and then fails to buy."""
    o = w.opp
    for pair in worst_first:
        h, score = pair
        if (h.race, h.wins_when) == (o.race, o.wins_when) or cooldown.blocks(h.market_id, "sell"):
            continue
        if new_score - score < margin - EPS:
            return None  # worst_first is sorted, so no later holding is beaten by enough either
        shares, average, _ = sell_fill(h)
        proceeds = shares * (average or 0)
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
        pair = swap_out(w, w.opp.expected_return, worst_first, portfolio, cooldown, config.SWAP_MARGIN)
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
        value = min(w.want, investable)
        shares = math.floor(value / o.price) if w.rest_until else buy_shares(o, value)  # a resting bid is not limited by the shares on offer
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
                          w.reason or f"edge {o.edge * 100:.1f}c, half Kelly {o.half_kelly:.1%}", w.book,
                          rest_until=w.rest_until, rest_kind="entry" if w.rest_until else None))
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


def cap_order_sizes(orders, equity, scale=lambda order: 1.0):
    """Hard cap on any single buy (see max_order_value x `scale(order)`: the election guard cuts the buys of uncalled races). A bigger
    buy is cut down to the cap (or dropped if what is left is below MIN_ORDER_VALUE). Sells are exempt, so the bot can always exit
    in full. Returns (orders, notes)."""
    kept, notes = [], []
    for o in orders:
        label = f"{o.race} {o.side.upper()} {o.market_id}"
        max_value = max_order_value(equity) * scale(o)
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
