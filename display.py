"""Console formatting for quotes."""
import time

import config
from strategy import z_score


def fmt(x, width=6):
    return f"{x:.3f}".rjust(width) if x is not None else "-".rjust(width)


def fmt_size(x):
    return f"{x:,.0f}".rjust(8) if x is not None else "-".rjust(8)


def age(q):
    return f"{time.time() - q['updated']:5.1f}s" if q["updated"] else "   n/a"


def print_quote(label, q):
    if q["bid"] is None and q["ask"] is None:
        print(f"    {label:<20} unavailable")
        return
    mid = spread = None
    if q["bid"] is not None and q["ask"] is not None:
        mid, spread = (q["bid"] + q["ask"]) / 2, q["ask"] - q["bid"]
    print(f"    {label:<20} bid {fmt(q['bid'])} x{fmt_size(q['bid_size'])}   "
          f"ask {fmt(q['ask'])} x{fmt_size(q['ask_size'])}   "
          f"mid {fmt(mid)}  spread {fmt(spread)}  age {age(q)}")


def table_lines(columns, rows):
    widths = [max(len(str(r[i])) for r in (columns, *rows)) for i in range(len(columns))]
    return ["  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip() for r in (columns, *rows)]


def print_table(columns, rows):
    print("\n".join(table_lines(columns, rows)))


OPPORTUNITY_COLUMNS = ("race", "mkt", "side", "price", "fair", "edge", "kelly", "half", "avail", "decision")


OPPORTUNITY_COLUMNS_V2 = ("race", "mkt", "side", "price", "fair", "edge", "ROI", "z", "avail", "decision")  # v2: the hold book picks by z, not Kelly


def opportunity_cells(c, v2=False):
    avail = f"{c.size:,.0f}" if c.size is not None else "-"
    sizing = (f"{c.expected_return:.1%}", f"{c.z:.2f}") if v2 else (f"{c.kelly * 100:.1f}%", f"{c.half_kelly * 100:.1f}%")
    return (c.race, c.market_id, c.side.upper(), f"{c.price:.3f}", f"{c.fair:.3f}", f"{c.edge * 100:+.1f}c", *sizing, avail, c.decision)


HOLD_COLUMNS = ("bucket", "target", "pick", "chance", "edge", "ROI", "z", "held", "to buy", "note")


def print_hold_report(buckets, equity):
    """The hold book bucket by bucket: each pick (what it holds against its slot, and why it does or does not buy), each held bet
    keeping its slot without a top-up (chance, edge, ROI and z at its sell price), then the bucket's empty slots."""
    rows = []
    for b in buckets:
        c, h = b.pick, b.holding
        if c:
            odds = (f"{c.fair:.0%}", f"{c.edge * 100:+.1f}c", f"{c.expected_return:.1%}", f"{c.z:.2f}")
        elif h and h.exp_return is not None:  # a held bet without a pick: at its sell price
            odds = (f"{h.fair:.0%}", f"{(h.fair - h.sell_price) * 100:+.1f}c", f"{h.exp_return:.1%}", f"{z_score(h.fair, h.sell_price):.2f}")
        else:
            odds = ("-",) * 4
        bet = c or h
        rows.append((b.bucket, f"{b.share:.1%} = {b.share * equity:,.0f}", f"{bet.race} {bet.side.upper()} {bet.market_id}" if bet else "-",
                     *odds, f"{b.held:,.0f}" if bet else "-", f"{b.want:,.0f}" if c else "-", b.note))
    print("\nHold book (positions per bucket: " + ", ".join(f"{name} {config.HOLD_SLOTS[name]}" for name, *_ in config.BUCKETS[::-1]) + ")")
    print_table(HOLD_COLUMNS, rows)


FAST_COLUMNS = ("position", "usual gap", "fast part", "chance", "ROI", "z", "held", "to buy", "held for", "status", "note")


def elapsed(seconds):
    """Time since an entry as '2h 15m' (under an hour: '35m'); '-' when there is none."""
    if seconds is None:
        return "-"
    minutes = int(max(seconds, 0) // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


EXIT_NAMES = {"early": "early exit", "patient": "patient exit", "swap": "swap exit", "entry": "patient entry"}


def status(row):
    """'early exit, 23h 10m left' / 'patient exit, 42m left' / 'swap exit, 3m left' for a position with a sell resting, 'swap exit' for
    a swapped-out one between two steps, 'patient exit, waiting' for one that cannot rest yet, else '-'."""
    name = EXIT_NAMES.get(row.order_kind)
    if name and row.resting_left is not None:
        return f"{name}, {elapsed(row.resting_left)} left"
    if row.order_kind == "swap":
        return name
    return f"{name}, waiting" if name and row.waiting else "-"


def print_fast_report(rows):
    """The fast book: the positions it holds with what they still have to earn, and the best candidates with why they do or do not enter.
    Chance, ROI and z are taken at the ask for a candidate and at the sell price for a held position (what keeping it earns to resolution)."""
    def odds(r):
        if r.fair is None or not r.price:
            return ("-", "-", "-")
        return (f"{r.fair:.0%}", f"{(r.fair - r.price) / r.price:.1%}", f"{z_score(r.fair, r.price):.2f}")
    def cents(x):
        return "-" if x is None else f"{x * 100:+.1f}c"
    def size(x):  # the usual gap is a size, with no sign
        return "-" if x is None else f"{x * 100:.1f}c"
    print(f"\nFast book (entry at {config.FAST_MIN_EDGE * 100:.0f}c beyond the usual gap, at most {config.FAST_MAX_POSITIONS} positions)")
    print_table(FAST_COLUMNS, [(r.label, size(r.usual), cents(r.part), *odds(r), f"{r.held:,.0f}" if r.held else "-",
                                f"{r.want:,.0f}" if r.want else "-", elapsed(r.age),
                                status(r), r.note) for r in rows]
                or [("none",) + ("",) * (len(FAST_COLUMNS) - 1)])


HOLDING_COLUMNS = ("race", "mkt", "side", "shares", "value", "sell", "fair", "edge", "exp return", "z", "action")


def holding_cells(h, action, ledger=None):
    """One holdings row, its edge, expected return and z at the sell price; with a ledger (v2) the row also says how many shares
    each book owns."""
    def price(x):
        return f"{x:.3f}" if x is not None else "-"
    books = ()
    if ledger is not None:
        books = (f"{ledger.shares(h.market_id, h.side, 'hold'):,.0f} / {ledger.shares(h.market_id, h.side, 'fast'):,.0f}",)
    return (h.race, h.market_id, h.side.upper(), f"{h.shares:,.0f}", f"{h.value:,.0f}",
            price(h.sell_price), price(h.fair),
            f"{(h.fair - h.sell_price) * 100:+.1f}c" if h.exp_return is not None else "-",
            f"{h.exp_return:+.1%}" if h.exp_return is not None else "-",
            f"{z_score(h.fair, h.sell_price):+.2f}" if h.exp_return is not None else "-", *books, action)


def print_opportunities(chosen, v2=False):
    """The loop's picks with the biggest raw edge (fair less price, before any election haircut) first; the last thing a loop prints."""
    print("\nTop opportunities (by edge)")
    print_table(OPPORTUNITY_COLUMNS_V2 if v2 else OPPORTUNITY_COLUMNS,
                [opportunity_cells(c, v2) for c in sorted(chosen, key=lambda c: c.edge, reverse=True)])


def print_report(portfolio, sells, notes, ledger=None):
    """Holdings and sizing of one loop, in reading order. With the v2 ledger the table shows ROI instead of Kelly and the
    hold / fast split of each holding."""
    p = portfolio
    print(f"\ncash {p.cash:,.0f} | invested {p.invested:,.0f} ({p.invested / p.bankroll:.1%}) | "
          f"bankroll {p.bankroll:,.0f} | investable cash {p.investable:,.0f}")
    v2 = ledger is not None
    if p.holdings or p.outside_value:
        print("\nHoldings")
        actions = {(o.market_id, o.side): o.reason for o in sells}
        columns = (*HOLDING_COLUMNS[:-1], "hold / fast", HOLDING_COLUMNS[-1]) if v2 else HOLDING_COLUMNS
        print_table(columns, [holding_cells(h, actions.get((h.market_id, h.side), "hold"), ledger) for h in p.holdings])
        if p.outside_value:
            print(f"  plus {p.outside_value:,.0f} in markets outside races.json (counted, never traded)")
    print("\nSizing")
    for note in notes or ["nothing to size"]:
        print(f"  {note}")
