"""Order execution. DryRunTrader only prints. LiveTrader sends real limit orders to SIG, cancels whatever has not
filled once the order timeout is up, and reads what filled from the SIG positions.

Both have the same four methods, so main.py does not care which one it has:
    cancel_open(loop_no)                    cancel orders left over from an earlier loop
    execute(orders, timeout, positions_before, loop_no, max_order_value) -> Execution (problems and what filled)
    has_open_orders()                       True while the bot may have resting orders
    cancel_everything()                     account-wide cancel (kill switch and shutdown)
"""
import time
import uuid
from dataclasses import dataclass

import logbook
import orders
import sig
import sig_trade as st
from safety import stop_file_exists

ANY_STATUS = tuple(range(100, 600))
UNCONFIRMED_STATUSES = (None, 500, 502, 503)  # None: no response at all. The order may or may not exist.
MAX_SHARES = 2_147_483_647                     # SIG's limit on one order's quantity


@dataclass
class Execution:
    problems: int  # orders rejected, never confirmed, or that could not be cancelled
    fills: list    # [(Order, shares filled)] for every order that filled at least one share; the ledger books them


class DryRunTrader:
    mode = "DRY RUN"

    def __init__(self):
        self._placed = False

    def cancel_open(self, loop_no):
        orders.cancel_all()

    def execute(self, to_place, timeout, positions_before, loop_no, max_order_value):
        for o in to_place:
            orders.place_order(o, timeout)
            logbook.log("orders", loop_no, [logbook.order_event("would_place", o)])
        self._placed = bool(to_place)
        return Execution(0, [])

    def has_open_orders(self):
        return self._placed

    def cancel_everything(self):
        orders.cancel_all()


@dataclass
class Placement:
    order: orders.Order
    status: str = "placed"      # placed | rejected | unconfirmed
    order_id: int | None = None
    traded: float = 0.0         # filled at once, from the placement response
    open: bool = False          # still resting after placement
    detail: str = ""
    cancel_failed: bool = False


def signed_by_market(positions):
    """{market_id: shares}, positive for Yes and negative for No (SIG's convention)."""
    return {p["market_id"]: p["shares"] if p["side"] == "yes" else -p["shares"] for p in positions}


def filled_from_positions(order, before, after):
    """Shares of `order` that filled, from the change in the position in its market. Handles netting: buying Yes
    against a No position shows up as the No position shrinking."""
    change = after.get(order.market_id, 0) - before.get(order.market_id, 0)
    held_change = change if order.side == "yes" else -change  # change in shares of the ordered side
    return max(0, held_change if order.action == "buy" else -held_change)


def _error_text(data):
    error = data.get("error") if isinstance(data, dict) else None
    return f"{error.get('code')}: {error.get('message')}" if isinstance(error, dict) else str(data)[:200]


