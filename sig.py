"""SIG Super Market API: read-only market lookup and orderbook prices."""
import time

import requests

import config
from races import clear_quote, iter_contracts, warn

NAME = "SIG"


class SigError(Exception):
    pass


def _get(path, **params):
    if config.SIG_TOURNAMENT_ID:
        params["tournamentId"] = config.SIG_TOURNAMENT_ID
    resp = requests.get(
        config.SIG_BASE + path,
        params=params,
        headers={"Authorization": f"Bearer {config.SIG_API_KEY}"},
        timeout=config.HTTP_TIMEOUT,
    )
    if not resp.ok:
        try:
            err = resp.json()["error"]
            detail = f"{err['code']}: {err['message']}"
        except (ValueError, KeyError, TypeError):
            detail = resp.text[:200]
        raise SigError(f"GET {path} -> {resp.status_code} {detail}")
    return resp.json()


def _resolve(contract):
    """Resolve a SIG market id to its YES exchange id (binary markets have one exchange)."""
    market = _get(f"/markets/{contract['market_id']}")
    exchanges = market["exchanges"]
    yes = next((e for e in exchanges if (e.get("option") or "").upper() == "YES"), exchanges[0])
    contract["sig_exchange_id"] = yes["id"]
    contract["title"] = market["title"]


def setup(races):
    for race_id, c in iter_contracts(races):
        try:
            _resolve(c)
        except Exception as e:  # a bad market must not stop the other races
            warn(race_id, NAME, f"market {c['market_id']} lookup failed: {e}")


def fetch_prices(races):
    """Best YES bid/ask with sizes for every SIG contract, one orderbook read each."""
    for race_id, c in iter_contracts(races):
        clear_quote(c["yes"])
        try:
            if c["sig_exchange_id"] is None:  # setup failed earlier; retry it
                _resolve(c)
            book = _get(f"/markets/{c['market_id']}/orderbook", depth=1)
            ex = next((e for e in book["exchanges"] if e["exchangeId"] == c["sig_exchange_id"]), None)
            if ex is None:
                raise SigError(f"exchange {c['sig_exchange_id']} not in orderbook")
            best_bid = max(ex["bids"], key=lambda lvl: lvl["price"], default=None)
            best_ask = min(ex["asks"], key=lambda lvl: lvl["price"], default=None)
        except Exception as e:
            warn(race_id, NAME, f"market {c['market_id']}: {e}")
            continue
        c["yes"].update(
            bid=best_bid["price"] if best_bid else None,
            bid_size=best_bid["quantity"] if best_bid else None,
            ask=best_ask["price"] if best_ask else None,
            ask_size=best_ask["quantity"] if best_ask else None,
            updated=time.time(),
        )
        if best_bid is None and best_ask is None:
            warn(race_id, NAME, f"market {c['market_id']} has an empty orderbook")


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
