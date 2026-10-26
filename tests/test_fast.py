"""Offline tests for the fast book: entry beyond the usual gap, sizing, unwinds, swaps and the merged v2 plan.
Run: python3 -m unittest discover -s tests -t ."""
import unittest

import config
import fast
from books import Ledger, Lot
from cooldown import Cooldown
from gapmodel import Baseline, UNCLASSIFIED
from liquidity import Volume, VolumeStore
from orders import Order
from planner import depth_targets, make_plan_v2
from portfolio import Holding, Portfolio
from races import empty_quote
from strategy import Fair, Opportunity

EQUITY = 100_000.0
NOW = 1_700_000_000.0
HOURS = 3600


def opp(market, fair=0.985, price=0.90, side="no", wins="D", size=1e6):
    return Opportunity(f"R{market}", market, "D", side, price, fair, size, wins, 2)


def base(usual=-0.05, structural=True):
    """A classified baseline whose usual gap is `usual` in Yes space (negative: the No side has it)."""
    return Baseline(usual, "yes" if usual > 0 else "no", abs(usual), 0.8 * abs(usual), structural, 192, abs(usual), abs(usual))


def races_for(*markets, bid=0.09, ask=0.10):
    return {f"R{m}": {"outcomes": {}, "contracts": [{"market_id": m, "pays_if": "D", "yes": {**empty_quote(), "bid": bid, "ask": ask}}]}
            for m in markets}


def held(market, value, fair=0.985, sell=0.90, side="no", wins="D"):
    return Holding(f"R{market}", market, side, wins, value / sell, value, sell, 1e6, fair)


def fast_lot(market, shares, age_hours=1.0, side="no"):
    return Lot("fast", market, side, shares, 0.9, NOW - age_hours * HOURS)


def volumes_for(*markets, total=10e6, recent=50_000):
    return VolumeStore({m: Volume(total, recent, NOW) for m in markets})


def run(candidates, baselines, holdings=(), lots=(), cooldown=None, races=None, cash=None, volumes=None):
    portfolio = Portfolio(cash if cash is not None else EQUITY - sum(h.value for h in holdings), list(holdings), 0.0)
    markets = {c.market_id for c in candidates} | {h.market_id for h in holdings}
    return fast.fast_plan(candidates, baselines, races or races_for(*markets), portfolio, Ledger(list(lots)), cooldown or Cooldown(0),
                          volumes or volumes_for(*markets), NOW)


class CaughtUp(unittest.TestCase):
    def test_it_is_caught_up_when_what_selling_earns_beyond_the_usual_gap_is_noise(self):
        self.assertTrue(fast.caught_up(base(-0.05), "no", 0.055))         # 0.5c beyond the 5c usual gap
        self.assertFalse(fast.caught_up(base(-0.05), "no", 0.08))         # 3c still ahead
        self.assertFalse(fast.caught_up(UNCLASSIFIED, "no", 0.0))         # no baseline: no verdict
        self.assertTrue(fast.caught_up(base(0.05), "no", 0.01))           # a usual gap pointing the other way counts as 0

    def test_the_time_stop(self):
        self.assertFalse(fast.time_up(NOW - 23 * HOURS, NOW))
        self.assertTrue(fast.time_up(NOW - 24 * HOURS, NOW))


