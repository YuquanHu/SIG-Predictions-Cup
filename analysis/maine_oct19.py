#!/usr/bin/env python3
"""Read-only: Maine Senate, Troy Jackson (D) to win, 2026-10-19. Kalshi vs SIG around 2pm-3pm ET. Places no orders."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maine_lag as m

m.START = datetime(2026, 10, 19, 13, 0, tzinfo=m.ET)
m.END = datetime(2026, 10, 19, 17, 0, tzinfo=m.ET)
k = m.kalshi_series()
s, seed, seed_time = m.sig_series()
df = k.join(s)
df.index.name = "time_utc"
df.to_csv(os.path.join(m.OUT, "maine_oct19_1m.csv"))
print(f"SIG seed {seed} from {seed_time}; SIG minutes with trades {int(df.s_traded.sum())}, Kalshi trade minutes {int((df.k_volume > 0).sum())}")
view = df.copy()
view.index = view.index.tz_convert(m.ET).strftime("%H:%M")
cols = ["k_bid", "k_ask", "k_mid", "k_trade", "k_trade_price", "s_close", "s_price", "s_trades", "s_volume"]
changed = (df.k_mid.diff() != 0) | (df.s_price.diff() != 0) | (df.k_trade_price.diff() != 0) | df.s_traded | (df.k_volume > 0)
print(view[cols][changed.to_numpy()].to_string())
