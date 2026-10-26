"""Fast book (SIG prices that lag a real market): buy when the edge is at least FAST_MIN_EDGE beyond the market's usual gap,
sell when SIG has caught up to that usual gap. Pure logic: it turns opportunities, baselines, the ledger and the portfolio
into Wants (buys) and sells for the fast book only. The hold book's own exit (negative edge) still sells a whole position.

    entry   fast part = executable edge - the usual gap on that side (gapmodel.fast_part), at least FAST_MIN_EDGE
    rank    fast part / price
    size    one slice = FAST_POSITION_PCT of the fast book (MEDIUM_SLICE_FACTOR of it for a market with middling volume, THIN_SLICES
            of it and only for a big fast part when the volume is low: liquidity.py), at most FAST_MAX_POSITIONS positions
    unwind  (fair - sell price) - usual gap is down to NOISE_TOLERANCE; or FAST_MAX_HOURS have passed; or a better
            candidate needs the slot (swap)
"""
from dataclasses import dataclass, field, replace

import config
from books import book_value
from gapmodel import UNCLASSIFIED, fast_part
from hold import spreads
from liquidity import liquidity_blocker, millions, slice_factor
from strategy import EPS
from trading import Want, bet_room, sell_order, swap_out

ROWS_SHOWN = 5  # candidates in the report besides the held positions


@dataclass
class FastRow:
    """One line of the fast report: a held fast position or a candidate."""
    label: str
    usual: float | None  # the usual gap on this side
    part: float          # fast part now (a held position: still ahead of the usual gap when sold now)
    held: float
    want: float
    note: str


@dataclass
class FastPlan:
    wants: list = field(default_factory=list)  # Wants for the fast buys
    exits: list = field(default_factory=list)  # [(fast share of a holding, reason)] to sell
    sells: list = field(default_factory=list)  # their sell Orders
    rows: list = field(default_factory=list)   # FastRow for the report


def caught_up(baseline, side, exit_edge):
    """True when what selling now would still earn beyond the usual gap (exit_edge = fair - sell price) is down to noise."""
    part = fast_part(baseline, side, exit_edge)
    return part is not None and part <= config.NOISE_TOLERANCE + EPS


def time_up(entry_time, now):
    return now - entry_time >= config.FAST_MAX_HOURS * 3600


def fast_views(portfolio, ledger):
    """The fast book's share of each holding: the holding with shares and value cut to the fast lots."""
    views = []
    for h in portfolio.holdings:
        owned = min(ledger.shares(h.market_id, h.side, "fast"), h.shares)
        if owned > 0:
            views.append(replace(h, shares=owned, value=h.value * owned / h.shares))
    return views


def _key(x):
    return x.market_id, x.side


def _leftover(view, baseline):
    """The fast part a held position still has if sold now, or None when there is no fair value or sell price."""
    if view.fair is None or view.sell_price is None:
        return None
    return fast_part(baseline, view.side, view.fair - view.sell_price)


def unwinds(views, baselines, ledger, now):
    """[(view, reason)] for the fast positions to sell: time is up, or SIG has caught up. A position with a negative edge is
    left to the hold book's exit, which sells the whole position."""
    out = []
    for v in views:
        if v.negative_edge:
            continue
        entered = min(l.entry_time for l in ledger.lots_of(v.market_id, v.side, "fast"))
        left = _leftover(v, baselines.get(v.market_id, UNCLASSIFIED))
        if time_up(entered, now):
            out.append((v, f"time stop: held {(now - entered) / 3600:.0f}h"))
        elif left is not None and left <= config.NOISE_TOLERANCE + EPS:
            out.append((v, f"caught up: {left * 100:+.1f}c of fast part left"))
    return out


def entry_blocker(c, part, spread, volume, ledger, cooldown, leaving):
    """Why the fast book may not enter `c`, or None."""
    if part < config.FAST_MIN_EDGE - EPS:
        return f"fast part {part * 100:+.1f}c is below {config.FAST_MIN_EDGE * 100:.0f}c"
    if why := liquidity_blocker(volume, part):
        return why
    if _key(c) in leaving:
        return "being sold this loop"
    if ledger.shares(c.market_id, "no" if c.side == "yes" else "yes") > 0:
        return "the other side of this market is held"
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
        part = fast_part(baselines.get(c.market_id, UNCLASSIFIED), c.side, c.edge)
        if part is None:
            continue
        parts[_key(c)] = part
        why = entry_blocker(c, part, spread_of.get(c.market_id, 0.0), volumes.get(c.market_id), ledger, cooldown, leaving)
        if why:
            blocked[_key(c)] = why
        else:
            entering.append(c)
    entering.sort(key=lambda c: parts[_key(c)] / c.price, reverse=True)
    best = {}
    for c in entering:  # of two equivalent bets (Dem Yes / Rep No) that both qualify, buy the cheaper one
        group = (c.race, c.wins_when)
        if group not in best or c.price < best[group].price:
            best[group] = c
    for c in entering:
        winner = best[(c.race, c.wins_when)]
        if c is not winner:
            blocked[_key(c)] = f"same bet as {winner.side.upper()} {winner.market_id}, which is cheaper ({winner.price:.3f} vs {c.price:.3f})"
    return [c for c in entering if best[(c.race, c.wins_when)] is c], parts, blocked


