"""Offline tests for patient entries in the hold book: a hold buy on a spread wider than one tick bids one tick above the best bid, for
PATIENT_ENTRY_MINUTES (shorter, or none, on a big edge), then buys at the ask; its fills are booked to the hold book. Run: python3 -m unittest discover -s tests -t ."""
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import hold
import trading
from books import Freeze, Ledger
from cooldown import Cooldown
from liquidity import VolumeStore
from orders import Order
from patient import PatientBook
from planner import make_plan_v2
from portfolio import Portfolio
from races import empty_quote
from strategy import Fair
from tests.test_hold import EQUITY, NOW, opp, structural
from tests.test_patient_entries import plan as fast_plan_with

ENTRIES_ON = mock.patch.multiple(config, PATIENT_EXITS=True, PATIENT_ENTRIES=True)
KEY = ("1", "no")
SPREAD = {"1": 0.01}


def bid(book="hold", price=0.905, shares=6_000):
    return Order("buy", "no", "R1", "1", shares, price, 0.99, "entry", book, rest_until=NOW + 600, rest_kind="entry")


def resting(*orders):
    patient = PatientBook()
    for order_id, order in enumerate(orders, 9):
        patient.add(order_id, order)
    return patient


def rows_for(c, patient, ledger=None):
    return hold.hold_wants({"sure bet": [c]}, {}, Portfolio(EQUITY, [], 0.0), ledger or Ledger(), patient=patient)


def bids_for(c, patient=None, taken=(), spread=0.01):
    patient = patient or PatientBook()
    with ENTRIES_ON:
        rows = hold.hold_bids(rows_for(c, patient), {"1": spread}, patient, taken, NOW)
    return next((w, s) for w, s in rows if w)


class Bids(unittest.TestCase):
    def test_a_buy_on_a_wide_spread_bids_one_tick_above_the_best_bid_and_says_so(self):
        c = opp("1", fair=0.99, price=0.91)                                   # NO ask 0.91, bid 0.90
        w, status = bids_for(c)
        self.assertEqual((w.opp.price, w.rest_until, w.book), (0.905, NOW + config.PATIENT_ENTRY_MINUTES * 60, "hold"))
        self.assertIn("patient entry: bid 0.905 (ask 0.910)", status.note)
        [order], _ = trading.size_buys([w], 1e6)
        self.assertEqual((order.price, order.rest_kind, order.resting, order.book), (0.905, "entry", True, "hold"))

    def test_the_wait_follows_the_edge_in_the_same_bands_as_the_fast_part(self):
        def wait(edge):
            w, _ = bids_for(opp("1", fair=0.99, price=0.99 - edge))
            return None if w.rest_until is None else (w.rest_until - NOW) / 60
        short, longest = config.PATIENT_ENTRY_SHORT_PART, config.PATIENT_ENTRY_MAX_PART
        self.assertEqual([wait(e) for e in (short - 0.01, short + 0.01, longest - 0.005, longest + 0.01)],
                         [config.PATIENT_ENTRY_MINUTES, config.PATIENT_ENTRY_SHORT_MINUTES, config.PATIENT_ENTRY_SHORT_MINUTES, None])

    def test_a_one_tick_spread_buys_at_the_ask(self):
        c = opp("1", fair=0.99, price=0.91)
        with ENTRIES_ON:
            [(w, _)] = [r for r in hold.hold_bids(rows_for(c, PatientBook()), {"1": 0.005}, PatientBook(), (), NOW) if r[0]]
        self.assertEqual((w.opp.price, w.rest_until), (0.91, None))

    def test_with_the_switch_off_it_buys_at_the_ask(self):
        c = opp("1", fair=0.99, price=0.91)
        with mock.patch.multiple(config, PATIENT_EXITS=True, PATIENT_ENTRIES=False):
            [(w, _)] = [r for r in hold.hold_bids(rows_for(c, PatientBook()), SPREAD, PatientBook(), (), NOW) if r[0]]
        self.assertIsNone(w.rest_until)

    def test_after_a_patient_entry_ended_the_market_buys_at_the_ask(self):
        patient = PatientBook()
        patient._entered[KEY] = (1, NOW)
        self.assertIsNone(bids_for(opp("1", fair=0.99, price=0.91), patient)[0].rest_until)

    def test_a_bid_is_not_made_where_a_fast_bid_rests_or_is_planned(self):
        c = opp("1", fair=0.99, price=0.91)
        self.assertIsNone(bids_for(c, resting(bid("fast")))[0].rest_until)
        self.assertIsNone(bids_for(c, taken={KEY})[0].rest_until)


