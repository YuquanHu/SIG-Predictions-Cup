"""Fair values of one race add up to exactly 1, so both sides of the same bet (Dem Yes / Rep No) have the same fair value.
Run: python3 -m unittest discover -s tests -t ."""
import unittest
from unittest import mock

import strategy
from races import empty_quote
from strategy import Fair, compute_fair_values, find_opportunities, normalize, probability_gaps


def race(*codes):
    return {"outcomes": {c: {} for c in codes},
            "contracts": [{"market_id": str(i), "pays_if": c, "yes": {**empty_quote(), "bid": 0.40, "ask": 0.42}}
                          for i, c in enumerate(codes, start=1)]}


def fair_values(races, values):
    """compute_fair_values with fair_value() replaced by fixed per-outcome values."""
    by_outcome = iter(values)
    with mock.patch.object(strategy, "fair_value", side_effect=lambda o, now: Fair(next(by_outcome), "t", 2)):
        return compute_fair_values(races, 1.0)


class Normalize(unittest.TestCase):
    def test_values_are_scaled_to_add_up_to_one(self):
        scaled = normalize({"D": 0.185, "R": 0.825})
        self.assertAlmostEqual(sum(scaled.values()), 1.0)
        self.assertAlmostEqual(scaled["D"], 0.185 / 1.01)

    def test_a_race_far_from_one_is_left_alone_because_a_candidate_is_probably_missing(self):
        self.assertEqual(normalize({"D": 0.60, "R": 0.30}), {"D": 0.60, "R": 0.30})

    def test_an_outcome_without_a_value_leaves_the_race_alone(self):
        self.assertEqual(normalize({"D": 0.60, "R": None}), {"D": 0.60, "R": None})


class ComputeFairValues(unittest.TestCase):
    races = {"FL": race("D", "R")}

    def test_dem_yes_and_rep_no_now_share_one_fair_value(self):
        fair = fair_values(self.races, [0.185, 0.825])                       # the Florida governor numbers: they added up to 1.01
        opps = {(o.market_id, o.side): o for o in find_opportunities(self.races, fair)}
        self.assertAlmostEqual(opps[("1", "no")].fair, opps[("2", "yes")].fair)          # D No == R Yes
        self.assertAlmostEqual(opps[("1", "yes")].fair, opps[("2", "no")].fair)

    def test_the_raw_value_is_kept_for_the_probability_warning(self):
        fair = fair_values(self.races, [0.185, 0.825])
        self.assertEqual(fair[("FL", "R")].raw, 0.825)
        self.assertEqual(fair[("FL", "R")].value, 0.825 / 1.01)

    def test_a_race_that_already_adds_up_is_unchanged(self):
        fair = fair_values(self.races, [0.20, 0.80])
        self.assertEqual((fair[("FL", "D")].value, fair[("FL", "D")].raw), (0.20, None))

    def test_the_missing_candidate_warning_still_fires_on_the_unscaled_numbers(self):
        races = {"X": race("D", "R")}
        fair = fair_values(races, [0.60, 0.30])                              # 0.90: too far off to scale
        self.assertEqual([r for r, _ in probability_gaps(races, fair)], ["X"])
        scaled = fair_values(races, [0.53, 0.50])                            # 1.03: scaled, and not a warning
        self.assertEqual(probability_gaps(races, scaled), [])


if __name__ == "__main__":
    unittest.main()
