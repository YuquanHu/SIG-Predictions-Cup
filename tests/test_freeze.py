"""Offline tests for the top-up freeze: a bet held TOPUP_FREEZE_DROP below its entry is not topped up by either book for
TOPUP_FREEZE_HOURS after the last loop it was that far down, and nothing is sold for it. Run: python3 -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest
from dataclasses import replace

import config
import fast
import hold
from books import Freeze, Ledger, Lot
from cooldown import Cooldown
from liquidity import Volume, VolumeStore
from patient import PatientBook
from planner import make_plan_v2
from portfolio import Portfolio
from races import empty_quote
from strategy import Fair, Opportunity
from tests.test_fast import EQUITY, NOW, base, held, opp, races_for, volumes_for
from tests.test_patient_entries import ENTRIES_ON, KEY, bid_order

HOURS = 3600
FREEZE = config.TOPUP_FREEZE_HOURS * HOURS


def ledger_at(entry=0.90, book="hold", market="1"):
    return Ledger([Lot(book, market, "no", 10_000, entry, NOW - HOURS)])


def falls(ledger, sell, now=NOW, market="1"):
    return ledger.freeze_falls([held(market, 10_000 * sell, sell=sell)], now)


class Falls(unittest.TestCase):
    def test_ten_cents_under_the_entry_freezes_the_bet(self):
        ledger = ledger_at(0.90)
        [note] = falls(ledger, 0.80)
        self.assertEqual(list(ledger.frozen), [("R1", "D")])
        self.assertEqual(ledger.frozen[("R1", "D")].until, NOW + FREEZE)
        self.assertIn("10.0c below its entry 0.900", note)

    def test_a_smaller_fall_does_not(self):
        ledger = ledger_at(0.90)
        self.assertEqual(falls(ledger, 0.801), [])
        self.assertEqual(ledger.frozen, {})

    def test_the_entry_is_the_average_of_every_lot_of_either_book(self):
        ledger = Ledger([Lot("hold", "1", "no", 5_000, 0.95, NOW), Lot("fast", "1", "no", 5_000, 0.85, NOW)])
        falls(ledger, 0.80)
        self.assertIn(("R1", "D"), ledger.frozen)

    def test_both_plans_of_one_loop_report_a_new_freeze_and_later_loops_do_not(self):
        ledger = ledger_at(0.90)
        self.assertEqual(len(falls(ledger, 0.80)), 1)
        self.assertEqual(len(falls(ledger, 0.80)), 1)                       # the plan after the depth read, same `now`
        self.assertEqual(falls(ledger, 0.80, NOW + 60), [])

    def test_it_lasts_from_the_last_loop_down_and_then_ends(self):
        ledger = ledger_at(0.90)
        falls(ledger, 0.80)
        falls(ledger, 0.79, NOW + 2 * HOURS)                                 # still down: the clock starts again
        falls(ledger, 0.88, NOW + 3 * HOURS)                                 # recovered: the freeze runs on
        self.assertEqual(ledger.frozen[("R1", "D")].until, NOW + 2 * HOURS + FREEZE)
        falls(ledger, 0.88, NOW + 2 * HOURS + FREEZE)
        self.assertEqual(ledger.frozen, {})

    def test_a_holding_without_a_sell_price_or_lots_is_ignored(self):
        ledger = ledger_at(0.90)
        self.assertEqual(ledger.freeze_falls([replace(held("1", 8_000, sell=0.80), sell_price=None)], NOW), [])
        self.assertEqual(Ledger().freeze_falls([held("1", 8_000, sell=0.50)], NOW), [])


class Saved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ledger.json")

    def test_a_freeze_survives_a_restart(self):
        ledger = ledger_at(0.90)
        falls(ledger, 0.80)
        ledger.save(self.path)
        self.assertEqual(Ledger.load(self.path).frozen, ledger.frozen)

    def test_a_ledger_saved_before_freezes_loads_without_any(self):
        with open(self.path, "w") as f:
            f.write('{"lots": [], "picks": {}}')
        self.assertEqual(Ledger.load(self.path).frozen, {})


def frozen_ledger(lots=()):
    return Ledger(list(lots), frozen={("R1", "D"): Freeze(NOW, NOW + FREEZE, "frozen: test")})


class HoldBook(unittest.TestCase):
    def test_a_frozen_pick_stays_the_pick_and_buys_nothing(self):
        rows = hold.hold_wants({"sure bet": [opp("1")]}, {}, Portfolio(EQUITY, [], 0.0), frozen_ledger())
        want, status = rows[-1]
        self.assertEqual((want.want, status.pick.market_id, status.note), (0.0, "1", "frozen: test"))

    def test_another_bet_still_buys(self):
        rows = hold.hold_wants({"sure bet": [opp("2")]}, {}, Portfolio(EQUITY, [], 0.0), frozen_ledger())
        self.assertGreater(rows[-1][0].want, 0)


class FastBook(unittest.TestCase):
    def run_plan(self, ledger, candidates, holdings=(), book=None):
        portfolio = Portfolio(EQUITY - sum(h.value for h in holdings), list(holdings), 0.0)
        return fast.fast_plan(candidates, {"1": base(-0.05)}, races_for("1"), portfolio, ledger, Cooldown(0), volumes_for("1"), NOW, book)

    def test_a_frozen_fast_position_is_not_topped_up(self):
        h = held("1", 2_000, sell=0.80)
        lots = [Lot("fast", "1", "no", h.shares, 0.90, NOW - HOURS)]
        self.assertEqual(len(self.run_plan(Ledger(list(lots)), [opp("1")], [h]).wants), 1)
        self.assertEqual(self.run_plan(frozen_ledger(lots), [opp("1")], [h]).wants, [])

    def test_the_equivalent_market_is_blocked_with_the_reason(self):
        yes_dem = Opportunity("R1", "2", "D", "yes", 0.90, 0.985, 1e6, "D", 2)
        why = fast.entry_blocker(yes_dem, 0.05, 0.01, Volume(10e6, 50_000, NOW), frozen_ledger(), Cooldown(0), set())
        self.assertEqual(why, "frozen: test")

    def test_a_resting_entry_bid_on_a_frozen_bet_is_cancelled(self):
        book = PatientBook()
        book.add(9, bid_order())
        with ENTRIES_ON:
            p = self.run_plan(frozen_ledger(), [opp("1", size=10)], book=book)
        self.assertEqual(p.stale_entries, {KEY: "no longer a fast entry"})


class BothBooks(unittest.TestCase):
    """One structural No market, held 10c under its entry: SIG Yes 0.19/0.20, so No sells at 0.80 and costs 0.81."""
    races = {"R1": {"outcomes": {"D": {}, "R": {}}, "rules_checked": False,
                    "contracts": [{"market_id": "1", "pays_if": "D", "sig_exchange_id": "x", "title": "t",
                                   "yes": {**empty_quote(), "bid": 0.19, "ask": 0.20, "bid_size": 1e6, "ask_size": 1e6}}]}}

    def plan(self, fair_no):
        h = held("1", 8_000, fair=fair_no, sell=0.80, wins="R")                # a No on a contract that pays if D wins when R does
        fair = {("R1", "D"): Fair(1 - fair_no, "t", 2), ("R1", "R"): Fair(fair_no, "t", 2)}
        ledger = Ledger([Lot("hold", "1", "no", h.shares, 0.90, NOW - HOURS, bucket="sure bet")])
        return make_plan_v2(self.races, fair, Portfolio(EQUITY - h.value, [h], 0.0), Cooldown(0), {"1": base(-0.05)}, ledger,
                            VolumeStore(), NOW)

    def test_neither_book_buys_and_the_plan_says_why(self):
        p = self.plan(0.99)
        self.assertEqual((p.buys, p.sells), ([], []))
        self.assertTrue(any("frozen" in n for n in p.notes))

    def test_a_negative_edge_still_sells(self):
        p = self.plan(0.75)
        self.assertEqual([(o.action, o.market_id) for o in p.sells], [("sell", "1")])


if __name__ == "__main__":
    unittest.main()