class Rebids(unittest.TestCase):
    def after(self, ended, spread=config.PATIENT_REBID_SPREAD + 0.005, edge=0.08):
        patient = PatientBook()
        if ended:
            patient._entered[KEY] = (ended, NOW)
        return bids_for(opp("1", fair=0.99, price=0.99 - edge), patient, spread=spread)[0].rest_until is not None

    def test_a_wide_spread_is_bid_again_until_the_limit_then_buys_at_the_ask(self):
        self.assertEqual([self.after(n) for n in range(config.PATIENT_MAX_BIDS + 1)], [True] * config.PATIENT_MAX_BIDS + [False])

    def test_a_spread_no_wider_than_the_rebid_spread_buys_at_the_ask_after_a_bid(self):
        self.assertFalse(self.after(1, spread=config.PATIENT_REBID_SPREAD))
        self.assertTrue(self.after(0, spread=config.PATIENT_REBID_SPREAD))

    def test_an_edge_over_the_patient_limit_is_never_bid_again(self):
        self.assertFalse(self.after(1, edge=config.PATIENT_ENTRY_MAX_PART + 0.01))


class WhileItRests(unittest.TestCase):
    def test_a_pick_with_a_hold_bid_resting_gets_no_second_buy_and_the_bid_counts_against_the_budget(self):
        patient = resting(bid())
        rows = rows_for(opp("1", fair=0.99, price=0.91), patient)
        [(w, status)] = [r for r in rows if r[0]]
        self.assertEqual(w.want, 0.0)
        self.assertIn("patient entry: 6,000 bid at 0.905", status.note)
        self.assertAlmostEqual(patient.entry_value("hold"), 6_000 * 0.905)
        self.assertEqual(patient.entry_value("fast"), 0.0)

    def test_a_fast_bid_in_the_market_does_not_stop_the_hold_buy(self):
        [(w, _)] = [r for r in rows_for(opp("1", fair=0.99, price=0.91), resting(bid("fast"))) if r[0]]
        self.assertGreater(w.want, 0)

    def test_resting_hold_bids_use_up_the_hold_budget(self):
        other = Order("buy", "no", "R2", "2", int(config.HOLD_BOOK_PCT * EQUITY / 0.9) + 10, 0.9, 0.99, "entry", "hold", rest_until=NOW + 600, rest_kind="entry")
        [(w, status)] = [r for r in rows_for(opp("1", fair=0.99, price=0.91), resting(other)) if r[0]]
        self.assertEqual(w.want, 0.0)
        self.assertIn("hold book budget", status.note)

    def test_a_hold_bid_does_not_stop_the_fast_book_buying_in_the_same_market(self):
        [w] = fast_plan_with(resting(bid("hold"))).wants
        self.assertIsNone(w.rest_until)                                              # one bid per market: it buys at the ask

    def test_entries_are_listed_per_book(self):
        patient = resting(bid("hold"))
        self.assertEqual((set(patient.entries("hold")), set(patient.entries("fast")), set(patient.entries())), ({KEY}, set(), {KEY}))


