"""Offline tests for the hold book: buckets, eligibility, picks, sizing and the v2 plan. Run: python3 -m unittest discover -s tests -t ."""
import math
from dataclasses import replace
import unittest
from unittest import mock

import config
import hold
from books import Freeze, Ledger, Lot
from cooldown import Cooldown
from gapmodel import Baseline
from orders import Order
from liquidity import VolumeStore
from planner import make_plan, make_plan_v2
from portfolio import Holding, Portfolio
from races import empty_quote
from strategy import Fair, Opportunity

EQUITY = 100_000.0
NOW = 1_700_000_000.0


def opp(market, fair=0.97, price=0.90, side="no", race=None, wins="D", size=1e6, sources=2):
    return Opportunity(race or f"R{market}", market, "D", side, price, fair, size, wins, sources)


def at_z(market, z, fair=0.99, **kw):
    """A candidate whose z (edge / sqrt(fair (1 - fair))) is `z`."""
    return opp(market, fair=fair, price=fair - z * math.sqrt(fair * (1 - fair)), **kw)


def structural(side="no", size=0.07):
    return Baseline(0.07 if side == "yes" else -0.07, side, size, 0.056, True, 192, 0.06, 0.07)


def race(*codes, checked=False, bid=0.09, ask=0.10):
    return {"outcomes": {c: {} for c in codes}, "rules_checked": checked,
            "contracts": [{"market_id": "1", "pays_if": "D", "yes": {**empty_quote(), "bid": bid, "ask": ask}}]}


def holding(market, value, race_id=None, fair=0.97, sell=0.90, side="no", wins="D"):
    return Holding(race_id or f"R{market}", market, side, wins, value / sell, value, sell, 1e6, fair)


class Buckets(unittest.TestCase):
    def test_a_position_keeps_its_bucket_inside_the_band(self):
        self.assertEqual(hold.bucket_for(0.89), "favourite")
        self.assertEqual(hold.bucket_for(0.91, "favourite"), "favourite")        # 1c over the line, inside the 2c band
        self.assertEqual(hold.bucket_for(0.93, "favourite"), "sure bet")         # 3c over: moves up
        self.assertEqual(hold.bucket_for(0.91, "sure bet"), "sure bet")
        self.assertEqual(hold.bucket_for(0.88, "sure bet"), "sure bet")          # exactly 2c under the line: still inside
        self.assertEqual(hold.bucket_for(0.87, "sure bet"), "favourite")         # 3c under: moves down
        self.assertEqual(hold.bucket_for(0.30, None), "unlikely")


class Eligible(unittest.TestCase):
    races = {"R1": race("D", "R")}

    def run_eligible(self, c, baseline, cooldown=None, races=None):
        kept = hold.eligible([c], {"1": baseline} if baseline else {}, races or self.races, cooldown or Cooldown(0))
        return kept, c.decision

    def test_a_structural_candidate_on_the_gaps_side_qualifies(self):
        kept, why = self.run_eligible(opp("1", race="R1", fair=0.97, price=0.90), structural("no"))
        self.assertEqual((len(kept), why), (1, None))

    def test_each_filter_gives_its_reason(self):
        c = lambda **kw: opp("1", race="R1", **{"fair": 0.97, "price": 0.90, **kw})
        self.assertEqual(self.run_eligible(c(), None)[1], "not structural")
        self.assertEqual(self.run_eligible(c(), Baseline(None, None, 0, 0, False, 5))[1], "not structural")
        self.assertEqual(self.run_eligible(c(side="yes"), structural("no"))[1], "the other side of the usual gap")
        self.assertIn("below 3c", self.run_eligible(c(price=0.95), structural("no"))[1])
        self.assertIn("spread", self.run_eligible(c(), structural("no"), races={"R1": race("D", "R", bid=0.05, ask=0.10)})[1])
        cooldown = Cooldown(300)
        cooldown.record([Order("sell", "no", "R1", "1", 1, 0.9, 0.97, "t")])
        self.assertEqual(self.run_eligible(c(), structural("no"), cooldown)[1], "sold within the cooldown")

    def test_a_big_edge_qualifies_without_a_structural_gap(self):
        not_structural = Baseline(-0.04, "no", 0.04, 0.032, False, 192, 0.01, 0.07)
        c = lambda price: opp("1", race="R1", fair=0.98, price=price)
        kept, why = self.run_eligible(c(0.88), not_structural)
        self.assertEqual((len(kept), why), (1, None))
        self.assertEqual(self.run_eligible(c(0.89), not_structural)[1], "not structural")      # 9c of edge: under the 10c big edge
        self.assertEqual(self.run_eligible(c(0.88), None)[1], "not structural")                # no gap history: never
        self.assertEqual(self.run_eligible(c(0.88), Baseline(None, None, 0, 0, False, 5))[1], "not structural")
        self.assertEqual(self.run_eligible(opp("1", race="R1", side="yes", fair=0.98, price=0.88), not_structural)[1],
                         "the other side of the usual gap")                                    # the side check still applies

    def test_a_race_with_an_independent_needs_its_rules_checked(self):
        three = {"R1": race("D", "R", "I")}
        self.assertIn("rules not checked", self.run_eligible(opp("1", race="R1"), structural("no"), races=three)[1])
        checked = {"R1": race("D", "R", "I", checked=True)}
        self.assertEqual(len(self.run_eligible(opp("1", race="R1"), structural("no"), races=checked)[0]), 1)

    def test_equivalent_bets_keep_the_cheaper(self):
        a, b = opp("1", race="R1", side="no", price=0.90), opp("2", race="R1", side="yes", price=0.88)
        kept = hold.eligible([a, b], {"1": structural("no"), "2": structural("yes")}, {"R1": race("D", "R")}, Cooldown(0))
        self.assertEqual([c.market_id for c in kept], ["2"])


