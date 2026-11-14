"""Offline tests for patient exits: the fast plan, the PatientBook and LiveTrader's resting orders (fake SIG client only).
Run: python3 -m unittest discover -s tests -t ."""
import time
import unittest
from datetime import datetime
from dataclasses import replace
from unittest import mock

import config
import display
import fast
import trading
from books import Ledger
from liquidity import Volume, VolumeStore
from orders import Order
from patient import PatientBook
from portfolio import Portfolio
from tests.test_fast import EQUITY, NOW, HOURS, OVERDUE_HOURS, base, fast_lot, held, opp, races_for
from tests.test_live_trader import Base, RACES, FakeClient, placed, position, sell
from trader import DryRunTrader, LiveTrader

PATIENT_ON = mock.patch.object(config, "PATIENT_EXITS", True)
WEEK = 1_000_000          # SUSQies traded over the last 7 days: a big order is 40k


RIVAL = opp("9", fair=0.95, price=0.30)   # a fast entry 60c beyond a 5c usual gap (z 2.7): a swap of any position here still pays


def volumes(week=WEEK, market="1"):
    return VolumeStore({m: Volume(10e6, 50_000, NOW, week) for m in (market, RIVAL.market_id)})


def position_with_book(left_fair=0.97, shares_value=4_000, asks=((0.905, 100), (0.91, 200)), bids=((0.90, 50_000),)):
    """A fast NO position whose fast part left is (fair - 0.90) - 5c usual gap: 2c at the default fair of 0.97."""
    h = held("1", shares_value, fair=left_fair, sell=0.90)
    return replace(h, sell_ladder=list(bids), rest_ladder=list(asks))


def plan(h, patient=None, vols=None, lots=None, baselines=None, candidates=()):
    portfolio = Portfolio(EQUITY - h.value, [h], 0.0)
    return fast.fast_plan(list(candidates), baselines or {"1": base(-0.05), RIVAL.market_id: base(-0.05)},
                          races_for("1", *(c.market_id for c in candidates)), portfolio,
                          Ledger(lots or [fast_lot("1", h.shares)]), __import__("cooldown").Cooldown(0), vols or volumes(), NOW, patient)


