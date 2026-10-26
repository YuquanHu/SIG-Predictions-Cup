"""Back-fill the gap history (fair value minus SIG yes price) from the Kalshi, Polymarket and SIG price histories, so the
gap model has HISTORY_HOURS of samples at start-up instead of waiting two days. Read-only.

Fair values are scaled to add up to 1 across a race's outcomes at every sample time, exactly as the live bot does
(strategy.normalize), so the usual gap and the live gap are measured the same way.

History is coarser than live data: Kalshi gives hourly bid/ask candles, Polymarket a price about every 15 minutes, SIG only
the minutes that had trades (its last trade price is carried forward). The error is about half a spread, small next to 3c.
"""
import time
from datetime import datetime, timezone

import requests

import config
import sig
from gapmodel import SAMPLE_SECONDS
from parallel import run_all
from races import iter_contracts, warn
from strategy import normalize

EXTERNAL_MAX_AGE = 3 * 3600  # a Kalshi/Polymarket point older than this does not count as the price at a sample time
MAX_ATTEMPTS = 4             # tries per history request when the API says 429 or fails
STALE_TAIL = 3600            # a contract whose newest sample is older than this gets the missing stretch fetched
WORKERS = 4                  # history requests at once; the public APIs answer 429 to a burst of 8


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _get_json(url, params):
    """GET with a few retries when the public API is busy (429) or failing (5xx), waiting as long as it asks."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = requests.get(url, params=params, timeout=config.HTTP_TIMEOUT * 3)
        if resp.status_code != 429 and resp.status_code < 500:
            resp.raise_for_status()
            return resp.json()
        if attempt == MAX_ATTEMPTS:
            resp.raise_for_status()
        time.sleep(float(resp.headers.get("Retry-After", 2 ** attempt)))


def kalshi_mids(ticker, start, end, interval=60):
    """{time: yes mid} from Kalshi candles. A candle is labelled by its END time, which is when its close happened."""
    series = ticker.split("-")[0]
    data = _get_json(f"{config.KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks",
                     {"start_ts": int(start), "end_ts": int(end), "period_interval": interval})
    return {c["end_period_ts"]: (float(c["yes_bid"]["close_dollars"]) + float(c["yes_ask"]["close_dollars"])) / 2
            for c in data["candlesticks"] if c["yes_bid"].get("close_dollars") and c["yes_ask"].get("close_dollars")}


def poly_prices(token, start, end, fidelity=15):
    """{time: yes price} from Polymarket's price history (a point every `fidelity` minutes or so)."""
    data = _get_json(f"{config.POLY_CLOB_BASE}/prices-history",
                     {"market": token, "startTs": int(start), "endTs": int(end), "fidelity": fidelity})
    return {p["t"]: p["p"] for p in data.get("history", [])}


SIG_CANDLE_SECONDS = 300  # sig_closes asks for 5-minute candles


def sig_closes(exchange_id, start, end):
    """{time: close} of SIG's yes trade candles (only periods with trades appear), keyed by the candle's END time."""
    data = sig._get(f"/exchanges/{exchange_id}/price-history", resolution="5m", limit=1000,
                    **{"from": _iso(start), "to": _iso(end)})
    return {int(datetime.fromisoformat(c["time"].replace("Z", "+00:00")).timestamp()) + SIG_CANDLE_SECONDS: c["close"]
            for c in data["candles"] if c["close"] is not None}


def value_at(series, mark, max_age=None):
    """The latest value in {time: value} at or before `mark` (and not older than max_age), else None."""
    times = [t for t in series if t <= mark and (max_age is None or mark - t <= max_age)]
    return series[max(times)] if times else None


def outcome_fair(outcome, start, end, kalshi_interval=60, step=SAMPLE_SECONDS):
    """{time: fair_yes} every `step` seconds between start and end, wherever a source has a price: the average of the
    Kalshi and Polymarket prices that exist at that time."""
    external = []
    if outcome["kalshi_id"]:
        external.append(kalshi_mids(outcome["kalshi_id"], start, end, kalshi_interval))
    if outcome["poly_yes_token"]:
        external.append(poly_prices(outcome["poly_yes_token"], start, end))
    fair = {}
    for mark in range(int(start) - int(start) % step + step, int(end) + 1, step):
        prices = [p for p in (value_at(s, mark, EXTERNAL_MAX_AGE) for s in external) if p is not None]
        if prices:
            fair[mark] = sum(prices) / len(prices)
    return fair


def contract_prices(outcome, contract, start, end, kalshi_interval=60, step=SAMPLE_SECONDS):
    """{time: (fair_yes, SIG yes)} every `step` seconds between start and end, wherever both exist. The fair value is this
    outcome's alone (not scaled with the rest of its race)."""
    fair = outcome_fair(outcome, start, end, kalshi_interval, step)
    sig_series = sig_closes(contract["sig_exchange_id"], start, end)
    return {mark: (f, sig_price) for mark, f in fair.items() for sig_price in [value_at(sig_series, mark)] if sig_price is not None}


def race_gaps(race, starts, end):
    """{market_id: {time: fair_yes - SIG yes}} for the contracts in `starts` ([(contract, first time wanted)]), with the
    race's outcome fair values scaled to add up to 1 at every sample time."""
    first = min(start for _, start in starts)
    fair = {code: outcome_fair(o, first, end) for code, o in race["outcomes"].items()}
    gaps = {}
    for c, start in starts:
        sig_series = sig_closes(c["sig_exchange_id"], start, end)
        gaps[c["market_id"]] = {}
        for mark in fair[c["pays_if"]]:
            sig_price = value_at(sig_series, mark)
            if mark >= start and sig_price is not None:
                scaled = normalize({code: series.get(mark) for code, series in fair.items()})
                gaps[c["market_id"]][mark] = scaled[c["pays_if"]] - sig_price
    return gaps


def fetch_start(store, market_id, now):
    """Where a contract's missing history begins: 48 hours ago when it has too few samples, else just after its newest
    sample when that is more than STALE_TAIL old (the bot was off), else None (nothing to fetch)."""
    window_start = now - config.HISTORY_HOURS * 3600
    latest = store.latest(market_id)
    if store.count(market_id, now) < config.MIN_HISTORY_SAMPLES or latest is None:
        return window_start
    return max(latest, window_start) if now - latest > STALE_TAIL else None


def backfill(races, store, now=None):
    """Fill `store` with the gaps it is missing: 48 hours for a contract with too few samples, or the stretch since its
    newest sample when the bot was off for a while. Returns how many contracts were filled; a failure is a warning for
    that race only. A race is fetched as a whole, because its fair values are scaled together."""
    now = now or time.time()
    todo = {}  # race_id -> [(contract, first time wanted)]
    for race_id, c in iter_contracts(races):
        start = fetch_start(store, c["market_id"], now) if c["sig_exchange_id"] else None
        if start is not None:
            todo.setdefault(race_id, []).append((c, start))

    def fill(race_id, starts):
        try:
            gaps = race_gaps(races[race_id], starts, now)
        except Exception as e:
            warn(race_id, "history", f"markets {', '.join(c['market_id'] for c, _ in starts)}: {type(e).__name__}: {e}")
            return 0
        for market_id, series in gaps.items():
            for mark, gap in series.items():
                store.add(market_id, mark, gap)
        return sum(1 for series in gaps.values() if series)

    return sum(run_all([lambda race_id=race_id, starts=starts: fill(race_id, starts) for race_id, starts in todo.items()],
                       workers=WORKERS))
