"""SIG Super Market API: read-only market lookup, bulk prices, and order-book depth.

SIG allows 100 reads a minute per account. Bulk calls cover up to 100 markets per read, and every read goes
through a sliding-window budget guard so the bot slows down instead of being locked out.
"""
import time

import requests

import budget
import config
from parallel import run_all
from races import clear_quote, iter_contracts, warn

NAME = "SIG"
BULK = 100  # most ids SIG accepts per bulk call


class SigError(Exception):
    pass


def take_read_count():
    """SIG reads made since the last call (for logging reads per loop)."""
    return budget.READS.take_count()


def _get(path, **params):
    if config.SIG_TOURNAMENT_ID:
        params["tournamentId"] = config.SIG_TOURNAMENT_ID
    for attempt in range(2):
        budget.READS.acquire()
        resp = requests.get(
            config.SIG_BASE + path,
            params=params,
            headers={"Authorization": f"Bearer {config.SIG_API_KEY}"},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code == 429 and attempt == 0:  # rate limited despite the guard: wait as told, retry once
            wait = float(resp.headers.get("Retry-After", 60))
            print(f"  (SIG answered 429; waiting {wait:.0f}s as asked)")
            time.sleep(wait)
            continue
        break
    if not resp.ok:
        try:
            err = resp.json()["error"]
            detail = f"{err['code']}: {err['message']}"
        except (ValueError, KeyError, TypeError):
            detail = resp.text[:200]
        raise SigError(f"GET {path} -> {resp.status_code} {detail}")
    return resp.json()


def _chunks(items):
    items = list(items)
    return [items[i:i + BULK] for i in range(0, len(items), BULK)]


def _resolve_all(pairs):
    """Look up the YES exchange id and title of each (race_id, contract) with one read per BULK markets."""
    by_market = {c["market_id"]: (race_id, c) for race_id, c in pairs}
    returned, failed = {}, set()
    for chunk in _chunks(by_market):
        try:
            data = _get("/markets", ids=",".join(chunk), limit=len(chunk))["data"]
        except Exception as e:  # only this chunk's races lose SIG
            failed.update(chunk)
            for market_id in chunk:
                warn(by_market[market_id][0], NAME, f"market {market_id} lookup failed: {e}")
            continue
        returned.update({m["id"]: m for m in data})
    for market_id, (race_id, c) in by_market.items():
        market = returned.get(market_id)
        if market is None:
            if market_id not in failed:
                warn(race_id, NAME, f"market {market_id} not returned by SIG")
            continue
        yes = next((e for e in market["exchanges"] if (e.get("option") or "").upper() == "YES"),
                   market["exchanges"][0])
        c["sig_exchange_id"] = yes["id"]
        c["title"] = market["title"]


def setup(races):
    _resolve_all(list(iter_contracts(races)))


def fetch_prices(races):
    """Best YES bid and ask for every contract, one read per BULK contracts. Sizes are not available here
    (they stay None); fetch_depth supplies them for the contracts that are about to trade."""
    pairs = list(iter_contracts(races))
    for _, c in pairs:
        clear_quote(c["yes"])
    _resolve_all([(r, c) for r, c in pairs if c["sig_exchange_id"] is None])  # retry earlier lookup failures
    by_exchange = {c["sig_exchange_id"]: (r, c) for r, c in pairs if c["sig_exchange_id"]}
    now = time.time()
    run_all([lambda chunk=chunk: _price_chunk(chunk, by_exchange, now) for chunk in _chunks(by_exchange)])


def _price_chunk(chunk, by_exchange, now):
    try:
        data = _get("/exchanges/prices", ids=",".join(chunk))
    except Exception as e:
        for exchange_id in chunk:
            race_id, c = by_exchange[exchange_id]
            warn(race_id, NAME, f"market {c['market_id']}: {e}")
        return
    for row in data["data"]:
        race_id, c = by_exchange[row["exchangeId"]]
        c["yes"].update(bid=row["bestBid"], ask=row["bestAsk"], updated=now)
        if row["bestBid"] is None and row["bestAsk"] is None:
            warn(race_id, NAME, f"market {c['market_id']} has an empty orderbook")
    for exchange_id in data.get("missingIds", []):
        race_id, c = by_exchange[exchange_id]
        warn(race_id, NAME, f"market {c['market_id']}: exchange {exchange_id} not returned")


def fetch_depth(races, market_ids):
    """Best bid and ask with sizes for the given SIG markets, one orderbook read each (run side by side)."""
    wanted = set(market_ids)
    run_all([lambda race_id=race_id, c=c: _read_depth(race_id, c)
             for race_id, c in iter_contracts(races) if c["market_id"] in wanted])


def _read_depth(race_id, c):
    try:
        book = _get(f"/markets/{c['market_id']}/orderbook", depth=1)
        ex = next((e for e in book["exchanges"] if e["exchangeId"] == c["sig_exchange_id"]), None)
        if ex is None:
            raise SigError(f"exchange {c['sig_exchange_id']} not in orderbook")
    except Exception as e:  # keep the bulk quote, but without sizes nothing will trade here
        warn(race_id, NAME, f"market {c['market_id']} depth: {e}")
        return
    best_bid = max(ex["bids"], key=lambda lvl: lvl["price"], default=None)
    best_ask = min(ex["asks"], key=lambda lvl: lvl["price"], default=None)
    c["yes"].update(
        bid=best_bid["price"] if best_bid else None,
        bid_size=best_bid["quantity"] if best_bid else None,
        ask=best_ask["price"] if best_ask else None,
        ask_size=best_ask["quantity"] if best_ask else None,
        updated=time.time(),
    )


def fetch_open_orders(exchange_id=None):
    """The account's resting orders (all of them, or those on one exchange)."""
    params = {"status": "open", "limit": 200}
    if exchange_id:
        params["exchangeId"] = exchange_id
    return _get("/orders", **params)["data"]


def fetch_account():
    """Cash balance and buying power (read-only)."""
    a = _get("/account")
    if a["balance"] is None:
        raise SigError("GET /account returned no cash balance")
    return {"balance": a["balance"], "buying_power": a["buyingPower"], "username": a.get("username")}


def fetch_positions():
    """Open positions: market, side, shares, average price. Quantity > 0 is YES shares, < 0 is NO."""
    slug = config.SIG_TOURNAMENT_SLUG
    path = f"/tournaments/{slug}/portfolio/positions" if slug else "/portfolio/positions"
    positions = []
    for p in _get(path)["positions"]:
        if p["settled"] or p["quantity"] == 0:
            continue
        positions.append({
            "market_id": p["marketId"],
            "exchange_id": p["exchangeId"],
            "title": p["marketTitle"],
            "side": "yes" if p["quantity"] > 0 else "no",
            "shares": abs(p["quantity"]),
            "avg_price": p["avgCost"],
            # currentPrice is the YES price; report the price of the side actually held
            "current_price": None if p["currentPrice"] is None
            else (p["currentPrice"] if p["quantity"] > 0 else 1 - p["currentPrice"]),
            "unrealized_pnl": p["unrealizedPnl"],
        })
    return positions