class LiveTrader:
    mode = "LIVE"

    def __init__(self, client, races):
        self.client = client
        self.races = races
        self.open_ids = {}  # order id -> Order: the bot's orders that may still be resting

    # ---- placing -------------------------------------------------------------------------------------------

    def _exchange_id(self, market_id):
        return next((c["sig_exchange_id"] for race in self.races.values() for c in race["contracts"]
                     if c["market_id"] == market_id), None)

    def _place(self, o, max_order_value):
        exchange_id = self._exchange_id(o.market_id)
        price = st.on_tick(o.price)
        if exchange_id is None:
            return Placement(o, "rejected", detail="no SIG exchange id for this market")
        if o.shares != int(o.shares):
            return Placement(o, "rejected", detail=f"{o.shares} is not a whole number of shares")
        try:
            st.check_limit_order(int(o.shares), price, max_quantity=MAX_SHARES)
        except st.TradeError as e:
            return Placement(o, "rejected", detail=str(e))
        if o.action == "buy" and int(o.shares) * price > max_order_value * 1.0001:
            return Placement(o, "rejected", detail=f"value {int(o.shares) * price:,.0f} is over the single-order cap "
                                                   f"{max_order_value:,.0f}")
        body = {"idempotencyKey": str(uuid.uuid4()), "exchangeId": exchange_id, "side": o.side, "action": o.action,
                "quantity": int(o.shares), "price": price}
        for attempt in (1, 2):  # a retry reuses the idempotency key, so it can never place the order twice
            try:
                status, data = self.client.request("POST", "/orders", body=body, expect=ANY_STATUS)
            except st.TradeError as e:
                status, data = None, {"error": {"code": "NO_RESPONSE", "message": str(e)}}
            if status == 200:
                if data.get("orderId") is None:
                    return Placement(o, "rejected", detail="SIG accepted it as a no-op: nothing was placed")
                return Placement(o, "placed", order_id=data["orderId"], traded=data.get("quantityTraded") or 0,
                                 open=bool(data.get("open")))
            code = (data.get("error") or {}).get("code") if isinstance(data, dict) else None
            if status in UNCONFIRMED_STATUSES or code == "REQUEST_IN_FLIGHT":
                time.sleep(1)
                continue
            return Placement(o, "rejected", detail=f"HTTP {status} {_error_text(data)}")
        return Placement(o, "unconfirmed", detail=f"no confirmation from SIG: {_error_text(data)}")

    # ---- cancelling ------------------------------------------------------------------------------------------

    def _cancel(self, order_id):
        """DELETE one order. True when it is now closed (cancelled, or already filled/closed: HTTP 409)."""
        try:
            status, data = self.client.request("DELETE", f"/orders/{order_id}", expect=ANY_STATUS)
        except st.TradeError:
            return False
        return status in (200, 409)

    def cancel_open(self, loop_no):
        """Cancel the bot's own orders left over from an earlier loop (never other orders on the account)."""
        for order_id, o in list(self.open_ids.items()):
            ok = self._cancel(order_id)
            if ok:
                del self.open_ids[order_id]
            event = "cancelled" if ok else "cancel_failed"
            logbook.log("orders", loop_no, [logbook.order_event(event, o, order_id, detail="left over from earlier")])
            print(f"  {event}: order {order_id} ({o.race} {o.side.upper()} {o.market_id}) left over from an earlier loop")

    def cancel_everything(self):
        """Cancel EVERY open order on the account, including ones placed by hand. Used by the kill switch and shutdown."""
        for attempt in range(5):
            try:
                status, data = self.client.request("POST", "/orders/cancel-all", body={}, expect=ANY_STATUS)
            except st.TradeError:
                status, data = None, {}
            if status == 200:
                self.open_ids.clear()
                print(f"  cancelled all open orders ({data.get('cancelled')})")
                logbook.log("orders", 0, [logbook.order_event("cancel_all", detail=f"cancelled {data.get('cancelled')}")])
                return True
            time.sleep(1)  # SIG says to retry a 503 until it returns 200
        print("  WARNING: cancel-all did not confirm. Check your open orders on SIG now.")
        return False

    def _reconcile(self, unconfirmed, loop_no):
        """An order SIG never confirmed may still exist. Cancel resting orders on its exchange that match it exactly."""
        for p in unconfirmed:
            exchange_id = self._exchange_id(p.order.market_id)
            try:
                resting = sig.fetch_open_orders(exchange_id)
            except Exception:
                continue
            price = st.on_tick(p.order.price)
            for r in resting:
                if (r["id"] not in self.open_ids and r["side"] == p.order.side and r["action"] == p.order.action
                        and r["priceLimit"] == price):
                    self._cancel(r["id"])
                    logbook.log("orders", loop_no, [logbook.order_event(
                        "cancelled", p.order, r["id"], detail="found while checking an unconfirmed order")])

    # ---- reading what filled -----------------------------------------------------------------------------------

    def _order_fill(self, p):
        """Filled quantity according to the order itself (a cross-check on the positions)."""
        try:
            status, data = self.client.request("GET", f"/orders/{p.order_id}", expect=ANY_STATUS)
        except st.TradeError:
            return None
        if status != 200:
            return None
        return data.get("quantityFilled") if data.get("quantityFilled") is not None else p.traded

    def _read_fills(self, placed, before):
        """Filled shares per placement, from the SIG positions (re-read a few times if the order says otherwise)."""
        order_says = [self._order_fill(p) if p.order_id else None for p in placed]
        shared = {p.order.market_id for p in placed if sum(q.order.market_id == p.order.market_id for q in placed) > 1}
        fills = []
        for attempt in range(3):
            after = signed_by_market(sig.fetch_positions())
            fills = [filled_from_positions(p.order, before, after) for p in placed]
            if all(says is None or says == fill for says, fill in zip(order_says, fills)):
                break
            time.sleep(1)
        for i, p in enumerate(placed):  # two orders in one market cannot be told apart by the position alone
            if p.order.market_id in shared and order_says[i] is not None:
                fills[i] = order_says[i]
        return fills, order_says

    # ---- one loop's orders ---------------------------------------------------------------------------------------

    def _wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not stop_file_exists():
            time.sleep(min(1.0, max(0.0, end - time.time())))

    def execute(self, to_place, timeout, positions_before, loop_no, max_order_value):
        placements = [self._place(o, max_order_value) for o in to_place]
        problems = 0
        for p in placements:
            if p.status == "placed":
                if p.open:  # one that filled at once is already closed and needs no cancel
                    self.open_ids[p.order_id] = p.order
                event, detail = "placed", f"{p.traded:,.0f} filled at once" if p.traded else ""
            else:
                problems += 1
                event, detail = p.status if p.status == "unconfirmed" else "rejected", p.detail
            print(f"  {event}: {p.order.action.upper()} {p.order.side.upper()} {p.order.market_id} ({p.order.race}) "
                  f"x{p.order.shares:,.0f} @ {p.order.price:.3f}" + (f"  | {detail}" if detail else ""))
            logbook.log("orders", loop_no, [logbook.order_event(event, p.order, p.order_id or "", detail=detail)])

        resting = [p for p in placements if p.status == "placed" and p.open]
        if resting:
            print(f"  waiting {timeout}s for fills...")
            self._wait(timeout)
        for p in resting:  # cancel the rest of every order that did not fill completely
            if self._cancel(p.order_id):
                del self.open_ids[p.order_id]
            else:
                p.cancel_failed = True  # counted as a problem when its outcome is logged
        unconfirmed = [p for p in placements if p.status == "unconfirmed"]
        self._reconcile(unconfirmed, loop_no)

        tracked = [p for p in placements if p.status in ("placed", "unconfirmed")]
        fills = []
        if tracked:
            fills, order_says = self._read_fills(tracked, signed_by_market(positions_before))
            for p, filled, says in zip(tracked, fills, order_says):
                problems += self._log_outcome(p, filled, says, loop_no)
        return Execution(problems, [(p.order, filled) for p, filled in zip(tracked, fills) if filled > 0])

    def _log_outcome(self, p, filled, order_says, loop_no):
        o = p.order
        mismatch = order_says is not None and order_says != filled
        note = f"positions show {filled:,.0f} but the order reports {order_says:,.0f}" if mismatch else ""
        rows = []
        if filled >= o.shares:
            rows.append(logbook.order_event("filled", o, p.order_id or "", filled, note))
        else:
            if filled > 0:
                rows.append(logbook.order_event("partial_fill", o, p.order_id or "", filled, note))
            if p.cancel_failed:
                rows.append(logbook.order_event("cancel_failed", o, p.order_id or "", filled,
                                                "; ".join(x for x in ("still resting; will retry", note) if x)))
            elif p.status == "placed":
                rows.append(logbook.order_event("cancelled", o, p.order_id or "", filled,
                                                "; ".join(x for x in (f"cancelled the unfilled {o.shares - filled:,.0f}", note) if x)))
        logbook.log("orders", loop_no, rows)
        for r in rows:
            print(f"  {r['event']}: order {p.order_id or '?'} ({o.race} {o.side.upper()} {o.market_id}) "
                  f"filled {filled:,.0f} of {o.shares:,.0f}" + (f"  | {r['detail']}" if r["detail"] else ""))
        if mismatch:  # the positions may simply lag; the next loop re-reads them, but a person should see it
            print(f"  WARNING: order {p.order_id} ({o.race} {o.side.upper()} {o.market_id}): {note}")
            logbook.log("errors", loop_no, [{"where": "fills", "error": f"order {p.order_id} {o.race} {o.market_id}: {note}"}])
        return 1 if p.cancel_failed else 0

    def has_open_orders(self):
        return bool(self.open_ids)