class Picks(unittest.TestCase):
    def test_the_best_z_in_each_bucket_wins(self):
        sure = [opp("1", fair=0.99, price=0.90), opp("2", fair=0.99, price=0.93)]         # ROI 10.0% and 6.5%
        fav = [opp("3", fair=0.80, price=0.70)]
        picks, _ = hold.choose_picks(sure + fav, Ledger())
        self.assertEqual({b: [c.market_id for c in cs] for b, cs in picks.items()}, {"sure bet": ["1"], "favourite": ["3"]})
        self.assertEqual(sorted(picks), ["favourite", "sure bet"])                       # empty buckets have no pick

    def test_the_previous_pick_stays_unless_beaten_by_the_switch_margin(self):
        ledger = Ledger(picks={"sure bet": [("2", "no")]})
        lead = config.HOLD_SWITCH_Z
        close = [at_z("1", 1.0 + lead - 0.01), at_z("2", 1.0)]
        self.assertEqual(hold.choose_picks(close, ledger)[0]["sure bet"][0].market_id, "2")
        clear = [at_z("1", 1.0 + lead + 0.01), at_z("2", 1.0)]
        self.assertEqual(hold.choose_picks(clear, ledger)[0]["sure bet"][0].market_id, "1")
        self.assertEqual(ledger.picks, {"sure bet": [("1", "no")]})                        # and the ledger remembers it

    def test_a_held_bet_keeps_its_slot_against_a_new_bet_without_the_swap_margin(self):
        ledger = Ledger([Lot("hold", "2", "no", 35_000, 0.85, NOW, grandfathered=True)], picks={"sure bet": [("1", "no")]})
        held = hold.held_bets([holding("2", 30_000, fair=0.99)], ledger)
        z = hold.held_z(held["sure bet"][0])
        picks, _ = hold.choose_picks([at_z("1", z + config.HOLD_SWAP_Z - 0.03), at_z("2", z)], ledger, held)
        self.assertEqual([c.market_id for c in picks["sure bet"]], ["2"])
        self.assertEqual(ledger.picks, {"sure bet": [("2", "no")]})

    def test_a_previous_pick_that_is_no_longer_eligible_is_replaced(self):
        ledger = Ledger(picks={"sure bet": [("9", "no")]})
        self.assertEqual(hold.choose_picks([opp("1", fair=0.99, price=0.90)], ledger)[0]["sure bet"][0].market_id, "1")


