"""LiveTrader against a fake SIG client: no test here ever touches the real API."""
import csv
import os
import tempfile
import unittest
from unittest import mock

import config
import sig_trade
from orders import Order
from trader import LiveTrader, filled_from_positions, signed_by_market

RACES = {"R": {"name": "R", "group": "Senate", "outcomes": {},
               "contracts": [{"market_id": "274", "pays_if": "D", "sig_exchange_id": "x274"},
                             {"market_id": "300", "pays_if": "D", "sig_exchange_id": "x300"}]}}


def buy(market="274", side="no", shares=100, price=0.94):
    return Order("buy", side, "R", market, shares, price, 0.99, "test")


def sell(market="274", side="no", shares=100, price=0.90):
    return Order("sell", side, "R", market, shares, price, 0.80, "test")


def position(market, side, shares):
    return {"market_id": market, "side": side, "shares": shares}


class FakeClient:
    """Answers requests from a router function and remembers every call."""
    def __init__(self, router):
        self.router, self.calls = router, []

    def request(self, method, path, params=None, body=None, expect=(200,)):
        self.calls.append((method, path, body))
        result = self.router(method, path, body)
        if isinstance(result, Exception):
            raise result
        return result

    def sent(self, method, path_start):
        return [c for c in self.calls if c[0] == method and c[1].startswith(path_start)]


def placed(order_id=1, open_=True, traded=0):
    return 200, {"orderId": order_id, "open": open_, "quantityTraded": traded}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(config, "LOG_DIR", self.tmp.name), mock.patch("trader.time.sleep"),
                   mock.patch.object(LiveTrader, "_wait")]
        self.sleep, self.wait = None, None
        started = [p.start() for p in patches]
        self.wait = started[2]
        self.addCleanup(lambda: [p.stop() for p in patches])
        self.addCleanup(self.tmp.cleanup)

    def events(self):
        path = os.path.join(self.tmp.name, "orders.csv")
        if not os.path.exists(path):
            return []
        with open(path, newline="") as f:
            return list(csv.DictReader(f))

    def run_trader(self, router, orders, before, after, max_value=10_000, order_says=None):
        client = FakeClient(router)
        trader = LiveTrader(client, RACES)
        with mock.patch("trader.sig.fetch_positions", return_value=after), mock.patch("trader.sig.fetch_open_orders", return_value=[]):
            problems = trader.execute(orders, timeout=3, positions_before=before, loop_no=1, max_order_value=max_value)
        return client, trader, problems


class Fills(unittest.TestCase):
    def test_buying_no_shows_as_a_more_negative_position(self):
        before, after = signed_by_market([position("274", "no", 1000)]), signed_by_market([position("274", "no", 1040)])
        self.assertEqual(filled_from_positions(buy(side="no"), before, after), 40)

    def test_buying_yes_against_a_no_position_nets_it_down(self):
        before, after = signed_by_market([position("274", "no", 1000)]), signed_by_market([position("274", "no", 990)])
        self.assertEqual(filled_from_positions(buy(side="yes"), before, after), 10)

    def test_selling_no_shows_as_a_smaller_no_position(self):
        before, after = signed_by_market([position("274", "no", 1000)]), signed_by_market([position("274", "no", 900)])
        self.assertEqual(filled_from_positions(sell(side="no"), before, after), 100)

    def test_a_new_position_comes_from_nothing(self):
        self.assertEqual(filled_from_positions(buy(), {}, signed_by_market([position("274", "no", 25)])), 25)

    def test_never_negative(self):
        self.assertEqual(filled_from_positions(buy(), {"274": -50}, {"274": -40}), 0)