class FastExits(unittest.TestCase):
    def test_with_the_switch_off_a_fast_part_of_two_cents_is_not_an_exit(self):
        self.assertEqual(plan(position_with_book()).exits, [])

    def test_with_the_switch_on_a_fast_part_below_three_cents_rests_the_sell_above_the_bid(self):
        with PATIENT_ON:
            p = plan(position_with_book(), PatientBook())
        self.assertEqual([e.kind for e in p.exits], ["caught_up"])
        [order] = p.sells                                              # one order, never two
        self.assertTrue(order.resting)
        self.assertEqual((order.book, order.action, order.side, order.rest_kind), ("fast", "sell", "no", "patient"))
        self.assertGreater(order.price, 0.90)                         # above the bid
        self.assertEqual(order.shares, int(position_with_book().shares))

    def test_a_position_resting_for_a_patient_exit_stays_in_the_report_with_its_timer(self):
        h = position_with_book()
        book = PatientBook()
        with PATIENT_ON:
            first = plan(h, book)
            for i, order in enumerate(first.sells):
                book.add(f"7{i}", order)
            row = plan(h, book).rows[0]                                      # every share is reserved now
        self.assertEqual(plan(h, book).sells, [])
        self.assertEqual(row.label, "R1 NO 1")
        self.assertAlmostEqual(row.resting_left, (config.FAST_MAX_HOURS * 0.9 + config.PATIENT_MAX_MINUTES / 60) * HOURS)   # to the time stop, plus the hour
        self.assertAlmostEqual(row.held, h.value)
        self.assertRegex(row.note, r"^[\d,]+ resting at 0\.\d+$")
        self.assertEqual((row.order_kind, display.status(row)[:14]), ("patient", "patient exit, "))

    def test_the_resting_price_is_the_farthest_a_big_order_still_fills(self):
        h = position_with_book(shares_value=3_500)
        with PATIENT_ON:
            [order] = plan(h, PatientBook(), vols=volumes(week=100_000)).sells       # a big order is 4,000 (4% pinned in tests)
        self.assertEqual(order.price, 0.955)
        ahead = sum(p * q for p, q in h.rest_ladder if p <= order.price)
        self.assertLessEqual(ahead + order.shares * order.price, 4_000 + 1e-6)
        higher = order.price + 0.005
        self.assertGreater(sum(p * q for p, q in h.rest_ladder if p <= higher) + order.shares * higher, 4_000)

    def test_a_three_percent_big_order_rests_closer_to_the_bid(self):
        h = position_with_book(shares_value=2_500)
        with PATIENT_ON, mock.patch.object(config, "BIG_ORDER_WEEK_SHARE", 0.03):
            [order] = plan(h, PatientBook(), vols=volumes(week=100_000)).sells       # a big order is 3,000
        self.assertLessEqual(sum(p * q for p, q in h.rest_ladder if p <= order.price) + order.shares * order.price, 3_000 + 1e-6)
        self.assertLess(order.price, 0.995)

    def test_an_order_bigger_than_the_big_order_rests_one_tick_above_the_bid(self):
        h = position_with_book(shares_value=60_000)
        with PATIENT_ON:
            order = plan(h, PatientBook()).sells[0]
        self.assertEqual(order.price, 0.905)

    def test_without_book_levels_or_week_volume_a_position_that_is_not_caught_up_by_the_1c_rule_waits(self):
        h = position_with_book()
        with PATIENT_ON:
            self.assertEqual(plan(replace(h, rest_ladder=None), PatientBook()).sells, [])
            waiting = plan(h, PatientBook(), vols=volumes(week=None))
        self.assertEqual(waiting.sells, [])
        self.assertIn("cannot rest yet", waiting.rows[0].note)
        self.assertTrue(waiting.rows[0].waiting)
        self.assertIsNone(waiting.rows[0].resting_left)

    def test_without_book_levels_a_position_that_is_caught_up_by_the_1c_rule_sells_at_the_bid(self):
        h = position_with_book(left_fair=0.955)                          # 0.5c of fast part left
        with PATIENT_ON:
            order = plan(replace(h, rest_ladder=None), PatientBook()).sells[0]
        self.assertFalse(order.resting)
        self.assertEqual(order.price, 0.90)

    def test_a_position_whose_patience_ran_out_sells_at_the_bid(self):
        book = PatientBook()
        book._exhausted.add(("1", "no"))
        with PATIENT_ON:
            order = plan(position_with_book(), book).sells[0]
        self.assertFalse(order.resting)

    def test_a_resting_exit_lasts_until_the_time_stop_plus_the_patient_hour(self):
        h = position_with_book()
        with PATIENT_ON:
            order = plan(h, PatientBook()).sells[0]
        self.assertAlmostEqual(order.rest_until, NOW + (config.FAST_MAX_HOURS * 0.9) * HOURS + config.PATIENT_MAX_MINUTES * 60)

    def test_the_time_stop_rests_for_the_patient_hour_before_selling_at_the_bid(self):
        h = position_with_book(left_fair=0.99)                           # plenty of fast part left, but the lot is old
        old = fast_lot("1", h.shares, age_hours=OVERDUE_HOURS)
        with PATIENT_ON:
            p = plan(h, PatientBook(), lots=[old])
        self.assertEqual([e.kind for e in p.exits], ["time_stop"])
        self.assertTrue(p.sells[0].resting)
        self.assertAlmostEqual(p.sells[0].rest_until, NOW + config.PATIENT_MAX_MINUTES * 60)

    def test_a_time_stop_that_cannot_rest_sells_at_the_bid_at_once(self):
        h = replace(position_with_book(left_fair=0.99), rest_ladder=None)
        with PATIENT_ON:
            p = plan(h, PatientBook(), lots=[fast_lot("1", h.shares, age_hours=OVERDUE_HOURS)])
        self.assertFalse(p.sells[0].resting)
        self.assertEqual(p.sells[0].price, 0.90)

    def test_shares_that_already_rest_are_not_sold_again_and_the_market_is_not_bought(self):
        h = position_with_book()
        book = PatientBook()
        book.add(7, Order("sell", "no", "R1", "1", int(h.shares), 0.95, 0.97, "r", "fast", rest_until=NOW + 3600))
        with PATIENT_ON:
            p = plan(h, book)
        self.assertEqual((p.exits, p.sells), ([], []))
        with PATIENT_ON:
            buy = fast.fast_plan([opp("1", fair=0.995)], {"1": base(-0.05)}, races_for("1"), Portfolio(EQUITY - h.value, [h], 0.0),
                                 Ledger([fast_lot("1", h.shares)]), __import__("cooldown").Cooldown(0), volumes(), NOW, book)
        self.assertEqual(buy.wants, [])

    def test_a_negative_edge_is_left_to_the_urgent_exit_even_with_the_switch_on(self):
        h = position_with_book(left_fair=0.85)                           # fair below the sell price
        with PATIENT_ON:
            self.assertEqual(plan(h, PatientBook()).exits, [])


