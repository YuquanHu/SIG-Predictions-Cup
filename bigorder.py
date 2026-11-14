"""Patient and early exits: how big a "big order" is in a market, where a sell rests so that one fills it, where an early-exit listing
goes in front of a liquidity wall, and what an immediate sale into the bids really returns. Pure. Prices are in the held side's own book (a No holding uses the mirrored Yes book)."""
import math

import config

TICK = 0.005
MAX_PRICE = 0.995


def big_order_size(week_volume):
    """SUSQies that count as a big order in a market that traded `week_volume` over the last 7 days."""
    return max(config.BIG_ORDER_MIN, min(config.BIG_ORDER_CAP, config.BIG_ORDER_WEEK_SHARE * week_volume))


def rest_price(rest_ladder, shares, bid, big):
    """The highest price a sell of `shares` can rest at so that one big order still fills all of it: the value of the asks at or
    below that price (they queue ahead of it), plus the sell itself, must not exceed `big`. A sell that cannot fit rests one tick
    above the bid, which is the best ask when the spread is one tick. When the ladder is as long as the book read (DEPTH_LEVELS) more
    asks may hide behind it, so the price stops at its last level."""
    first = round(bid / TICK) + 1
    last = round(MAX_PRICE / TICK)
    if len(rest_ladder) >= config.DEPTH_LEVELS:
        last = max(first, round(rest_ladder[-1][0] / TICK))
    best = first
    for tick in range(first, last + 1):
        price = tick * TICK
        ahead = sum(p * q for p, q in rest_ladder if p <= price + 1e-9)
        if ahead + shares * price > big:
            break
        best = tick
    return round(best * TICK, 3)


def _value(level):
    return level[0] * level[1]


def _is_wall(level, neighbour):
    """True when an ask level (price, shares) is WALL_RATIO times the value of the `neighbour` level (both worth WALL_MIN_VALUE or more)."""
    return _value(level) >= config.WALL_RATIO * _value(neighbour) - 1e-9


def wall_price(rest_ladder, structural, floor):
    """Where an early-exit listing rests in the asks `rest_ladder` (best first): never above `structural` nor more than EARLY_EXIT_MAX_TICKS
    above the best ask (the cap), and just in front of a liquidity wall when that is at least `floor` (the entry price plus a profit).
    Only levels worth WALL_MIN_VALUE or more count, as a wall or as what a wall is compared with: smaller ones (and empty ticks) are
    skipped, so a level is compared with the nearest one that counts (the next one up for the best ask, the one below for the others).
      the best ask is a wall   one tick below it (the bid on a one-tick spread: it sells at once), else at the wall, if at least floor
      a wall within the cap    one tick below it if at least floor
      otherwise                the cap"""
    levels = [(p, q) for p, q in rest_ladder if q > 0]
    if not levels:
        return structural
    best = levels[0][0]
    top = best + config.EARLY_EXIT_MAX_TICKS * TICK
    cap = round(min(top, structural), 3)
    counted = [l for l in levels if _value(l) >= config.WALL_MIN_VALUE - 1e-9]
    if len(counted) > 1 and counted[0][0] == best and _is_wall(counted[0], counted[1]):
        choice = next((p for p in (best - TICK, best) if p >= floor - 1e-9), cap)
        return round(min(choice, structural), 3)
    for below, level in zip(counted, counted[1:]):
        if level[0] > top + 1e-9:
            break
        if _is_wall(level, below):
            front = level[0] - TICK
            return round(min(front, structural), 3) if front >= floor - 1e-9 else cap
    return cap


def exit_fill(sell_ladder, shares, band):
    """(shares sold, average price, worst price) for selling up to `shares` into the bids no more than `band` below the best bid;
    (0, None, None) when there are no bids."""
    shares = math.floor(shares)
    if not sell_ladder or shares < 1:
        return 0, None, None
    top = sell_ladder[0][0]
    taken, value, worst = 0, 0.0, None
    for price, qty in sell_ladder:
        if price < top - band - 1e-9 or taken >= shares:
            break
        take = min(qty, shares - taken)
        taken, value, worst = taken + take, value + take * price, price
    return (taken, value / taken, worst) if taken else (0, None, None)
