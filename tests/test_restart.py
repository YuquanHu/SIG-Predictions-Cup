"""Offline tests for restarts: Ctrl+C keeps the resting orders on SIG, the next start follows them again, and the kill switch cancels all.
Run: python3 -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import config
import main
from books import Ledger
from patient import PatientBook
from portfolio import Portfolio
from tests.test_fast import EQUITY, NOW, base, fast_lot
from tests.test_live_trader import RACES, FakeClient
from tests.test_patient_entries import KEY, bid_order
from tests.test_patient_exits import EARLY_ON, FakeTrader, early_listing, plan, position_with_book
from trader import LiveTrader

LOG = mock.patch("patient.logbook.log")


class Saved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "resting.json")

    def test_the_book_comes_back_as_it_was_saved(self):
        book = PatientBook(self.path)
        book.add(7, early_listing(5_000))
        book.add("abc", bid_order())
        book.start_steps([("2", "no")])
        book._entered[("3", "yes")], book._settling[("3", "yes")], book._exhausted = (2, NOW), (0.5, "hold"), {("4", "no")}
        book.save()
        loaded = PatientBook.load(self.path)
        self.assertEqual(loaded.orders, book.orders)
        self.assertEqual((loaded._stepping, loaded._entered, loaded._settling, loaded._exhausted),
                         (book._stepping, book._entered, book._settling, book._exhausted))

    def test_a_book_without_a_path_writes_nothing(self):
        book = PatientBook()
        book.add(7, early_listing(5_000))
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_no_file_is_an_empty_book(self):
        self.assertEqual(PatientBook.load(self.path).orders, {})

    def test_an_unreadable_file_stops_the_start(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        with self.assertRaises(SystemExit):
            PatientBook.load(self.path)


class Adopted(unittest.TestCase):
    """A book loaded after a restart, with the position and the order still there."""
    def setUp(self):
        self.h = position_with_book()
        self.lots = [fast_lot("1", self.h.shares, age_hours=1)]
        self.book = PatientBook()
        self.book.add(7, early_listing(int(self.h.shares)))

    def review(self, book, trader):
        with EARLY_ON, LOG:
            return book.review(trader, Portfolio(EQUITY - self.h.value, [self.h], 0.0), Ledger(self.lots), {"1": base(-0.05)}, NOW + 60, 1)

    def test_the_position_is_not_sold_a_second_time(self):
        with EARLY_ON:
            p = plan(self.h, self.book, lots=self.lots)
        self.assertEqual(p.sells, [])

    def test_an_order_still_open_is_kept(self):
        trader = FakeTrader(open_orders={7: self.book.orders[7].remaining})
        with LOG:
            self.assertEqual(self.book.sync(trader, 1), [])
        self.assertEqual(self.review(self.book, trader), [])
        self.assertEqual(trader.cancelled, [])

    def test_an_order_that_closed_while_the_bot_was_down_is_logged_and_dropped(self):
        trader = FakeTrader(open_orders={}, fills={7: 100})
        with LOG:
            [note] = self.book.sync(trader, 1)
        self.assertIn("closed", note)
        self.assertEqual(self.book.orders, {})

    def test_an_early_exit_is_cancelled_when_the_restart_switched_early_exits_off(self):
        trader = FakeTrader(open_orders={7: 1})
        with mock.patch.object(config, "PATIENT_EXITS", True), mock.patch.object(config, "EARLY_EXITS", False), LOG:
            notes = self.book.review(trader, Portfolio(EQUITY - self.h.value, [self.h], 0.0), Ledger(self.lots), {"1": base(-0.05)}, NOW + 60, 1)
        self.assertEqual(trader.cancelled, [7])
        self.assertIn("early exits are off", notes[0])

    def test_a_bid_is_cancelled_when_the_restart_switched_patient_entries_off(self):
        book, trader = PatientBook(), FakeTrader(open_orders={9: 1})
        book.add(9, bid_order())
        with mock.patch.object(config, "PATIENT_ENTRIES", False), LOG:
            notes = book.review(trader, Portfolio(EQUITY, [], 0.0), Ledger(), {}, NOW + 60, 1)
        self.assertEqual(trader.cancelled, [9])
        self.assertIn("patient entries are off", notes[0])

    def test_an_adopted_bid_still_books_its_fills_as_fast(self):
        book = PatientBook()
        book.add(9, bid_order())
        self.assertEqual(book.take_entry_buys(), {KEY: (0.895, "fast")})


class Trader(unittest.TestCase):
    def test_open_resting_forgets_the_orders_that_closed(self):
        trader = LiveTrader(FakeClient(lambda m, p, b: (200, {})), RACES)
        trader.resting = {1: early_listing(100), 2: early_listing(100)}
        with mock.patch("trader.sig.fetch_open_orders", return_value=[{"id": 2, "quantity": 100, "quantityFilled": 40}]):
            self.assertEqual(trader.open_resting(), {2: 60})
        self.assertEqual(list(trader.resting), [2])


class Start(unittest.TestCase):
    def test_the_last_runs_orders_become_the_resting_orders_of_this_one(self):
        book, trader = PatientBook(), StubTrader()
        book.add(7, early_listing(5_000))
        with mock.patch.object(config, "STRATEGY", "v2"), mock.patch.object(config, "PATIENT_EXITS", True):
            main.follow_resting(trader, book)
        self.assertEqual((list(trader.resting), trader.calls), ([7], []))

    def test_a_run_that_does_not_follow_them_cancels_the_recorded_ones(self):
        book, trader = PatientBook(), StubTrader()
        book.add(7, early_listing(5_000))
        with mock.patch.object(config, "STRATEGY", "v2"), mock.patch.object(config, "PATIENT_EXITS", False), LOG:
            main.follow_resting(trader, book)
        self.assertEqual((trader.resting, trader.calls, book.orders), ({}, ["cancel_resting 7"], {}))


class StubTrader:
    def __init__(self, cancel_everything_ok=True):
        self.resting, self.calls, self.ok = {}, [], cancel_everything_ok

    def has_open_orders(self):
        return True

    def cancel_open(self, loop_no):
        self.calls.append("cancel_open")

    def cancel_everything(self):
        self.calls.append("cancel_everything")
        return self.ok

    def cancel_resting(self, order_id):
        self.calls.append(f"cancel_resting {order_id}")
        return True


class CloseDown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "resting.json")
        self.book = PatientBook(self.path)
        self.book.add(7, early_listing(5_000))
        self.state = SimpleNamespace(patient=self.book)

    def test_ctrl_c_leaves_the_resting_orders_and_keeps_them_on_disk(self):
        trader = StubTrader()
        main.close_down(trader, self.state, "interrupted", 3)
        self.assertEqual(trader.calls, ["cancel_open"])                      # only the short-lived orders of a cut-short loop
        self.assertEqual(list(PatientBook.load(self.path).orders), [7])

    def test_ctrl_c_saves_a_resting_order_placed_just_before_it(self):
        trader = StubTrader()
        trader.resting = {8: early_listing(100)}                             # placed by execute, never handed to the book
        main.close_down(trader, self.state, "interrupted", 3)
        self.assertEqual(sorted(PatientBook.load(self.path).orders), [7, 8])

    def test_the_kill_switch_cancels_every_order_and_forgets_them(self):
        trader = StubTrader()
        main.close_down(trader, self.state, "kill", 3)
        self.assertEqual(trader.calls, ["cancel_everything"])
        self.assertEqual(PatientBook.load(self.path).orders, {})

    def test_a_cancel_all_that_did_not_confirm_keeps_the_ids_for_the_next_start(self):
        main.close_down(StubTrader(cancel_everything_ok=False), self.state, "kill", 3)
        self.assertEqual(list(PatientBook.load(self.path).orders), [7])

    def test_a_single_loop_run_cancels_the_bots_own_orders(self):
        trader = StubTrader()
        with LOG:
            main.close_down(trader, self.state, "end", 3)
        self.assertEqual(trader.calls, ["cancel_open", "cancel_resting 7"])
        self.assertEqual(PatientBook.load(self.path).orders, {})


if __name__ == "__main__":
    unittest.main()