class SwapPricing(unittest.TestCase):
    baselines = {str(m): base(-0.03) for m in range(1, 7)}

    def full_book(self, ladder, others=0.985):
        fairs = [0.945] + [others] * 4
        holdings = [held(str(i), 4_000, fair=f) for i, f in zip(range(2, 7), fairs)]
        holdings[0] = replace(holdings[0], sell_ladder=ladder)
        return holdings, [fast_lot(h.market_id, h.shares) for h in holdings]

    def swap(self, ladder, others=0.985):
        holdings, lots = self.full_book(ladder, others)
        portfolio = Portfolio(EQUITY - sum(h.value for h in holdings), holdings, 0.0)
        return fast.fast_plan([opp("1", fair=0.995)], self.baselines, races_for(*"123456"), portfolio, Ledger(lots),
                              __import__("cooldown").Cooldown(0), VolumeStore({"1": Volume(10e6, 50_000, NOW, WEEK)}), NOW)

    def test_a_swap_sells_into_the_bids_near_the_best_at_their_average_price(self):
        p = self.swap([(0.90, 3_000), (0.895, 3_000), (0.80, 9_000)])
        order = p.sells[0]
        self.assertEqual((p.exits[0].kind, order.market_id), ("swap", "2"))
        self.assertEqual(order.price, 0.895)                              # limited at the worst level used
        self.assertEqual(order.shares, int(4_000 / 0.90))                 # the whole position fits inside the band

    def test_a_thin_book_near_the_best_bid_makes_the_swap_too_small_to_do(self):
        p = self.swap([(0.90, 100), (0.80, 9_000)], others=0.99)         # only 100 shares within 1c of the best bid
        self.assertEqual((p.exits, p.sells), ([], []))

    def test_the_average_price_is_used_for_the_held_fast_part(self):
        h = replace(held("2", 4_000, fair=0.945), sell_ladder=[(0.90, 10), (0.895, 5_000)])
        at_best = fast.held_part(replace(h, sell_ladder=None), base(-0.03))
        at_average = fast.held_part(h, base(-0.03))
        self.assertGreater(at_average, at_best)                           # a lower average price leaves more fast part to earn
        sold = int(h.shares)
        self.assertAlmostEqual(trading.sell_fill(h)[1], (10 * 0.90 + (sold - 10) * 0.895) / sold, places=6)


class SwapOut(unittest.TestCase):
    """A full book of five fast positions at a 3c usual gap; the candidate has 6.5c of fast part on a 0.90 price (7.2% return)."""
    baselines = {str(m): base(-0.03) for m in range(1, 7)}

    def book_of(self, fairs, resting=True):
        holdings = [replace(held(str(i), 4_000, fair=f), sell_ladder=[(0.90, 50_000)], rest_ladder=[(0.905, 100)])
                    for i, f in zip(range(2, 7), fairs)]
        book = PatientBook()
        for i, h in enumerate(holdings if resting else ()):
            book.add(i, Order("sell", "no", h.race, h.market_id, int(h.shares), 0.95, h.fair, "r", "fast", rest_until=NOW + 3600))
        return holdings, book

    def plan(self, holdings, book):
        portfolio = Portfolio(EQUITY - sum(h.value for h in holdings), holdings, 0.0)
        vols = VolumeStore({str(m): Volume(10e6, 50_000, NOW, WEEK) for m in range(1, 7)})
        with PATIENT_ON:
            return fast.fast_plan([opp("1", fair=0.995)], self.baselines, races_for(*"123456"), portfolio,
                                  Ledger([fast_lot(h.market_id, h.shares) for h in holdings]), __import__("cooldown").Cooldown(0), vols, NOW, book)

    def test_resting_positions_keep_their_slots_and_the_worst_is_swapped_out_by_a_better_edge(self):
        holdings, book = self.book_of([0.945] + [0.955] * 4)             # 1.5c left (1.7%) and 2.5c left (2.8%), all resting
        p = self.plan(holdings, book)
        self.assertEqual([w.opp.market_id for w in p.wants], ["1"])
        self.assertEqual(p.swaps, [("2", "no")])
        self.assertEqual(p.sells, [])                                     # sold by steps from the next loop
        self.assertIn("sold by steps from the next loop", p.exits[0].reason)
        self.assertEqual(next(r for r in p.rows if r.label == "R2 NO 2").order_kind, "swap")

    def test_a_full_book_of_resting_positions_none_beaten_by_the_margin_lets_nothing_in(self):
        holdings, book = self.book_of([0.99] * 5)                         # 6c left: 6.7% against the candidate's 7.2%
        p = self.plan(holdings, book)
        self.assertEqual((p.wants, p.swaps), ([], []))
        self.assertIn("book full", p.rows[-1].note)

    def test_a_patient_exit_about_to_rest_keeps_its_slot_and_a_swap_replaces_it(self):
        holdings, book = self.book_of([0.95] + [0.985] * 4, resting=False)   # the first has 2c left: it would rest a patient exit now
        p = self.plan(holdings, book)
        self.assertEqual(p.swaps, [("2", "no")])
        self.assertEqual(p.sells, [])                                     # its patient exit is not placed
        self.assertEqual([w.opp.market_id for w in p.wants], ["1"])