class Placing(Base):
    def test_unfilled_order_waits_then_is_cancelled_and_logged(self):
        def router(method, path, body):
            return placed(7) if method == "POST" else (200, {"quantityFilled": 0}) if method == "GET" else (200, {})
        client, trader, problems = self.run_trader(router, [buy()], before=[], after=[])
        self.assertEqual(problems, 0)
        self.wait.assert_called_once_with(3)
        self.assertEqual(len(client.sent("DELETE", "/orders/7")), 1)
        self.assertEqual([e["event"] for e in self.events()], ["placed", "cancelled"])
        self.assertFalse(trader.has_open_orders())

    def test_order_that_fills_at_once_is_not_waited_for_or_cancelled(self):
        def router(method, path, body):
            return placed(8, open_=False, traded=100) if method == "POST" else (200, {"quantityFilled": 100})
        client, _, problems = self.run_trader(router, [buy()], before=[], after=[position("274", "no", 100)])
        self.wait.assert_not_called()
        self.assertEqual(client.sent("DELETE", "/orders"), [])
        self.assertEqual([e["event"] for e in self.events()], ["placed", "filled"])
        self.assertEqual(problems, 0)

    def test_partial_fill_cancels_the_rest_and_reads_the_amount_from_positions(self):
        def router(method, path, body):
            return placed(9) if method == "POST" else (200, {"quantityFilled": 30}) if method == "GET" else (200, {})
        client, _, _ = self.run_trader(router, [buy(shares=100)], before=[], after=[position("274", "no", 30)])
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["placed", "partial_fill", "cancelled"])
        self.assertEqual(events[1]["filled"], "30")
        self.assertIn("70", events[2]["detail"])
        self.assertEqual(len(client.sent("DELETE", "/orders/9")), 1)

    def test_positions_that_disagree_with_the_order_are_flagged_loudly(self):
        def router(method, path, body):
            return placed(6) if method == "POST" else (200, {"quantityFilled": 60}) if method == "GET" else (200, {})
        with mock.patch("builtins.print") as shown:
            self.run_trader(router, [buy(shares=100)], before=[], after=[])      # positions show nothing, the order says 60
        events = self.events()
        self.assertIn("positions show 0 but the order reports 60", events[-1]["detail"])
        self.assertTrue(any("WARNING" in str(c) for c in shown.call_args_list))
        with open(os.path.join(self.tmp.name, "errors.csv"), newline="") as f:
            self.assertIn("order reports 60", list(csv.DictReader(f))[0]["error"])

    def test_a_rejected_order_is_logged_counts_as_a_problem_and_is_not_cancelled(self):
        def router(method, path, body):
            return 400, {"error": {"code": "VALIDATION_ERROR", "message": "insufficient funds"}}
        client, _, problems = self.run_trader(router, [buy()], before=[], after=[])
        self.assertEqual(problems, 1)
        self.assertEqual([e["event"] for e in self.events()], ["rejected"])
        self.assertIn("insufficient funds", self.events()[0]["detail"])
        self.assertEqual(client.sent("DELETE", "/orders"), [])

    def test_a_noop_acceptance_is_a_rejection(self):
        client, _, problems = self.run_trader(lambda m, p, b: (200, {"orderId": None, "open": False, "quantityTraded": 0}),
                                              [buy()], before=[], after=[])
        self.assertEqual((problems, self.events()[0]["event"]), (1, "rejected"))

    def test_a_503_is_retried_with_the_same_idempotency_key(self):
        answers = iter([(503, {"error": {"code": "SERVICE_UNAVAILABLE", "message": "x"}}), placed(5, open_=False, traded=100)])
        def router(method, path, body):
            return next(answers) if method == "POST" else (200, {"quantityFilled": 100})
        client, _, problems = self.run_trader(router, [buy()], before=[], after=[position("274", "no", 100)])
        posts = client.sent("POST", "/orders")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][2]["idempotencyKey"], posts[1][2]["idempotencyKey"])
        self.assertEqual(problems, 0)

    def test_an_order_sig_never_confirms_is_flagged_and_matching_resting_orders_are_cancelled(self):
        def router(method, path, body):
            if method == "POST":
                return sig_trade.TradeError("no response")
            return 200, {}
        client = FakeClient(router)
        trader = LiveTrader(client, RACES)
        resting = [{"id": 99, "side": "no", "action": "buy", "priceLimit": 0.94}, {"id": 98, "side": "yes", "action": "buy", "priceLimit": 0.5}]
        with mock.patch("trader.sig.fetch_positions", return_value=[]), mock.patch("trader.sig.fetch_open_orders", return_value=resting):
            problems = trader.execute([buy()], 3, [], 1, 10_000)
        self.assertEqual(problems, 1)
        self.assertEqual([e["event"] for e in self.events()][0], "unconfirmed")
        self.assertEqual([c[1] for c in client.sent("DELETE", "/orders")], ["/orders/99"])  # only the exact match

    def test_a_cancel_that_fails_is_a_problem_and_is_retried_next_loop(self):
        state = {"fail": True}
        def router(method, path, body):
            if method == "POST":
                return placed(4)
            if method == "DELETE":
                return (500, {}) if state["fail"] else (200, {})
            return 200, {"quantityFilled": 0}
        client, trader, problems = self.run_trader(router, [buy()], before=[], after=[])
        self.assertEqual(problems, 1)
        self.assertTrue(trader.has_open_orders())
        self.assertIn("cancel_failed", [e["event"] for e in self.events()])
        state["fail"] = False
        trader.cancel_open(loop_no=2)
        self.assertFalse(trader.has_open_orders())


