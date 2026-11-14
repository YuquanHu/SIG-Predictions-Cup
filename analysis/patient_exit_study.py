#!/usr/bin/env python3
"""Would resting the sell above the bid have beaten selling at the bid? Read-only study on the trade tape.

    python3 analysis/tape_data.py              # first: cache the tapes
    python3 analysis/patient_exit_study.py

Part 1 sizes a "big order" per market (a fill group: fills with the same timestamp) and checks the BIG_ORDER_WEEK_SHARE rule
across all markets. Part 2 takes every caught-up / time-stop exit of the baseline backtest and asks, for a sell resting k ticks above
the bid for the exit's patience window (60 min caught up, 30 min time stop): did a later print reach the price, how much did it
gain per share, and what did waiting cost when it did not (the sale then happens at the bid of that moment).
Upper bound: any print at the price counts as a fill (no queue position), and the bid is last trade -+ half a tick.
"""
import csv
import os
import sys
from bisect import bisect_right
from collections import defaultdict
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from backtest_data import load as load_history
from races import load_races
from tape_data import TRADES_CSV, WINDOW, exits, load as load_tape, load_window

TICK = 0.005
HALF = 0.005                         # the backtest's half spread: bid = last trade - HALF (YES)
OFFSETS = (0, 1, 2, 3, 4, 6)         # ticks above the bid; 0 = just wait, then sell at the bid
WEEK = 7 * 86400
SHARES = (0.02, 0.04, 0.08)          # big order as a share of the 7-day volume
CAP = 50_000


def groups(tape):
    """[(time, total value, lowest price, highest price)] one per timestamp."""
    by_time = defaultdict(list)
    for t, price, size, value in tape:
        by_time[t].append((price, value))
    return [(t, sum(v for _, v in g), min(p for p, _ in g), max(p for p, _ in g)) for t, g in sorted(by_time.items())]


def week_volume(tape, end):
    return sum(row[3] for row in tape if end - WEEK < row[0] <= end)


def validate_rule(markets):
    """How many markets see at least 1 / 3 / 7 big orders in their last week, for each share (and the flat 50k)."""
    print("Part 1: big orders per market over the last 7 days (all enabled markets with a tape)")
    rows = []
    for market_id, tape in markets.items():
        if not tape:
            continue
        end = tape[-1][0]
        week = week_volume(tape, end)
        g = [x for x in groups(tape) if x[0] > end - WEEK]
        rows.append((market_id, week, g))
    print(f"  {len(rows)} markets; weekly volume median {median(r[1] for r in rows):,.0f} SUSQies, min {min(r[1] for r in rows):,.0f}, "
          f"max {max(r[1] for r in rows):,.0f}")
    for label, size_of in [(f"{s:.0%} of the week (cap {CAP:,})", lambda w, s=s: max(500, min(CAP, s * w))) for s in SHARES] + [("flat 50,000", lambda w: CAP)]:
        counts = [sum(x[1] >= size_of(week) for x in g) for _, week, g in rows]
        print(f"  {label:32s} markets with >=1 big order {sum(c >= 1 for c in counts):3d}, >=3 {sum(c >= 3 for c in counts):3d}, "
              f">=7 {sum(c >= 7 for c in counts):3d}; total big orders {sum(counts)}")


def fallback_and_fill(side, last, tape_after, k, big_prints=None):
    """(filled?, price per share of the held side, ticks reached) for one exit. `last` is the last YES trade price at the exit."""
    sign = 1 if side == "yes" else -1
    bid = last - HALF if side == "yes" else 1 - (last + HALF)               # what selling at once returns per share
    reach = max((sign * (p - last) for _, p, _, _ in tape_after), default=0.0)  # best price a print touched, in the held side's terms
    target = bid + k * TICK
    # YES holder: a print at YES price >= target; NO holder: a print at YES price <= 1 - target
    prints = tape_after if big_prints is None else big_prints   # big_prints: only the fills of big-order groups count
    hit = next((t for t, p, _, _ in prints if (p >= target if side == "yes" else p <= 1 - target)), None) if k else None
    if hit is not None:
        return True, target, reach
    end_last = tape_after[-1][1] if tape_after else last
    end_bid = end_last - HALF if side == "yes" else 1 - (end_last + HALF)
    return False, end_bid, reach