class Steps(unittest.TestCase):
    """A swapped-out position sold by steps down the bids."""
    bids = ((0.90, 1_000), (0.895, 2_000), (0.88, 50_000))

    def stepping_book(self):
        book = PatientBook()
        book.start_steps([("1", "no")])
        return book

    def test_the_first_step_rests_at_the_best_bid_for_five_minutes(self):
        book = self.stepping_book()
        with PATIENT_ON:
            p = plan(position_with_book(left_fair=0.99, bids=self.bids), book, candidates=[RIVAL])    # whatever its fast part
        self.assertEqual([e.kind for e in p.exits], ["step"])
        order = p.sells[0]
        self.assertEqual((order.price, order.shares, order.rest_until), (0.90, int(position_with_book().shares), NOW + config.SWAP_STEP_MINUTES * 60))
        book.add(8, order)
        self.assertEqual(book.step_price("1", "no"), 0.90)
        self.assertEqual((order.rest_kind, book.orders[8].kind), ("step", "step"))

    def test_the_next_step_is_the_next_bid_level_down_or_a_tick_lower_without_levels(self):
        book = self.stepping_book()
        book._stepping[("1", "no")] = 0.90
        with PATIENT_ON:
            self.assertEqual(plan(position_with_book(bids=self.bids), book, candidates=[RIVAL]).sells[0].price, 0.895)
            book._stepping[("1", "no")] = 0.895
            self.assertEqual(plan(position_with_book(bids=self.bids), book, candidates=[RIVAL]).sells[0].price, 0.88)
            self.assertEqual(plan(replace(position_with_book(), sell_ladder=None), book, candidates=[RIVAL]).sells[0].price, 0.89)

    def test_a_partly_filled_position_steps_with_what_is_left(self):
        h = position_with_book(shares_value=1_000)
        book = self.stepping_book()
        with PATIENT_ON:
            order = plan(h, book, lots=[fast_lot("1", h.shares)], candidates=[RIVAL]).sells[0]
        self.assertEqual(order.shares, int(h.shares))

    def test_the_report_status_says_swap_exit(self):
        row = fast.FastRow("R1 NO 1", 0.05, 0.01, 4_000, 0.0, "n", order_kind="swap")
        self.assertEqual(display.status(row), "swap exit")
        self.assertEqual(display.status(replace(row, resting_left=180)), "swap exit, 3m left")
        self.assertEqual(display.status(replace(row, order_kind="patient", resting_left=180)), "patient exit, 3m left")
        self.assertEqual(display.status(replace(row, order_kind="early", resting_left=23 * 3600 + 600)), "early exit, 23h 10m left")
        self.assertEqual(display.status(replace(row, order_kind="early", waiting=True)), "early exit, waiting")
        self.assertEqual(display.status(replace(row, order_kind="patient")), "-")             # sold at the bid: nothing rests

    def test_a_stepping_position_with_a_negative_edge_is_left_to_the_hold_exit(self):
        with PATIENT_ON:
            self.assertEqual(plan(position_with_book(left_fair=0.85), self.stepping_book(), candidates=[RIVAL]).sells, [])


class FakeTrader:
    """What PatientBook needs from a trader."""
    def __init__(self, open_orders=None, fills=None, cancel_ok=True):
        self.open, self.fills, self.cancel_ok, self.cancelled = open_orders or {}, fills or {}, cancel_ok, []

    def open_resting(self):
        return dict(self.open)

    def fill_of(self, order_id):
        return self.fills.get(order_id)

    def cancel_resting(self, order_id):
        if self.cancel_ok:
            self.cancelled.append(order_id)
        return self.cancel_ok


