"""Stage 4: v2 start-up lines, the v2 report tables, the fast_part log column and the slow-regime pause.
Run: python3 -m unittest discover -s tests -t ."""
import contextlib
import io
import unittest
from dataclasses import replace
from unittest import mock

import bookkeeping
import config
import display
import fast
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
        for fact in ("hold 30% + fast 30%", "position cap 25%", "3c beyond the usual gap", "up to 5 positions", f"time stop {config.FAST_MAX_HOURS}h"):
            self.assertIn(fact, text)
        self.assertNotIn("minimum edge", text)

    def test_v1_still_shows_the_minimum_edge(self):
        self.assertIn("minimum edge", self.check("v1"))


class Report(unittest.TestCase):
    holding = Holding("R1", "1", "no", "D", 1_000, 900.0, 0.90, 1e6, 0.985)
    portfolio = Portfolio(9_100.0, [holding], 0.0)

    def test_v1_report_keeps_the_kelly_columns(self):
        text = output(display.print_report, self.portfolio, [], [])
        self.assertNotIn("hold / fast", text)
        self.assertIn("kelly", output(display.print_opportunities, [opp()]))

    def test_v2_report_shows_roi_and_the_split_between_books(self):
        ledger = Ledger([Lot("hold", "1", "no", 700, 0.9, 1.0), Lot("fast", "1", "no", 300, 0.9, 1.0)])
        text = output(display.print_report, self.portfolio, [], [], ledger)
        self.assertIn("700 / 300", text)
        self.assertNotIn("Top opportunities", text)                       # printed last, by print_opportunities
        picks = output(display.print_opportunities, [opp()], True)
        self.assertIn("ROI", picks)
        self.assertNotIn("kelly", picks)

    def test_the_opportunities_are_ranked_by_raw_edge_not_by_the_order_given_or_the_haircut(self):
        small, big, shaky = opp("1", price=0.90), opp("2", price=0.80), opp("3", price=0.70)
        shaky = replace(shaky, uncertainty=0.5)                           # its edge after the haircut is the smallest; the raw edge is still the biggest
        text = output(display.print_opportunities, [small, big, shaky], True)
        self.assertEqual([line.split()[1] for line in text.splitlines()[3:]], ["3", "2", "1"])

    def test_time_since_entry_reads_as_hours_and_minutes(self):
        self.assertEqual([display.elapsed(x) for x in (None, 30, 3599, 8100)], ["-", "0m", "59m", "2h 15m"])

    def test_the_fast_report_shows_the_patient_exit_status_and_its_timer(self):
        text = output(display.print_fast_report, [fast.FastRow("TX-23 YES 245", 0.0125, 0.02, 4000, 0, "900 resting at 0.835", 8100, "patient", 2520.0),
                                                  fast.FastRow("NE-GOV YES 301", 0.02, 0.02, 3000, 0, "cannot rest yet", 600, "patient", None, True),
                                                  fast.FastRow("NH-01 NO 9", 0.02, 0.05, 0, 900, "buy up to 900")])
        self.assertIn("patient exit, 42m left", text)
        self.assertIn("patient exit, waiting", text)
        self.assertEqual(text.count("patient exit"), 2)

    def test_the_fast_report_shows_chance_roi_and_z_like_the_hold_report(self):
        row = fast.FastRow("NH-01 NO 9", 0.02, 0.05, 0, 900, "buy up to 900", fair=0.96, price=0.80)
        text = output(display.print_fast_report, [row, fast.FastRow("NO FAIR 1", 0.02, 0.0, 100, 0, "holds")])
        for column in ("chance", "ROI", "z"):
            self.assertIn(column, text)
        z = Opportunity("R", "9", "D", "no", 0.80, 0.96, None, "D").z
        self.assertRegex(text, rf"NH-01 NO 9 .*96%\s+20\.0%\s+{z:.2f}\s")
        self.assertIn("NO FAIR 1", text)

    def test_the_holdings_table_shows_z_at_the_sell_price(self):
        h = Holding("R", "9", "no", "D", 1_000, 800, 0.80, 1e6, 0.96)
        cells = dict(zip(display.HOLDING_COLUMNS, display.holding_cells(h, "hold")))
        self.assertEqual(cells["z"], f"{Opportunity('R', '9', 'D', 'no', 0.80, 0.96, None, 'D').z:+.2f}")
        self.assertEqual(dict(zip(display.HOLDING_COLUMNS, display.holding_cells(replace(h, fair=None), "hold")))["z"], "-")

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
