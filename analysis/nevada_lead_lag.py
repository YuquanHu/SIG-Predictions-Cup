#!/usr/bin/env python3
"""Read-only: Nevada Governor, Democrat (Aaron Ford) to win. Polymarket vs SIG, 1-minute prices, Oct 19 12:00 UTC - Oct 23 12:00 UTC.
Polymarket: Gamma market 629505, Yes token (CLOB prices-history). SIG: market 182, exchange 871, YES. Places no orders."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import sig

UTC = timezone.utc
OUT = os.path.dirname(os.path.abspath(__file__))
POLY_TOKEN = "1246864710804983890692377994891598401616546411512113588399830364959833951829"
SIG_EXCHANGE = "871"
START = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
END = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def poly_history():
    """Polymarket Yes price per minute (point labelled by its timestamp)."""
    rows, a = [], START
    while a < END:
        b = min(a + timedelta(hours=12), END)
        r = requests.get(f"{config.POLY_CLOB_BASE}/prices-history",
                         params={"market": POLY_TOKEN, "startTs": int(a.timestamp()), "endTs": int(b.timestamp()), "fidelity": 1},
                         timeout=30)
        r.raise_for_status()
        rows += r.json().get("history", [])
        a = b
    df = pd.DataFrame(rows).drop_duplicates("t").sort_values("t")
    df["time"] = pd.to_datetime(df.t, unit="s", utc=True)
    series = df.set_index("time")["p"].rename("poly")  # raw points, a few seconds off the minute
    return series[(series.index >= START) & (series.index < END)]


def sig_history():
    """SIG YES trade candles per minute: close, volume, trade count (minutes with no trade are missing)."""
    rows, a = [], START
    while a < END:
        b = min(a + timedelta(hours=16), END)
        h = sig._get(f"/exchanges/{SIG_EXCHANGE}/price-history", resolution="1m", limit=1000,
                     **{"from": a.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "to": b.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        rows += h["candles"]
        a = b
    df = pd.DataFrame(rows).drop_duplicates("time")
    df["time"] = pd.to_datetime(df.time, utc=True)
    return df.set_index("time")[["open", "high", "low", "close", "volume", "tradeCount"]].add_prefix("sig_")


if __name__ == "__main__":
    p, s = poly_history(), sig_history()
    p.to_frame().to_csv(os.path.join(OUT, "nevada_gov_poly_raw.csv"))
    idx = pd.date_range(START, END, freq="min", inclusive="left")
    poly_minute = p.groupby(p.index.floor("min")).last()  # last Polymarket point inside each minute
    df = pd.concat([poly_minute, s], axis=1, sort=True).reindex(idx)
    df.index.name = "time_utc"
    df.to_csv(os.path.join(OUT, "nevada_gov_1m.csv"))
    print(f"Polymarket points: {int(df.poly.notna().sum())} of {len(df)} minutes; SIG minutes with trades: {int(df.sig_close.notna().sum())}")
    print(df.poly.describe().round(3).to_dict())
    print(df.sig_close.describe().round(3).to_dict())
