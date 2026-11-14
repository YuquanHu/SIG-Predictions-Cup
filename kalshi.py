"""Kalshi public market data (no auth)."""
import time

import requests

import config
from races import clear_quote, fill_missing_bid, iter_outcomes

NAME = "Kalshi"


def _price(value):
    """Dollar string to float; empty or 0 means 'no quote' (None), never a real price of zero."""
    price = float(value) if value not in (None, "") else None
    return price or None


def _size(value):
    return float(value) if value not in (None, "") else None


def setup(races):
    """Kalshi needs no id lookup."""


def fetch_prices(races):
    """One batched request for every Kalshi ticker in the race table. Kalshi is optional (Polymarket is the fair value's source), so a failed
    request, a ticker it no longer returns (a settled market) and a market with no two-sided quote leave that outcome without a Kalshi quote
    and say nothing: the fair value's note shows it was dropped."""
    outcomes = {}  # ticker -> outcome
    for _, _, o in iter_outcomes(races):
        clear_quote(o["kalshi"])
        if o["kalshi_id"]:  # outcomes with no Kalshi market are skipped
            outcomes[o["kalshi_id"]] = o
    if not outcomes:
        return
    try:
        resp = requests.get(
            f"{config.KALSHI_BASE}/markets",
            params={"tickers": ",".join(outcomes)},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        markets = {m["ticker"]: m for m in resp.json()["markets"]}
    except Exception:
        return

    now = time.time()
    for ticker, o in outcomes.items():
        m = markets.get(ticker)
        if m is None:
            continue
        o["kalshi"].update(
            bid=_price(m.get("yes_bid_dollars")),
            ask=_price(m.get("yes_ask_dollars")),
            bid_size=_size(m.get("yes_bid_size_fp")),
            ask_size=_size(m.get("yes_ask_size_fp")),
            updated=now,
        )
        fill_missing_bid(o["kalshi"])