class RiskNeedsMoreReturn(unittest.TestCase):
    """A riskier bucket only picks a candidate whose ROI beats the best pick of every more certain bucket."""
    sure = lambda self, roi_price=0.90: opp("1", fair=0.99, price=roi_price)         # ROI 10.0% at 0.90
    fav = lambda self, market, fair, price: opp(market, fair=fair, price=price)

    def test_a_favourite_that_returns_less_than_the_sure_bet_is_not_picked(self):
        picks, skipped = hold.choose_picks([self.sure(), self.fav("2", 0.80, 0.74)], Ledger())   # 10.0% vs 8.1%
        self.assertEqual(list(picks), ["sure bet"])
        self.assertIn("does not beat the sure bet pick", skipped["favourite"])

    def test_a_favourite_that_returns_more_is_picked(self):
        picks, skipped = hold.choose_picks([self.sure(), self.fav("2", 0.80, 0.70)], Ledger())   # 10.0% vs 14.3%
        self.assertEqual(sorted(picks), ["favourite", "sure bet"])
        self.assertEqual(skipped, {})

    def test_an_equal_return_is_not_enough(self):
        picks, skipped = hold.choose_picks([self.sure(), self.fav("2", 0.80, 0.80 / 1.1)], Ledger())   # favourite also 10.0%
        self.assertEqual(list(picks), ["sure bet"])
        self.assertIn("favourite", skipped)

    def test_the_hurdle_is_the_best_pick_of_any_more_certain_bucket_not_just_the_next_one(self):
        cands = [self.sure(), self.fav("2", 0.80, 0.74),                                          # favourite 8.1%: dropped
                 self.fav("3", 0.50, 0.45)]                                                       # coinflip 11.1% beats the sure bet's 10.0%
        picks, skipped = hold.choose_picks(cands, Ledger())
        self.assertEqual(sorted(picks), ["coinflip", "sure bet"])
        low = [self.sure(), self.fav("2", 0.80, 0.74), self.fav("3", 0.50, 0.465)]                # coinflip 7.5%: dropped too
        picks, skipped = hold.choose_picks(low, Ledger())
        self.assertEqual(list(picks), ["sure bet"])
        self.assertEqual(sorted(skipped), ["coinflip", "favourite"])

    def test_with_no_sure_bet_the_next_bucket_down_is_picked_freely(self):
        picks, _ = hold.choose_picks([self.fav("2", 0.80, 0.74)], Ledger())
        self.assertEqual(list(picks), ["favourite"])

    def test_a_dropped_bucket_is_forgotten_by_the_ledger_and_labelled_in_the_decision(self):
        ledger = Ledger(picks={"favourite": [("2", "no")]})
        low = self.fav("2", 0.80, 0.74)
        hold.choose_picks([self.sure(), low], ledger)
        self.assertNotIn("favourite", ledger.picks)
        self.assertIn("does not beat", low.decision)

    def test_the_report_says_why_a_bucket_with_candidates_stays_cash(self):
        picks, skipped = hold.choose_picks([self.sure(), self.fav("2", 0.80, 0.74)], Ledger())
        rows = hold.hold_wants(picks, skipped, Portfolio(EQUITY, [], 0.0), Ledger())
        self.assertIn("does not beat the sure bet pick", rows[3][1].note)


