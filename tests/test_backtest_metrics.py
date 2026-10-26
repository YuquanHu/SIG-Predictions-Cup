"""Offline tests for the backtest's performance numbers. Run: python3 -m unittest discover -s tests -t ."""
import os
import sys
import unittest
from math import sqrt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))
import backtest_metrics as m  # noqa: E402

D = m.DAY


def curve(*values, step=D):
    """Equity points one `step` apart starting at day 10 00:00 UTC."""
    return [(10 * D + i * step, v) for i, v in enumerate(values)]


class Returns(unittest.TestCase):
    def test_daily_returns_use_the_last_point_of_each_utc_day(self):
        equity = [(10 * D, 100.0), (10 * D + 3600, 101.0), (11 * D + 100, 102.0), (11 * D + 7200, 110.0)]
        self.assertEqual([round(r, 6) for _, r in m.daily_returns(equity)], [round(101 / 100 - 1, 6), round(110 / 101 - 1, 6)])

    def test_volatility_and_sharpe_by_hand(self):
        returns = [0.01, -0.01, 0.03]                    # mean 1%, deviations 0, -2%, +2%: variance 0.0004 / 2 -> sd 2%
        self.assertAlmostEqual(m.volatility(returns), 0.02)
        self.assertAlmostEqual(m.sharpe(returns), 0.5)
        self.assertAlmostEqual(m.sharpe(returns, annualise=True), 0.5 * sqrt(365))
        self.assertAlmostEqual(m.volatility(returns, annualise=True), 0.02 * sqrt(365))

    def test_a_flat_curve_has_no_sharpe_and_one_return_has_no_volatility(self):
        self.assertIsNone(m.sharpe([0.0, 0.0, 0.0]))
        self.assertIsNone(m.volatility([0.02]))


class Drawdown(unittest.TestCase):
    def test_peak_trough_and_recovery(self):
        d = m.max_drawdown(curve(100, 120, 90, 100, 130, 125))
        self.assertEqual((d["value"], round(d["pct"], 6)), (30, 0.25))
        self.assertEqual((d["peak_time"], d["trough_time"], d["recovery_time"]), (11 * D, 12 * D, 14 * D))

    def test_not_recovered(self):
        d = m.max_drawdown(curve(100, 120, 80, 90))
        self.assertEqual((d["value"], d["recovery_time"]), (40, None))

    def test_a_rising_curve_has_no_drawdown(self):
        d = m.max_drawdown(curve(100, 101, 105))
        self.assertEqual((d["value"], d["pct"], d["recovery_time"]), (0.0, 0.0, None))

    def test_the_deepest_of_two_falls_wins(self):
        d = m.max_drawdown(curve(100, 110, 100, 120, 90, 95))
        self.assertEqual((d["value"], d["peak_time"]), (30, 13 * D))


class Summary(unittest.TestCase):
    def test_summary_numbers(self):
        s = m.summarize_metrics(curve(100_000, 101_000, 100_500, 102_000))
        self.assertEqual((s["gain"], s["days"]), (2_000, 3))
        self.assertAlmostEqual(s["return_pct"], 0.02)
        self.assertAlmostEqual(s["days_up"], 2 / 3)
        self.assertAlmostEqual(s["best_day"], 1_500 / 100_500)
        self.assertEqual(s["max_drawdown"]["value"], 500)


if __name__ == "__main__":
    unittest.main()
