#!/usr/bin/env python3
"""Stage 1: read-only price fetch from SIG, Kalshi and Polymarket for races.json.

    python3 fetch_prices.py            # fetch once and print
    python3 fetch_prices.py --loop 20  # re-fetch prices every 20 s (setup runs once)
"""
import argparse
import time

import config
from display import fmt, fmt_size, print_quote
from feeds import fetch_all, setup_all
from races import load_races, race_has_data


def print_races(races):
    for race_id, race in races.items():
        print(f"\n== {race_id}  {race['name']} ({race['group']}) ==")
        if not race_has_data(race):
            print("  no Kalshi/Polymarket data: fair value will skip this race")
        for code, o in race["outcomes"].items():
            print(f"  outcome {code}")
            print_quote(f"Kalshi {o['kalshi_id']}", o["kalshi"])
            print_quote(f"Poly {o['poly_id'] or '(no match)'}", o["poly"])
        for c in race["contracts"]:
            yes = c["yes"]
            print(f"  SIG market {c['market_id']} (pays if {c['pays_if']}): {c['title'] or '?'}")
            print_quote("YES", yes)
            no_bid = 1 - yes["ask"] if yes["ask"] is not None else None
            no_ask = 1 - yes["bid"] if yes["bid"] is not None else None
            print(f"    {'NO (implied)':<20} bid {fmt(no_bid)} x{fmt_size(yes['ask_size'])}   "
                  f"ask {fmt(no_ask)} x{fmt_size(yes['bid_size'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", type=float, metavar="SECONDS", help="re-fetch prices every SECONDS")
    args = parser.parse_args()

    races = load_races(config.RACES_FILE)
    print(f"Loaded {len(races)} race(s) from {config.RACES_FILE}. DRY_RUN={config.DRY_RUN}, read-only.")

    print("Setup...")
    setup_all(races)

    while True:
        print(f"\nFetching prices at {time.strftime('%H:%M:%S')}...")
        fetch_all(races)
        print_races(races)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