class MinimumZ(unittest.TestCase):
    """HOLD_MIN_Z: every bucket needs the same edge per unit of risk (tests pin it to 0; each case here sets it)."""
    LEVELS = (0.10, 0.25, 0.40)

    def picks(self, *cands, ledger=None):
        return hold.choose_picks(list(cands), ledger or Ledger())

    def test_every_bucket_needs_the_same_z(self):
        for level in self.LEVELS:
            with self.subTest(min_z=level), mock.patch.object(config, "HOLD_MIN_Z", level):
                for fair in (0.95, 0.75, 0.5, 0.3, 0.1):
                    bucket = hold.bucket_of(fair)[0]
                    above, below = at_z("1", level + 0.01, fair=fair), at_z("1", level - 0.01, fair=fair)
                    if above.price < 0.01:
                        continue                                                     # no price is low enough for this edge
                    self.assertIn(bucket, self.picks(above)[0], (bucket, level))
                    self.assertNotIn(bucket, self.picks(below)[0], (bucket, level))

    def test_the_roi_floor_is_the_roi_at_the_minimum_z_and_climbs_faster_than_linearly(self):
        for level in self.LEVELS:
            with self.subTest(min_z=level), mock.patch.object(config, "HOLD_MIN_Z", level), mock.patch.object(config, "HOLD_MIN_EDGE", 0.0):
                for fair in (0.95, 0.75, 0.5):
                    self.assertAlmostEqual(hold.roi_floor(fair), at_z("1", level, fair=fair).expected_return)
                floors = [hold.roi_floor(p) for p in (0.9, 0.7, 0.5, 0.3)]                # equal steps down in probability
                steps = [b - a for a, b in zip(floors, floors[1:])]
                self.assertTrue(all(b > a > 0 for a, b in zip(steps, steps[1:])), floors)
                self.assertEqual(hold.roi_floor(0.001), math.inf)                        # too unlikely for any price

    def test_the_roi_floor_also_holds_the_minimum_edge_where_it_is_more_than_the_z_edge(self):
        for floor in (0.03, 0.05, 0.08):
            with self.subTest(min_edge=floor), mock.patch.object(config, "HOLD_MIN_EDGE", floor):
                for fair in (0.99, 0.95, 0.5):
                    edge = max(config.HOLD_MIN_Z * math.sqrt(fair * (1 - fair)), floor)
                    self.assertAlmostEqual(hold.roi_floor(fair), edge / (fair - edge))

    def test_a_longshot_with_a_big_roi_is_dropped_and_a_safer_favourite_kept(self):
        for level in self.LEVELS[1:]:
            with self.subTest(min_z=level), mock.patch.object(config, "HOLD_MIN_Z", level):
                longshot = at_z("1", level - 0.05, fair=0.27)                           # the SENATE-CONTROL 153 kind
                favourite = at_z("2", level + 0.05, fair=0.894)                         # the TX-15 kind
                self.assertGreater(longshot.expected_return, favourite.expected_return)  # ROI alone would take the longshot
                picks, skipped = self.picks(longshot, favourite)
                self.assertEqual({b: [c.market_id for c in cs] for b, cs in picks.items()}, {"favourite": ["2"]})
                self.assertIn(f"is under the {level:.2f} minimum", longshot.decision)
                self.assertIn(f"is under the {level:.2f} minimum", skipped["unlikely"])

    def test_a_safer_sure_bet_outranks_one_with_more_roi(self):
        safer, riskier = opp("1", fair=0.99, price=0.86), opp("2", fair=0.93, price=0.80)
        self.assertGreater(riskier.expected_return, safer.expected_return)
        self.assertGreater(safer.z, riskier.z)
        self.assertEqual(self.picks(safer, riskier)[0]["sure bet"][0].market_id, "1")

    def test_a_held_pick_that_falls_below_the_minimum_is_no_longer_picked(self):
        for level in self.LEVELS:
            with self.subTest(min_z=level), mock.patch.object(config, "HOLD_MIN_Z", level):
                ledger = Ledger([Lot("hold", "2", "no", 100, 0.7, NOW)], picks={"favourite": [("2", "no")]})
                picks, _ = self.picks(at_z("2", level - 0.01, fair=0.80), ledger=ledger)
                self.assertEqual((picks, ledger.picks), ({}, {}))


def slots(n):
    return mock.patch.object(config, "HOLD_SLOTS", {name: n for name, *_ in config.BUCKETS})