class Cancelled(unittest.TestCase):
    def test_a_bid_is_cancelled_when_its_bet_is_no_longer_a_pick(self):
        self.assertEqual(hold.stale_bids({}, Ledger(), resting(bid())), {KEY: "no longer a hold pick"})

    def test_a_bid_is_cancelled_when_the_bet_is_frozen(self):
        c = opp("1", fair=0.99, price=0.91)
        ledger = Ledger(frozen={(c.race, c.wins_when): Freeze(NOW + 3600, 0.0, "x")})
        self.assertEqual(hold.stale_bids({"sure bet": [c]}, ledger, resting(bid())), {KEY: "frozen"})

    def test_a_bid_is_cancelled_when_the_edge_grows_too_big_to_wait(self):
        c = opp("1", fair=0.99, price=0.99 - config.PATIENT_ENTRY_MAX_PART - 0.01)
        [why] = hold.stale_bids({"sure bet": [c]}, Ledger(), resting(bid())).values()
        self.assertIn("buying at the ask", why)

    def test_a_pick_with_a_bid_keeps_it_and_a_fast_bid_is_left_alone(self):
        c = opp("1", fair=0.99, price=0.91)
        self.assertEqual(hold.stale_bids({"sure bet": [c]}, Ledger(), resting(bid())), {})
        self.assertEqual(hold.stale_bids({}, Ledger(), resting(bid("fast"))), {})


class Booked(unittest.TestCase):
    def test_new_shares_in_a_market_with_a_hold_entry_are_a_hold_lot_at_the_bid_in_its_bucket(self):
        ledger = Ledger()
        ledger.picks = {"sure bet": [("1", "no")]}
        notes = ledger.reconcile([{"market_id": "1", "side": "no", "shares": 1_500, "avg_price": 0.9}], NOW, {KEY: (0.905, "hold")})
        [lot] = ledger.lots
        self.assertEqual((lot.book, lot.shares, lot.entry_price, lot.grandfathered, lot.bucket), ("hold", 1_500, 0.905, False, "sure bet"))
        self.assertIn("booked to the hold book", notes[0])

    def test_the_book_comes_with_the_buys_whether_the_bid_rests_or_ended(self):
        patient = resting(bid("hold"))
        self.assertEqual(patient.take_entry_buys(), {KEY: (0.905, "hold")})
        patient._ended(next(iter(patient.orders.values())), NOW)
        patient.orders.clear()
        self.assertEqual(patient.take_entry_buys(), {KEY: (0.905, "hold")})
        self.assertEqual(patient.take_entry_buys(), {})

    def test_a_book_file_from_before_hold_entries_loads_its_settling_as_fast(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "resting.json")
            with open(path, "w") as f:
                json.dump({"orders": [], "stepping": [], "exhausted": [], "entered": [], "settling": [["3", "yes", 0.5]]}, f)
            self.assertEqual(PatientBook.load(path)._settling, {("3", "yes"): (0.5, "fast")})


class InThePlan(unittest.TestCase):
    def setUp(self):
        contract = {"market_id": "1", "pays_if": "D", "sig_exchange_id": "x", "title": "t",
                    "yes": {**empty_quote(), "bid": 0.09, "ask": 0.10, "bid_size": 1e6, "ask_size": 1e6}}
        self.races = {"R1": {"outcomes": {"D": {}, "R": {}}, "rules_checked": False, "contracts": [contract]}}
        self.fair = {("R1", "D"): Fair(0.01, "t", 2), ("R1", "R"): Fair(0.99, "t", 2)}

    def plan(self, patient, ledger=None):
        with ENTRIES_ON:
            return make_plan_v2(self.races, self.fair, Portfolio(EQUITY, [], 0.0), Cooldown(0), {"1": structural("no")}, ledger or Ledger(),
                                VolumeStore(), NOW, patient=patient)

    def test_the_hold_buy_rests_a_bid_and_the_report_row_says_so(self):
        plan = self.plan(PatientBook())
        [order] = plan.buys
        self.assertEqual((order.book, order.price, order.resting), ("hold", 0.905, True))
        self.assertIn("patient entry: bid 0.905", plan.buckets[-1].note)

    def test_a_resting_bid_on_a_bet_that_is_no_longer_a_pick_is_cancelled_through_the_plan(self):
        self.fair[("R1", "D")], self.fair[("R1", "R")] = Fair(0.20, "t", 2), Fair(0.80, "t", 2)   # No is worth 0.80: the edge is gone
        plan = self.plan(resting(bid()))
        self.assertEqual(plan.stale_entries, {KEY: "no longer a hold pick"})


if __name__ == "__main__":
    unittest.main()
