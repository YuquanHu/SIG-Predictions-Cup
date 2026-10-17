#!/usr/bin/env python3
"""Stage 1: read-only price fetch from SIG, Kalshi and Polymarket for races.json.

    python3 fetch_prices.py            # fetch once and print
    python3 fetch_prices.py --loop 20  # re-fetch prices every 20 s (setup runs once)
"""
import argparse
import time

import config
import kalshi
import polymarket
import sig
from races import load_races, race_has_data

SOURCES = [  # (name, setup or None, fetch)
    ("SIG", sig.setup, sig.fetch_prices),
    ("Kalshi", None, kalshi.fetch_prices),
    ("Polymarket", polymarket.setup, polymarket.fetch_prices),
]


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


def print_races(races):
    for race_id, race in races.items():
        print(f"\n== {race_id}  {race['name']} ({race['group']}) ==")
        if not race_has_data(race):
            print("  no Kalshi/Polymarket data: fair value will skip this race")
        for code, o in race["outcomes"].items():
            print(f"  outcome {code}")
            print_quote(f"Kalshi {o['kalshi_id']}", o["kalshi"])
            print_quote(f"Poly {o['poly_id']}", o["poly"])
        for c in race["contracts"]:
            yes = c["yes"]
            print(f"  SIG market {c['market_id']} (pays if {c['pays_if']}): {c['title'] or '?'}")
            print_quote("YES", yes)
            no_bid = 1 - yes["ask"] if yes["ask"] is not None else None
            no_ask = 1 - yes["bid"] if yes["bid"] is not None else None
            print(f"    {'NO (implied)':<20} bid {fmt(no_bid)} x{fmt_size(yes['ask_size'])}   "
                  f"ask {fmt(no_ask)} x{fmt_size(yes['bid_size'])}")


def run_each(races, stage):
    """Run `stage` ("setup" or "fetch") for each source. The sources already warn per race; this
    is the backstop so an unexpected error in one source can't stop the others."""
    for name, setup, fetch in SOURCES:
        fn = setup if stage == "setup" else fetch
        try:
            if fn:
                fn(races)
        except Exception as e:
            print(f"  WARNING {name} {stage} crashed for all races: {type(e).__name__}: {e}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", type=float, metavar="SECONDS", help="re-fetch prices every SECONDS")
    args = parser.parse_args()

    races = load_races(config.RACES_FILE)
    print(f"Loaded {len(races)} race(s) from {config.RACES_FILE}. DRY_RUN={config.DRY_RUN}, read-only.")

    print("Setup...")
    run_each(races, "setup")

    while True:
        print(f"\nFetching prices at {time.strftime('%H:%M:%S')}...")
        run_each(races, "fetch")
        print_races(races)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
