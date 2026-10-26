"""Console formatting for quotes."""
import time

import config


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


def print_table(columns, rows):
    widths = [max(len(str(r[i])) for r in (columns, *rows)) for i in range(len(columns))]
    for r in (columns, *rows):
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip())


OPPORTUNITY_COLUMNS = ("race", "mkt", "side", "price", "fair", "edge", "kelly", "half", "avail", "decision")


OPPORTUNITY_COLUMNS_V2 = ("race", "mkt", "side", "price", "fair", "edge", "ROI", "avail", "decision")  # v2 ranks by ROI, not Kelly


def opportunity_cells(c, v2=False):
    avail = f"{c.size:,.0f}" if c.size is not None else "-"
    sizing = (f"{c.expected_return:.1%}",) if v2 else (f"{c.kelly * 100:.1f}%", f"{c.half_kelly * 100:.1f}%")
    return (c.race, c.market_id, c.side.upper(), f"{c.price:.3f}", f"{c.fair:.3f}", f"{c.edge * 100:+.1f}c", *sizing, avail, c.decision)


HOLD_COLUMNS = ("bucket", "target", "pick", "chance", "ROI", "held", "to buy", "note")


def print_hold_report(buckets, equity):
    """The hold book bucket by bucket: what it would pick, what it holds against its target, and why it does or does not buy."""
    rows = []
    for b in buckets:
        c = b.pick
        rows.append((b.bucket, f"{b.share:.1%} = {b.share * equity:,.0f}",
                     f"{c.race} {c.side.upper()} {c.market_id}" if c else "-", f"{c.fair:.0%}" if c else "-",
                     f"{c.expected_return:.1%}" if c else "-", f"{b.held:,.0f}" if c else "-",
                     f"{b.want:,.0f}" if c else "-", b.note))
    print("\nHold book (one position per bucket)")
    print_table(HOLD_COLUMNS, rows)


FAST_COLUMNS = ("position", "usual gap", "fast part", "held", "to buy", "note")


def print_fast_report(rows):
    """The fast book: the positions it holds with what they still have to earn, and the best candidates with why they do or do not enter."""
    def cents(x):
        return "-" if x is None else f"{x * 100:+.1f}c"
    print(f"\nFast book (entry at {config.FAST_MIN_EDGE * 100:.0f}c beyond the usual gap, at most {config.FAST_MAX_POSITIONS} positions)")
    print_table(FAST_COLUMNS, [(r.label, cents(r.usual), cents(r.part), f"{r.held:,.0f}" if r.held else "-",
                                f"{r.want:,.0f}" if r.want else "-", r.note) for r in rows] or [("none", "", "", "", "", "")])


HOLDING_COLUMNS = ("race", "mkt", "side", "shares", "value", "sell", "fair", "exp return", "action")


def holding_cells(h, action, ledger=None):
    """One holdings row; with a ledger (v2) the row also says how many shares each book owns."""
    def price(x):
        return f"{x:.3f}" if x is not None else "-"
    books = ()
    if ledger is not None:
        books = (f"{ledger.shares(h.market_id, h.side, 'hold'):,.0f} / {ledger.shares(h.market_id, h.side, 'fast'):,.0f}",)
    return (h.race, h.market_id, h.side.upper(), f"{h.shares:,.0f}", f"{h.value:,.0f}",
            price(h.sell_price), price(h.fair),
            f"{h.exp_return:+.1%}" if h.exp_return is not None else "-", *books, action)


def print_report(portfolio, chosen, sells, notes, ledger=None):
    """Everything one loop decided, in reading order. With the v2 ledger the tables show ROI instead of Kelly and the
    hold / fast split of each holding."""
    p = portfolio
    print(f"\ncash {p.cash:,.0f} | invested {p.invested:,.0f} ({p.invested / p.bankroll:.1%}) | "
          f"bankroll {p.bankroll:,.0f} | investable cash {p.investable:,.0f}")
    print("\nTop opportunities")
    v2 = ledger is not None
    print_table(OPPORTUNITY_COLUMNS_V2 if v2 else OPPORTUNITY_COLUMNS, [opportunity_cells(c, v2) for c in chosen])
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