class Entry(unittest.TestCase):
    def test_the_fast_part_is_the_edge_beyond_the_usual_gap(self):
        edge_8_5c = opp("1", fair=0.985, price=0.90)                       # 8.5c edge, 5c usual: 3.5c fast part
        plan = run([edge_8_5c], {"1": base(-0.05)})
        self.assertEqual([(w.opp.market_id, w.book) for w in plan.wants], [("1", "fast")])
        self.assertIn("3.5c beyond the usual gap", plan.wants[0].reason)
        self.assertTrue(edge_8_5c.decision.startswith("FAST"))

    def test_a_big_edge_that_is_all_usual_gap_is_not_a_fast_entry(self):
        plan = run([opp("1", fair=0.97, price=0.90)], {"1": base(-0.05)})   # 7c edge, 2c beyond the usual gap, below x = 3c
        self.assertEqual(plan.wants, [])
        self.assertIn("below 3c", plan.rows[0].note)

    def test_an_unclassified_market_never_enters(self):
        plan = run([opp("1", fair=0.99, price=0.90)], {"1": UNCLASSIFIED})
        self.assertEqual((plan.wants, plan.rows), ([], []))
        self.assertEqual(run([opp("1", fair=0.99, price=0.90)], {}).wants, [])

    def test_a_position_is_one_slice_of_the_fast_book(self):
        plan = run([opp("1")], {"1": base(-0.05)})
        self.assertAlmostEqual(plan.wants[0].target, 0.30 * 0.20 * EQUITY)     # 6% of equity
        self.assertAlmostEqual(plan.wants[0].want, 6_000)

    def test_a_wide_spread_the_other_side_held_and_a_cooldown_each_block_entry(self):
        wide = run([opp("1")], {"1": base()}, races=races_for("1", bid=0.05, ask=0.10))
        self.assertIn("too wide", wide.rows[0].note)
        other = run([opp("1")], {"1": base()}, lots=[fast_lot("1", 10, side="yes")])
        self.assertIn("other side", other.rows[0].note)
        cooldown = Cooldown(300)
        cooldown.record([Order("sell", "no", "R1", "1", 1, 0.9, 0.9, "t")])
        self.assertIn("cooldown", run([opp("1")], {"1": base()}, cooldown=cooldown).rows[0].note)

    def test_an_entry_that_the_spread_would_turn_into_caught_up_is_refused(self):
        c = opp("1", fair=0.985, price=0.90)                                           # 3.5c fast part
        self.assertEqual(len(run([c], {"1": base()}, races=races_for("1", bid=0.09, ask=0.11)).wants), 1)   # 2c spread: 1.5c left
        plan = run([opp("1", fair=0.985, price=0.90)], {"1": base()}, races=races_for("1", bid=0.08, ask=0.11))   # 3c spread: 0.5c left
        self.assertEqual(plan.wants, [])
        self.assertIn("does not survive the 3.0c spread", plan.rows[0].note)

    def test_candidates_are_ranked_by_fast_part_over_price(self):
        small, big = opp("1", fair=0.985, price=0.90), opp("2", fair=0.995, price=0.90)     # 3.5c and 4.5c beyond 5c usual
        plan = run([small, big], {"1": base(), "2": base()})
        self.assertEqual([w.opp.market_id for w in plan.wants], ["2", "1"])

    def test_of_two_equivalent_bets_that_both_qualify_the_cheaper_is_bought(self):
        dearer = Opportunity("FL", "300", "R", "yes", 0.795, 0.84, 1e6, "R", 2)   # 4.5c edge, ranks first
        cheaper = Opportunity("FL", "299", "D", "no", 0.78, 0.82, 1e6, "R", 2)    # 4.0c edge, 1.5c cheaper
        plan = run([dearer, cheaper], {"299": base(-0.0), "300": base(-0.0)})
        self.assertEqual([(w.opp.market_id, w.opp.side) for w in plan.wants], [("299", "no")])
        self.assertIn("cheaper", next(r.note for r in plan.rows if "300" in r.label))

    def test_an_equivalent_bet_that_fails_the_fast_part_gate_does_not_displace_the_qualifying_one(self):
        weak = Opportunity("FL", "299", "D", "no", 0.78, 0.805, 1e6, "R", 2)      # 2.5c: below the 3c minimum
        strong = Opportunity("FL", "300", "R", "yes", 0.795, 0.84, 1e6, "R", 2)
        plan = run([weak, strong], {"299": base(-0.0), "300": base(-0.0)})
        self.assertEqual([w.opp.market_id for w in plan.wants], ["300"])

    def test_a_market_with_little_volume_or_none_known_does_not_enter(self):
        for volumes, why in [(volumes_for("1", total=0.9e6), "below 1.0M"), (volumes_for("1", recent=4_000), "since yesterday"),
                             (VolumeStore(), "not known yet")]:
            plan = run([opp("1")], {"1": base()}, volumes=volumes)
            self.assertEqual(plan.wants, [])
            self.assertIn(why, plan.rows[0].note)

    def test_a_market_with_middling_volume_gets_half_a_slice_and_a_busy_one_a_full_slice(self):
        half = run([opp("1")], {"1": base()}, volumes=volumes_for("1", total=1e6))
        full = run([opp("1")], {"1": base()}, volumes=volumes_for("1", total=5e6))
        self.assertAlmostEqual(half.wants[0].want, 3_000)
        self.assertAlmostEqual(full.wants[0].want, 6_000)
        self.assertIn("slice x0.5: volume 1.0M", half.rows[0].note)

    def test_a_thin_market_enters_an_eighth_slice_at_5c_and_a_quarter_at_10c(self):
        thin = volumes_for("1", total=0.5e6)
        eighth = run([opp("1", fair=0.995, price=0.90)], {"1": base(-0.02)}, volumes=thin)     # 9.5c edge - 2c usual = 7.5c
        quarter = run([opp("1", fair=0.99, price=0.85)], {"1": base(-0.02)}, volumes=thin)     # 14c edge - 2c usual = 12c
        self.assertAlmostEqual(eighth.wants[0].want, 6_000 / 8)
        self.assertAlmostEqual(quarter.wants[0].want, 6_000 / 4)
        self.assertIn("slice x0.125: volume 0.5M", eighth.rows[0].note)

    def test_low_volume_does_not_stop_a_held_position_from_unwinding(self):
        h = held("1", 4_000, fair=0.905)
        plan = run([], {"1": base()}, [h], [fast_lot("1", h.shares)], volumes=VolumeStore())
        self.assertEqual([(o.action, o.book) for o in plan.sells], [("sell", "fast")])


