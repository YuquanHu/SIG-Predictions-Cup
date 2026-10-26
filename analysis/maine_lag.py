#!/usr/bin/env python3
"""Read-only: Maine Senate, Troy Jackson (Democratic) to win. Compare Kalshi and SIG 1-minute prices on 2026-10-16,
08:00-23:59 ET, and measure how many minutes SIG lags Kalshi. Places no orders.

Both markets are the same outcome and the same side: Kalshi SENATEME-26-D YES (yes_sub_title "Troy Jackson") and
SIG market 270 "Will the Democratic Party win the Maine Senate?" exchange 959 YES. No flip is needed.
Every bucket is keyed by its START time in UTC (Kalshi labels a candle by its END time, so that is shifted back 60s).
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import sig

UTC, ET = ZoneInfo("UTC"), ZoneInfo("America/New_York")
START = datetime(2026, 10, 16, 8, 0, tzinfo=ET)
END = datetime(2026, 10, 17, 0, 0, tzinfo=ET)   # exclusive: the last bucket is 23:59 ET
KALSHI_SERIES, KALSHI_TICKER, SIG_EXCHANGE = "SENATEME", "SENATEME-26-D", "959"
OUT = os.path.dirname(os.path.abspath(__file__))
MAX_LAG = 30


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def minutes():
    return pd.date_range(START.astimezone(UTC), END.astimezone(UTC), freq="min", inclusive="left")


def kalshi_series():
    """Kalshi YES bid, ask, mid and last-trade price per minute (bucket start, UTC)."""
    resp = requests.get(f"{config.KALSHI_BASE}/series/{KALSHI_SERIES}/markets/{KALSHI_TICKER}/candlesticks",
                        params={"start_ts": int(START.timestamp()), "end_ts": int(END.timestamp()), "period_interval": 1},
                        timeout=30)
    resp.raise_for_status()
    rows = []
    for c in resp.json()["candlesticks"]:
        trade = c["price"].get("close_dollars")
        previous = c["price"].get("previous_dollars")
        rows.append({"k_previous": float(previous) if previous is not None else np.nan,"time": pd.Timestamp(c["end_period_ts"] - 60, unit="s", tz="UTC"),
                     "k_bid": float(c["yes_bid"]["close_dollars"]), "k_ask": float(c["yes_ask"]["close_dollars"]),
                     "k_trade": float(trade) if trade is not None else np.nan, "k_volume": float(c["volume_fp"])})
    df = pd.DataFrame(rows).set_index("time").reindex(minutes())
    df["k_mid"] = (df.k_bid + df.k_ask) / 2
    df["k_trade_price"] = df.k_trade.ffill().fillna(df.k_previous.bfill())  # last trade, carried forward
    return df


def sig_series():
    """SIG YES trade price per minute: the close of each minute that had trades, carried forward. SIG only returns
    minutes with trades, so the price before the first trade of the window is seeded from the last candle before it."""
    window = sig._get(f"/exchanges/{SIG_EXCHANGE}/price-history", resolution="1m", limit=1000,
                      **{"from": iso(START), "to": iso(END)})
    before = sig._get(f"/exchanges/{SIG_EXCHANGE}/price-history", resolution="1m", limit=1, to=iso(START))["candles"]
    df = pd.DataFrame(window["candles"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").rename(columns={"close": "s_close", "volume": "s_volume", "tradeCount": "s_trades"})
    df = df[["s_close", "s_volume", "s_trades"]].reindex(minutes())
    df["s_traded"] = df.s_trades.notna()
    df["s_trades"] = df.s_trades.fillna(0)
    df["s_volume"] = df.s_volume.fillna(0)
    seed = before[-1]["close"] if before else np.nan
    df["s_price"] = df.s_close.ffill().fillna(seed)
    return df, seed, (before[-1]["time"] if before else None)


def cross_correlation(a, b, max_lag):
    """corr(a[t], b[t+k]) for k in -max_lag..max_lag. A peak at k > 0 means b follows a by k minutes."""
    lags = np.arange(-max_lag, max_lag + 1)
    out = []
    for k in lags:
        x, y = (a[:len(a) - k], b[k:]) if k >= 0 else (a[-k:], b[:len(b) + k])
        out.append(np.corrcoef(x, y)[0, 1] if x.std() and y.std() else np.nan)
    return lags, np.array(out)


def main():
    k = kalshi_series()
    s, seed, seed_time = sig_series()
    df = k.join(s)
    df.index.name = "time_utc"
    df.to_csv(os.path.join(OUT, "maine_jackson_1m.csv"))
    print(f"window {iso(START)} -> {iso(END)} (UTC), {len(df)} one-minute buckets")
    print(f"Kalshi: bid/ask in every bucket: {df.k_mid.notna().all()}; minutes with a Kalshi trade: {int((df.k_volume > 0).sum())}")
    print(f"SIG: minutes with trades: {int(df.s_traded.sum())} of {len(df)}; {int(df.s_trades.sum())} trades; "
          f"price before window seeded from {seed_time} = {seed}")
    print(f"mean level: Kalshi mid {df.k_mid.mean():.4f}, SIG {df.s_price.mean():.4f}; "
          f"mean gap (SIG - Kalshi) {np.mean(df.s_price - df.k_mid):+.4f}")

    # ---- lag from the cross-correlation of price changes. Kalshi quotes (mid) and Kalshi last-trade price are both
    # tried: SIG only publishes trade prices, so the Kalshi last-trade series is the like-for-like comparison.
    results = {}
    for name, kcol in (("Kalshi last-trade", "k_trade_price"), ("Kalshi mid", "k_mid")):
        for label, step in (("1-minute", 1), ("5-minute", 5)):
            dk, ds = df[kcol].diff(step).dropna().to_numpy(), df.s_price.diff(step).dropna().to_numpy()
            lags, cc = cross_correlation(dk, ds, MAX_LAG)
            best = int(np.nanargmax(cc))
            band = 2 / np.sqrt(len(dk))
            top = np.argsort(-np.nan_to_num(cc, nan=-9))[:4]
            print(f"{name}, {label} changes ({int((df[kcol].diff(1) != 0).sum() - 1)} Kalshi changes, "
                  f"{int((df.s_price.diff(1) != 0).sum() - 1)} SIG changes): peak {cc[best]:.3f} at {lags[best]:+d} min, "
                  f"zero lag {cc[MAX_LAG]:.3f}, noise band +/-{band:.3f}; top: "
                  + ", ".join(f"{lags[i]:+d}m={cc[i]:.3f}" for i in top))
            results[(kcol, step)] = (lags, cc)
    cc_plot = results[("k_trade_price", 1)]

    # ---- biggest Kalshi move: largest change in the Kalshi mid over any 5 minutes
    move = df.k_mid.diff(5)
    t_end = move.abs().idxmax()
    t_start = t_end - pd.Timedelta(minutes=5)
    k_before, k_after = df.k_mid[t_start], df.k_mid[t_end]
    direction = np.sign(k_after - k_before)
    print(f"\nbiggest Kalshi move: mid {k_before:.3f} -> {k_after:.3f} ({k_after - k_before:+.3f}) from {t_start:%H:%M} to {t_end:%H:%M} UTC"
          f" = {t_start.astimezone(ET):%H:%M} to {t_end.astimezone(ET):%H:%M} ET")
    s_base = df.s_price[t_start]
    after = df.loc[t_start:]
    moved = after[(after.s_price - s_base) * direction > 0]
    caught = after[(after.s_price - s_base) * direction >= 0.75 * abs(k_after - k_before)]
    print(f"SIG price at the start of the move: {s_base:.3f}")
    catch_time = None
    for name, hit in (("first moved the same way", moved), ("moved 75%+ of Kalshi's move", caught)):
        if hit.empty:
            print(f"   SIG never {name} in the window")
        else:
            t = hit.index[0]
            print(f"   SIG {name} at {t:%H:%M} UTC / {t.astimezone(ET):%H:%M} ET: price {hit.s_price.iloc[0]:.3f}, "
                  f"{(t - t_start).total_seconds() / 60:.0f} min after the move began, {(t - t_end).total_seconds() / 60:+.0f} min vs the Kalshi move ending")
            if name.startswith("moved 75"):
                catch_time = t

    # ---- chart
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 8), gridspec_kw={"height_ratios": [3, 1.3]})
    idx = df.index.tz_convert(ET)
    ax.step(idx, df.k_mid, where="post", label="Kalshi YES mid (bid/ask)", color="#1f77b4", lw=1.6)
    ax.step(idx, df.k_trade_price, where="post", label="Kalshi YES last trade", color="#1f77b4", lw=1, ls=":")
    ax.step(idx, df.s_price, where="post", label="SIG YES (last trade, carried forward)", color="#d62728", lw=1.6)
    traded = df[df.s_traded]
    ax.scatter(traded.index.tz_convert(ET), traded.s_close, s=10, color="#d62728", alpha=.5, zorder=3, label="SIG minutes with trades")
    ax.axvspan(t_start.astimezone(ET), t_end.astimezone(ET), color="gold", alpha=.35, label="biggest Kalshi 5-min move")
    if catch_time is not None:
        ax.axvline(catch_time.astimezone(ET), color="#d62728", ls="--", lw=1, label="SIG caught up (75% of the move)")
    ax.set_ylabel("YES price ($)")
    ax.set_title("Maine Senate: Troy Jackson (Democratic) to win, Oct 16 2026, 08:00-23:59 ET (12:00 Oct 16 - 03:59 Oct 17 UTC)")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=.3)
    ax2.bar(traded.index.tz_convert(ET), traded.s_volume, width=1 / 1440, color="#d62728", alpha=.7, label="SIG volume")
    ax2.set_ylabel("SIG volume per minute")
    ax2.grid(alpha=.3)
    for a in (ax, ax2):
        a.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=ET))
        a.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=ET))
    ax2.set_xlabel("time, ET (UTC-4)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "maine_jackson_kalshi_vs_sig.png"), dpi=140)

    fig2, bx = plt.subplots(figsize=(8, 3.5))
    bx.bar(*cc_plot, color="#555")
    bx.axvline(0, color="k", lw=.5)
    bx.set_xlabel("lag in minutes (positive: SIG follows Kalshi)")
    bx.set_ylabel("correlation of 1-min changes")
    fig2.tight_layout()
    fig2.savefig(os.path.join(OUT, "maine_jackson_cross_correlation.png"), dpi=140)


if __name__ == "__main__":
    main()
