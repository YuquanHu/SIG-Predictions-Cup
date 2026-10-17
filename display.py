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
