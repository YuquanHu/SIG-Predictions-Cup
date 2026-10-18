#!/usr/bin/env python3
"""Stage 5: prove REAL order handling works on SIG market 388. This script sends real orders.

    python3 test_trade.py --preflight   # reads only: shows the book, your position, and what it would send
    python3 test_trade.py               # the real test; asks you to type "yes" before every order

Step A  limit buy of 1 Yes far below the bid: it must rest as an open order; cancel it; confirm it is gone.
Step B  limit buy of 1 Yes at the ask: it must fill; check the position; sell it back at the bid; confirm
        the position is back where it started.
Step C  a deliberately invalid order (off-tick price), to see how a rejection looks.

Rules: 1 share per order, one order at a time, limit orders only, stop on any unexpected error.
main.py is not involved and still never sends orders.
"""
import sys
import time
import uuid

import sig_trade as st

MARKET = "388"
FAR_PRICE = 0.005       # lowest allowed price, for the order that must not fill
MIN_GAP = 0.05          # step A needs the bid at least this far above FAR_PRICE
MAX_SPREAD = 0.05       # refuse to trade into a book wider than this
FILL_TIMEOUT = 30       # seconds to wait for a fill
SHARES = st.MIN_ORDER_SHARES


class Stop(Exception):
    """An expected check failed; the run ends here."""


def confirm(description):
    print(f"\nABOUT TO SEND A REAL ORDER: {description}")
    try:
        answer = input('Type "yes" to send it, anything else stops: ').strip()
    except EOFError:
        answer = ""
    if answer != "yes":
        raise Stop("not confirmed")


def wait_for(description, check, timeout, interval=2):
    """Poll `check()` until it returns something truthy; Stop on timeout."""
    end = time.time() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.time() >= end:
            raise Stop(f"timed out after {timeout}s waiting for {description}")
        time.sleep(interval)


def read_state(client):
    market = client.request("GET", f"/markets/{MARKET}")[1]
    exchange = next((e["id"] for e in market["exchanges"] if (e["option"] or "").upper() == "YES"), None)
    if exchange is None:
        raise Stop("market has no YES exchange")
    print(f"\nmarket {MARKET}: {market['title']} (status {market['status']}), YES exchange {exchange}")
    if market["status"] != "open":
        raise Stop("market is not open")
    return exchange


def read_book(client):
    bid, bid_size, ask, ask_size = st.best_prices(client, MARKET)
    if bid is None or ask is None:
        raise Stop("the book has no bid or no ask")
    if ask - bid > MAX_SPREAD:
        raise Stop(f"spread {ask - bid:.3f} is wider than {MAX_SPREAD}")
    print(f"\nYES book: bid {bid} x{bid_size:,.0f}   ask {ask} x{ask_size:,.0f}")
    return bid, ask


def preflight(client, exchange):
    bid, ask = read_book(client)
    position = st.signed_position(client, MARKET)
    print(f"\nyour position in market {MARKET}: {position:,.0f} (positive Yes, negative No)")
    print(f"your cash balance: {st.balance(client):,}")
    resting = st.open_orders(client, exchange)
    print(f"your open orders on this exchange: {len(resting)}")
    print("\n--- PREFLIGHT: nothing below has been sent ---")
    print(f"Step A would send: buy {SHARES} yes @ {FAR_PRICE} (bid is {bid}, gap {bid - FAR_PRICE:.3f}; "
          f"needs {MIN_GAP}), then cancel it")
    print(f"Step B would send: buy {SHARES} yes @ {ask}, then sell {SHARES} yes @ {bid}")
    print(f"Step C would send: buy {SHARES} yes @ 0.0025 (off the 0.005 tick), expecting HTTP 400")
    if position < 0:
        print(f"\nNOTE: you hold {-position:,.0f} No shares here. Buying 1 Yes nets against them (the docs say an "
              f"opposite-signed buy reduces the position), so after Step B's buy your position should read "
              f"{position + 1:,.0f}, not a separate Yes position. The sell is then converted by the engine into "
              f"a buy of 1 No, and the position should return to {position:,.0f}.")


def step_a(client, exchange):
    print("\n========== STEP A: an order that cannot fill ==========")
    bid, _ = read_book(client)
    if bid - FAR_PRICE < MIN_GAP:
        raise Stop(f"bid {bid} is too close to {FAR_PRICE} for a resting order test")
    before = st.signed_position(client, MARKET)
    confirm(f"BUY {SHARES} YES on market {MARKET} @ {FAR_PRICE} (bid is {bid})")
    placed = st.place_limit(client, exchange, "yes", "buy", SHARES, FAR_PRICE)
    order_id = placed["orderId"]
    if st.classify(placed, SHARES) != "resting":
        raise Stop(f"expected a resting order, got {st.classify(placed, SHARES)}")
    print(f"\nCHECK: order {order_id} is resting, nothing traded")
    try:
        if order_id not in [o["id"] for o in st.open_orders(client, exchange)]:
            raise Stop(f"order {order_id} is not in the open orders list")
        print(f"CHECK: order {order_id} appears in GET /orders (open)")
    except Exception:
        st.cancel(client, order_id)  # never leave the test order resting
        raise
    st.cancel(client, order_id)
    wait_for("the order to close", lambda: not st.get_order(client, order_id)["open"], 10, interval=1)
    if order_id in [o["id"] for o in st.open_orders(client, exchange)]:
        raise Stop(f"order {order_id} is still in the open orders list after cancel")
    if st.signed_position(client, MARKET) != before:
        raise Stop("position changed during step A")
    print(f"CHECK: order {order_id} is cancelled, gone from open orders, position unchanged. STEP A PASSED")


