"""The risk rules you asked me to confirm, as tests: caps, equity-based sizing, the daily-loss kill switch,
the single-order cap, settings.json, and the start-up check."""
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

import config
import main
from orders import Order
from portfolio import Holding, Portfolio
from safety import RunState, check_loss, check_stop, load_day_start
from startup import startup_check
from strategy import Opportunity
from cooldown import Cooldown
from trading import cap_order_sizes, compute_wants, sell_decisions


def holding(race, market, value, sell=0.80, fair=0.90, wins="D"):
    return Holding(race, market, "no", wins, value / sell, value, sell, 1e6, fair)


def opp(race="A", market="1", price=0.5, fair=0.9, wins="D"):
    return Opportunity(race, market, "D", "no", price, fair, 1e6, wins, sources=2)


class MaxInvested(unittest.TestCase):
    def test_settings_json_is_valid_whatever_limits_it_holds(self):
        settings = config._load_settings(config.SETTINGS_FILE)  # stops with an error on an unknown key or an out-of-range value
        self.assertLessEqual(settings["hold_book_pct"] + settings["fast_book_pct"], settings["max_invested"])

    def test_over_the_cap_means_no_new_buys_and_no_forced_sale(self):
        portfolio = Portfolio(30_000.0, [holding("A", "1", 35_000), holding("B", "2", 35_000)], 0.0)  # 70% invested
        self.assertEqual(portfolio.investable, 0)
        self.assertEqual(sell_decisions(portfolio, [], Cooldown(0)), [])      # nothing is sold just to get under the cap

    def test_only_a_better_bet_or_negative_edge_sells_a_position_never_the_cap_itself(self):
        worse = holding("A", "1", 35_000, sell=0.80, fair=0.84)    # +5% expected return
        better = holding("B", "2", 35_000, sell=0.80, fair=0.96)   # +20%
        portfolio = Portfolio(30_000.0, [worse, better], 0.0)      # over the cap: no investable cash
        nothing_new = compute_wants([], portfolio)
        self.assertEqual(sell_decisions(portfolio, nothing_new, Cooldown(0)), [])
        decisions = sell_decisions(portfolio, compute_wants([opp("C", "3", price=0.70, fair=0.80)], portfolio), Cooldown(0))
        self.assertEqual([h.market_id for h, _ in decisions], ["1"])   # only the worst, only for a better bet
        broken = holding("A", "1", 35_000, sell=0.80, fair=0.75)       # fair fell below the sell price
        self.assertEqual([h.market_id for h, _ in sell_decisions(Portfolio(30_000.0, [broken], 0.0), [], Cooldown(0))], ["1"])

    def test_existing_positions_and_outside_positions_count_toward_the_cap(self):
        portfolio = Portfolio(40_000.0, [holding("A", "1", 20_000)], 10_000.0)   # equity 70k, invested 30k
        self.assertAlmostEqual(portfolio.bankroll, 70_000)
        self.assertAlmostEqual(portfolio.investable, 0.60 * 70_000 - 30_000)

    def test_the_per_race_cap_counts_what_is_already_held_in_that_race_on_either_side(self):
        held = [holding("A", "1", 20_000, wins="D"), holding("A", "2", 5_000, wins="R")]   # 25k in race A of 100k equity
        wants = compute_wants([opp("A", "3", wins="D")], Portfolio(75_000.0, held, 0.0))
        self.assertAlmostEqual(wants[0].want, 0.30 * 100_000 - 25_000, places=0)


class KellyUsesTotalEquity(unittest.TestCase):
    def test_target_is_half_kelly_times_cash_plus_positions(self):
        portfolio = Portfolio(50_000.0, [holding("Z", "9", 50_000)], 0.0)    # equity 100k, cash only 50k
        o = opp("A", "1", price=0.5, fair=0.6)                                # kelly 0.2, half 0.1
        wants = compute_wants([o], portfolio)
        self.assertAlmostEqual(wants[0].target, o.half_kelly * 100_000)
        self.assertNotAlmostEqual(wants[0].target, o.half_kelly * 50_000)


class DailyLossKillSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.object(config, "DAY_START_FILE", os.path.join(self.tmp.name, "day.json"))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_total_equity_is_cash_plus_every_position_including_ones_outside_races_json(self):
        self.assertEqual(Portfolio(1_000.0, [holding("A", "1", 2_000)], 500.0).bankroll, 3_500)

    def test_trips_only_when_equity_is_more_than_10_percent_below_the_start_of_the_day(self):
        state = RunState()
        self.assertIsNone(check_loss(state, 100_000))      # the first reading becomes the baseline
        self.assertIsNone(check_loss(state, 90_000))       # exactly 10% down is not "more than 10%"
        self.assertIn("down", check_loss(state, 89_999))

    def test_a_restart_keeps_the_start_of_day_baseline(self):
        check_loss(RunState(), 100_000)
        restarted = RunState()
        self.assertTrue(load_day_start(restarted))
        self.assertIn("down", check_loss(restarted, 80_000))   # would be a fresh baseline if it had reset

    def test_yesterdays_baseline_is_ignored(self):
        with open(config.DAY_START_FILE, "w") as f:
            json.dump({"date": (date.today() - timedelta(days=1)).isoformat(), "equity": 500_000}, f)
        state = RunState()
        self.assertFalse(load_day_start(state))
        self.assertIsNone(check_loss(state, 100_000))

    def test_five_errors_in_a_row_stop_the_bot_and_a_good_loop_resets_the_count(self):
        state = RunState()
        for i in range(config.MAX_CONSECUTIVE_ERRORS - 1):
            state.errors_in_row += 1
            self.assertIsNone(check_stop(state))
        state.errors_in_row += 1
        self.assertIn("errors in a row", check_stop(state))

    def test_rejected_orders_count_as_a_failed_loop(self):
        state = RunState()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "LOG_DIR", d), \
                mock.patch.object(main, "run_loop", side_effect=main.OrderProblems("1 order(s) were rejected")), \
                contextlib.redirect_stdout(io.StringIO()):
            main.guarded_loop(1, {}, state, config.REGIMES["normal"], trader=None)
            with open(os.path.join(d, "errors.csv"), newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(state.errors_in_row, 1)
        self.assertIn("rejected", rows[0]["error"])
        with mock.patch.object(main, "run_loop", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            main.guarded_loop(2, {}, state, config.REGIMES["normal"], trader=None)
        self.assertEqual(state.errors_in_row, 0)


class SingleOrderCap(unittest.TestCase):
    def order(self, action="buy", shares=2000, price=0.94):
        return Order(action, "no", "R", "274", shares, price, 0.99, "t")

    def test_cap_is_2_percent_of_total_equity(self):
        self.assertEqual(config.ORDER_CAP_PCT, 0.02)

    def test_a_large_buy_is_cut_to_the_cap(self):
        kept, notes = cap_order_sizes([self.order()], equity=42_000)       # cap 840 -> 893 shares at 0.94
        self.assertEqual(kept[0].shares, 893)
        self.assertLessEqual(kept[0].value, 840)
        self.assertIn("cut", notes[0])

    def test_a_buy_that_would_shrink_below_the_minimum_is_dropped(self):
        kept, notes = cap_order_sizes([self.order()], equity=400)          # cap 8 < MIN_ORDER_VALUE
        self.assertEqual((kept, "dropped" in notes[0]), ([], True))

    def test_sells_and_small_buys_pass_unchanged(self):
        orders = [self.order("sell", 30_000, 0.90), self.order("buy", 100, 0.94)]
        self.assertEqual(cap_order_sizes(orders, equity=42_000)[0], orders)


class SettingsFile(unittest.TestCase):
    def load(self, content):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                f.write(content if isinstance(content, str) else json.dumps(content))
            return config._load_settings(path)

    good = {"max_invested": 0.6, "max_per_race": 0.3, "max_position_pct": 0.25, "hold_book_pct": 0.3, "fast_book_pct": 0.3, "max_order_pct_of_equity": 0.02,
            "max_daily_loss": 0.1, "max_consecutive_errors": 5}

    def test_a_valid_file_loads(self):
        self.assertEqual(self.load(self.good)["max_invested"], 0.6)

    def test_bad_files_stop_the_bot_with_a_clear_message(self):
        for bad in ({**self.good, "max_invested": 1.5}, {**self.good, "max_invested": "0.6"}, {**self.good, "max_invested": True},
                    {**self.good, "mystery": 1}, {k: v for k, v in self.good.items() if k != "max_daily_loss"}, "not json"):
            with self.assertRaises(SystemExit):
                self.load(bad)


class StartupCheck(unittest.TestCase):
    account = {"balance": 42_000.0, "username": "me"}
    positions = [{"shares": 1000, "current_price": 0.9}]

    def run_check(self, mode, answers):
        asked = []
        def ask(prompt):
            asked.append(prompt)
            reply = next(answers)
            if isinstance(reply, Exception):
                raise reply
            return reply
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            startup_check(mode, self.account, self.positions, [], RunState(), 115, 2, config.REGIMES["normal"], ask=ask)
        return out.getvalue(), asked

    def test_dry_run_prints_the_facts_and_does_not_ask_for_anything(self):
        text, asked = self.run_check("DRY RUN", iter([]))
        self.assertEqual(asked, [])
        for fact in ("DRY RUN", "42,000.00", "42,900.00", "60%", "2.0%", "of total equity = 858.00", "10%", "5 loops in a row"):
            self.assertIn(fact, text)

    def test_live_continues_only_when_you_type_LIVE(self):
        text, asked = self.run_check("LIVE", iter(["LIVE"]))
        self.assertIn("REAL ORDERS", text)
        self.assertEqual(len(asked), 1)
        for wrong in ("live", "yes", "", "LIVEE", "y"):
            with self.assertRaises(SystemExit):
                self.run_check("LIVE", iter([wrong]))
        with self.assertRaises(SystemExit):
            self.run_check("LIVE", iter([EOFError()]))


if __name__ == "__main__":
    unittest.main()
