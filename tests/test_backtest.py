"""Offline tests for the v2 backtest: accounting, the simulated market, and a small end-to-end run on made-up history.
Run: python3 -m unittest discover -s tests -t ."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))
import backtest_v2 as bt  # noqa: E402
import config  # noqa: E402
from backtest_data import ContractHistory, History, Series, Traded  # noqa: E402
from orders import Order  # noqa: E402
from races import empty_quote  # noqa: E402

DAY = 86400
T1 = bt.TRADE_START + 2 * DAY      # the day the made-up fair value jumps
CATCH_UP = T1 + 2 * 3600           # and SIG follows two hours later


def order(action, side="yes", book="fast", shares=100, price=0.5, market="1", reason="r"):
    return Order(action, side, "R1", market, shares, price, 0.6, reason, book)


class AccountMath(unittest.TestCase):
    def test_a_round_trip_books_realised_profit_to_the_book_and_closes_the_fast_trade(self):
        a = bt.Account(1_000)
        a.execute([order("buy", price=0.5)], 100, lambda m: ("high", 0.0))
        self.assertAlmostEqual(a.cash, 950)
        a.execute([order("sell", price=0.6, reason="caught up: 0.5c left")], 200, lambda m: ("high", 0.0))
        self.assertAlmostEqual(a.cash, 1_010)
        self.assertAlmostEqual(a.realised["fast"], 10)
        self.assertEqual(a.realised["hold"], 0)
        trade = a.trades[0]
        self.assertEqual((trade.reason, trade.tier, trade.exit_time), ("caught up: 0.5c left", "high", 200))
        self.assertAlmostEqual(trade.pnl, 10)

    def test_a_sale_takes_its_own_book_first_then_the_other(self):
        a = bt.Account(1_000)
        a.execute([order("buy", book="hold", shares=100, price=0.5), order("buy", book="fast", shares=50, price=0.5)], 1, lambda m: ("high", 0.0))
        a.execute([order("sell", book="fast", shares=80, price=0.6)], 2, lambda m: ("high", 0.0))
        self.assertAlmostEqual(a.shares("1", "yes", "fast"), 0)
        self.assertAlmostEqual(a.shares("1", "yes", "hold"), 70)
        self.assertAlmostEqual(a.realised["fast"], 5)     # 50 x 0.1
        self.assertAlmostEqual(a.realised["hold"], 3)     # 30 x 0.1

    def test_marks_split_the_profit_by_book_and_equity_adds_up(self):
        a = bt.Account(1_000)
        a.execute([order("buy", book="hold", shares=100, price=0.5)], 1, lambda m: ("high", 0.0))
        a.marks[("1", "yes")] = 0.55
        value, pnl = a.value_and_pnl()
        self.assertAlmostEqual(value, 55)
        self.assertAlmostEqual(pnl["hold"], 5)
        self.assertAlmostEqual(a.cash + value, 1_000 + sum(pnl.values()))

    def test_a_buy_is_cut_to_the_cash_left(self):
        a = bt.Account(30)
        fills = a.execute([order("buy", shares=100, price=0.5)], 1, lambda m: ("high", 0.0))
        self.assertAlmostEqual(fills[0][1], 60)
        self.assertAlmostEqual(a.cash, 0)


def race():
    def quotes():
        return {"kalshi": empty_quote(), "poly": empty_quote()}
    return {"R1": {"name": "Race", "group": "g", "rules_checked": True,
                   "outcomes": {"D": {"kalshi_id": "KD", "poly_id": "p", "poly_yes_token": "PD", **quotes()},
                                "R": {"kalshi_id": "KR", "poly_id": "q", "poly_yes_token": "PR", **quotes()}},
                   "contracts": [{"market_id": "1", "pays_if": "D", "sig_exchange_id": "x", "title": "d", "yes": empty_quote()},
                                 {"market_id": "2", "pays_if": "R", "sig_exchange_id": "y", "title": "r", "yes": empty_quote()}]}}


def steps(a, b, step=300):
    return list(range(a, b + 1, step))


def made_up_history(sig_catches_up=True):
    """Fair value 0.50 / 0.50 until T1, then 0.60 / 0.40; SIG follows at CATCH_UP. Plenty of volume."""
    times = steps(bt.START, bt.TRADE_START + 5 * DAY)
    fair_d = [0.50 if t < T1 else 0.60 for t in times]
    fair_r = [1 - p for p in fair_d]
    sig_d = [0.50 if t < CATCH_UP or not sig_catches_up else 0.60 for t in times]
    sig_r = [1 - p for p in sig_d]
    volume = 5_000.0
    traded = Traded(times, [volume * (i + 1) for i in range(len(times))])
    return History({"1": ContractHistory(Series(times, sig_d), traded), "2": ContractHistory(Series(times, sig_r), traded)},
                   {"KD": Series(times, fair_d), "KR": Series(times, fair_r)},
                   {"PD": Series(times, fair_d), "PR": Series(times, fair_r)})


class SimulatedMarket(unittest.TestCase):
    def sim(self, history=None):
        s = bt.Sim(race(), history or made_up_history())
        return s

    def test_the_quote_is_the_last_trade_plus_and_minus_half_a_spread_with_a_depth_cap(self):
        s = self.sim()
        t = bt.TRADE_START
        s.set_sig(t)
        y = s.races["R1"]["contracts"][0]["yes"]
        self.assertAlmostEqual(y["bid"], 0.495)
        self.assertAlmostEqual(y["ask"], 0.505)
        hourly = 12 * 5_000.0                                  # twelve 5-minute candles of 5,000
        self.assertAlmostEqual(y["ask_size"], bt.SIZE_SHARE * hourly / 0.5)

    def test_a_last_trade_older_than_the_limit_is_no_quote_and_is_counted(self):
        h = made_up_history()
        short = Series(h.contracts["1"].price.times[:100], h.contracts["1"].price.values[:100])   # data stops early
        h.contracts["1"] = ContractHistory(short, h.contracts["1"].traded)
        s = self.sim(h)
        s.set_sig(bt.TRADE_START + DAY)
        self.assertIsNone(s.races["R1"]["contracts"][0]["yes"]["ask"])
        self.assertEqual(s.result.stale_quotes, 1)

    def test_external_prices_older_than_three_hours_are_dropped(self):
        h = made_up_history()
        h.kalshi["KD"] = Series([bt.START], [0.5])
        s = self.sim(h)
        s.set_external(bt.START + 4 * 3600)
        o = s.races["R1"]["outcomes"]["D"]
        self.assertIsNone(o["kalshi"]["bid"])
        self.assertEqual(o["poly"]["bid"], 0.5)

    def test_volume_is_as_of_the_step_with_no_future_candles(self):
        s = self.sim()
        t = bt.TRADE_START
        v = s.volumes(t).get("1")
        self.assertAlmostEqual(v.total, 5_000.0 * ((t - bt.START) // 300 + 1))
        self.assertLess(v.total, 5_000.0 * len(steps(bt.START, bt.TRADE_START + 5 * DAY)))


class EndToEnd(unittest.TestCase):
    def test_the_fast_book_buys_the_lag_and_sells_when_sig_catches_up(self):
        result = bt.run(race(), made_up_history(), "made up", end=bt.TRADE_START + 5 * DAY)
        closed = [t for t in result.trades if t.exit_time]
        self.assertTrue(closed, "no fast trade closed")
        self.assertTrue(all(T1 <= t.entry_time < CATCH_UP + 3600 for t in closed))
        self.assertTrue(all(t.reason.startswith("caught up") for t in closed))
        self.assertGreater(sum(t.pnl for t in closed), 0)
        self.assertGreater(result.equity[-1][1], bt.START_EQUITY)
        self.assertGreaterEqual(result.first_order, T1)

    def test_the_volume_rules_hold_a_market_back_and_switching_them_off_lets_it_in(self):
        history = made_up_history()
        strict = {"MIN_RECENT_VOLUME": 1e12}                    # nothing has traded that much since yesterday
        on = bt.run(race(), history, "on", strict, end=bt.TRADE_START + 5 * DAY)
        off = bt.run(race(), history, "off", {**strict, **bt.VOLUME_RULES_OFF}, end=bt.TRADE_START + 5 * DAY)
        self.assertEqual(on.orders, 0)
        self.assertGreater(len(on.volume_blocked), 0)
        self.assertGreater(off.orders, 0)
        self.assertEqual(len(off.volume_blocked), 0)

    def test_overrides_are_put_back_after_a_run(self):
        before = (config.FAST_MIN_EDGE, config.LIQUIDITY_LOW)
        bt.run(race(), made_up_history(), "x", {"FAST_MIN_EDGE": 0.5, "LIQUIDITY_LOW": 0}, end=bt.TRADE_START + DAY)
        self.assertEqual((config.FAST_MIN_EDGE, config.LIQUIDITY_LOW), before)


if __name__ == "__main__":
    unittest.main()
