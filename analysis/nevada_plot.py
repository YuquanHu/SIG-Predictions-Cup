#!/usr/bin/env python3
"""Chart for analysis/nevada_gov_1m.csv: Polymarket vs SIG, Nevada Governor (D), with a zoom on the Oct 21 jump. Read-only."""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OUT = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(OUT, "nevada_gov_1m.csv"), index_col=0, parse_dates=True)
df["sig"], df["poly"] = df.sig_close.ffill(), df.poly.ffill()
df.index = df.index.tz_convert(ET)
jump = pd.Timestamp("2026-10-21 14:25:28", tz="UTC").tz_convert(ET)
fig, (a, b) = plt.subplots(2, 1, figsize=(13, 8))
for ax, lo, hi, fmt in ((a, "2026-10-19 12:00", "2026-10-23 12:00", "%b %d %H:%M"), (b, "2026-10-21 08:00", "2026-10-21 18:00", "%H:%M")):
    w = df.loc[pd.Timestamp(lo, tz="UTC"):pd.Timestamp(hi, tz="UTC")]
    ax.step(w.index, w.poly, where="post", label="Polymarket Yes (D, Aaron Ford)", color="#1f77b4", lw=1.6)
    ax.step(w.index, w.sig, where="post", label="SIG market 182 YES (last trade)", color="#d62728", lw=1.6)
    ax.axvline(jump, color="gold", lw=2, label="Polymarket +4c jump, 10:25:28 ET Oct 21")
    ax.grid(alpha=.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=ET))
    ax.set_ylabel("YES price ($)")
a.set_title("Nevada Governor, Democrat to win: Polymarket vs SIG (times in ET, UTC-4)")
a.legend(fontsize=8, loc="upper left")
b.set_title("Zoom: Oct 21, 08:00-18:00 UTC = 04:00-14:00 ET")
b.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=ET))
fig.tight_layout()
fig.savefig(os.path.join(OUT, "nevada_gov_poly_vs_sig.png"), dpi=140)
