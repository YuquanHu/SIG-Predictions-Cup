"""Offline tests for: merging only in two-candidate races, the Idaho-shaped race, the single-source cap,
probability sums, and long-shots with no bid."""
import unittest
from unittest import mock

import config
from portfolio import Portfolio
from races import drain_warnings, empty_quote, fill_missing_bid
import kalshi
from strategy import Fair, Opportunity, fair_value, find_opportunities, keep_top, probability_gaps, wins_when
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


class SingleSourceRaces(unittest.TestCase):
    def test_kalshi_sends_no_request_when_no_outcome_has_a_kalshi_id(self):
        races = race(["D", "R"], {"D": (.5, .6), "R": (.3, .4)})
        for o in races["X-RACE"]["outcomes"].values():
            o["kalshi_id"] = None
        with mock.patch("kalshi.requests.get") as get:
            kalshi.fetch_prices(races)
        get.assert_not_called()

    def kalshi_fetch(self, **response):
        """Run kalshi.fetch_prices on a one-outcome race against this response; (the race's Kalshi quote, the warnings it raised)."""
        races = race(["D"], {"D": (.5, .6)})
        drain_warnings()
        with mock.patch("kalshi.requests.get", **response):
            kalshi.fetch_prices(races)
        return races["X-RACE"]["outcomes"]["D"]["kalshi"], drain_warnings()

    def test_kalshi_being_unavailable_leaves_no_quote_and_raises_no_warning(self):
        listed = lambda **market: mock.Mock(json=lambda: {"markets": [{"ticker": "D", **market}]})
        cases = {"the request fails": dict(side_effect=OSError("down")),
                 "the ticker is no longer returned (settled)": dict(return_value=mock.Mock(json=lambda: {"markets": []})),
                 "the market has no two-sided quote": dict(return_value=listed(yes_bid_dollars="0.0000", yes_ask_dollars="0.0000"))}
        for why, response in cases.items():
            with self.subTest(why):
                quote, warnings = self.kalshi_fetch(**response)
                self.assertIsNone(quote["bid"])
                self.assertEqual(warnings, [])

    def test_a_kalshi_market_that_is_quoted_still_fills_its_quote(self):
        market = mock.Mock(json=lambda: {"markets": [{"ticker": "D", "yes_bid_dollars": "0.60", "yes_ask_dollars": "0.62"}]})
        quote, warnings = self.kalshi_fetch(return_value=market)
        self.assertEqual((quote["bid"], quote["ask"], warnings), (0.60, 0.62, []))

    def test_fair_value_works_from_polymarket_alone_and_from_kalshi_alone(self):
        quote = dict(empty_quote(), bid=0.60, ask=0.62, updated=1.0)
        poly_only = {"kalshi": empty_quote(), "poly": quote}
        kalshi_only = {"kalshi": quote, "poly": empty_quote()}
        for outcome in (poly_only, kalshi_only):
            fair = fair_value(outcome, now=1.0)
            self.assertAlmostEqual(fair.value, 0.61)
            self.assertEqual(fair.sources, 1)


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
