#!/usr/bin/env python3
"""Read-only audit: what is on SIG and Kalshi that races.json does not cover, and what in it is switched off.

    python3 coverage.py             # SIG (3 reads) and Kalshi (about 65 requests, ~1 minute)
    python3 coverage.py --no-kalshi # SIG and the file only
"""
import argparse
import json
import re
from collections import defaultdict

import requests

import config
import sig
from display import print_table

KALSHI_EVENTS = f"{config.KALSHI_BASE}/events"
KALSHI_2026 = ("SENATE", "GOVPARTY", "HOUSE")  # event ticker prefixes; "-26" suffix. CONTROL* is matched separately.
SIG_TITLES = (r"Will the (.+?) win the (.+?) Senate\?$", r"Will the (.+?) win the (.+?) Governor\?$",
              r"Will the (.+?) win the ([A-Z]{2}-\d{2}) House race\?$", r"Will the (.+?) win the U\.S\. (Senate|House)\?$")


def sig_markets():
    markets, cursor = [], None
    while True:
        page = sig._get("/markets", limit=100, **({"cursor": cursor} if cursor else {}))
        markets += page["data"]
        if not page["pagination"]["hasMore"]:
            return markets
        cursor = page["pagination"]["nextCursor"]


def kalshi_events():
    events, cursor = [], ""
    while True:
        resp = requests.get(KALSHI_EVENTS, timeout=30, params={
            "limit": 200, "status": "open", "with_nested_markets": "true", "cursor": cursor})
        resp.raise_for_status()
        body = resp.json()
        events += body["events"]
        cursor = body.get("cursor")
        if not cursor:
            return events


def is_2026_politics(event_ticker):
    return (event_ticker.startswith(KALSHI_2026) and event_ticker.endswith("-26")) or event_ticker.startswith("CONTROL")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-kalshi", action="store_true", help="skip the slow Kalshi scan")
    args = parser.parse_args()
    with open(config.RACES_FILE) as f:
        races = json.load(f)
    enabled = {k: v for k, v in races.items() if v.get("enabled", True) is not False}

    print(f"races.json: {len(races)} races = {len(enabled)} enabled + {len(races) - len(enabled)} disabled")

    used_sig = {c["market_id"] for v in races.values() for c in v["contracts"]}
    markets = sig_markets()
    unrecorded = [m for m in markets if m["id"] not in used_sig]
    unknown = [m for m in markets if not any(re.match(p, m["title"]) for p in SIG_TITLES)]
    print(f"\nSIG: {len(markets)} markets; not in races.json: {len(unrecorded)}; titles this script does not recognise: {len(unknown)}")
    print_table(("id", "title"), [(m["id"], m["title"]) for m in unrecorded + unknown])
    gone = sorted(used_sig - {m["id"] for m in markets})
    if gone:
        print(f"  races.json lists SIG markets that SIG no longer shows: {gone}")

    if not args.no_kalshi:
        used_kalshi = {o["kalshi_id"] for v in races.values() for o in v["outcomes"].values() if o.get("kalshi_id")}
        whole, extra = [], []
        for e in kalshi_events():
            if not is_2026_politics(e["event_ticker"]):
                continue
            missing = [m for m in e["markets"] if m["ticker"] not in used_kalshi]
            if len(missing) == len(e["markets"]):
                whole.append((e["event_ticker"], e["title"]))
            else:
                extra += [(m["ticker"], m["yes_sub_title"], f"{m['yes_bid_dollars']}-{m['yes_ask_dollars']}") for m in missing]
        print(f"\nKalshi events with no market in races.json (no SIG counterpart, so nothing to trade): {len(whole)}")
        print_table(("event", "title"), whole)
        print(f"\nKalshi candidate markets inside tracked events that races.json does not list: {len(extra)}")
        print_table(("ticker", "candidate", "bid-ask"), extra)

    no_poly = [(k, ",".join(c for c, o in v["outcomes"].items() if not o.get("poly_id")), v.get("poly_note", ""))
               for k, v in enabled.items() if any(not o.get("poly_id") for o in v["outcomes"].values())]
    print(f"\nEnabled races with an outcome that has no Polymarket id: {len(no_poly)}")
    print_table(("race", "outcomes", "note"), no_poly)
    by_reason = defaultdict(list)
    for k, v in races.items():
        if v.get("enabled", True) is False:
            by_reason[v.get("disabled_reason", "no reason given")].append(k)
    print(f"\nDisabled races ({sum(len(v) for v in by_reason.values())}):")
    for reason, keys in by_reason.items():
        print(f"  {', '.join(keys)}\n    {reason}")


if __name__ == "__main__":
    main()
