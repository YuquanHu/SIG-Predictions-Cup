"""Real SIG order calls, used only by test_trade.py. Every request and response is printed, with the
API key hidden. The guards here make it impossible to send a market order, an off-tick price, or more
than MAX_ORDER_SHARES shares in one order."""
import json
import time
import uuid

import requests

import config

MIN_ORDER_SHARES = 1  # the docs state no minimum; quantity is an integer, so one share is the smallest order
MAX_ORDER_SHARES = 1  # hard cap for this test
TICK, MIN_PRICE, MAX_PRICE = 0.005, 0.005, 0.995
READ_RETRIES = 4         # extra attempts for a GET that answers 503 (documented as temporary and safe to retry)
READ_RETRY_PAUSE = 2     # seconds


class TradeError(Exception):
    pass


def scrub(text):
    return text.replace(config.SIG_API_KEY, "****")


class Client:
    @staticmethod
    def _send(method, url, path, params, body):
        """One HTTP attempt: print the request, then the response; return (response, parsed json)."""
        print(f"\n>>> {method} {url}" + (f"  params={params}" if params else ""))
        print(">>> Authorization: Bearer ****")
        if body is not None:
            print(f">>> body: {json.dumps(body)}")
        try:
            resp = requests.request(method, url, params=params, json=body, timeout=config.HTTP_TIMEOUT,
                                    headers={"Authorization": f"Bearer {config.SIG_API_KEY}"})
        except requests.RequestException as e:
            raise TradeError(f"{method} {path}: no response ({scrub(str(e))}); "
                             f"it may or may not have been processed") from e
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:500]}
        print(f"<<< HTTP {resp.status_code}")
        print(scrub(json.dumps(data, indent=2)))
        return resp, data

    def request(self, method, path, params=None, body=None, expect=(200,)):
        """Send one request, print it and the response, and return (status, json).
        Any status outside `expect` raises TradeError."""
        params, body = dict(params or {}), (dict(body) if body is not None else None)
        if config.SIG_TOURNAMENT_ID:
            if body is not None:
                body.setdefault("tournamentId", config.SIG_TOURNAMENT_ID)
            elif method == "GET":
                params.setdefault("tournamentId", config.SIG_TOURNAMENT_ID)
        url = config.SIG_BASE + path
        for attempt in range(READ_RETRIES + 1):
            resp, data = self._send(method, url, path, params, body)
            if method != "GET" or resp.status_code != 503 or attempt == READ_RETRIES:
                break  # orders and cancels are never retried
            print(f"(read returned 503; retrying in {READ_RETRY_PAUSE}s, attempt {attempt + 2} of {READ_RETRIES + 1})")
            time.sleep(READ_RETRY_PAUSE)
        if resp.status_code not in expect:
            raise TradeError(f"{method} {path}: unexpected HTTP {resp.status_code}")
        return resp.status_code, data


def check_limit_order(quantity, price):
    """Raise unless this is a whole, minimum-size limit order on the price tick (never a market order)."""
    if not isinstance(quantity, int) or not MIN_ORDER_SHARES <= quantity <= MAX_ORDER_SHARES:
        raise TradeError(f"refusing quantity {quantity!r}: only {MIN_ORDER_SHARES}..{MAX_ORDER_SHARES} allowed")
    if price is None or not MIN_PRICE - 1e-9 <= price <= MAX_PRICE + 1e-9:
        raise TradeError(f"refusing price {price!r}: market orders and prices outside "
                         f"{MIN_PRICE}..{MAX_PRICE} are not allowed")
    if abs(price / TICK - round(price / TICK)) > 1e-6:
        raise TradeError(f"refusing price {price!r}: not on the {TICK} tick")


def place_limit(client, exchange_id, side, action, quantity, price, expect=(200,)):
    check_limit_order(quantity, price)
    body = {"idempotencyKey": str(uuid.uuid4()), "exchangeId": exchange_id, "side": side,
            "action": action, "quantity": quantity, "price": round(price, 3)}
    return client.request("POST", "/orders", body=body, expect=expect)[1]


def classify(placement, quantity):
    """What a POST /orders response means: noop, filled, partial, resting, or unexpected."""
    if placement["orderId"] is None:
        return "noop"
    traded = placement["quantityTraded"]
    if not placement["open"] and traded >= quantity:
        return "filled"
    if placement["open"] and 0 < traded < quantity:
        return "partial"
    if placement["open"] and traded == 0:
        return "resting"
    return "unexpected"


def cancel(client, order_id):
    return client.request("DELETE", f"/orders/{order_id}", expect=(200, 409))


def open_orders(client, exchange_id):
    return client.request("GET", "/orders", params={"status": "open", "exchangeId": exchange_id})[1]["data"]


def get_order(client, order_id):
    return client.request("GET", f"/orders/{order_id}")[1]


def best_prices(client, market_id):
    """(bid, bid_size, ask, ask_size) of the Yes book; None where a side is empty."""
    book = client.request("GET", f"/markets/{market_id}/orderbook", params={"depth": 1})[1]["exchanges"][0]
    bid = max(book["bids"], key=lambda lvl: lvl["price"], default=None)
    ask = min(book["asks"], key=lambda lvl: lvl["price"], default=None)
    return (bid and bid["price"], bid and bid["quantity"], ask and ask["price"], ask and ask["quantity"])


def signed_position(client, market_id):
    """Shares held in the market: positive Yes, negative No, 0 when none."""
    slug = config.SIG_TOURNAMENT_SLUG
    path = f"/tournaments/{slug}/portfolio/positions" if slug else "/portfolio/positions"
    positions = client.request("GET", path)[1]["positions"]
    return next((p["quantity"] for p in positions if p["marketId"] == market_id), 0)


def balance(client):
    return client.request("GET", "/account")[1]["balance"]
