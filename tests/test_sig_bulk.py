"""Offline tests for SIG bulk reads, the read budget, and two-pass planning. No network."""
import unittest
from unittest import mock

import budget
import config
import sig
from planner import depth_targets, make_plan
from portfolio import Portfolio
from races import drain_warnings, empty_quote
from strategy import Fair


def races_with(n_contracts):
    contracts = [{"market_id": str(i), "pays_if": "D" if i % 2 == 0 else "R", "sig_exchange_id": None,
                  "title": None, "yes": empty_quote()} for i in range(n_contracts)]
    return {"R": {"name": "R", "group": "Senate", "contracts": contracts,
                  "outcomes": {"D": {"kalshi_id": "K", "poly_id": None, "poly_yes_token": None,
                                     "kalshi": empty_quote(), "poly": empty_quote()},
                               "R": {"kalshi_id": "K2", "poly_id": None, "poly_yes_token": None,
                                     "kalshi": empty_quote(), "poly": empty_quote()}}}}


def reply(json_body, status=200, headers=None):
    r = mock.Mock(status_code=status, ok=status < 400, headers=headers or {})
    r.json.return_value = json_body
    return r


def markets_reply(url, params, headers, timeout):
    ids = params["ids"].split(",")
    return reply({"data": [{"id": i, "title": f"m{i}", "exchanges": [{"id": f"x{i}", "option": "YES"}]} for i in ids]})


def prices_reply(url, params, headers, timeout):
    return reply({"data": [{"exchangeId": x, "bestBid": 0.4, "bestAsk": 0.45} for x in params["ids"].split(",")],
                  "missingIds": []})


class BulkReads(unittest.TestCase):
    def setUp(self):
        budget.READS._times.clear()
        sig.take_read_count()
        drain_warnings()

    def test_setup_and_prices_use_one_read_per_100_markets(self):
        races = races_with(184)
        with mock.patch("sig.requests.get", side_effect=markets_reply) as get:
            sig.setup(races)
        self.assertEqual([len(c.kwargs["params"]["ids"].split(",")) for c in get.call_args_list], [100, 84])
        with mock.patch("sig.requests.get", side_effect=prices_reply) as get:
            sig.fetch_prices(races)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(sig.take_read_count(), 4)
        q = races["R"]["contracts"][0]["yes"]
        self.assertEqual((q["bid"], q["ask"], q["bid_size"], q["ask_size"]), (0.4, 0.45, None, None))

    def test_fetch_depth_reads_only_the_requested_markets_and_sets_sizes(self):
        races = races_with(4)
        for c in races["R"]["contracts"]:
            c["sig_exchange_id"] = f"x{c['market_id']}"
        book = {"exchanges": [{"exchangeId": "x2", "bids": [{"price": 0.4, "quantity": 7}],
                               "asks": [{"price": 0.45, "quantity": 9}]}]}
        with mock.patch("sig.requests.get", return_value=reply(book)) as get:
            sig.fetch_depth(races, {"2"})
        self.assertEqual(get.call_count, 1)
        q = races["R"]["contracts"][2]["yes"]
        self.assertEqual((q["bid_size"], q["ask_size"]), (7, 9))
        self.assertIsNone(races["R"]["contracts"][0]["yes"]["bid_size"])

    def test_a_failing_chunk_only_loses_its_own_markets(self):
        races = races_with(150)
        def flaky(url, params, headers, timeout):
            if "0" in params["ids"].split(",")[:1]:
                return reply({"error": {"code": "SERVICE_UNAVAILABLE", "message": "x"}}, status=503)
            return markets_reply(url, params, headers, timeout)
        with mock.patch("sig.requests.get", side_effect=flaky):
            sig.setup(races)
        resolved = sum(1 for c in races["R"]["contracts"] if c["sig_exchange_id"])
        self.assertEqual(resolved, 50)
        self.assertEqual(len(drain_warnings()), 100)


class ReadBudget(unittest.TestCase):
    def setUp(self):
        budget.READS._times.clear()

    def test_the_read_over_the_limit_waits_for_the_window_to_clear(self):
        clock = [1000.0]
        slept = []
        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds
        with mock.patch.object(config, "SIG_READS_PER_MINUTE", 3), mock.patch("budget.time.time", lambda: clock[0]), \
                mock.patch("budget.time.sleep", sleep), mock.patch("sig.requests.get", return_value=reply({"ok": 1})):
            for _ in range(3):
                sig._get("/x")
            self.assertEqual(slept, [])
            sig._get("/x")  # the 4th read inside 60s must wait
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 60.05, places=1)

    def test_429_waits_for_retry_after_then_retries_once(self):
        with mock.patch("sig.time.sleep") as sleep, mock.patch(
                "sig.requests.get", side_effect=[reply({}, status=429, headers={"Retry-After": "7"}), reply({"ok": 1})]) as get:
            self.assertEqual(sig._get("/x"), {"ok": 1})
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_with(7.0)


class TwoPassPlanning(unittest.TestCase):
    def plan_for(self, held_value=0.0):
        races = races_with(2)
        for c, (bid, ask) in zip(races["R"]["contracts"], ((0.40, 0.45), (0.50, 0.55))):
            c["yes"].update(bid=bid, ask=ask)         # bulk quote: no sizes yet
        fair = {("R", "D"): Fair(0.60, "t"), ("R", "R"): Fair(0.40, "t")}
        return make_plan(races, fair, Portfolio(100000.0, [], 0.0))

    def test_bulk_prices_alone_buy_nothing_but_name_the_markets_to_read(self):
        plan = self.plan_for()
        self.assertEqual(plan.buys, [])                      # no sizes yet, so nothing is sized
        self.assertEqual(depth_targets(plan, limit=6), ["0"])  # Yes on D (edge 15c) is the trade worth a read

    def test_depth_reads_are_capped(self):
        plan = self.plan_for()
        self.assertEqual(depth_targets(plan, limit=0), [])


if __name__ == "__main__":
    unittest.main()