class Slots(unittest.TestCase):
    """HOLD_SLOTS: up to N positions per bucket, each an equal slot of the bucket's share (tests pin 1; each case here sets N)."""
    COUNTS = (3, 5)

    def test_a_bucket_picks_up_to_its_slots_by_z_and_names_the_rest(self):
        for n in self.COUNTS:
            with self.subTest(slots=n), slots(n):
                cands = [at_z(str(i), 1.5 - 0.1 * i) for i in range(n + 1)]              # z 1.5, 1.4, ... one more than fits
                picks, _ = hold.choose_picks(list(reversed(cands)), Ledger())
                self.assertEqual([c.market_id for c in picks["sure bet"]], [str(i) for i in range(n)])
                self.assertIn(f"not in the top {n} new bets by z", cands[n].decision)

    def test_each_pick_is_one_slot_and_the_empty_slots_stay_cash(self):
        for n in self.COUNTS:
            with self.subTest(slots=n), slots(n):
                rows = hold.hold_wants({"sure bet": [opp("1"), opp("2")]}, {}, Portfolio(EQUITY, [], 0.0), Ledger())
                slot = config.HOLD_BOOK_PCT * hold.BUCKET_SHARE["sure bet"] / n
                sure = [(w, st) for w, st in rows if st.bucket == "sure bet"]
                self.assertEqual([w.opp.market_id for w, _ in sure if w], ["1", "2"])
                self.assertTrue(all(abs(w.target - slot * EQUITY) < 1e-6 for w, _ in sure if w))
                empty_want, empty = sure[-1]
                self.assertIsNone(empty_want)
                self.assertAlmostEqual(empty.share, slot * (n - 2))
                self.assertIn(f"{n - 2} of {n} slots empty: cash", empty.note)

    def held(self, *markets, fair=0.99):
        ledger = Ledger([Lot("hold", m, "no", 1_000, 0.85, NOW, grandfathered=(m == markets[-1])) for m in markets])  # the last: a hand buy
        return ledger, hold.held_bets([holding(m, 850, fair=fair) for m in markets], ledger)

    def test_held_bets_take_the_slots_first(self):
        ledger, held = self.held("1", "2")
        challenger = at_z("3", hold.held_z(held["sure bet"][-1]) + config.HOLD_SWAP_Z - 0.03)   # beats neither by the swap margin
        with slots(2):
            picks, _ = hold.choose_picks([at_z("1", 1.0), at_z("2", 0.9), challenger], ledger, held)
        self.assertEqual(sorted(c.market_id for c in picks["sure bet"]), ["1", "2"])
        self.assertIn("under 0.30 z above the weakest held", challenger.decision)

    def test_a_held_bet_that_does_not_qualify_keeps_its_slot_without_a_top_up(self):
        ledger, held = self.held("1")
        cands = [at_z("1", 0.20), at_z("2", 0.30), at_z("3", 0.40)]
        with slots(2), mock.patch.object(config, "HOLD_MIN_Z", 0.25):
            picks, skipped = hold.choose_picks(cands, ledger, held)
            rows = hold.hold_wants(picks, skipped, Portfolio(EQUITY, [], 0.0), ledger, held, cands)
        self.assertEqual([c.market_id for c in picks["sure bet"]], ["3"])          # one slot is the held bet's
        sure = [(w, st) for w, st in rows if st.bucket == "sure bet"]
        self.assertEqual([(w is None, (st.pick or st.holding).market_id) for w, st in sure], [(False, "3"), (True, "1")])
        self.assertIn("held, no top-up: z 0.20 is under the 0.25 minimum", sure[1][1].note)

    def test_an_equivalent_contract_is_the_held_bets_top_up(self):
        ledger, held = self.held("1")                                                 # a No on R1 that pays if D wins
        same_bet = opp("2", race="R1", side="yes", fair=0.99, price=0.85)             # a Yes on another R1 contract, also D
        other = at_z("3", hold.held_z(held["sure bet"][0]) + config.HOLD_SWAP_Z - 0.03)
        with slots(1):
            picks, _ = hold.choose_picks([same_bet, other], ledger, held)
        self.assertEqual([c.market_id for c in picks["sure bet"]], ["2"])

    def test_a_held_bet_without_a_candidate_keeps_its_slot_and_says_why(self):
        ledger, held = self.held("1")
        with slots(3):
            rows = hold.hold_wants({}, {}, Portfolio(EQUITY, [], 0.0), ledger, held, [])
        sure = [st for _, st in rows if st.bucket == "sure bet"]
        self.assertEqual(sure[0].holding.market_id, "1")
        self.assertIn("not a candidate", sure[0].note)
        self.assertIn("2 of 3 slots empty", sure[1].note)

    def test_the_riskier_bucket_must_beat_the_best_roi_of_the_safer_picks(self):
        sure = [opp("1", fair=0.99, price=0.90), opp("2", fair=0.99, price=0.86)]          # ROI 10.0% and 15.1%
        with slots(2):
            for price, picked in ((0.71, False), (0.68, True)):                              # favourite ROI 12.7% and 17.6%
                fav = opp("3", fair=0.80, price=price)
                picks, skipped = hold.choose_picks(sure + [fav], Ledger())
                self.assertEqual("favourite" in picks, picked, price)
                if not picked:
                    self.assertIn("does not beat the sure bet pick's 15.1%", skipped["favourite"])


