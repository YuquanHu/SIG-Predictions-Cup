"""Offline tests for the bot's own resting orders in the order book: they are taken out of every depth read, and an order that would
trade against one of them is not sent. Run: python3 -m unittest discover -s tests -t ."""
import unittest
from unittest import mock

import sig
from orders import Order
from patient import PatientBook, meets
from tests.test_sig_bulk import races_with, reply


def order(action, side, price, shares=100, market="2"):
    return Order(action, side, "R", market, shares, price, 0.7, "r", "fast", rest_until=1e12, rest_kind="early" if action == "sell" else "entry")


def read(book, own):
    races = races_with(4)
    for c in races["R"]["contracts"]:
        c["sig_exchange_id"] = f"x{c['market_id']}"
    reply_book = {"exchanges": [{"exchangeId": "x2", **book}]}
    with mock.patch("sig.requests.get", return_value=reply(reply_book)):
        sig.fetch_depth(races, {"2"}, own)
    return races["R"]["contracts"][2]["yes"]


BOOK = {"bids": [{"price": 0.385, "quantity": 1_600}, {"price": 0.38, "quantity": 28_837}],
        "asks": [{"price": 0.39, "quantity": 2_509}, {"price": 0.395, "quantity": 2_600}]}


class Levels(unittest.TestCase):
    def test_where_each_kind_of_order_sits_in_the_yes_book(self):
        self.assertEqual(sig.own_level(order("sell", "no", 0.63)), ("bids", 0.37))
        self.assertEqual(sig.own_level(order("buy", "no", 0.595)), ("asks", 0.405))
        self.assertEqual(sig.own_level(order("buy", "yes", 0.385)), ("bids", 0.385))
        self.assertEqual(sig.own_level(order("sell", "yes", 0.39)), ("asks", 0.39))

    def test_a_book_without_own_orders_is_read_as_before(self):
        q = read(BOOK, [])
        self.assertEqual((q["bid"], q["bid_size"], q["ask"], q["ask_size"]), (0.385, 1_600, 0.39, 2_509))
        self.assertEqual(q["ask_levels"], [(0.39, 2_509), (0.395, 2_600)])

    def test_a_resting_sell_that_is_the_whole_best_level_is_not_the_best_price(self):
        # the bot's own NO sell at 0.615 (8,830 left) is a Yes bid at 0.385: with nothing else there, the next level is the best bid
        book = {**BOOK, "bids": [{"price": 0.385, "quantity": 7_371}, {"price": 0.38, "quantity": 28_837}]}
        q = read(book, [(order("sell", "no", 0.615), 7_371)])
        self.assertEqual((q["bid"], q["bid_size"]), (0.38, 28_837))
        self.assertEqual(q["bid_levels"], [(0.38, 28_837)])

    def test_only_the_bots_share_of_a_shared_level_is_taken_out(self):
        book = {**BOOK, "asks": [{"price": 0.405, "quantity": 2_680}]}
        q = read(book, [(order("buy", "no", 0.595), 1_680)])                  # a NO bid at 0.595 is a Yes ask at 0.405
        self.assertEqual((q["ask"], q["ask_size"]), (0.405, 1_000))

    def test_an_order_on_a_level_the_book_no_longer_has_changes_nothing(self):
        q = read(BOOK, [(order("sell", "no", 0.50), 100)])
        self.assertEqual((q["bid"], q["ask"]), (0.385, 0.39))

    def test_other_markets_orders_are_ignored(self):
        q = read(BOOK, [(order("sell", "no", 0.615, market="3"), 1_600)])
        self.assertEqual(q["bid_size"], 1_600)


class SelfMatches(unittest.TestCase):
    def test_a_buy_at_or_above_its_own_sell_and_a_sell_at_or_below_its_own_buy_meet(self):
        sell, buy = order("sell", "no", 0.615), order("buy", "no", 0.595)
        self.assertTrue(meets(order("buy", "no", 0.615), sell))
        self.assertTrue(meets(order("buy", "no", 0.62), sell))
        self.assertFalse(meets(order("buy", "no", 0.61), sell))
        self.assertTrue(meets(order("sell", "no", 0.595), buy))
        self.assertFalse(meets(order("sell", "no", 0.60), buy))

    def test_another_market_another_side_or_the_same_action_do_not_meet(self):
        sell = order("sell", "no", 0.615)
        self.assertFalse(meets(order("buy", "no", 0.62, market="3"), sell))
        self.assertFalse(meets(order("buy", "yes", 0.62), sell))
        self.assertFalse(meets(order("sell", "no", 0.60), sell))

    def test_the_book_drops_such_an_order_and_says_why(self):
        book = PatientBook()
        book.add(7, order("sell", "no", 0.615))
        kept, notes = book.drop_self_matches([order("buy", "no", 0.615), order("buy", "no", 0.60), order("buy", "no", 0.62, market="3")])
        self.assertEqual([o.price for o in kept], [0.60, 0.62])
        self.assertEqual(len(notes), 1)
        self.assertIn("not sent", notes[0])
        self.assertIn("early exit", notes[0])


if __name__ == "__main__":
    unittest.main()
