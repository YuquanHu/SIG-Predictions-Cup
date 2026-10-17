#!/usr/bin/env python3
"""Stage 3: fair value, edge and Kelly for every SIG contract; print what it would trade. No orders.

    python3 find_trades.py
"""
import time

import config
from feeds import fetch_all, setup_all
from races import load_races
from strategy import compute_fair_values, find_opportunities, keep_top

COLUMNS = ("race", "mkt", "side", "price", "fair", "edge", "kelly", "half", "avail", "decision")


def row(c):
    return (c.race, c.market_id, c.side.upper(), f"{c.price:.3f}", f"{c.fair:.3f}",
            f"{c.edge * 100:+.1f}c", f"{c.kelly * 100:.1f}%", f"{c.half_kelly * 100:.1f}%",
            f"{c.size:,.0f}" if c.size is not None else "-", c.decision)


def print_table(rows):
    widths = [max(len(str(r[i])) for r in (COLUMNS, *rows)) for i in range(len(COLUMNS))]
    for r in (COLUMNS, *rows):
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip())


def main():
    races = load_races(config.RACES_FILE)
    print(f"Loaded {len(races)} race(s). DRY_RUN={config.DRY_RUN}, no orders are placed.")
    setup_all(races)
    fetch_all(races)

    fair_values = compute_fair_values(races, time.time())
    candidates = find_opportunities(races, fair_values)
    chosen = keep_top(candidates)

    print("\nFair values")
    for (race_id, code), fair in fair_values.items():
        value = f"{fair.value:.3f}" if fair.value is not None else "none, its contracts are skipped"
        print(f"  {race_id} {code}: {value}  ({fair.note})")

    print("\nWould trade" if chosen else "\nNothing to trade")
    print_table([row(c) for c in chosen])

    rest = [c for c in candidates if c.decision != "TRADE"]
    if rest:
        print("\nConsidered but not traded")
        print_table([row(c) for c in sorted(rest, key=lambda c: c.edge, reverse=True)])


if __name__ == "__main__":
    main()