class Book(Base):
    """PatientBook.review and sync (logs go to a temp folder)."""
    def setUp(self):
        super().setUp()
        self.h = position_with_book()
        self.lots = [fast_lot("1", self.h.shares)]
        self.until = fast.patient_deadline(Ledger(self.lots), self.h, NOW)    # the time stop plus the patient hour
        self.order = Order("sell", "no", "R1", "1", int(self.h.shares), 0.95, 0.97, "r", "fast", rest_until=self.until)

    def review(self, book, trader, h=None, now=NOW + 60, lots=None, baselines=None):
        h = h or self.h
        return book.review(trader, Portfolio(EQUITY - h.value, [h], 0.0), Ledger(lots or self.lots), baselines or {"1": base(-0.05)}, now, 1)

    def test_a_resting_order_that_still_makes_sense_stays(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, self.order)
        with PATIENT_ON:
            self.assertEqual(self.review(book, trader), [])
        self.assertEqual((trader.cancelled, book.reserved("1", "no")), ([], self.order.shares))

    def test_it_is_cancelled_when_patience_runs_out_and_the_next_exit_sells_at_the_bid(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, self.order)
        with PATIENT_ON:
            self.review(book, trader, now=self.until + 1)
        self.assertEqual(trader.cancelled, [7])
        self.assertTrue(book.exhausted("1", "no"))
        self.assertEqual(book.reserved("1", "no"), 0)

    def test_it_is_cancelled_when_the_edge_turns_negative(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, self.order)
        with PATIENT_ON:
            self.review(book, trader, h=position_with_book(left_fair=0.85))
        self.assertEqual(trader.cancelled, [7])
        self.assertFalse(book.exhausted("1", "no"))                       # an urgent exit, not a used-up patience

    def test_it_keeps_resting_through_the_time_stop_and_whatever_the_fast_part_then_is(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, self.order)
        with PATIENT_ON:                                                  # past the time stop, with 4c of fast part left again
            self.review(book, trader, h=position_with_book(left_fair=0.99), lots=[fast_lot("1", self.h.shares, age_hours=OVERDUE_HOURS)])
        self.assertEqual(trader.cancelled, [])

    def test_it_is_cancelled_when_the_fast_part_is_back_above_the_trigger(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, self.order)
        with PATIENT_ON:
            self.review(book, trader, h=position_with_book(left_fair=0.99))       # 4c left again
        self.assertEqual(trader.cancelled, [7])

    def test_a_cancel_that_fails_keeps_the_shares_reserved_and_is_retried(self):
        book, trader = PatientBook(), FakeTrader(cancel_ok=False)
        book.add(7, self.order)
        with PATIENT_ON:
            notes = self.review(book, trader, now=self.until + 1)
        self.assertIn("could not cancel", notes[0])
        self.assertEqual(book.reserved("1", "no"), self.order.shares)

    def test_a_swapped_out_patient_exit_is_cancelled_and_its_patience_is_not_used_up(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, self.order)
        book.start_steps([("1", "no")])
        with PATIENT_ON:
            notes = self.review(book, trader)
        self.assertEqual(trader.cancelled, [7])
        self.assertIn("swapped out", notes[0])
        self.assertFalse(book.exhausted("1", "no"))
        self.assertEqual(book.step_keys(), {("1", "no")})

    def step(self, book):
        book.start_steps([("1", "no")])
        book.add(8, replace(self.order, rest_until=NOW + config.SWAP_STEP_MINUTES * 60, rest_kind="step"))

    def test_a_step_rests_its_five_minutes_whatever_the_fast_part_then_moves_on(self):
        book, trader = PatientBook(), FakeTrader()
        self.step(book)
        with PATIENT_ON:
            self.review(book, trader, h=position_with_book(left_fair=0.99))     # 4c left again: a patient exit would be cancelled
            self.assertEqual(trader.cancelled, [])
            notes = self.review(book, trader, now=NOW + config.SWAP_STEP_MINUTES * 60 + 1)
        self.assertEqual(trader.cancelled, [8])
        self.assertIn("next bid down", notes[0])
        self.assertIn("swap step", notes[0])
        self.assertFalse(book.exhausted("1", "no"))
        self.assertEqual(book.step_price("1", "no"), self.order.price)

    def test_a_step_is_cancelled_when_the_edge_turns_negative(self):
        book, trader = PatientBook(), FakeTrader()
        self.step(book)
        with PATIENT_ON:
            self.review(book, trader, h=position_with_book(left_fair=0.85))
        self.assertEqual(trader.cancelled, [8])

    def test_the_steps_end_when_the_position_is_sold(self):
        book = PatientBook()
        book.start_steps([("1", "no")])
        with PATIENT_ON:
            book.review(FakeTrader(), Portfolio(EQUITY, [], 0.0), Ledger([]), {}, NOW, 1)
        self.assertEqual(book.step_keys(), set())

    def test_sync_follows_partial_fills_and_drops_closed_orders(self):
        book = PatientBook()
        book.add(7, self.order)
        book.sync(FakeTrader(open_orders={7: 100}), 1)
        self.assertEqual(book.reserved("1", "no"), 100)
        notes = book.sync(FakeTrader(open_orders={}, fills={7: self.order.shares}), 2)
        self.assertEqual(book.reserved("1", "no"), 0)
        self.assertIn("closed", notes[0])
        self.assertEqual([e["event"] for e in self.events()], ["filled"])


class LiveResting(Base):
    """A resting sell is placed once, left alone by the next loop's cancel, and cancelled only on request."""
    def resting_sell(self):
        return replace(sell(market="274", side="no", shares=100, price=0.95), rest_until=time.time() + 3 * 3600)

    def router(self, method, path, body):
        if (method, path) == ("POST", "/orders"):
            return placed(order_id=41, open_=True)
        if method == "DELETE":
            return 200, {}
        if method == "GET" and path.startswith("/orders/"):
            return 200, {"quantityFilled": 30}
        return 404, {}

    def test_a_resting_sell_is_not_waited_for_nor_cancelled_and_carries_an_expiry(self):
        client, trader, problems = self.run_trader(self.router, [self.resting_sell()], {}, [])
        self.assertEqual(problems, 0)
        self.assertEqual([(o.market_id, i) for o, i in self.execution.rested], [("274", 41)])
        self.assertEqual(client.sent("DELETE", "/orders"), [])
        self.assertIn(41, trader.resting)
        self.assertNotIn(41, trader.open_ids)
        expires = datetime.fromisoformat(client.sent("POST", "/orders")[0][2]["expirationDate"].replace("Z", "+00:00"))
        self.assertAlmostEqual(expires.timestamp(), time.time() + 3 * 3600 + 300, delta=5)    # five minutes after the bot gives up
        self.wait.assert_not_called()

    def test_the_next_loops_cancel_of_leftovers_leaves_it_alone(self):
        client, trader, _ = self.run_trader(self.router, [self.resting_sell()], {}, [])
        trader.cancel_open(2)
        self.assertEqual(client.sent("DELETE", "/orders"), [])
        self.assertTrue(trader.cancel_resting(41))
        self.assertEqual(len(client.sent("DELETE", "/orders/41")), 1)
        self.assertNotIn(41, trader.resting)

    def test_an_ordinary_order_is_still_waited_for_and_cancelled(self):
        client, trader, _ = self.run_trader(self.router, [sell(market="274", side="no", shares=100, price=0.90)], {}, [])
        self.assertEqual(len(client.sent("DELETE", "/orders/41")), 1)
        self.assertEqual(self.execution.rested, [])

    def test_open_resting_reads_what_is_left_and_fill_of_reads_a_closed_order(self):
        client, trader, _ = self.run_trader(self.router, [self.resting_sell()], {}, [])
        with mock.patch("trader.sig.fetch_open_orders", return_value=[{"id": 41, "quantity": 100, "quantityFilled": 30}, {"id": 9, "quantity": 5}]):
            self.assertEqual(trader.open_resting(), {41: 70})
        self.assertEqual(trader.fill_of(41), 30)

    def test_cancel_everything_clears_the_resting_orders(self):
        client, trader, _ = self.run_trader(self.router, [self.resting_sell()], {}, [])
        client.router = lambda m, p, b: (200, {"cancelled": 1})
        trader.cancel_everything()
        self.assertEqual(trader.resting, {})


