#!/usr/bin/env python3
"""Price history for the v2 backtest, cached on disk so a run needs no network.

    python3 analysis/backtest_data.py                      # fetch (or extend) the cache up to now
    python3 analysis/backtest_data.py --reads-per-minute 30

Per SIG contract: 5-minute trade candles (end time, close, volume). Per outcome: Kalshi hourly mids and Polymarket prices
(about every 15 minutes). Read-only. SIG reads share the account's 100 a minute with the live bot, so the fetch is held to
--reads-per-minute (default 50) through the same budget guard the bot uses. A re-run only fetches what is missing.
"""
import argparse
import json
import os
import sys
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import polymarket
import sig
from backfill import _iso, kalshi_mids, poly_prices
from parallel import run_all
from races import iter_contracts, iter_outcomes, load_races

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_cache")
START = int(datetime(2026, 9, 29, tzinfo=timezone.utc).timestamp())  # two days before the competition: the baseline needs 48h
CANDLE = 300           # seconds in a SIG candle
POLY_WINDOW = 10 * 86400  # Polymarket refuses a longer window at 15-minute fidelity
REFETCH_AFTER = 3600   # a cached series is extended when it is older than this
WORKERS = 4


@dataclass
class Series:
    """Values at increasing times, read as 'the latest value at or before t'."""
    times: list
    values: list

    def at(self, t, max_age=None):
        i = bisect_right(self.times, t) - 1
        if i < 0 or (max_age is not None and t - self.times[i] > max_age):
            return None
        return self.values[i]

    def last_time(self, t):
        i = bisect_right(self.times, t) - 1
        return self.times[i] if i >= 0 else None


@dataclass
class Traded:
    """SUSQies traded, as a running total: the volume in any window is a difference of two lookups."""
    times: list
    cumulative: list

    def total(self, t):
        i = bisect_right(self.times, t) - 1
        return self.cumulative[i] if i >= 0 else 0.0

    def between(self, start, end):
        return self.total(end) - self.total(start)


@dataclass
class ContractHistory:
    price: Series   # SIG yes close, keyed by the candle's end time
    traded: Traded


@dataclass
class History:
    contracts: dict  # market_id -> ContractHistory
    kalshi: dict     # ticker -> Series of mids
    poly: dict       # token -> Series of prices


def _path(kind, key):
    return os.path.join(CACHE_DIR, f"{kind}_{key}.json")


def _read(kind, key):
    try:
        with open(_path(kind, key)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(kind, key, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_path(kind, key) + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(_path(kind, key) + ".tmp", _path(kind, key))


def _iso_seconds(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def sig_candles(exchange_id, start, end):
    """[[end time, close, volume]] of SIG's 5-minute yes candles, following the continuation boundary until complete."""
    out, cursor = [], start
    while True:
        page = sig._get(f"/exchanges/{exchange_id}/price-history", resolution="5m", limit=1000,
                        **{"from": _iso(cursor), "to": _iso(end)})
        out += [[_iso_seconds(c["time"]) + CANDLE, c["close"], c["volume"]] for c in page["candles"] if c["close"] is not None]
        if page["coverage"].get("complete") or not page["candles"]:
            return out
        cursor = _iso_seconds(page["to"])


def _extend(kind, key, end, fetch):
    """Cache entry {"fetched_to", "rows"} for one series, fetching only what is newer than the cached rows."""
    cached = _read(kind, key) or {"fetched_to": START, "rows": []}
    if end - cached["fetched_to"] < REFETCH_AFTER and cached["rows"]:
        return False
    since = max(START, cached["fetched_to"] - CANDLE * 2)
    rows = [row for row in cached["rows"] if row[0] < since] + sorted(
        (row for row in fetch(since, end)), key=lambda row: row[0])
    _write(kind, key, {"fetched_to": end, "rows": rows})
    return True


def fetch_sig(c, end):
    return _extend("sig", c["market_id"], end, lambda a, b: sig_candles(c["sig_exchange_id"], a, b))


def fetch_kalshi(ticker, end):
    return _extend("kalshi", ticker, end, lambda a, b: [[t, p] for t, p in sorted(kalshi_mids(ticker, a, b, 60).items())])


def fetch_poly(token, end):
    def windows(a, b):
        rows = []
        for lo in range(int(a), int(b), POLY_WINDOW):
            rows += [[t, p] for t, p in poly_prices(token, lo, min(lo + POLY_WINDOW, b)).items()]
        return rows
    return _extend("poly", token, end, windows)


def fetch(races, end=None):
    """Fill the cache for every enabled contract and outcome up to `end` (default: now). Returns how many series were fetched."""
    end = int((end or time.time()) // CANDLE * CANDLE)
    sig.setup(races)
    polymarket.setup(races)
    tasks = [lambda c=c: fetch_sig(c, end) for _, c in iter_contracts(races) if c["sig_exchange_id"]]
    for _, _, o in iter_outcomes(races):
        if o["kalshi_id"]:
            tasks.append(lambda t=o["kalshi_id"]: fetch_kalshi(t, end))
        if o["poly_yes_token"]:
            tasks.append(lambda t=o["poly_yes_token"]: fetch_poly(t, end))
    done = 0

    def counted(task):
        nonlocal done
        try:
            result = task()
        except Exception as e:  # one failed series is left for the next run
            print(f"  WARNING {type(e).__name__}: {e}")
            result = False
        done += 1
        if done % 25 == 0:
            print(f"  {done}/{len(tasks)} series checked")
        return result

    return sum(run_all([lambda task=task: counted(task) for task in tasks], workers=WORKERS))


def _series(rows, column=1):
    return Series([row[0] for row in rows], [row[column] for row in rows])


def load(races):
    """The cached History for these races (a series that was never fetched is simply missing)."""
    contracts = {}
    for _, c in iter_contracts(races):
        entry = _read("sig", c["market_id"])
        if entry and entry["rows"]:
            rows, total, cumulative = entry["rows"], 0.0, []
            for row in rows:
                total += row[2]
                cumulative.append(total)
            contracts[c["market_id"]] = ContractHistory(_series(rows), Traded([row[0] for row in rows], cumulative))
    kalshi_series, poly_series = {}, {}
    for _, _, o in iter_outcomes(races):
        for kind, key, store in (("kalshi", o["kalshi_id"], kalshi_series), ("poly", o["poly_yes_token"], poly_series)):
            entry = _read(kind, key) if key else None
            if entry and entry["rows"]:
                store[key] = _series(entry["rows"])
    return History(contracts, kalshi_series, poly_series)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reads-per-minute", type=int, default=50, help="SIG reads a minute this fetch may use (the bot needs the rest)")
    args = parser.parse_args()
    config.SIG_READS_PER_MINUTE = args.reads_per_minute
    races = load_races(config.RACES_FILE)
    started = time.time()
    print(f"Fetching history for {len(races)} races from {_iso(START)} (at most {args.reads_per_minute} SIG reads a minute)...")
    fetched = fetch(races)
    history = load(races)
    print(f"Done in {(time.time() - started) / 60:.1f} min: {fetched} series fetched; cache holds {len(history.contracts)} SIG "
          f"contracts, {len(history.kalshi)} Kalshi and {len(history.poly)} Polymarket series.")


if __name__ == "__main__":
    main()
