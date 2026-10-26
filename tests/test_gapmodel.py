"""Offline tests for the gap model, the bucket lookup and the history back-fill. Run: python3 -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest
from unittest import mock

import backfill
import config
import gapmodel
from gapmodel import Baseline, GapStore, classify, fast_part, percentile, quarter, sample_due
from hold import bucket_of
from races import empty_quote
from strategy import Fair

NOW = 1_800_000_000 - 1_800_000_000 % 900  # on a sample time
STEP = 900
N = 192  # samples in 48 hours


def series(gap_at):
    """[(time, gap)] for the last 48 hours, newest sample at NOW; gap_at(i) gives the gap i samples ago."""
    return [(NOW - i * STEP, gap_at(i)) for i in range(N)]


class Percentile(unittest.TestCase):
    def test_interpolates_between_values(self):
        self.assertAlmostEqual(percentile([0, 10, 20, 30, 40], 10), 4.0)
        self.assertEqual(percentile([5], 10), 5)
        self.assertEqual(percentile([1, 2, 3], 50), 2)


class Classify(unittest.TestCase):
    def test_too_little_history_is_unclassified(self):
        b = classify(series(lambda i: 0.05)[:config.MIN_HISTORY_SAMPLES - 1], NOW)
        self.assertEqual((b.signed, b.structural, b.side), (None, False, None))

    def test_a_steady_gap_is_structural_and_the_hurdle_follows_the_formula(self):
        b = classify(series(lambda i: 0.05), NOW)
        self.assertTrue(b.structural)
        self.assertEqual(b.side, "yes")
        self.assertAlmostEqual(b.hurdle, 0.04)           # min(0.8 * 0.05, 0.05 - 0.01)
        self.assertAlmostEqual(classify(series(lambda i: 0.03), NOW).hurdle, 0.02)   # M - 1c is the smaller
        self.assertAlmostEqual(classify(series(lambda i: 0.10), NOW).hurdle, 0.08)   # 0.8 M is the smaller

    def test_a_gap_just_under_three_cents_is_not_structural(self):
        self.assertFalse(classify(series(lambda i: 0.029), NOW).structural)
        self.assertTrue(classify(series(lambda i: 0.03), NOW).structural)

    def test_a_negative_gap_is_a_no_side_gap(self):
        b = classify(series(lambda i: -0.06), NOW)
        self.assertEqual((b.side, b.structural), ("no", True))
        self.assertAlmostEqual(b.size, 0.06)

    def test_a_gap_that_is_only_recent_is_not_structural(self):
        b = classify(series(lambda i: 0.06 if i < 32 else 0.0), NOW)   # only the last 8 hours
        self.assertFalse(b.structural)

    def test_a_gap_that_often_disappears_fails_the_10th_percentile(self):
        b = classify(series(lambda i: 0.0 if i % 5 == 0 else 0.05), NOW)   # gone 20% of the time
        self.assertAlmostEqual(b.size, 0.05)
        self.assertFalse(b.structural)

    def test_a_gap_that_vanished_in_the_last_two_hours_fails_the_recent_test(self):
        b = classify(series(lambda i: 0.0 if i < 8 else 0.05), NOW)
        self.assertFalse(b.structural)

    def test_a_spike_on_top_of_a_steady_gap_keeps_the_baseline(self):
        b = classify(series(lambda i: 0.08 if i < 4 else 0.04), NOW)    # 4c steady, 8c for the last hour
        self.assertTrue(b.structural)
        self.assertAlmostEqual(b.size, 0.04)
        self.assertAlmostEqual(fast_part(b, "yes", 0.08), 0.04)         # 4c structural, 4c fast


class FastPart(unittest.TestCase):
    def test_unclassified_means_no_fast_part(self):
        self.assertIsNone(fast_part(classify([], NOW), "yes", 0.09))

    def test_the_usual_gap_is_taken_off_on_the_same_side(self):
        b = classify(series(lambda i: 0.05), NOW)
        self.assertAlmostEqual(fast_part(b, "yes", 0.09), 0.04)
        self.assertAlmostEqual(fast_part(b, "yes", 0.05), 0.0)

    def test_a_usual_gap_pointing_the_other_way_gives_no_credit(self):
        b = classify(series(lambda i: 0.05), NOW)                       # Yes usually cheap by 5c
        self.assertAlmostEqual(fast_part(b, "no", 0.02), 0.02)          # a No edge of 2c is a 2c fast part, not 7c

    def test_a_usual_gap_inside_the_noise_tolerance_counts_as_none(self):
        b = classify(series(lambda i: 0.008), NOW)
        self.assertEqual(b.for_side("yes"), 0.0)
        self.assertAlmostEqual(fast_part(b, "yes", 0.06), 0.06)


class Store(unittest.TestCase):
    def test_samples_are_clock_aligned_and_a_newer_one_replaces_an_older_one(self):
        store = GapStore()
        store.add("1", NOW + 100, 0.01)
        store.add("1", NOW + 800, 0.02)
        self.assertEqual(store.samples("1"), [(NOW, 0.02)])

    def test_count_prune_and_roundtrip(self):
        store = GapStore()
        for t, g in series(lambda i: 0.05):
            store.add("1", t, g)
        store.add("1", NOW - 60 * 3600, 0.9)                            # older than the window
        self.assertEqual(store.count("1", NOW), N)
        store.prune(NOW)
        self.assertEqual(len(store.samples("1")), N)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gaps.csv")
            store.save(path)
            loaded = GapStore.load(path)
        self.assertEqual(loaded.samples("1"), store.samples("1"))
        self.assertEqual(loaded.last_sample(), NOW)
        self.assertEqual(GapStore.load("/nonexistent/file.csv").samples("1"), [])

    def test_baselines_for_several_markets(self):
        store = GapStore()
        for t, g in series(lambda i: 0.05):
            store.add("1", t, g)
        result = store.baselines(["1", "2"], NOW)
        self.assertTrue(result["1"].structural)
        self.assertIsNone(result["2"].signed)

    def test_sample_due_on_the_quarter_hour(self):
        self.assertTrue(sample_due(None, NOW))
        self.assertFalse(sample_due(NOW, NOW + 899))
        self.assertTrue(sample_due(NOW, NOW + 900))
        self.assertEqual(quarter(NOW + 899), NOW)

    def test_current_gaps_need_a_fair_value_and_a_two_sided_sig_quote(self):
        def contract(market, pays, bid, ask):
            return {"market_id": market, "pays_if": pays, "yes": {**empty_quote(), "bid": bid, "ask": ask}}
        races = {"R": {"contracts": [contract("1", "D", 0.80, 0.82), contract("2", "R", 0.10, None), contract("3", "X", 0.5, 0.52)]}}
        fair = {("R", "D"): Fair(0.90, ""), ("R", "R"): Fair(0.10, ""), ("R", "X"): Fair(None, "none")}
        self.assertEqual(list(gapmodel.current_gaps(races, fair)), ["1"])
        self.assertAlmostEqual(gapmodel.current_gaps(races, fair)["1"], 0.09)


class Buckets(unittest.TestCase):
    def test_edges_belong_to_the_higher_bucket_and_90_is_a_sure_bet(self):
        names = {p: bucket_of(p)[0] for p in (0.0, 0.19, 0.2, 0.39, 0.4, 0.59, 0.6, 0.89, 0.9, 1.0)}
        self.assertEqual(names, {0.0: "longshot", 0.19: "longshot", 0.2: "unlikely", 0.39: "unlikely", 0.4: "coinflip",
                                 0.59: "coinflip", 0.6: "favourite", 0.89: "favourite", 0.9: "sure bet", 1.0: "sure bet"})

    def test_shares_add_up_to_the_whole_hold_book(self):
        self.assertAlmostEqual(sum(share for *_, share in config.BUCKETS), 1.0)
        self.assertEqual([share for *_, share in config.BUCKETS], [0.02, 0.10, 0.15, 0.33, 0.40])


class Backfill(unittest.TestCase):
    outcome = {"kalshi_id": "SENATEXX-26-D", "poly_yes_token": "tok"}
    contract = {"market_id": "9", "pays_if": "D", "sig_exchange_id": "77"}

    def gaps(self, kalshi, poly, sig_series, start=NOW - 3 * STEP, end=NOW):
        with mock.patch.object(backfill, "kalshi_mids", return_value=kalshi), \
                mock.patch.object(backfill, "poly_prices", return_value=poly), \
                mock.patch.object(backfill, "sig_closes", return_value=sig_series):
            race = {"outcomes": {"D": self.outcome}}                       # one outcome: the total is far from 1, so no scaling
            return backfill.race_gaps(race, [(self.contract, start)], end)["9"]

    def test_fair_is_the_average_of_both_sources_minus_the_sig_price(self):
        gaps = self.gaps({NOW - 2 * STEP: 0.60}, {NOW - 2 * STEP: 0.64}, {NOW - 3 * STEP: 0.55})
        self.assertAlmostEqual(gaps[NOW - 2 * STEP], 0.62 - 0.55)

    def test_one_source_is_enough_and_no_sig_trade_yet_means_no_sample(self):
        self.assertAlmostEqual(self.gaps({NOW - 3 * STEP: 0.60}, {}, {NOW - 3 * STEP: 0.55})[NOW - 2 * STEP], 0.05)
        self.assertEqual(self.gaps({NOW - 3 * STEP: 0.60}, {}, {}), {})

    def test_float_start_and_end_times_work_because_the_bot_passes_time_dot_time(self):
        gaps = self.gaps({NOW - 2 * STEP: 0.60}, {}, {NOW - 3 * STEP: 0.55}, start=NOW - 3 * STEP + 0.5, end=NOW + 0.5)
        self.assertAlmostEqual(gaps[NOW - 2 * STEP], 0.05)

    def test_a_price_older_than_three_hours_is_not_used(self):
        old = NOW - 4 * 3600
        self.assertEqual(self.gaps({old: 0.60}, {old: 0.60}, {old: 0.55}), {})

    def test_a_candle_label_is_never_used_before_it_closes(self):
        self.assertIsNone(backfill.value_at({NOW + 1: 0.5}, NOW))
        self.assertEqual(backfill.value_at({NOW: 0.5, NOW - 5: 0.4}, NOW), 0.5)

    def test_a_stale_tail_is_fetched_from_the_newest_sample_and_a_fresh_one_is_not(self):
        races = {"A": {"outcomes": {"D": self.outcome}, "contracts": [dict(self.contract, market_id="1")]},
                 "B": {"outcomes": {"D": self.outcome}, "contracts": [dict(self.contract, market_id="2")]}}
        store = GapStore()
        for t, g in series(lambda i: 0.05):
            store.add("1", t - 5 * 3600, g)                             # full history, newest sample 5 hours old
            store.add("2", t - 600, g)                                  # full history, newest sample 10 minutes old
        with mock.patch.object(backfill, "race_gaps", return_value={"1": {NOW: 0.05}}) as fetch:
            self.assertEqual(backfill.backfill(races, store, NOW), 1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[1][0][1], NOW - 5 * 3600)  # starts at the newest sample, not 48 hours ago

    def test_only_contracts_without_enough_history_are_fetched_and_a_failure_is_a_warning(self):
        races = {"A": {"outcomes": {"D": self.outcome}, "contracts": [dict(self.contract, market_id="1")]},
                 "B": {"outcomes": {"D": self.outcome}, "contracts": [dict(self.contract, market_id="2")]}}
        store = GapStore()
        for t, g in series(lambda i: 0.05):
            store.add("1", t, g)                                        # market 1 already has history
        with mock.patch.object(backfill, "race_gaps", side_effect=RuntimeError("down")) as fetch, \
                mock.patch.object(backfill, "warn") as warn:
            self.assertEqual(backfill.backfill(races, store, NOW), 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("2", warn.call_args.args[2])


class BackfillScalesTheRace(unittest.TestCase):
    """Dem Yes and Rep No must get the same fair value in the history too."""
    def test_both_outcomes_are_scaled_to_add_up_to_one_at_every_sample_time(self):
        race = {"outcomes": {"D": {"kalshi_id": "K-D", "poly_yes_token": None}, "R": {"kalshi_id": "K-R", "poly_yes_token": None}}}
        contracts = [({"market_id": "1", "pays_if": "D", "sig_exchange_id": "d"}, NOW - 3 * STEP),
                     ({"market_id": "2", "pays_if": "R", "sig_exchange_id": "r"}, NOW - 3 * STEP)]
        kalshi = {"K-D": {NOW - 2 * STEP: 0.185}, "K-R": {NOW - 2 * STEP: 0.825}}              # adds up to 1.01
        sig_series = {"d": {NOW - 3 * STEP: 0.22}, "r": {NOW - 3 * STEP: 0.80}}
        with mock.patch.object(backfill, "kalshi_mids", side_effect=lambda ticker, *a: kalshi[ticker]), \
                mock.patch.object(backfill, "sig_closes", side_effect=lambda ex, *a: sig_series[ex]):
            gaps = backfill.race_gaps(race, contracts, NOW)
        fair_d, fair_r = 0.185 / 1.01, 0.825 / 1.01
        self.assertAlmostEqual(gaps["1"][NOW - 2 * STEP], fair_d - 0.22)
        self.assertAlmostEqual(gaps["2"][NOW - 2 * STEP], fair_r - 0.80)
        self.assertAlmostEqual(fair_d + fair_r, 1.0)


class HistoryRetries(unittest.TestCase):
    def reply(self, status, body=None, headers=None):
        r = mock.Mock(status_code=status, headers=headers or {})
        r.json.return_value = body
        r.raise_for_status.side_effect = None if status < 400 else RuntimeError(f"HTTP {status}")
        return r

    def test_a_429_is_retried_after_the_wait_it_asks_for(self):
        with mock.patch.object(backfill.requests, "get", side_effect=[self.reply(429, headers={"Retry-After": "3"}),
                                                                      self.reply(200, {"ok": 1})]) as get, \
                mock.patch.object(backfill.time, "sleep") as sleep:
            self.assertEqual(backfill._get_json("u", {}), {"ok": 1})
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(3.0)

    def test_it_gives_up_after_the_last_attempt_and_a_404_is_not_retried(self):
        with mock.patch.object(backfill.requests, "get", return_value=self.reply(429)) as get, \
                mock.patch.object(backfill.time, "sleep"):
            with self.assertRaises(RuntimeError):
                backfill._get_json("u", {})
        self.assertEqual(get.call_count, backfill.MAX_ATTEMPTS)
        with mock.patch.object(backfill.requests, "get", return_value=self.reply(404)) as get:
            with self.assertRaises(RuntimeError):
                backfill._get_json("u", {})
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