class HoldSwaps(unittest.TestCase):
    """HOLD_SWAP_Z: a new bet takes a held bet's slot (and the held bet is sold) with that much more z; loop 1 builds from the held bets."""
    MARGINS = (0.0, 0.10, config.HOLD_SWAP_Z, 0.5)

    def book(self, *fairs):
        """A hold book holding one No per fair value (markets 1, 2, ...), at a sell price of 0.90."""
        ledger = Ledger([Lot("hold", str(i), "no", 1_000, 0.85, NOW, grandfathered=True) for i, _ in enumerate(fairs, 1)])
        return ledger, hold.held_bets([holding(str(i), 900, fair=f) for i, f in enumerate(fairs, 1)], ledger)

    def test_a_new_bet_replaces_a_held_one_only_with_the_swap_margin_more_z(self):
        ledger, held = self.book(0.99)
        z = hold.held_z(held["sure bet"][0])
        for margin in self.MARGINS:
            with self.subTest(margin=margin), slots(1), mock.patch.object(config, "HOLD_SWAP_Z", margin):
                for extra, replaced in ((0.03, True), (-0.03, False)):
                    new = at_z("9", z + margin + extra)
                    picks, _ = hold.choose_picks([new, at_z("1", z)], ledger, held)
                    self.assertEqual([c.market_id for c in picks["sure bet"]], ["9"] if replaced else ["1"], (margin, extra))
                    self.assertEqual([(h.market_id, c.market_id) for h, c in hold.displaced(held, picks, ledger)],
                                     [("1", "9")] if replaced else [])

    def test_the_bets_over_the_slots_are_the_first_to_go(self):
        ledger, held = self.book(0.99, 0.95)                                           # market 2 has the lower z: over the one slot
        z2 = hold.held_z(held["sure bet"][1])
        with slots(1):
            picks, _ = hold.choose_picks([at_z("1", 1.0), at_z("2", 0.3), at_z("9", z2 + config.HOLD_SWAP_Z + 0.03)], ledger, held)
            self.assertEqual([h.market_id for h, _ in hold.displaced(held, picks, ledger)], ["2"])
            rows = hold.hold_wants(picks, {}, Portfolio(EQUITY, [], 0.0), ledger, held, [], {("R2", "D"): "swap: R9"})
        notes = {(st.pick or st.holding).market_id: st.note for _, st in rows if st.bucket == "sure bet" and (st.pick or st.holding)}
        self.assertIn("swap: R9: sold", notes["2"])

    def test_loop_one_builds_the_book_from_the_held_bets_alone(self):
        ledger, held = self.book(0.99)
        z = hold.held_z(held["sure bet"][0])
        great = at_z("9", z + 5.0)
        with slots(2):
            picks, _ = hold.choose_picks([great, at_z("1", z)], ledger, held, first_loop=True)
            self.assertEqual([c.market_id for c in picks["sure bet"]], ["1"])
            self.assertIn("loop 1: held bets only", great.decision)
            later, _ = hold.choose_picks([at_z("9", z + 5.0), at_z("1", z)], ledger, held)
        self.assertEqual(sorted(c.market_id for c in later["sure bet"]), ["1", "9"])        # a free slot: the new bet comes in at loop 2

    def test_a_frozen_bet_is_not_swapped_out_and_keeps_its_slot(self):
        ledger, held = self.book(0.99)
        ledger.frozen[("R1", "D")] = Freeze(NOW, NOW + 3600, "frozen: test")
        with slots(1):
            picks, _ = hold.choose_picks([at_z("9", 5.0), at_z("1", 1.0)], ledger, held)
        self.assertEqual([c.market_id for c in picks["sure bet"]], ["1"])

    def test_the_sale_is_the_hold_lots_only_and_skipped_for_dust_or_the_cooldown(self):
        ledger = Ledger([Lot("hold", "1", "no", 10_000, 0.85, NOW), Lot("fast", "1", "no", 3_000, 0.85, NOW)])
        h = holding("1", 11_700, fair=0.99)                                             # 13,000 shares
        held, new = hold.held_bets([h], ledger), at_z("9", 5.0)
        with slots(1):
            picks, _ = hold.choose_picks([new, at_z("1", 1.0)], ledger, held)
            [(view, why)] = hold.hold_swaps(held, picks, ledger, Portfolio(EQUITY, [h], 0.0), Cooldown(0))
            self.assertEqual((view.shares, view.market_id), (10_000, "1"))
            self.assertIn("swap: R9 NO 9", why)
            self.assertEqual(hold.hold_swaps(held, picks, ledger, Portfolio(EQUITY * 1_000, [h], 0.0), Cooldown(0)), [])    # dust
            cooled = Cooldown(300)
            cooled.record([Order("buy", "no", "R1", "1", 1, 0.9, 0.99, "t")])
            self.assertEqual(hold.hold_swaps(held, picks, ledger, Portfolio(EQUITY, [h], 0.0), cooled), [])


