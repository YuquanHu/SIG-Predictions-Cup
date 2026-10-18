"""Console formatting for quotes."""
import time


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


def opportunity_cells(c):
    return (c.race, c.market_id, c.side.upper(), f"{c.price:.3f}", f"{c.fair:.3f}",
            f"{c.edge * 100:+.1f}c", f"{c.kelly * 100:.1f}%", f"{c.half_kelly * 100:.1f}%",
            f"{c.size:,.0f}" if c.size is not None else "-", c.decision)


HOLDING_COLUMNS = ("race", "mkt", "side", "shares", "value", "sell", "fair", "exp return", "action")


def holding_cells(h, action):
    def price(x):
        return f"{x:.3f}" if x is not None else "-"
    return (h.race, h.market_id, h.side.upper(), f"{h.shares:,.0f}", f"{h.value:,.0f}",
            price(h.sell_price), price(h.fair),
            f"{h.exp_return:+.1%}" if h.exp_return is not None else "-", action)


def print_report(portfolio, chosen, sells, notes):
    """Everything one loop decided, in reading order."""
    p = portfolio
    print(f"\ncash {p.cash:,.0f} | invested {p.invested:,.0f} ({p.invested / p.bankroll:.1%}) | "
          f"bankroll {p.bankroll:,.0f} | investable cash {p.investable:,.0f}")
    print("\nTop opportunities")
    print_table(OPPORTUNITY_COLUMNS, [opportunity_cells(c) for c in chosen])
    if p.holdings or p.outside_value:
        print("\nHoldings")
        actions = {(o.market_id, o.side): o.reason for o in sells}
        print_table(HOLDING_COLUMNS, [holding_cells(h, actions.get((h.market_id, h.side), "hold"))
                                      for h in p.holdings])
        if p.outside_value:
            print(f"  plus {p.outside_value:,.0f} in markets outside races.json (counted, never traded)")
    print("\nSizing")
    for note in notes or ["nothing to size"]:
        print(f"  {note}")
