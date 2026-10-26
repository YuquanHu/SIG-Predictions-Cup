"""Offline tests for the hold book: buckets, eligibility, picks, sizing and the v2 plan. Run: python3 -m unittest discover -s tests -t ."""
import unittest

import config
import hold
from books import Ledger, Lot
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
    def test_the_best_roi_in_each_bucket_wins(self):
        sure = [opp("1", fair=0.99, price=0.90), opp("2", fair=0.99, price=0.93)]         # ROI 10.0% and 6.5%
        fav = [opp("3", fair=0.80, price=0.70)]
        picks, _ = hold.choose_picks(sure + fav, Ledger())
        self.assertEqual({b: c.market_id for b, c in picks.items()}, {"sure bet": "1", "favourite": "3"})
        self.assertEqual(sorted(picks), ["favourite", "sure bet"])                       # empty buckets have no pick

    def test_the_previous_pick_stays_unless_beaten_by_the_switch_margin(self):
        ledger = Ledger(picks={"sure bet": ("2", "no")})
        close = [opp("1", fair=0.99, price=0.90), opp("2", fair=0.99, price=0.91)]       # 10.0% vs 8.8%: lead of 1.2%
        self.assertEqual(hold.choose_picks(close, ledger)[0]["sure bet"].market_id, "2")
        clear = [opp("1", fair=0.99, price=0.90), opp("2", fair=0.99, price=0.94)]       # 10.0% vs 5.3%: lead of 4.7%
        self.assertEqual(hold.choose_picks(clear, ledger)[0]["sure bet"].market_id, "1")
        self.assertEqual(ledger.picks, {"sure bet": ("1", "no")})                        # and the ledger remembers it

    def test_a_previous_pick_that_is_no_longer_eligible_is_replaced(self):
        ledger = Ledger(picks={"sure bet": ("9", "no")})
        self.assertEqual(hold.choose_picks([opp("1", fair=0.99, price=0.90)], ledger)[0]["sure bet"].market_id, "1")


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
        ledger = Ledger(picks={"favourite": ("2", "no")})
        low = self.fav("2", 0.80, 0.74)
        hold.choose_picks([self.sure(), low], ledger)
        self.assertNotIn("favourite", ledger.picks)
        self.assertIn("does not beat", low.decision)

    def test_the_report_says_why_a_bucket_with_candidates_stays_cash(self):
        picks, skipped = hold.choose_picks([self.sure(), self.fav("2", 0.80, 0.74)], Ledger())
        rows = hold.hold_wants(picks, skipped, Portfolio(EQUITY, [], 0.0), Ledger())
        self.assertIn("more risk for no more return", rows[3][1].note)


class Sizing(unittest.TestCase):
    def wants(self, picks, holdings=(), lots=(), cash=None):
        portfolio = Portfolio(cash if cash is not None else EQUITY - sum(h.value for h in holdings), list(holdings), 0.0)
        return hold.hold_wants(picks, {}, portfolio, Ledger(list(lots))), portfolio

    def test_every_bucket_gets_a_row_and_an_empty_one_stays_cash(self):
        rows, _ = self.wants({"sure bet": opp("1")})
        self.assertEqual([s.bucket for _, s in rows], ["longshot", "unlikely", "coinflip", "favourite", "sure bet"])
        self.assertIsNone(rows[0][0])
        self.assertIn("stays cash", rows[0][1].note)

    def test_the_target_is_the_hold_share_of_the_bucket_times_equity(self):
        rows, _ = self.wants({"sure bet": opp("1"), "favourite": opp("2", fair=0.8, price=0.7)})
        by = {s.bucket: w for w, s in rows if w}
        self.assertAlmostEqual(by["sure bet"].target, 0.3 * 0.40 * EQUITY)               # 12% of equity
        self.assertAlmostEqual(by["favourite"].target, 0.3 * 0.33 * EQUITY)              # 9.9%
        self.assertAlmostEqual(by["sure bet"].want, 12_000)

    def test_what_the_hold_book_already_holds_is_taken_off(self):
        h = holding("1", 5_000)
        rows, _ = self.wants({"sure bet": opp("1")}, [h], [Lot("hold", "1", "no", h.shares, 0.9, 1.0)])
        self.assertAlmostEqual(rows[4][0].want, 12_000 - 5_000)

    def test_a_position_above_its_target_gets_no_buy_and_names_the_limit(self):
        h = holding("1", 30_000)
        rows, _ = self.wants({"sure bet": opp("1")}, [h], [Lot("hold", "1", "no", h.shares, 0.9, 1.0)])
        want, status = rows[4]
        self.assertEqual(want.want, 0)
        self.assertIn("no buy", status.note)

    def test_the_position_cap_the_race_cap_and_the_hold_budget_each_limit_a_want(self):
        h = holding("1", 24_000)                                                          # the fast book holds this one
        fast = [Lot("fast", "1", "no", h.shares, 0.9, 1.0)]
        rows, _ = self.wants({"sure bet": opp("1")}, [h], fast)
        self.assertAlmostEqual(rows[4][0].want, 1_000)                                    # 25% position cap minus 24%
        crowded = [holding(str(i), 9_000) for i in range(2, 5)]
        owned = [Lot("hold", h2.market_id, "no", h2.shares, 0.9, 1.0) for h2 in crowded]  # 27% already in the hold book
        rows, _ = self.wants({"sure bet": opp("1")}, crowded, owned)
        self.assertAlmostEqual(rows[4][0].want, 3_000)                                    # the 30% hold budget minus 27%

    def test_an_over_budget_hold_book_buys_nothing_anywhere(self):
        big = [holding("8", 31_000), holding("9", 21_000)]
        owned = [Lot("hold", h.market_id, "no", h.shares, 0.9, 1.0, grandfathered=True) for h in big]
        rows, _ = self.wants({"favourite": opp("2", fair=0.8, price=0.7)}, big, owned)
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