class PlanSwaps(unittest.TestCase):
    """make_plan_v2: R1 is held (No at a 0.90 sell price, z 0.90); R2 offers a No at 0.81 (z 1.81)."""
    def setUp(self):
        contract = lambda m, bid, ask: {"market_id": m, "pays_if": "D", "sig_exchange_id": "x", "title": "t",
                                        "yes": {**empty_quote(), "bid": bid, "ask": ask, "bid_size": 1e6, "ask_size": 1e6}}
        self.races = {f"R{m}": {"outcomes": {"D": {}, "R": {}}, "rules_checked": False, "contracts": [contract(m, bid, bid + 0.01)]}
                      for m, bid in (("1", 0.09), ("2", 0.19))}
        self.fair = {(f"R{m}", code): Fair(f, "t", 2) for m in "12" for code, f in (("D", 0.01), ("R", 0.99))}
        self.h = holding("1", 10_000, race_id="R1", fair=0.99, sell=0.90)
        self.ledger = lambda: Ledger([Lot("hold", "1", "no", self.h.shares, 0.85, NOW, bucket="sure bet")])
        self.baselines = {m: structural("no") for m in "12"}

    def plan(self, first_loop):
        return make_plan_v2(self.races, self.fair, Portfolio(EQUITY - 10_000, [self.h], 0.0), Cooldown(0), self.baselines, self.ledger(),
                            VolumeStore(), NOW, first_loop=first_loop)

    def test_from_loop_two_the_held_bet_is_sold_and_the_better_one_bought(self):
        plan = self.plan(first_loop=False)
        self.assertEqual([(o.action, o.market_id, o.book) for o in plan.sells], [("sell", "1", "hold")])
        self.assertIn("swap: R2 NO 2", plan.sells[0].reason)
        self.assertEqual([o.market_id for o in plan.buys], ["2"])
        self.assertIn("1", plan.fast_depth)                                             # its book is read for the sale

    def test_a_displaced_bet_is_read_even_when_its_sale_looks_like_dust_on_bulk_prices(self):
        self.h = replace(self.h, sell_depth=None)                                       # bulk prices carry no bid depth
        plan = self.plan(first_loop=False)
        self.assertEqual((plan.sells, "1" in plan.fast_depth), ([], True))

    def test_in_loop_one_nothing_new_is_bought_and_nothing_is_swapped(self):
        plan = self.plan(first_loop=True)
        self.assertEqual(plan.sells, [])
        self.assertNotIn("2", [o.market_id for o in plan.buys])


