"""Offline tests for the election window: the frozen gap baseline, the fast book holding to resolution, and keeping the Mac awake.
Run: python3 -m unittest discover -s tests -t ."""
import unittest
from unittest import mock

import backfill
import config
import main
from books import Ledger, Lot
from gapmodel import GapStore
from orders import Order
from patient import PatientBook
from portfolio import Portfolio
from tests import test_fast, test_freeze
from tests.test_fast import EQUITY, NOW, OVERDUE_HOURS, base, fast_lot, held, opp, run
from tests.test_patient_entries import bid_order
from tests.test_patient_exits import FakeTrader, early_listing, position_with_book

HOURS = 3600
STEP = 900
HOLDING = mock.patch.object(config, "FAST_HOLD_UNTIL", NOW + HOURS)
OVER = mock.patch.object(config, "FAST_HOLD_UNTIL", NOW - HOURS)
LOG = mock.patch("patient.logbook.log")


def shifted_store(freeze):
    """48h of a 1c gap up to `freeze`, then 24h of 10c: the shift of 2 Nov."""
    store = GapStore()
    for i in range(192):
        store.add("1", freeze - i * STEP, 0.01)
    for i in range(1, 97):
        store.add("1", freeze + i * STEP, 0.10)
    return store


class FrozenBaseline(unittest.TestCase):
    FREEZE = NOW - 24 * HOURS

    def test_the_gap_since_the_freeze_does_not_move_the_usual_gap(self):
        store = shifted_store(self.FREEZE)
        self.assertGreater(store.baselines(["1"], NOW)["1"].size, 0.05)                 # live: the shift is half the window
        with mock.patch.object(config, "BASELINE_FROZEN_AT", self.FREEZE):
            self.assertAlmostEqual(store.baselines(["1"], NOW)["1"].size, 0.01)

    def test_pruning_keeps_the_frozen_window(self):
        store = shifted_store(self.FREEZE)
        with mock.patch.object(config, "BASELINE_FROZEN_AT", self.FREEZE):
            store.prune(NOW + 72 * HOURS)
            self.assertAlmostEqual(store.baselines(["1"], NOW + 72 * HOURS)["1"].size, 0.01)
        store.prune(NOW + 72 * HOURS)                                                    # unfrozen: the old window goes
        self.assertIsNone(store.baselines(["1"], NOW + 72 * HOURS)["1"].signed)


    def test_a_frozen_window_short_of_samples_is_back_filled_from_its_start(self):
        store = shifted_store(self.FREEZE)
        with mock.patch.object(config, "BASELINE_FROZEN_AT", self.FREEZE):
            store.prune(NOW + 72 * HOURS)
            self.assertEqual(store.count("1", NOW + 72 * HOURS), 192)
            self.assertIsNone(backfill.fetch_start(store, "1", NOW))                   # enough samples, none missing: nothing to fetch
            thin = GapStore()
            thin.add("1", self.FREEZE, 0.01)
            self.assertEqual(backfill.fetch_start(thin, "1", NOW), self.FREEZE - config.HISTORY_HOURS * HOURS)


class HoldToResolution(unittest.TestCase):
    def test_no_time_stop_and_no_caught_up_exit_inside_the_window(self):
        overdue = held("1", 10_000, fair=0.985, sell=0.90)
        caught_up = held("2", 10_000, fair=0.95, sell=0.90)
        holdings = [overdue, caught_up]
        lots = [fast_lot("1", overdue.shares, age_hours=OVERDUE_HOURS), fast_lot("2", caught_up.shares)]
        baselines = {"1": base(-0.05), "2": base(-0.05)}
        with HOLDING:
            self.assertEqual(run([], baselines, holdings, lots).sells, [])
        with OVER:
            self.assertEqual(len(run([], baselines, holdings, lots).sells), 2)

    def test_a_swap_still_happens(self):
        holdings, lots = test_fast.Swap().full_book([0.945, 0.985, 0.985, 0.985, 0.985])
        with HOLDING:
            plan = run([opp("1", fair=0.995)], test_fast.Swap.baselines, holdings, lots)
        self.assertEqual([(e.view.market_id, e.kind) for e in plan.exits], [("2", "swap")])

    def test_a_negative_edge_still_sells(self):
        with HOLDING:
            p = test_freeze.BothBooks().plan(0.75)
        self.assertEqual([(o.action, o.market_id) for o in p.sells], [("sell", "1")])


class RestingExits(unittest.TestCase):
    def review(self, *orders):
        h = position_with_book()
        book = PatientBook()
        for order_id, order in enumerate(orders, 1):
            book.add(order_id, order)
        trader = FakeTrader(open_orders={i: 1 for i in range(1, len(orders) + 1)})
        with HOLDING, LOG, mock.patch.object(config, "PATIENT_ENTRIES", True):
            notes = book.review(trader, Portfolio(EQUITY - h.value, [h], 0.0), Ledger([fast_lot("1", h.shares)]), {"1": base(-0.05)},
                                NOW + 60, 1)
        return trader.cancelled, notes

    def test_an_early_listing_and_a_patient_exit_are_cancelled(self):
        patient = Order("sell", "no", "R1", "1", 100, 0.91, 0.97, "patient", "fast", rest_until=NOW + 9 * HOURS, rest_kind="patient")
        cancelled, notes = self.review(early_listing(100), patient)
        self.assertEqual(cancelled, [1, 2])
        self.assertTrue(all("holding to resolution" in n for n in notes))

    def test_a_swap_step_and_an_entry_bid_are_kept(self):
        step = Order("sell", "no", "R1", "1", 100, 0.90, 0.97, "step", "fast", rest_until=NOW + 300, rest_kind="step")
        self.assertEqual(self.review(step, bid_order(until=NOW + 600))[0], [])


class KeepAwake(unittest.TestCase):
    def test_caffeinate_follows_the_bot_on_a_mac(self):
        with mock.patch.object(main.sys, "platform", "darwin"), mock.patch("main.subprocess.Popen") as popen:
            self.assertIn("caffeinate", main.keep_awake())
        self.assertEqual(popen.call_args[0][0][:3], ["caffeinate", "-ims", "-w"])

    def test_nothing_elsewhere(self):
        with mock.patch.object(main.sys, "platform", "linux"), mock.patch("main.subprocess.Popen") as popen:
            self.assertIsNone(main.keep_awake())
        popen.assert_not_called()

    def test_a_missing_caffeinate_is_reported_not_fatal(self):
        with mock.patch.object(main.sys, "platform", "darwin"), mock.patch("main.subprocess.Popen", side_effect=OSError("no such file")):
            self.assertIn("could not start", main.keep_awake())


if __name__ == "__main__":
    unittest.main()