class Guards(Base):
    def test_a_buy_over_the_single_order_cap_is_refused_before_anything_is_sent(self):
        client, _, problems = self.run_trader(lambda m, p, b: placed(), [buy(shares=2000, price=0.94)], [], [], max_value=840)
        self.assertEqual(client.calls, [])
        self.assertEqual(problems, 1)
        self.assertIn("single-order cap", self.events()[0]["detail"])

    def test_a_sell_is_not_subject_to_the_cap(self):
        client, _, _ = self.run_trader(lambda m, p, b: placed(3, open_=False, traded=5000) if m == "POST" else (200, {"quantityFilled": 5000}),
                                       [sell(shares=5000, price=0.9)], [position("274", "no", 5000)], [], max_value=840)
        self.assertEqual(len(client.sent("POST", "/orders")), 1)

    def test_fractional_shares_and_unknown_markets_are_refused(self):
        client, _, problems = self.run_trader(lambda m, p, b: placed(), [buy(shares=10.5), buy(market="999")], [], [])
        self.assertEqual((client.calls, problems), ([], 2))

    def test_only_limit_orders_on_the_price_tick_are_ever_sent(self):
        client, _, _ = self.run_trader(lambda m, p, b: placed(2, open_=False, traded=100) if m == "POST" else (200, {"quantityFilled": 100}),
                                       [buy(price=0.9400000000000001)], [], [position("274", "no", 100)])
        body = client.sent("POST", "/orders")[0][2]
        self.assertEqual(body["price"], 0.94)
        self.assertEqual((body["action"], body["side"], body["quantity"], body["exchangeId"]), ("buy", "no", 100, "x274"))

    def test_the_single_share_test_limit_is_not_applied_to_live_orders(self):
        with self.assertRaises(sig_trade.TradeError):
            sig_trade.check_limit_order(2, 0.5)                   # test_trade keeps its 1-share limit
        sig_trade.check_limit_order(5000, 0.5, max_quantity=10**9)  # the live trader passes its own


class Cancelling(Base):
    def test_cancel_open_cancels_only_the_bots_own_orders_never_cancel_all(self):
        client = FakeClient(lambda m, p, b: (200, {}))
        trader = LiveTrader(client, RACES)
        trader.open_ids = {11: buy(), 12: sell()}
        trader.cancel_open(loop_no=2)
        self.assertEqual(sorted(c[1] for c in client.sent("DELETE", "/orders")), ["/orders/11", "/orders/12"])
        self.assertEqual(client.sent("POST", "/orders/cancel-all"), [])

    def test_cancel_everything_posts_cancel_all_and_retries_a_503(self):
        answers = iter([(503, {}), (200, {"cancelled": 4})])
        client = FakeClient(lambda m, p, b: next(answers))
        trader = LiveTrader(client, RACES)
        self.assertTrue(trader.cancel_everything())
        self.assertEqual(len(client.sent("POST", "/orders/cancel-all")), 2)
        self.assertEqual(self.events()[-1]["event"], "cancel_all")


if __name__ == "__main__":
    unittest.main()