class Sizing(unittest.TestCase):
    def wants(self, picks, holdings=(), lots=(), cash=None):
        portfolio = Portfolio(cash if cash is not None else EQUITY - sum(h.value for h in holdings), list(holdings), 0.0)
        return hold.hold_wants(picks, {}, portfolio, Ledger(list(lots))), portfolio

    def test_every_bucket_gets_a_row_and_an_empty_one_stays_cash(self):
        rows, _ = self.wants({"sure bet": [opp("1")]})
        self.assertEqual([s.bucket for _, s in rows], ["longshot", "unlikely", "coinflip", "favourite", "sure bet"])
        self.assertIsNone(rows[0][0])
        self.assertIn(": cash", rows[0][1].note)

    def test_the_target_is_the_hold_share_of_the_bucket_times_equity(self):
        rows, _ = self.wants({"sure bet": [opp("1")], "favourite": [opp("2", fair=0.8, price=0.7)]})
        by = {s.bucket: w for w, s in rows if w}
        self.assertAlmostEqual(by["sure bet"].target, 0.3 * 0.40 * EQUITY)               # 12% of equity
        self.assertAlmostEqual(by["favourite"].target, 0.3 * 0.33 * EQUITY)              # 9.9%
        self.assertAlmostEqual(by["sure bet"].want, 12_000)

    def test_what_the_hold_book_already_holds_is_taken_off(self):
        h = holding("1", 5_000)
        rows, _ = self.wants({"sure bet": [opp("1")]}, [h], [Lot("hold", "1", "no", h.shares, 0.9, 1.0)])
        self.assertAlmostEqual(rows[4][0].want, 12_000 - 5_000)

    def test_a_position_above_its_target_gets_no_buy_and_names_the_limit(self):
        h = holding("1", 30_000)
        rows, _ = self.wants({"sure bet": [opp("1")]}, [h], [Lot("hold", "1", "no", h.shares, 0.9, 1.0)])
        want, status = rows[4]
        self.assertEqual(want.want, 0)
        self.assertIn("no buy", status.note)

    def test_the_position_cap_the_race_cap_and_the_hold_budget_each_limit_a_want(self):
        h = holding("1", 24_000)                                                          # the fast book holds this one
        fast = [Lot("fast", "1", "no", h.shares, 0.9, 1.0)]
        rows, _ = self.wants({"sure bet": [opp("1")]}, [h], fast)
        self.assertAlmostEqual(rows[4][0].want, 1_000)                                    # 25% position cap minus 24%
        crowded = [holding(str(i), 9_000) for i in range(2, 5)]
        owned = [Lot("hold", h2.market_id, "no", h2.shares, 0.9, 1.0) for h2 in crowded]  # 27% already in the hold book
        rows, _ = self.wants({"sure bet": [opp("1")]}, crowded, owned)
        self.assertAlmostEqual(rows[4][0].want, 3_000)                                    # the 30% hold budget minus 27%

    def test_an_over_budget_hold_book_buys_nothing_anywhere(self):
        big = [holding("8", 31_000), holding("9", 21_000)]
        owned = [Lot("hold", h.market_id, "no", h.shares, 0.9, 1.0, grandfathered=True) for h in big]
        rows, _ = self.wants({"favourite": [opp("2", fair=0.8, price=0.7)]}, big, owned)
        self.assertEqual(rows[3][0].want, 0)
        self.assertIn("hold book budget", rows[3][1].note)


class Plan(unittest.TestCase):
    def setUp(self):
        self.races = {"R1": {"outcomes": {"D": {}, "R": {}}, "rules_checked": False,
                             "contracts": [{"market_id": "1", "pays_if": "D", "sig_exchange_id": "x", "title": "t",
                                            "yes": {**empty_quote(), "bid": 0.09, "ask": 0.10, "bid_size": 1e6, "ask_size": 1e6}}]}}
        self.fair = {("R1", "D"): Fair(0.01, "t", 2), ("R1", "R"): Fair(0.99, "t", 2)}   # No on market 1 pays 99% at a price of 0.91

    def test_a_structural_market_is_bought_into_its_bucket(self):
        portfolio = Portfolio(EQUITY, [], 0.0)
        plan = make_plan_v2(self.races, self.fair, portfolio, Cooldown(0), {"1": structural("no")}, Ledger(), VolumeStore(), NOW)
        self.assertEqual([(o.action, o.side, o.market_id, o.book) for o in plan.buys], [("buy", "no", "1", "hold")])
        self.assertAlmostEqual(plan.buys[0].value, 12_000, delta=1)
        self.assertEqual(plan.buckets[-1].pick.market_id, "1")

    def test_without_a_structural_baseline_nothing_is_bought(self):
        plan = make_plan_v2(self.races, self.fair, Portfolio(EQUITY, [], 0.0), Cooldown(0), {}, Ledger(), VolumeStore(), NOW)
        self.assertEqual((plan.buys, plan.sells), ([], []))

    def test_a_negative_edge_position_is_still_sold(self):
        bad = holding("1", 10_000, race_id="R1", fair=0.80, sell=0.90)
        plan = make_plan_v2(self.races, self.fair, Portfolio(EQUITY - 10_000, [bad], 0.0), Cooldown(0), {}, Ledger(), VolumeStore(), NOW)
        self.assertEqual([(o.action, o.market_id) for o in plan.sells], [("sell", "1")])

    def test_v1_is_still_the_default(self):
        self.assertEqual(config.STRATEGY, "v1")
        self.assertEqual(make_plan(self.races, self.fair, Portfolio(EQUITY, [], 0.0), Cooldown(0)).buckets, [])


if __name__ == "__main__":
    unittest.main()
