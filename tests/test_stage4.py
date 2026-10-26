"""Stage 4: v2 start-up lines, the v2 report tables, the fast_part log column and the slow-regime pause.
Run: python3 -m unittest discover -s tests -t ."""
import contextlib
import io
import unittest
from unittest import mock

import bookkeeping
import config
import display
import logbook
from books import Ledger, Lot
from gapmodel import Baseline
from portfolio import Holding, Portfolio
from safety import RunState
from startup import startup_check
from strategy import Opportunity


def output(fn, *args, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*args, **kwargs)
    return out.getvalue()


def opp(market="1", fair=0.985, price=0.90, side="no"):
    return Opportunity("R1", market, "D", side, price, fair, 1e6, "D", 2)


class Startup(unittest.TestCase):
    def check(self, strategy):
        with mock.patch.object(config, "STRATEGY", strategy):
            return output(startup_check, "DRY RUN", {"balance": 42_000.0, "username": "me"}, [], [], RunState(), 115, 2,
                          config.REGIMES["normal"])

    def test_v2_shows_the_books_and_the_fast_rules_instead_of_the_v1_minimum_edge(self):
        text = self.check("v2")
        for fact in ("hold 30% + fast 30%", "position cap 25%", "3c beyond the usual gap", "up to 5 positions", "time stop 24h"):
            self.assertIn(fact, text)
        self.assertNotIn("minimum edge", text)

    def test_v1_still_shows_the_minimum_edge(self):
        self.assertIn("minimum edge", self.check("v1"))


class Report(unittest.TestCase):
    holding = Holding("R1", "1", "no", "D", 1_000, 900.0, 0.90, 1e6, 0.985)
    portfolio = Portfolio(9_100.0, [holding], 0.0)

    def test_v1_report_keeps_the_kelly_columns(self):
        text = output(display.print_report, self.portfolio, [opp()], [], [])
        self.assertIn("kelly", text)
        self.assertNotIn("hold / fast", text)

    def test_v2_report_shows_roi_and_the_split_between_books(self):
        ledger = Ledger([Lot("hold", "1", "no", 700, 0.9, 1.0), Lot("fast", "1", "no", 300, 0.9, 1.0)])
        text = output(display.print_report, self.portfolio, [opp()], [], [], ledger)
        self.assertIn("ROI", text)
        self.assertNotIn("kelly", text)
        self.assertIn("700 / 300", text)

    def test_the_fast_report_with_nothing_to_show_says_none(self):
        self.assertIn("none", output(display.print_fast_report, []))


class Logs(unittest.TestCase):
    def test_the_opportunity_log_has_a_fast_part_column(self):
        self.assertIn("fast_part", logbook.COLUMNS["opportunities"])

    def test_rows_carry_the_fast_part_for_a_classified_market_and_blanks_otherwise(self):
        known = Baseline(-0.05, "no", 0.05, 0.04, True, 192, 0.05, 0.05)
        rows = bookkeeping.opportunity_rows([opp("1"), opp("2")], {"1": known}, 0.03)       # edge 8.5c, 5c usual
        self.assertAlmostEqual(rows[0]["fast_part"], 0.035)
        self.assertEqual((rows[1]["fast_part"], rows[1]["usual_gap"], rows[1]["bucket"]), ("", "", ""))


if __name__ == "__main__":
    unittest.main()
