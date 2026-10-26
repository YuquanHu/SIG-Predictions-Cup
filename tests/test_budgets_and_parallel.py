"""Offline tests for: the write guard, the order cap, and parallel fetching. No network."""
import contextlib
import io
import threading
import time
import unittest
from unittest import mock

import budget
import config
import feeds
import orders
import sig_trade
from cooldown import Cooldown
from parallel import run_all
from planner import make_plan
from portfolio import Portfolio
from races import empty_quote
from strategy import Fair
from trading import limit_orders


class WriteBudget(unittest.TestCase):
    def setUp(self):
        budget.WRITES._times.clear()
        budget.WRITES.take_count()

    def test_the_write_over_the_limit_waits(self):
        clock, slept = [1000.0], []
        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds
        with mock.patch.object(config, "SIG_WRITES_PER_MINUTE", 2), mock.patch("budget.time.time", lambda: clock[0]), \
                mock.patch("budget.time.sleep", sleep):
            budget.WRITES.acquire()
            budget.WRITES.acquire()
            self.assertEqual(slept, [])
            budget.WRITES.acquire()
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 60.05, places=1)

    def test_a_dry_run_reports_the_wait_without_sleeping(self):
        with mock.patch.object(config, "SIG_WRITES_PER_MINUTE", 1), mock.patch("budget.time.sleep") as sleep:
            self.assertEqual(budget.WRITES.acquire(block=False), 0)
            self.assertGreater(budget.WRITES.acquire(block=False), 50)
        sleep.assert_not_called()

    def test_dry_run_orders_and_cancels_are_counted(self):
        order = orders.Order("buy", "no", "R", "1", 10, 0.5, 0.6, "t")
        with mock.patch.object(config, "DRY_RUN", True), contextlib.redirect_stdout(io.StringIO()):  # whatever config.py says
            orders.place_order(order, timeout=3)
            orders.cancel_all()
        self.assertEqual(budget.WRITES.take_count(), 2)

    def test_real_requests_wait_for_the_budget_but_reads_do_not_use_it(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {}
        with mock.patch("sig_trade.requests.request", return_value=resp):
            sig_trade.Client().request("GET", "/orders")
            self.assertEqual(budget.WRITES.take_count(), 0)
            sig_trade.Client().request("POST", "/orders", body={})
            sig_trade.Client().request("DELETE", "/orders/1")
        self.assertEqual(budget.WRITES.take_count(), 2)


class OrderCap(unittest.TestCase):
    def test_sells_go_first_then_buys_up_to_the_limit(self):
        sells, buys = ["s1", "s2"], ["b1", "b2", "b3", "b4"]
        self.assertEqual(limit_orders(sells, buys, 4), (["s1", "s2"], ["b1", "b2"], ["b3", "b4"]))

    def test_everything_fits_under_the_limit(self):
        self.assertEqual(limit_orders(["s"], ["b"], 6), (["s"], ["b"], []))

    def test_more_sells_than_the_limit_drop_the_extra_sells_and_all_buys(self):
        self.assertEqual(limit_orders(["s1", "s2", "s3"], ["b1"], 2), (["s1", "s2"], [], ["s3", "b1"]))

    def test_plan_keeps_at_most_the_cap_and_says_what_it_skipped(self):
        contracts, outcomes = [], {}
        for i in range(4):
            code = f"O{i}"
            contracts.append({"market_id": str(i), "pays_if": code, "sig_exchange_id": str(i), "title": None,
                              "yes": dict(empty_quote(), bid=0.40, ask=0.45, bid_size=1e6, ask_size=1e6, updated=1.0)})
            outcomes[code] = {"kalshi_id": code, "poly_id": None, "poly_yes_token": None,
                              "kalshi": empty_quote(), "poly": empty_quote()}
        races = {f"R{i}": {"name": "r", "group": "Senate", "contracts": [contracts[i]], "outcomes": {f"O{i}": outcomes[f"O{i}"]}}
                 for i in range(4)}
        fair = {(f"R{i}", f"O{i}"): Fair(0.70, "t", 2) for i in range(4)}
        with mock.patch.object(config, "MAX_ORDERS_PER_LOOP", 2):
            plan = make_plan(races, fair, Portfolio(1e6, [], 0.0), Cooldown(0))
        self.assertEqual(len(plan.orders), 2)
        self.assertEqual(len(plan.skipped), 2)
        self.assertEqual(sum("skipped" in n for n in plan.notes), 2)


class Parallel(unittest.TestCase):
    def test_results_come_back_in_order_and_calls_overlap(self):
        started = time.time()
        results = run_all([lambda i=i: (time.sleep(0.2), i)[1] for i in range(5)])
        self.assertEqual(results, [0, 1, 2, 3, 4])
        self.assertLess(time.time() - started, 0.6)  # 5 x 0.2 s one after another would be 1.0 s

    def test_other_calls_finish_then_the_first_error_is_raised(self):
        done = []
        def boom():
            raise ValueError("x")
        with self.assertRaises(ValueError):
            run_all([boom, lambda: (time.sleep(0.1), done.append(1))])
        self.assertEqual(done, [1])

    def test_fetch_all_runs_the_sources_together_and_survives_one_crashing(self):
        names = []
        def slow(name):
            def fetch(races):
                names.append(threading.current_thread().name)
                time.sleep(0.2)
            return fetch
        def crash(races):
            raise RuntimeError("down")
        sources = [mock.Mock(NAME="A", fetch_prices=slow("A")), mock.Mock(NAME="B", fetch_prices=crash),
                   mock.Mock(NAME="C", fetch_prices=slow("C"))]
        started = time.time()
        with mock.patch.object(feeds, "SOURCES", sources):
            feeds.fetch_all({})
        self.assertLess(time.time() - started, 0.35)
        self.assertEqual(len(set(names)), 2)  # two sources ran on different threads at the same time


if __name__ == "__main__":
    unittest.main()