class AcrossLoops(Base):
    """The whole cycle with the dry-run trader: rest, wait, run out of patience, sell at the bid."""
    def test_rest_then_wait_then_sell_at_the_bid(self):
        h, book, trader = position_with_book(), PatientBook(), DryRunTrader()
        lots = [fast_lot("1", h.shares)]

        def loop(now):
            portfolio = Portfolio(EQUITY - h.value, [h], 0.0)
            notes = book.sync(trader, 1) + book.review(trader, portfolio, Ledger(lots), {"1": base(-0.05)}, now, 1)
            p = fast.fast_plan([], {"1": base(-0.05)}, races_for("1"), portfolio, Ledger(lots), __import__("cooldown").Cooldown(0), volumes(), now, book)
            with mock.patch("trader.orders.place_order"):
                execution = trader.execute(p.sells, 30, {}, 1, 1_000)
            for order, order_id in execution.rested:
                book.add(order_id, order)
            return p, notes

        with PATIENT_ON:
            first, _ = loop(NOW)
            self.assertTrue(first.sells[0].resting)
            second, _ = loop(NOW + 60)                                    # still resting: nothing is planned or sold twice
            self.assertEqual(second.sells, [])
            until = first.sells[0].rest_until
            third, notes = loop(until + 60)                               # patience over: cancelled, then sold at the bid
            self.assertIn("patience over", notes[0])
            self.assertFalse(third.sells[0].resting)
            self.assertEqual(third.sells[0].price, 0.90)
            fourth, _ = loop(until + 120)     # exhausted: no new resting order for this position
            self.assertFalse(fourth.sells[0].resting)


class SwapAcrossLoops(Base):
    """With the dry-run trader: a resting patient exit is swapped out (RIVAL is the better entry), cancelled, sold at the best bid, then
    one bid level lower."""
    def test_swap_then_cancel_then_step_down_the_bids(self):
        h = position_with_book(bids=((0.90, 1_000), (0.895, 2_000)))
        book, trader, lots = PatientBook(), DryRunTrader(), [fast_lot("1", h.shares)]

        def loop(now, holdings):
            portfolio = Portfolio(EQUITY - sum(x.value for x in holdings), holdings, 0.0)
            notes = book.sync(trader, 1) + book.review(trader, portfolio, Ledger(lots), {"1": base(-0.05)}, now, 1)
            p = fast.fast_plan([RIVAL], {"1": base(-0.05), "9": base(-0.05)}, races_for("1", "9"), portfolio, Ledger(lots),
                               __import__("cooldown").Cooldown(0), volumes(), now, book)
            with mock.patch("trader.orders.place_order"):
                execution = trader.execute(p.sells, 30, {}, 1, 1_000)
            for order, order_id in execution.rested:
                book.add(order_id, order)
            return p, notes

        with PATIENT_ON:
            first, _ = loop(NOW, [h])
            self.assertTrue(first.sells[0].resting)                       # a patient exit above the bid
            book.start_steps([("1", "no")])                               # what main does with a swap of this position
            second, notes = loop(NOW + 60, [h])
            self.assertIn("swapped out", notes[0])
            self.assertEqual((second.sells[0].price, second.sells[0].rest_until), (0.90, NOW + 60 + config.SWAP_STEP_MINUTES * 60))
            self.assertEqual(loop(NOW + 120, [h])[0].sells, [])          # the step rests: nothing sold twice
            third, notes = loop(NOW + 60 + config.SWAP_STEP_MINUTES * 60 + 1, [h])
            self.assertIn("next bid down", notes[0])
            self.assertEqual(third.sells[0].price, 0.895)
            loop(NOW + 3600, [])                                          # sold
            self.assertEqual(book.step_keys(), set())


EARLY_H = config.FAST_MAX_HOURS / 2        # the early phase, whatever FAST_MAX_HOURS is
EARLY_ON = mock.patch.multiple(config, PATIENT_EXITS=True, EARLY_EXITS=True, EARLY_EXIT_HOURS=EARLY_H)


def early_listing(shares, price=0.92, entered=NOW - HOURS):
    """The early-exit listing of position_with_book(): structural value 0.97 - 5c usual gap = 0.92."""
    return Order("sell", "no", "R1", "1", shares, price, 0.97, "early", "fast", rest_until=entered + EARLY_H * HOURS, rest_kind="early")


