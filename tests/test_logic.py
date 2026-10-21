"""Offline tests for the trading logic. Run: python3 -m unittest discover -s tests -t ."""
import json
import os
import tempfile
import time
import unittest

import config
from portfolio import Holding, Portfolio
from races import disabled_races, empty_quote, load_races
from safety import RunState, check_loss, check_stop
from strategy import Opportunity, fair_value, keep_top
from trading import compute_wants, decide_sells, size_buys

NOW = time.time()


def quote(bid, ask, age=0):
    return {"bid": bid, "ask": ask, "bid_size": 1, "ask_size": 1, "updated": NOW - age}


def outcome(kalshi=None, poly=None):
    return {"kalshi": kalshi or empty_quote(), "poly": poly or empty_quote()}


def holding(race, market, shares, sell, fair, wins="D", depth=1e6):
    return Holding(race, market, "no", wins, shares, shares * sell, sell, depth, fair)


def opp(race, market, price, fair, wins="D", side="no", size=1e6):
    return Opportunity(race, market, "D", side, price, fair, size, wins)


class FairValue(unittest.TestCase):
    def test_blends_both_midpoints(self):
        self.assertAlmostEqual(fair_value(outcome(quote(.49, .51), quote(.51, .53)), NOW).value, 0.51)

    def test_drops_stale_source(self):
        f = fair_value(outcome(quote(.49, .51, age=90), quote(.51, .53)), NOW)
        self.assertAlmostEqual(f.value, 0.52)

    def test_spread_limit_is_inclusive_at_3_cents(self):
        self.assertAlmostEqual(fair_value(outcome(quote(.49, .52)), NOW).value, 0.505)
        self.assertAlmostEqual(fair_value(outcome(quote(.49, .52), quote(.50, .54)), NOW).value, 0.505)

    def test_disagreement_limit_is_inclusive_at_4_cents(self):
        self.assertIsNotNone(fair_value(outcome(quote(.49, .51), quote(.53, .55)), NOW).value)
        self.assertIsNone(fair_value(outcome(quote(.49, .51), quote(.54, .56)), NOW).value)

    def test_no_source_gives_none(self):
        self.assertIsNone(fair_value(outcome(), NOW).value)


class KeepTop(unittest.TestCase):
    def test_one_bet_per_race_and_top_n_by_kelly(self):
        cands = [opp(r, str(i), .5, .5 + k * .5, wins=f"w{i}") for i, (r, k) in
                 enumerate([("A", .3), ("A", .8), ("B", .3), ("C", .7), ("D", .2), ("E", .6), ("F", .4)])]
        kept = keep_top(cands, 4)
        self.assertEqual([(c.race, round(c.kelly, 2)) for c in kept], [("A", .8), ("C", .7), ("E", .6), ("F", .4)])

    def test_equivalent_bets_keep_the_cheaper(self):
        yes, no = opp("R", "1", .885, .985, wins="D", side="yes"), opp("R", "2", .870, .988, wins="D")
        self.assertEqual(keep_top([yes, no], 4), [no])

    def test_edge_below_minimum_is_rejected(self):
        c = opp("R", "1", .90, .93)
        self.assertEqual(keep_top([c], 4), [])
        self.assertIn("below", c.decision)


class Sizing(unittest.TestCase):
    def test_targets_scale_to_investment_cap_then_race_cap(self):
        chosen = [opp("A", "1", .5, .95), opp("B", "2", .5, .85), opp("C", "3", .5, .70)]  # half Kelly .45 .35 .20
        wants = compute_wants(chosen, Portfolio(100000, [], 0))
        self.assertEqual([round(w.target) for w in wants], [36000, 28000, 16000])
        self.assertEqual([round(w.want) for w in wants], [30000, 28000, 16000])

    def test_subtracts_what_is_held_in_the_same_bet(self):
        held = [holding("A", "9", 20000, .5, .9)]  # 10000 already in the bet
        wants = compute_wants([opp("A", "1", .5, .95)], Portfolio(90000, held, 0))
        self.assertAlmostEqual(wants[0].held, 10000)

    def test_buys_stop_when_investable_cash_runs_out(self):
        wants = compute_wants([opp("A", "1", .5, .95), opp("B", "2", .5, .85)], Portfolio(100000, [], 0))
        buys, _ = size_buys(wants, investable=20000)
        self.assertEqual([(b.market_id, b.shares) for b in buys], [("1", 40000)])

    def test_shares_on_offer_cap_the_buy(self):
        wants = compute_wants([opp("A", "1", .5, .95, size=100)], Portfolio(100000, [], 0))
        self.assertEqual(size_buys(wants, investable=80000)[0][0].shares, 100)


