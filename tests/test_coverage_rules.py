"""Offline tests for: merging only in two-candidate races, the Idaho-shaped race, the single-source cap,
probability sums, and long-shots with no bid."""
import unittest
from unittest import mock

import config
from portfolio import Portfolio
from races import empty_quote, fill_missing_bid
from strategy import Fair, Opportunity, find_opportunities, keep_top, probability_gaps, wins_when
from trading import compute_wants


def race(codes, quotes):
    """A race with SIG contracts only for D and R; any other code is a candidate with no SIG contract."""
    contracts = []
    for i, code in enumerate(c for c in codes if c in ("D", "R")):
        contracts.append({"market_id": str(i + 1), "pays_if": code, "sig_exchange_id": str(i + 1), "title": None,
                          "yes": dict(empty_quote(), bid=quotes[code][0], ask=quotes[code][1],
                                      bid_size=1e6, ask_size=1e6, updated=1.0)})
    outcomes = {c: {"kalshi_id": c, "poly_id": None, "poly_yes_token": None,
                    "kalshi": empty_quote(), "poly": empty_quote()} for c in codes}
    return {"X-RACE": {"name": "X", "group": "Senate", "contracts": contracts, "outcomes": outcomes}}


class MergeRule(unittest.TestCase):
    def test_two_candidate_race_merges_dem_yes_and_rep_no(self):
        r = race(["D", "R"], {"D": (.80, .82), "R": (.17, .19)})["X-RACE"]
        self.assertEqual(wins_when(r, "D", "yes"), wins_when(r, "R", "no"))

    def test_race_with_another_candidate_does_not_merge(self):
        r = race(["D", "R", "X"], {"D": (.80, .82), "R": (.17, .19)})["X-RACE"]
        self.assertNotEqual(wins_when(r, "D", "yes"), wins_when(r, "R", "no"))


class IdahoShapedRace(unittest.TestCase):
    """The Democrat dropped out: priced near 0 everywhere, but SIG still sells D at 5-6c."""
    def setUp(self):
        self.races = race(["D", "R", "X"], {"D": (.05, .06), "R": (.92, .925)})
        self.fair = {("X-RACE", "D"): Fair(0.00125, "t", 2), ("X-RACE", "R"): Fair(0.96, "t", 2),
                     ("X-RACE", "X"): Fair(0.04, "t", 1)}

    def test_no_on_the_dropped_out_democrat_is_the_trade(self):
        with mock.patch.object(config, "MIN_EDGE", 0.04):
            chosen = keep_top(find_opportunities(self.races, self.fair), 4)
        self.assertEqual([(c.side, c.pays_if) for c in chosen], [("no", "D")])
        self.assertAlmostEqual(chosen[0].price, 0.95)
        self.assertGreater(chosen[0].edge, 0.04)

    def test_outcome_without_a_contract_creates_no_candidate(self):
        found = find_opportunities(self.races, self.fair)
        self.assertEqual({c.pays_if for c in found}, {"D", "R"})


class SingleSourceCap(unittest.TestCase):
    def want_for(self, sources):
        o = Opportunity("R1", "1", "D", "yes", 0.5, 0.99, 1e6, "D", sources)  # huge edge: wants far more than any cap
        return compute_wants([o], Portfolio(100000.0, [], 0.0))[0].want

    def test_two_sources_use_the_normal_cap(self):
        self.assertAlmostEqual(self.want_for(2), config.RACE_CAP * 100000)

    def test_one_source_uses_the_smaller_cap(self):
        self.assertAlmostEqual(self.want_for(1), config.SINGLE_SOURCE_RACE_CAP * 100000)
        self.assertLess(config.SINGLE_SOURCE_RACE_CAP, config.RACE_CAP)


class ProbabilitySums(unittest.TestCase):
    def test_flags_a_race_that_does_not_add_up(self):
        races = race(["D", "R"], {"D": (.5, .6), "R": (.3, .4)})
        ok = {("X-RACE", "D"): Fair(0.60, "t", 2), ("X-RACE", "R"): Fair(0.40, "t", 2)}
        missing_candidate = {("X-RACE", "D"): Fair(0.60, "t", 2), ("X-RACE", "R"): Fair(0.30, "t", 2)}
        self.assertEqual(probability_gaps(races, ok), [])
        self.assertEqual([r for r, _ in probability_gaps(races, missing_candidate)], ["X-RACE"])

    def test_skips_races_with_an_outcome_that_has_no_fair_value(self):
        races = race(["D", "R"], {"D": (.5, .6), "R": (.3, .4)})
        fair = {("X-RACE", "D"): Fair(0.60, "t", 2), ("X-RACE", "R"): Fair(None, "no data", 0)}
        self.assertEqual(probability_gaps(races, fair), [])


class LongShotWithNoBid(unittest.TestCase):
    def test_small_ask_without_a_bid_becomes_a_zero_bid(self):
        q = dict(empty_quote(), ask=0.001)
        fill_missing_bid(q)
        self.assertEqual(q["bid"], 0.0)

    def test_wide_ask_without_a_bid_stays_unusable(self):
        q = dict(empty_quote(), ask=0.20)
        fill_missing_bid(q)
        self.assertIsNone(q["bid"])

    def test_no_ask_stays_unusable(self):
        q = dict(empty_quote(), bid=0.5)
        fill_missing_bid(q)
        self.assertIsNone(q["ask"])


if __name__ == "__main__":
    unittest.main()
