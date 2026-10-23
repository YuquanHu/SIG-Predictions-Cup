#!/usr/bin/env python3
"""Stage 2: read-only SIG connection test. No orders are placed.

Prints your account balance, your open positions, and the Yes bid/ask for markets 387 and 388.
"""
import sig
from config import RACES_FILE, SIG_TOURNAMENT_ID
from display import print_quote
from races import iter_contracts, load_races

TEST_MARKETS = {"387", "388"}


def section(title, fn):
    """Run one read; report a failure instead of crashing so the other reads still run."""
    print(f"\n-- {title}")
    try:
        fn()
    except Exception as e:
        print(f"  FAILED: {e}")


def show_account():
    a = sig.fetch_account()
    print(f"  user          {a['username']}")
    print(f"  cash balance  {a['balance']:,}")
    print(f"  buying power  {a['buying_power']:,.2f}")


def show_positions():
    positions = sig.fetch_positions()
    if not positions:
        print("  no open positions")
    for p in positions:
        price = f"{p['current_price']:.3f}" if p["current_price"] is not None else "-"
        print(f"  market {p['market_id']:<5} {p['side'].upper():<3} {p['shares']:>10,.2f} shares  "
              f"avg {p['avg_price']:.3f}  now {price}  pnl {p['unrealized_pnl']:+,.2f}  {p['title']}")


def show_prices():
    races = load_races(RACES_FILE)
    for race_id, race in races.items():
        race["contracts"] = [c for c in race["contracts"] if c["market_id"] in TEST_MARKETS]
    sig.setup(races)
    sig.fetch_prices(races)
    sig.fetch_depth(races, TEST_MARKETS)  # bulk prices carry no sizes
    for _, c in iter_contracts(races):
        print(f"  market {c['market_id']} (pays if {c['pays_if']}): {c['title'] or '?'}")
        print_quote("YES", c["yes"])


def main():
    print("SIG read-only connection test (no orders are placed)")
    print(f"tournament: {SIG_TOURNAMENT_ID or 'key default'}")
    section("Account", show_account)
    section("Open positions", show_positions)
    section("Yes bid/ask, markets 387 and 388", show_prices)


if __name__ == "__main__":
    main()