class ExitsAndSwaps(unittest.TestCase):
    def test_negative_edge_is_sold_even_with_cash(self):
        bad = holding("A", "1", 1000, .80, .75)
        sells = decide_sells(Portfolio(1e6, [bad], 0), [])
        self.assertEqual([(o.action, o.market_id, o.shares) for o in sells], [("sell", "1", 1000)])

    def test_no_sell_when_book_takes_nothing(self):
        self.assertEqual(decide_sells(Portfolio(1e6, [holding("A", "1", 1000, .80, .75, depth=0)], 0), []), [])

    def test_swap_only_when_cash_is_used_up(self):
        held = [holding("A", "1", 1000, .80, .84), holding("B", "2", 1000, .80, .96)]  # +5%, +20%
        new = opp("C", "3", .70, .80)                                                    # +14.3%
        rich = Portfolio(1e6, held, 0)
        self.assertEqual(decide_sells(rich, compute_wants([new], rich)), [])
        broke = Portfolio(0.0, held, 0)  # all value invested, nothing free
        sells = decide_sells(broke, compute_wants([new], broke))
        self.assertEqual([o.market_id for o in sells], ["1"])

    def test_swap_needs_a_one_percent_better_return(self):
        held = [holding("A", "1", 1000, .80, .84)]  # +5%
        broke = Portfolio(0.0, held, 0)
        for fair in (.735, .72):                       # +5.0% and +2.9%
            self.assertEqual(decide_sells(broke, compute_wants([opp("C", "3", .70, fair)], broke)), [])

    def test_never_swaps_a_bet_for_itself(self):
        held = [holding("C", "9", 1000, .60, .62, wins="D")]
        new = opp("C", "3", .50, .70, wins="D", side="yes")
        broke = Portfolio(0.0, held, 0)
        self.assertEqual(decide_sells(broke, compute_wants([new], broke)), [])


class MissingPolymarketId(unittest.TestCase):
    def test_race_without_poly_id_loads_and_prices_from_kalshi_alone(self):
        raw = {"X-HOUSE": {"name": "X House", "group": "House",
                           "outcomes": {"D": {"kalshi_id": "K-D"}, "R": {"kalshi_id": "K-R", "poly_id": None}},
                           "contracts": [{"market_id": "1", "pays_if": "D"}, {"market_id": "2", "pays_if": "R"}]}}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "races.json")
            json.dump(raw, open(path, "w"))
            races = load_races(path)
        outcomes = races["X-HOUSE"]["outcomes"]
        self.assertIsNone(outcomes["D"]["poly_id"])
        self.assertIsNone(outcomes["R"]["poly_id"])
        outcomes["D"]["kalshi"] = quote(.60, .62)
        self.assertAlmostEqual(fair_value(outcomes["D"], NOW).value, 0.61)


class DisabledRaces(unittest.TestCase):
    def test_disabled_race_is_skipped_and_its_reason_reported(self):
        on = {"name": "On", "group": "House", "outcomes": {"D": {"kalshi_id": "K-D", "poly_id": None}},
              "contracts": [{"market_id": "1", "pays_if": "D"}]}
        off = {"name": "Off", "group": "House", "enabled": False, "disabled_reason": "no Kalshi market",
               "outcomes": {"D": {"kalshi_id": None, "poly_id": None}},
               "contracts": [{"market_id": "2", "pays_if": "D"}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "races.json")
            json.dump({"ON": on, "OFF": off}, open(path, "w"))
            self.assertEqual(list(load_races(path)), ["ON"])
            self.assertEqual(disabled_races(path), {"OFF": "no Kalshi market"})


class KillSwitch(unittest.TestCase):
    def test_errors_in_a_row(self):
        self.assertIsNone(check_stop(RunState(errors_in_row=config.MAX_CONSECUTIVE_ERRORS - 1)))
        self.assertIn("errors", check_stop(RunState(errors_in_row=config.MAX_CONSECUTIVE_ERRORS)))

    def test_daily_loss(self):
        state = RunState()
        self.assertIsNone(check_loss(state, 100000))   # first reading is the baseline
        self.assertIsNone(check_loss(state, 96000))
        self.assertIn("down", check_loss(state, 94900))


if __name__ == "__main__":
    unittest.main()