class EarlyExits(unittest.TestCase):
    """From its first entry a fast position lists all its fast shares at the structural value, then rests a patient exit."""
    def test_the_structural_value_is_fair_less_the_usual_gap_on_the_tick(self):
        h = position_with_book()
        self.assertEqual(fast.structural_price(h, base(-0.05)), 0.92)
        self.assertEqual(fast.structural_price(replace(h, fair=0.9712), base(-0.05)), 0.92)
        self.assertEqual(fast.structural_price(h, base(-0.005)), 0.97)                     # a usual gap inside the noise is none
        self.assertEqual(fast.structural_price(replace(h, fair=0.999), base(-0.0)), 0.995)  # never above the top tick
        self.assertIsNone(fast.structural_price(replace(h, fair=None), base(-0.05)))

    def test_a_position_lists_all_its_shares_at_most_three_ticks_above_the_best_ask(self):
        h = position_with_book(left_fair=0.975)                                           # 2.5c of fast part left (under the 3c entry edge)
        with EARLY_ON:
            p = plan(h, PatientBook(), lots=[fast_lot("1", h.shares, age_hours=1)])
        self.assertEqual([e.kind for e in p.exits], ["early"])
        [order] = p.sells
        self.assertEqual((order.price, order.shares, order.rest_kind), (0.92, int(h.shares), "early"))   # structural 0.925, best ask 0.905 + 3 ticks
        self.assertAlmostEqual(order.rest_until, NOW - HOURS + EARLY_H * HOURS)
        self.assertEqual((p.rows[0].order_kind, display.status(p.rows[0])), ("early", "-"))    # nothing rests yet

    def test_a_position_that_is_still_a_buy_is_not_listed(self):
        h = position_with_book(left_fair=0.99)                                            # 4c of fast part left: the entry edge is 3c
        with EARLY_ON:
            p = plan(h, PatientBook(), lots=[fast_lot("1", h.shares, age_hours=1)])
        self.assertEqual((p.exits, p.sells), ([], []))
        self.assertEqual(p.rows[0].note, "holds")

    def test_it_goes_in_front_of_a_liquidity_wall(self):
        h = position_with_book(left_fair=0.975, asks=((0.905, 2_000), (0.91, 2_000), (0.915, 50_000)))   # a wall at 0.915
        with EARLY_ON:
            [order] = plan(h, PatientBook(), lots=[fast_lot("1", h.shares, age_hours=1)]).sells
        self.assertEqual(order.price, 0.91)

    def test_it_waits_without_book_levels(self):
        h = replace(position_with_book(), rest_ladder=None)
        with EARLY_ON:
            p = plan(h, PatientBook(), lots=[fast_lot("1", h.shares, age_hours=1)])
        self.assertEqual(p.sells, [])
        self.assertIn("book levels", p.rows[0].note)

    def test_without_a_fair_value_it_waits_and_says_so(self):
        h = replace(position_with_book(), fair=None)
        with EARLY_ON:
            p = plan(h, PatientBook(), lots=[fast_lot("1", h.shares, age_hours=1)])
        self.assertEqual(p.sells, [])
        self.assertIn("no fair value", p.rows[0].note)
        self.assertEqual(display.status(p.rows[0]), "early exit, waiting")

    def test_a_listed_position_is_topped_up_and_gets_no_second_sell(self):
        h = position_with_book(shares_value=2_000)
        book = PatientBook()
        book.add(7, early_listing(int(h.shares) - 100))                                   # 100 new shares since the listing
        with EARLY_ON:
            p = fast.fast_plan([opp("1", fair=0.995)], {"1": base(-0.05)}, races_for("1"), Portfolio(EQUITY - h.value, [h], 0.0),
                               Ledger([fast_lot("1", h.shares, age_hours=1)]), __import__("cooldown").Cooldown(0), volumes(), NOW, book)
        self.assertEqual([w.opp.market_id for w in p.wants], ["1"])
        self.assertEqual(p.sells, [])
        self.assertEqual((p.rows[0].order_kind, display.status(p.rows[0])[:12]), ("early", "early exit, "))

    def test_after_the_early_phase_a_patient_exit_rests_until_the_time_stop_whatever_the_fast_part(self):
        h = position_with_book(left_fair=0.99)
        lots = [fast_lot("1", h.shares, age_hours=EARLY_H + 1)]
        with EARLY_ON:
            p = plan(h, PatientBook(), lots=lots)
        self.assertIn("early exit is over", p.exits[0].reason)
        [order] = p.sells
        self.assertEqual(order.rest_kind, "patient")
        self.assertAlmostEqual(order.rest_until, fast.patient_deadline(Ledger(lots), h, NOW))
        self.assertGreater(order.price, 0.90)

    def test_with_the_switch_off_nothing_is_listed(self):
        with PATIENT_ON:
            p = plan(position_with_book(left_fair=0.99), PatientBook(), lots=[fast_lot("1", position_with_book().shares, age_hours=1)])
        self.assertEqual((p.exits, p.sells), ([], []))