class Sizing(unittest.TestCase):
    def test_a_held_position_is_topped_up_only_to_the_slice(self):
        h = held("1", 4_000)
        plan = run([opp("1")], {"1": base()}, [h], [fast_lot("1", h.shares)])
        self.assertAlmostEqual(plan.wants[0].want, 2_000)

    def test_a_position_at_its_slice_gets_no_buy_and_says_why(self):
        h = held("1", 6_000)
        plan = run([opp("1")], {"1": base()}, [h], [fast_lot("1", h.shares)])
        self.assertEqual(plan.wants, [])
        self.assertIn("no buy (limit: the position slice)", plan.rows[-1].note)

    def test_the_fast_book_budget_limits_new_positions(self):
        crowd = [held(str(i), 9_000) for i in range(2, 5)]                   # 27% of equity already in the fast book
        lots = [fast_lot(h.market_id, h.shares) for h in crowd]
        plan = run([opp("1")], {"1": base()}, crowd, lots)
        self.assertAlmostEqual(plan.wants[0].want, 3_000)                    # the 30% budget minus 27%

    def test_an_empty_budget_opens_nothing(self):
        crowd = [held(str(i), 10_000) for i in range(2, 5)]
        plan = run([opp("1")], {"1": base()}, crowd, [fast_lot(h.market_id, h.shares) for h in crowd])
        self.assertEqual(plan.wants, [])
        self.assertIn("no room to open a position", plan.rows[-1].note)


class Unwind(unittest.TestCase):
    def test_a_position_that_has_caught_up_sells_only_the_fast_shares(self):
        h = held("1", 10_000, fair=0.95, sell=0.90)                           # 5c edge on a 5c usual gap: caught up
        plan = run([], {"1": base(-0.05)}, [h], [Lot("hold", "1", "no", 5_000, 0.9, 1.0), fast_lot("1", 2_000)])
        self.assertEqual([(o.action, o.book, o.shares, o.price) for o in plan.sells], [("sell", "fast", 2_000, 0.90)])
        self.assertIn("caught up", plan.sells[0].reason)

    def test_a_position_with_fast_part_left_is_held(self):
        h = held("1", 10_000, fair=0.985, sell=0.90)
        plan = run([], {"1": base(-0.05)}, [h], [fast_lot("1", h.shares)])
        self.assertEqual((plan.sells, plan.rows[0].note), ([], "holds"))

    def test_the_time_stop_sells_after_24_hours(self):
        h = held("1", 10_000, fair=0.985, sell=0.90)
        plan = run([], {"1": base(-0.05)}, [h], [fast_lot("1", h.shares, age_hours=25)])
        self.assertIn("time stop", plan.sells[0].reason)

    def test_a_negative_edge_is_left_to_the_hold_books_whole_position_exit(self):
        h = held("1", 10_000, fair=0.85, sell=0.90)
        plan = run([], {"1": base(-0.05)}, [h], [fast_lot("1", h.shares)])
        self.assertEqual(plan.sells, [])

    def test_a_market_being_sold_is_not_bought_again_in_the_same_loop(self):
        h = held("1", 10_000, fair=0.95, sell=0.90)
        plan = run([opp("1", fair=0.995)], {"1": base(-0.05)}, [h], [fast_lot("1", h.shares)])
        self.assertEqual((len(plan.sells), plan.wants), (1, []))


