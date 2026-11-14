#!/usr/bin/env python3
"""Trade tape (every fill: time, YES price, shares, value) of the SIG markets, cached on disk for the patient-exit study.

    python3 analysis/tape_data.py [--reads-per-minute 50]

Every enabled market gets the last 7 days (enough to size its big orders). Each fast "caught up" or "time stop" exit of the backtest
(analysis/backtest_out/trades.csv) also gets the fills of the window that follows it (60 / 30 minutes), cached as window_<market>_<time>.json.
Read-only; resumable; SIG reads are held to --reads-per-minute because the live bot shares the account's 100 a minute.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import sig
from backfill import _iso
from backtest_data import CACHE_DIR
from parallel import run_all
from races import iter_contracts, load_races

TRADES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_out", "trades.csv")
WEEK = 7 * 86400
WINDOW = {"caught": 3600, "time": 1800}  # how long the study lets a resting sell wait, by exit kind
REFETCH_AFTER = 3600
WORKERS = 4


def path(market_id):
    return os.path.join(CACHE_DIR, f"tape_{market_id}.json")


def read(market_id):
    try:
        with open(path(market_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def window_path(market_id, exit_time):
    return os.path.join(CACHE_DIR, f"window_{market_id}_{int(exit_time)}.json")


def exits():
    """[(market id, exit time, kind)] of the baseline backtest's caught-up and time-stop exits."""
    if not os.path.exists(TRADES_CSV):
        return []
    with open(TRADES_CSV) as f:
        rows = [(t["market"], int(t["exit_time"]), t["reason"].split(":")[0].split(" ")[0]) for t in csv.DictReader(f)
                if t["run"].startswith("baseline") and t["exit_time"]]
    return [r for r in rows if r[2] in WINDOW]


def load_window(market_id, exit_time):
    try:
        with open(window_path(market_id, exit_time)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def fetch_tape(exchange_id, start, end):
    """[[time, YES price, shares, value]] oldest first, every fill between start and end."""
    rows, cursor = [], None
    while True:
        params = {"limit": 200, "from": _iso(start), "to": _iso(end)}
        if cursor:
            params["cursor"] = cursor
        page = sig._get(f"/exchanges/{exchange_id}/trades", **params)
        rows += [[datetime.fromisoformat(t["createdAt"].replace("Z", "+00:00")).timestamp(), t["price"], t["size"], t["volume"]]
                 for t in page["data"] if t["price"] is not None]
        if not page["pagination"].get("hasMore"):
            return sorted(rows)
        cursor = page["pagination"]["nextCursor"]


def _save(file, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(file + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(file + ".tmp", file)


def fetch(races, end=None):
    """Cache the weekly tape of every enabled market and the window after every exit. Returns how many files were written."""
    end = int(end or time.time())
    sig.setup(races)
    exchange = {c["market_id"]: c["sig_exchange_id"] for _, c in iter_contracts(races) if c["sig_exchange_id"]}
    jobs = [("window", m, t) for m, t, _ in exits() if m in exchange] + [("week", m, None) for m in exchange]  # exit windows first
    kind_of = {(m, t): kind for m, t, kind in exits()}
    done = 0

    def one(job):
        nonlocal done
        kind, market_id, t0 = job
        try:
            if kind == "week":
                cached = read(market_id)
                if cached and cached["from"] <= end - WEEK + REFETCH_AFTER and end - cached["to"] < REFETCH_AFTER:
                    return 0
                _save(path(market_id), {"from": end - WEEK, "to": end, "rows": fetch_tape(exchange[market_id], end - WEEK, end)})
            else:
                if load_window(market_id, t0) is not None:
                    return 0
                stop = min(t0 + WINDOW[kind_of[(market_id, t0)]], end)
                _save(window_path(market_id, t0), {"from": t0, "to": stop, "rows": fetch_tape(exchange[market_id], t0, stop)})
            return 1
        except Exception as e:  # left for the next run
            print(f"  WARNING market {market_id}: {type(e).__name__}: {e}")
            return 0
        finally:
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(jobs)} jobs checked", flush=True)

    return sum(run_all([lambda job=job: one(job) for job in jobs], workers=WORKERS))


def load(market_id):
    entry = read(market_id)
    return entry["rows"] if entry else []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reads-per-minute", type=int, default=40)
    args = parser.parse_args()
    config.SIG_READS_PER_MINUTE = args.reads_per_minute
    races = load_races(config.RACES_FILE)
    print(f"Fetching tapes: 7 days for every market and {len(exits())} exit windows (<= {args.reads_per_minute} SIG reads a minute)...", flush=True)
    started = time.time()
    print(f"Done in {(time.time() - started) / 60:.1f} min: {fetch(races)} tapes fetched.")


if __name__ == "__main__":
    main()