class EarlyBook(Base):
    """PatientBook.review of an early-exit listing."""
    def setUp(self):
        super().setUp()
        self.h = position_with_book()
        self.lots = [fast_lot("1", self.h.shares, age_hours=1)]

    def review(self, book, trader, h=None, now=NOW + 60):
        h = h or self.h
        with EARLY_ON:
            return book.review(trader, Portfolio(EQUITY - h.value, [h], 0.0), Ledger(self.lots), {"1": base(-0.05)}, now, 1)

    def listed(self, cancel_ok=True):
        book, trader = PatientBook(), FakeTrader(cancel_ok=cancel_ok)
        book.add(7, early_listing(int(self.h.shares)))
        return book, trader

    def test_it_stays_while_the_structural_value_is_within_a_cent(self):
        book, trader = self.listed()
        self.review(book, trader, h=replace(self.h, fair=0.975))                          # structural 0.925: 0.5c away
        self.assertEqual(trader.cancelled, [])

    def test_it_is_re_listed_when_the_structural_value_moves_more_than_a_cent(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, replace(early_listing(int(self.h.shares), price=0.905), fair=0.955))   # listed when fair was 0.955: structural 0.905
        notes = self.review(book, trader, h=replace(self.h, fair=0.97))                    # structural 0.92: 1.5c away
        self.assertEqual(trader.cancelled, [7])
        self.assertIn("from 0.905 to 0.920", notes[0])
        self.assertIn("early exit", notes[0])
        with EARLY_ON:                                                                     # the same loop lists it again: still under the cap
            p = plan(replace(self.h, fair=0.97), book, lots=self.lots)
        self.assertEqual([(o.price, o.shares) for o in p.sells], [(0.92, int(self.h.shares))])

    def test_a_listing_held_under_the_cap_is_not_re_listed_while_fair_is_steady(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, replace(early_listing(int(self.h.shares), price=0.91), fair=0.975))   # listed 1.5c under its 0.925 structural value
        self.review(book, trader, h=replace(self.h, fair=0.975))
        self.assertEqual(trader.cancelled, [])

    def test_a_listing_is_cancelled_when_the_fast_part_is_back_at_the_entry_edge(self):
        book, trader = self.listed()
        notes = self.review(book, trader, h=replace(self.h, fair=0.99))                   # 4c left: the bot buys, it does not sell
        self.assertEqual(trader.cancelled, [7])
        self.assertIn("held back", notes[0])
        with EARLY_ON:
            self.assertEqual(plan(replace(self.h, fair=0.99), book, lots=self.lots).sells, [])

    def test_new_shares_re_list_everything_in_one_order(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, early_listing(int(self.h.shares) - 100))
        self.assertIn("100 new shares", self.review(book, trader)[0])
        with EARLY_ON:
            self.assertEqual([o.shares for o in plan(self.h, book, lots=self.lots).sells], [int(self.h.shares)])

    def test_a_failed_cancel_never_leads_to_a_second_order(self):
        book, trader = PatientBook(), FakeTrader(cancel_ok=False)
        book.add(7, early_listing(int(self.h.shares) - 100))
        self.review(book, trader)
        with EARLY_ON:
            self.assertEqual(plan(self.h, book, lots=self.lots).sells, [])

    def test_it_is_cancelled_without_a_fair_value_or_with_a_negative_edge(self):
        for h, why in ((replace(self.h, fair=None), "no fair value"), (position_with_book(left_fair=0.85), "negative")):
            book, trader = self.listed()
            self.assertIn(why, self.review(book, trader, h=h)[0])

    def test_at_the_end_of_the_early_phase_it_gives_way_to_the_patient_exit(self):
        book, trader = self.listed()
        notes = self.review(book, trader, now=NOW - HOURS + EARLY_H * HOURS + 1)
        self.assertIn("early exit over", notes[0])
        self.assertFalse(book.exhausted("1", "no"))

    def test_a_patient_exit_after_the_early_phase_is_not_cancelled_for_its_fast_part(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(7, Order("sell", "no", "R1", "1", int(self.h.shares), 0.95, 0.99, "p", "fast", rest_until=NOW + 3600, rest_kind="patient"))
        self.review(book, trader, h=position_with_book(left_fair=0.99))                   # 4c of fast part again
        self.assertEqual(trader.cancelled, [])

    def test_shutdown_cancels_every_resting_sell(self):
        book, trader = self.listed()
        book.cancel_all(trader, 1)
        self.assertEqual((trader.cancelled, book.orders), ([7], {}))


class DryRun(unittest.TestCase):
    def test_a_dry_run_pretends_a_resting_order_rests_until_cancelled(self):
        trader = DryRunTrader()
        o = Order("sell", "no", "R1", "1", 100, 0.95, 0.97, "r", "fast", rest_until=NOW + 3600)
        with mock.patch("trader.logbook.log"), mock.patch("trader.orders.place_order"):
            execution = trader.execute([o], 30, {}, 1, 1_000)
        order_id = execution.rested[0][1]
        self.assertEqual(trader.open_resting(), {order_id: 100})
        self.assertTrue(trader.cancel_resting(order_id))
        self.assertEqual(trader.open_resting(), {})


if __name__ == "__main__":
    unittest.main()
