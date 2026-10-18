"""Offline tests for the order guards and response handling used by test_trade.py."""
import unittest
from unittest import mock

import config
import sig_trade as st


def placement(order_id=7, open_=True, traded=0):
    return {"orderId": order_id, "open": open_, "quantityTraded": traded}


class Guards(unittest.TestCase):
    def test_accepts_one_share_on_tick(self):
        for price in (0.005, 0.13, 0.995):
            st.check_limit_order(1, price)

    def test_refuses_market_orders(self):
        for price in (None, 0, 1, 0.0):
            with self.assertRaises(st.TradeError):
                st.check_limit_order(1, price)

    def test_refuses_more_than_one_share(self):
        for quantity in (0, 2, 100, 1.0, "1"):
            with self.assertRaises(st.TradeError):
                st.check_limit_order(quantity, 0.13)

    def test_refuses_off_tick_and_out_of_range(self):
        for price in (0.0025, 0.1234, 0.001, 0.999):
            with self.assertRaises(st.TradeError):
                st.check_limit_order(1, price)

    def test_place_limit_sends_nothing_when_a_guard_fails(self):
        client = mock.Mock()
        with self.assertRaises(st.TradeError):
            st.place_limit(client, "1077", "yes", "buy", 2, 0.13)
        client.request.assert_not_called()

    def test_place_limit_sends_a_limit_order_with_an_idempotency_key(self):
        client = mock.Mock()
        client.request.return_value = (200, {})
        st.place_limit(client, "1077", "yes", "buy", 1, 0.13)
        body = client.request.call_args.kwargs["body"]
        self.assertEqual((body["quantity"], body["price"], body["side"], body["action"]), (1, 0.13, "yes", "buy"))
        self.assertTrue(body["idempotencyKey"])


class Classify(unittest.TestCase):
    def test_resting(self):
        self.assertEqual(st.classify(placement(open_=True, traded=0), 1), "resting")

    def test_filled(self):
        self.assertEqual(st.classify(placement(open_=False, traded=1), 1), "filled")

    def test_partial_fill_leaves_a_resting_remainder(self):
        self.assertEqual(st.classify(placement(open_=True, traded=40), 100), "partial")

    def test_noop_when_nothing_was_placed(self):
        self.assertEqual(st.classify(placement(order_id=None, open_=False), 1), "noop")

    def test_closed_without_filling_is_unexpected(self):
        self.assertEqual(st.classify(placement(open_=False, traded=0), 1), "unexpected")


class KeyHidden(unittest.TestCase):
    def test_scrub_removes_the_key(self):
        self.assertNotIn(config.SIG_API_KEY, st.scrub(f"error for key {config.SIG_API_KEY}"))


if __name__ == "__main__":
    unittest.main()
