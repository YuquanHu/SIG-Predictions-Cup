#!/usr/bin/env python3
"""Read-only replay: what the fast book would have done on a past stretch of one market, for several entry thresholds x.

History comes from the same fetchers the bot uses to back-fill (Kalshi 1-minute candles, Polymarket ~15-minute prices, SIG
5-minute trade candles carried forward). The gap is sampled every 5 minutes; the usual gap (baseline) is classified from the
15-minute samples of the previous 48 hours. SIG quotes are not in the history, so the executable price is the last trade
plus or minus HALF_SPREAD. The exits are fast.caught_up and fast.time_up, the same functions the bot uses. Places no orders.

    python3 analysis/replay_fast.py
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import fast
import gapmodel
from backfill import contract_prices

ET = ZoneInfo("America/New_York")
HALF_SPREAD = 0.005       # assumed half spread on SIG, paid on the way in and again on the way out
STEP = 300                # evaluate every 5 minutes
THRESHOLDS = (0.03, 0.04, 0.05)
CASES = {
    "Nevada governor, Democrat (SIG 182)": dict(kalshi="GOVPARTYNV-26-D", poly_gamma="629505", exchange="871",
                                                start=(2026, 10, 19, 14, 0), end=(2026, 10, 22, 14, 0)),
    "Maine Senate, Democrat (SIG 270)": dict(kalshi="SENATEME-26-D", poly_gamma="630772", exchange="959",
                                             start=(2026, 10, 17, 12, 0), end=(2026, 10, 20, 12, 0)),
}


def ts(parts):
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


def poly_token(gamma_id):
    import json
    market = requests.get(f"{config.POLY_GAMMA_BASE}/markets", params={"id": gamma_id}, timeout=15).json()[0]
    return json.loads(market["clobTokenIds"])[0]


def price_series(case):
    outcome = {"kalshi_id": case["kalshi"], "poly_yes_token": poly_token(case["poly_gamma"])}
    contract = {"sig_exchange_id": case["exchange"]}
    start, end = ts(case["start"]), ts(case["end"])
    return contract_prices(outcome, contract, start, end, kalshi_interval=1, step=STEP), start


def replay(prices, start, x):
    """Entries and exits for threshold x. A gap g = fair_yes - SIG yes is a Yes edge when positive, a No edge when negative.
    Returns (closed trades, the trade still open at the end of the data or None)."""
    gaps = {t: fair - sig_price for t, (fair, sig_price) in prices.items()}
    quarter_samples = [(t, g) for t, g in gaps.items() if t % gapmodel.SAMPLE_SECONDS == 0]
    trades, open_trade = [], None
    for t in sorted(gaps):
        if t - start < config.HISTORY_HOURS * 3600:
            continue
        baseline = gapmodel.classify([(s, g) for s, g in quarter_samples if s <= t], t)
        if baseline.signed is None:
            continue
        g = gaps[t]
        if open_trade:
            sign = 1 if open_trade["side"] == "yes" else -1
            exit_edge = sign * g + HALF_SPREAD                                       # fair minus a sale price
            reason = ("caught up" if fast.caught_up(baseline, open_trade["side"], exit_edge) else   # the bot's own exit rules
                      "time stop" if fast.time_up(open_trade["t"], t) else None)
            if reason:
                moved = prices[t][1] - open_trade["sig"]                           # SIG yes price change since entry
                trades.append({**open_trade, "exit": t, "reason": reason, "pnl": sign * moved - 2 * HALF_SPREAD})
                open_trade = None
            continue
        for side, sign in (("yes", 1), ("no", -1)):
            edge = sign * g - HALF_SPREAD                                           # executable edge now
            part = gapmodel.fast_part(baseline, side, edge)
            if part is not None and part >= x - gapmodel.EPS:
                open_trade = {"t": t, "side": side, "edge": edge, "part": part, "usual": baseline.for_side(side),
                              "structural": baseline.structural, "sig": prices[t][1]}
                break
    return trades, open_trade


def main():
    for label, case in CASES.items():
        prices, start = price_series(case)
        print("=" * 100)
        print(f"{label}: {len(prices)} five-minute points from {datetime.fromtimestamp(min(prices), ET):%b %d %H:%M} ET "
              f"to {datetime.fromtimestamp(max(prices), ET):%b %d %H:%M} ET; the first {config.HISTORY_HOURS}h only build the baseline")
        for x in THRESHOLDS:
            trades, still_open = replay(prices, start, x)
            print(f"\n  threshold x = {x * 100:.0f}c over the usual gap")
            if not trades and not still_open:
                print("    no entries")
            for tr in trades + ([still_open] if still_open else []):
                out = (f"exit {datetime.fromtimestamp(tr['exit'], ET):%b %d %H:%M} ET ({(tr['exit'] - tr['t']) / 3600:.1f}h, {tr['reason']}), "
                       f"{tr['pnl'] * 100:+.1f}c per share" if "exit" in tr else "still open at the end of the data")
                print(f"    {datetime.fromtimestamp(tr['t'], ET):%b %d %H:%M} ET buy {tr['side'].upper()} edge {tr['edge'] * 100:+.1f}c, "
                      f"usual gap {tr['usual'] * 100:+.1f}c ({'structural' if tr['structural'] else 'not structural'}), "
                      f"fast part {tr['part'] * 100:+.1f}c -> {out}")


if __name__ == "__main__":
    main()