def fast_plan(candidates, baselines, races, portfolio, ledger, cooldown, volumes, now):
    """The fast book's buys, sells and report rows for one loop."""
    equity = portfolio.bankroll
    views = fast_views(portfolio, ledger)
    exits = unwinds(views, baselines, ledger, now)
    leaving = {_key(v) for v, _ in exits} | {_key(v) for v in views if v.negative_edge}
    entering, parts, blocked = rank_entries(candidates, baselines, races, ledger, cooldown, volumes, leaving)

    standing = [(v, _leftover(v, baselines.get(v.market_id, UNCLASSIFIED))) for v in views if _key(v) not in leaving]
    worst_first = sorted(((v, left / v.sell_price) for v, left in standing if left is not None), key=lambda pair: pair[1])
    held_keys = {_key(v) for v in views}
    free = config.FAST_MAX_POSITIONS - len(held_keys - leaving)
    budget = config.FAST_BOOK_PCT * equity - book_value(portfolio.holdings, ledger, "fast")
    full_slice = config.FAST_BOOK_PCT * config.FAST_POSITION_PCT * equity
    wants, outcome = [], {}  # outcome: key -> (value to buy, note)
    for c in entering:
        key, part = _key(c), parts[_key(c)]
        volume = volumes.get(c.market_id)
        slice_ = full_slice * slice_factor(volume, part)
        held = book_value([h for h in portfolio.holdings if (h.race, h.wins_when) == (c.race, c.wins_when)], ledger, "fast")
        value, limit = bet_room(c, portfolio, slice_ - held, budget, "the position slice", "the fast book budget")
        new = key not in held_keys
        if value < config.MIN_ORDER_VALUE:
            outcome[key] = (0.0, f"no room to open a position (limit: {limit})" if new else
                            f"holds {held:,.0f} against a slice of {slice_:,.0f}: no buy (limit: {limit})")
            continue
        w = Want(c, slice_, held, value, "fast", f"fast book: edge {c.edge * 100:.1f}c, {part * 100:.1f}c beyond the usual gap")
        if new and free <= 0:
            pair = swap_out(w, part / c.price, worst_first, portfolio, cooldown)
            if pair is None:
                outcome[key] = (0.0, f"book full ({config.FAST_MAX_POSITIONS} positions), none worth swapping out")
                continue
            worst_first.remove(pair)
            leaving.add(_key(pair[0]))
            exits.append((pair[0], f"swap: {c.race} {c.side.upper()} {c.market_id} returns {part / c.price:.1%} vs {pair[1]:.1%} held"))
            free += 1
        if new:
            free -= 1
        budget -= value
        wants.append(w)
        c.decision = f"FAST: {part * 100:.1f}c beyond the usual gap"
        outcome[key] = (value, f"buy up to {value:,.0f}" + ("" if slice_ == full_slice else
                                                         f" (slice x{slice_ / full_slice:g}: volume {millions(volume.total)})"))

    sells = [o for v, reason in exits for o in sell_order(v, reason, "fast")]
    rows = report_rows(views, exits, candidates, baselines, parts, {**blocked, **{k: n for k, (_, n) in outcome.items()}},
                       {k: v for k, (v, _) in outcome.items()})
    return FastPlan(wants, exits, sells, rows)


def report_rows(views, exits, candidates, baselines, parts, notes, bought):
    """The held fast positions, then the best ROWS_SHOWN other classified candidates by fast part."""
    reasons = {_key(v): r for v, r in exits}
    held = {_key(v) for v in views}
    rows = []
    for v in views:
        b = baselines.get(v.market_id, UNCLASSIFIED)
        left = _leftover(v, b)
        rows.append(FastRow(f"{v.race} {v.side.upper()} {v.market_id}", b.for_side(v.side), 0.0 if left is None else left,
                            v.value, bought.get(_key(v), 0.0), reasons.get(_key(v)) or notes.get(_key(v)) or "holds"))
    others = sorted((c for c in candidates if _key(c) in parts and _key(c) not in held), key=lambda c: parts[_key(c)], reverse=True)
    for c in others[:ROWS_SHOWN]:
        k = _key(c)
        rows.append(FastRow(f"{c.race} {c.side.upper()} {c.market_id}", baselines[c.market_id].for_side(c.side), parts[k],
                            0.0, bought.get(k, 0.0), notes.get(k, "")))
    return rows
