#!/usr/bin/env python3
"""Stage 3: fair value, edge and Kelly for every SIG contract; print what it would trade. No orders.

    python3 find_trades.py
"""
import time

import config
from fetch_prices import run_each
from races import load_races
from strategy import find_opportunities, keep_top

COLUMNS = ("race", "mkt", "side", "price", "fair", "edge", "kelly", "half", "avail", "decision")


def row(c):
    return (c["race"], c["market_id"], c["side"].upper(), f"{c['price']:.3f}", f"{c['fair']:.3f}",
            f"{c['edge'] * 100:+.1f}c", f"{c['kelly'] * 100:.1f}%", f"{c['half_kelly'] * 100:.1f}%",
            f"{c['size']:,.0f}" if c["size"] is not None else "-", c["decision"])


def print_table(rows):
    widths = [max(len(str(r[i])) for r in (COLUMNS, *rows)) for i in range(len(COLUMNS))]
    for r in (COLUMNS, *rows):
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip())


def main():
    races = load_races(config.RACES_FILE)
    print(f"Loaded {len(races)} race(s). DRY_RUN={config.DRY_RUN}, no orders are placed.")
    run_each(races, "setup")
    run_each(races, "fetch")
    now = time.time()

    candidates, skipped = find_opportunities(races, now)
    chosen = keep_top(candidates)

    print("\nFair values")
    for c in dict.fromkeys((c["race"], c["pays_if"], c["fair_note"]) for c in candidates):
        print(f"  {c[0]} {c[1]}: {c[2]}")
    for race_id, market_id, why in skipped:
        print(f"  {race_id} market {market_id}: skipped, {why}")

    print("\nWould trade" if chosen else "\nNothing to trade")
    print_table([row(c) for c in chosen])

    rest = [c for c in candidates if c["decision"] != "TRADE"]
    if rest:
        print("\nConsidered but not traded")
        print_table([row(c) for c in sorted(rest, key=lambda c: c["edge"], reverse=True)])


if __name__ == "__main__":
    main()