def step_b(client, exchange):
    print("\n========== STEP B: an order that fills, then closes ==========")
    start_position, start_balance = st.signed_position(client, MARKET), st.balance(client)
    _, ask = read_book(client)
    confirm(f"BUY {SHARES} YES on market {MARKET} @ {ask} (the current ask)")
    buy = st.place_limit(client, exchange, "yes", "buy", SHARES, ask)
    fill_order(client, buy, "buy")
    wait_for(f"position to read {start_position + SHARES:,.0f}",
             lambda: st.signed_position(client, MARKET) == start_position + SHARES, 20)
    print(f"CHECK: position went {start_position:,.0f} -> {start_position + SHARES:,.0f}")

    try:
        sell = sell_back(client, exchange, start_position)
    except Stop as e:
        raise Stop(f"{e}. WARNING: the share bought in step B is still held; your position is "
                   f"{start_position + SHARES:,.0f} but was {start_position:,.0f} before the test")
    print(f"CHECK: position is back to {start_position:,.0f}")
    print(f"\nbought for {buy['totalCost']}, sold for {sell['totalCost']} (canonical side shown above); "
          f"cash {start_balance:,} -> {st.balance(client):,}. STEP B PASSED")


def sell_back(client, exchange, start_position):
    bid, _ = read_book(client)
    confirm(f"SELL {SHARES} YES on market {MARKET} @ {bid} (the current bid)")
    sell = st.place_limit(client, exchange, "yes", "sell", SHARES, bid)
    print(f"\nthe engine placed the sell as: {sell.get('action')} {sell.get('side')} @ {sell.get('price')}")
    fill_order(client, sell, "sell")
    wait_for(f"position to return to {start_position:,.0f}",
             lambda: st.signed_position(client, MARKET) == start_position, 20)
    return sell


def fill_order(client, placed, label):
    """Wait for a placed order to fill completely; cancel it and stop if it does not."""
    order_id, kind = placed["orderId"], st.classify(placed, SHARES)
    print(f"\nCHECK: {label} order {order_id} came back as: {kind}")
    if kind == "noop":
        raise Stop("the order was accepted as a no-op (nothing placed)")
    if kind in ("resting", "partial"):
        try:
            wait_for("a full fill", lambda: not st.get_order(client, order_id)["open"], FILL_TIMEOUT)
        except Stop:
            st.cancel(client, order_id)
            raise
    order = st.get_order(client, order_id)
    fills = client.request("GET", f"/orders/{order_id}/fills")[1]
    if order["quantityFilled"] != SHARES or abs(fills["totalQuantityFilled"]) != SHARES:
        raise Stop(f"order {order_id} filled {order['quantityFilled']}, not {SHARES}")
    print(f"CHECK: order {order_id} filled {SHARES} @ {fills['avgFillPrice']}")
    return order


def step_c(client, exchange):
    print("\n========== STEP C: what a rejected order looks like ==========")
    confirm(f"BUY {SHARES} YES on market {MARKET} @ 0.0025, which is off the tick and should be rejected")
    body = {"idempotencyKey": str(uuid.uuid4()), "exchangeId": exchange, "side": "yes",
            "action": "buy", "quantity": SHARES, "price": 0.0025}
    status, data = client.request("POST", "/orders", body=body, expect=(200, 400))
    if status == 200:  # should not happen; do not leave it on the book
        if data.get("orderId") is not None:
            st.cancel(client, data["orderId"])
        raise Stop("the off-tick order was accepted, which the docs say should not happen")
    print(f"\nCHECK: rejected with {data['error']['code']}: {data['error']['message']}. STEP C PASSED")


def main():
    client = st.Client()
    try:
        exchange = read_state(client)
        if "--preflight" in sys.argv:
            return preflight(client, exchange)
        print("\nThis sends REAL orders of 1 share on market", MARKET, "(limit orders only).")
        step_a(client, exchange)
        step_b(client, exchange)
        step_c(client, exchange)
        print("\nALL STEPS PASSED")
    except Stop as e:
        print(f"\nSTOPPED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUNEXPECTED ERROR, stopping: {type(e).__name__}: {st.scrub(str(e))}")
        print("Check GET /orders?status=open and your positions before doing anything else.")
        sys.exit(1)


if __name__ == "__main__":
    main()