class Swap(unittest.TestCase):
    """Five fast positions at a 3c usual gap; the candidate has 6.5c of fast part on a 0.90 price (7.2% return)."""
    baselines = {str(m): base(-0.03) for m in range(1, 7)}

    def full_book(self, fairs):
        holdings = [held(str(i), 4_000, fair=f) for i, f in zip(range(2, 7), fairs)]
        return holdings, [fast_lot(h.market_id, h.shares) for h in holdings]

    def test_a_better_candidate_swaps_out_the_worst_held_position(self):
        holdings, lots = self.full_book([0.945, 0.985, 0.985, 0.985, 0.985])           # the first has 1.5c left (1.7% return)
        plan = run([opp("1", fair=0.995)], self.baselines, holdings, lots)
        self.assertEqual([w.opp.market_id for w in plan.wants], ["1"])
        self.assertEqual([(v.market_id, r.startswith("swap")) for v, r in plan.exits], [("2", True)])
        self.assertEqual([(o.action, o.book, o.market_id) for o in plan.sells], [("sell", "fast", "2")])

    def test_no_swap_when_the_gain_is_under_the_swap_margin(self):
        holdings, lots = self.full_book([0.99] * 5)                                     # 6c left: 6.7% against the candidate's 7.2%
        plan = run([opp("1", fair=0.995)], self.baselines, holdings, lots)
        self.assertEqual((plan.wants, plan.exits), ([], []))
        self.assertIn("book full", plan.rows[-1].note)

    def test_a_free_slot_needs_no_swap(self):
        h = held("2", 4_000)
        plan = run([opp("1")], {"1": base(), "2": base()}, [h], [fast_lot("2", h.shares)])
        self.assertEqual((len(plan.wants), plan.exits), (1, []))


class MergedPlan(unittest.TestCase):
    def setUp(self):
        self.races = {"R1": {"outcomes": {"D": {}, "R": {}}, "rules_checked": False,
                             "contracts": [{"market_id": "1", "pays_if": "D", "sig_exchange_id": "x", "title": "t",
                                            "yes": {**empty_quote(), "bid": 0.09, "ask": 0.10, "bid_size": 1e6, "ask_size": 1e6}}]}}
        self.fair = {("R1", "D"): Fair(0.01, "t", 2), ("R1", "R"): Fair(0.99, "t", 2)}

    def plan(self, baselines, holdings=(), ledger=None):
        portfolio = Portfolio(EQUITY - sum(h.value for h in holdings), list(holdings), 0.0)
        return make_plan_v2(self.races, self.fair, portfolio, Cooldown(0), baselines, ledger or Ledger(), volumes_for("1"), NOW)

    def test_a_fast_market_is_bought_into_the_fast_book(self):
        plan = self.plan({"1": base(-0.03, structural=False)})                           # 8c edge, 3c usual: 5c beyond it
        self.assertEqual([(o.action, o.book, o.market_id) for o in plan.buys], [("buy", "fast", "1")])
        self.assertIn("fast book", plan.buys[0].reason)
        self.assertEqual(depth_targets(plan, 6), ["1"])

    def test_a_structural_market_with_no_fast_part_is_only_a_hold_buy(self):
        structural = Baseline(-0.08, "no", 0.08, 0.064, True, 192, 0.07, 0.08)           # the whole 8c edge is the usual gap
        plan = self.plan({"1": structural})
        self.assertEqual([o.book for o in plan.buys], ["hold"])

    def test_fast_buys_come_before_hold_buys(self):
        structural = Baseline(-0.03, "no", 0.03, 0.024, True, 192, 0.03, 0.03)           # structural at 3c AND 5c of fast part
        plan = self.plan({"1": structural})
        self.assertEqual([o.book for o in plan.buys][0], "fast")

    def test_a_caught_up_fast_position_is_sold_and_its_market_is_read_for_depth(self):
        h = Holding("R1", "1", "no", "D", 10_000 / 0.91, 10_000, 0.91, 1e6, 0.96)         # 5c edge on a 5c usual gap
        ledger = Ledger([Lot("fast", "1", "no", 10_000 / 0.91, 0.9, NOW - HOURS)])
        plan = self.plan({"1": base(-0.05)}, [h], ledger)
        self.assertEqual([(o.action, o.book) for o in plan.sells], [("sell", "fast")])
        self.assertEqual(depth_targets(plan, 6)[0], "1")

    def test_one_exit_when_the_edge_is_negative_even_if_the_fast_book_holds_it(self):
        h = Holding("R1", "1", "no", "D", 10_000 / 0.99, 10_000, 0.99, 1e6, 0.95)         # fair 0.95 below the 0.99 sell price
        ledger = Ledger([Lot("fast", "1", "no", 10_000 / 0.99, 0.9, NOW - HOURS)])
        plan = self.plan({"1": base(-0.05)}, [h], ledger)
        self.assertEqual([(o.action, o.book) for o in plan.sells], [("sell", "hold")])


if __name__ == "__main__":
    unittest.main()