def big_prints(after, big):
    """The fills of the groups (same timestamp) worth at least `big`."""
    by_time = defaultdict(list)
    for row in after:
        by_time[row[0]].append(row)
    return [row for g in by_time.values() if sum(r[3] for r in g) >= big for row in g]


def study_exits(history):
    print("\nPart 2: the baseline backtest's caught-up and time-stop exits, resting k ticks above the bid")
    results = defaultdict(lambda: {"n": 0, "filled": 0, "gain": 0.0, "gain_filled": 0.0, "drift_unfilled": 0.0})
    big_results = defaultdict(lambda: {"n": 0, "filled": 0, "gain": 0.0})
    shares_of = {}
    with open(TRADES_CSV) as f:
        for t in csv.DictReader(f):
            if t["run"].startswith("baseline") and t["exit_time"]:
                shares_of[(t["market"], int(t["exit_time"]))] = (float(t["shares"]), t["side"])
    reaches, used = [], 0
    for market_id, t0, kind in exits():
        window = load_window(market_id, t0)
        ch = history.contracts.get(market_id)
        last = ch.price.at(t0) if ch else None
        if window is None or last is None or (market_id, t0) not in shares_of or window["to"] < t0 + WINDOW[kind] - 60:
            continue                                          # no tape for this exit, or its window had not finished yet
        used += 1
        shares, side = shares_of[(market_id, t0)]
        after = window["rows"]
        big = max(500, min(CAP, 0.04 * ch.traded.between(t0 - WEEK, t0)))
        bigs = big_prints(after, big)
        bid_now = last - HALF if side == "yes" else 1 - (last + HALF)
        for k in OFFSETS:
            for table, prints in ((results, None), (big_results, bigs)):
                filled, price, reach = fallback_and_fill(side, last, after, k, prints)
                r = table[(kind, k)]
                r["n"] += 1
                r["filled"] += filled
                r["gain"] += shares * (price - bid_now)
                if table is results:
                    r["gain_filled" if filled else "drift_unfilled"] += shares * (price - bid_now)
            if k == OFFSETS[0]:
                reaches.append(reach / TICK)
    print(f"  {used} exits covered by a tape window")
    for kind, name in (("caught", "caught up (60 min window)"), ("time", "time stop (30 min window)")):
        print(f"\n  {name}")
        print("    ticks above bid | exits | any print reaches | gain if filled | drift if unfilled | total vs selling at once | only big-order prints fill")
        for k in OFFSETS:
            r, b = results.get((kind, k)), big_results.get((kind, k))
            if not r or not r["n"]:
                continue
            print(f"    {k:^15d} | {r['n']:5d} | {r['filled'] / r['n']:16.0%} | {r['gain_filled']:14,.0f} | {r['drift_unfilled']:17,.0f} | "
                  f"{r['gain']:+22,.0f} | filled {b['filled'] / b['n']:4.0%}, total {b['gain']:+,.0f}")
    if reaches:
        reaches.sort()
        print(f"\n  highest price touched inside the windows, in ticks above the exit price: median {median(reaches):.1f}, "
              f"90th percentile {reaches[int(0.9 * len(reaches))]:.1f}, max {reaches[-1]:.1f}")
    print("\n  k = 0 is the control: wait the window, then sell at that moment's bid. Its total is the drift alone (what waiting costs or earns on average).")
    print(f"  'big order' = max(500, min(50,000, 4% of the market's 7-day volume before the exit)); a print fills only if its same-second group is that big.")


def main():
    races = load_races(config.RACES_FILE)
    history = load_history(races)
    markets = {c["market_id"]: load_tape(c["market_id"]) for race in races.values() for c in race["contracts"]}
    covered = {m: t for m, t in markets.items() if t}
    print(f"Tapes: {len(covered)} of {len(markets)} markets, {sum(len(t) for t in covered.values()):,} fills\n")
    validate_rule(covered)
    study_exits(history)


if __name__ == "__main__":
    main()
