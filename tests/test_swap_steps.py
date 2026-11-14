"""Offline tests for a swap's step sale stopping once the swap no longer pays: each loop a swapped-out position is sold on only while
the best fast entry has FAST_SWAP_Z more z (of its fast part) than the position still has at its step's price. Every case is built
from config.FAST_SWAP_Z and run at several margins, so changing the margin does not break them. Run: python3 -m unittest discover -s tests -t ."""
import unittest
from dataclasses import replace
from unittest import mock

import config
import fast
from books import Ledger
from cooldown import Cooldown
from liquidity import Volume, VolumeStore
from orders import Order
from patient import PatientBook
from portfolio import Portfolio
from strategy import Opportunity
from tests.test_fast import EQUITY, NOW, base, fair_at_z, fast_lot, held, opp, races_for
from tests.test_patient_exits import PATIENT_ON, FakeTrader

USUAL = 0.05                                    # the usual gap of every market here
RIVAL = opp("2", fair=0.65, price=0.30)         # the best fast entry: 30c beyond the usual gap (z 0.63), so every margin below fits
RIVAL_Z = fast.part_z(RIVAL.fair, RIVAL.fair - RIVAL.price - USUAL)
BIDS = ((0.50, 50_000), (0.49, 50_000))          # the held position's bids: the first step sells at 0.50, the next at 0.49
MARGINS = sorted({config.FAST_SWAP_Z, 0.0, 0.05, 0.10, 0.25, 0.50})
CLEAR = 0.02                                    # how far each case sits from the margin
KEY = ("1", "no")
LOG = mock.patch("patient.logbook.log")


def fair_for(z, price):
    """The fair value at which the held position, sold at `price`, has a fast part of z `z`."""
    return fair_at_z(price, USUAL, z)


def position(fair):
    return replace(held("1", 4_000, fair=fair, sell=BIDS[0][0]), sell_ladder=list(BIDS))


def stepping(last=None):
    book = PatientBook()
    book.start_steps([KEY])
    if last is not None:
        book._stepping[KEY] = last
    return book


def plan(h, book, candidates=(RIVAL,)):
    markets = ["1"] + [c.market_id for c in candidates]
    portfolio = Portfolio(EQUITY - h.value, [h], 0.0)
    with PATIENT_ON:
        return fast.fast_plan(list(candidates), {m: base(-USUAL) for m in markets}, races_for(*markets), portfolio,
                              Ledger([fast_lot("1", h.shares)]), Cooldown(0), VolumeStore({m: Volume(10e6, 50_000, NOW) for m in markets}),
                              NOW, book)


def at(margin):
    return mock.patch.object(config, "FAST_SWAP_Z", margin)


class Margins(unittest.TestCase):
    def test_the_steps_go_on_while_the_best_entry_beats_the_position_by_the_margin(self):
        for margin in MARGINS:
            with self.subTest(margin=margin), at(margin):
                p = plan(position(fair_for(RIVAL_Z - margin - CLEAR, 0.50)), stepping())
                self.assertEqual(([e.kind for e in p.exits], p.sells[0].price, p.ended_steps), (["step"], 0.50, {}))

    def test_the_steps_end_and_the_position_is_kept_once_it_is_beaten_by_less(self):
        for margin in MARGINS:
            with self.subTest(margin=margin), at(margin):
                p = plan(position(fair_for(RIVAL_Z - margin + CLEAR, 0.50)), stepping())
                self.assertEqual((p.exits, p.sells), ([], []))
                self.assertIn("swap no longer pays", p.ended_steps[KEY])
                row = p.rows[0]
                self.assertEqual((row.label, row.order_kind), ("R1 NO 1", None))
                self.assertIn("swap no longer pays", row.note)

    def test_a_step_down_that_makes_the_position_worth_keeping_ends_the_swap(self):
        for margin in MARGINS:
            with self.subTest(margin=margin), at(margin):
                h = position(fair_for(RIVAL_Z - margin + CLEAR, 0.49))     # still beaten at 0.50, no longer at the next bid down
                self.assertEqual(plan(h, stepping()).ended_steps, {})
                self.assertIn(KEY, plan(h, stepping(last=0.50)).ended_steps)

    def test_a_resting_step_is_weighed_at_its_own_price(self):
        for margin in MARGINS:
            with self.subTest(margin=margin), at(margin):
                for z, ends in ((RIVAL_Z - margin - CLEAR, False), (RIVAL_Z - margin + CLEAR, True)):
                    h = position(fair_for(z, 0.49))
                    book = stepping()
                    book.add(8, Order("sell", "no", "R1", "1", int(h.shares), 0.49, h.fair, "step", "fast", rest_until=NOW + 300, rest_kind="step"))
                    p = plan(h, book)
                    self.assertEqual((KEY in p.ended_steps, p.sells), (ends, []))


class Rivals(unittest.TestCase):
    h = position(fair_for(0.0, 0.50))          # earns nothing beyond the usual gap: any real entry beats it

    def test_without_a_fast_entry_the_steps_end(self):
        self.assertIn("no fast entry", plan(self.h, stepping(), candidates=()).ended_steps[KEY])

    def test_the_same_bet_on_another_market_is_no_rival(self):
        same_bet = Opportunity("R1", "3", "D", "yes", RIVAL.price, RIVAL.fair, 1e6, self.h.wins_when, 2)
        self.assertIn("no fast entry", plan(self.h, stepping(), candidates=(same_bet,)).ended_steps[KEY])

    def test_a_position_without_a_fair_value_stops_selling(self):
        self.assertIn("no fair value", plan(replace(self.h, fair=None), stepping()).ended_steps[KEY])


class EndSteps(unittest.TestCase):
    def book_with_step(self):
        book = stepping(last=0.50)
        book.add(8, Order("sell", "no", "R1", "1", 100, 0.49, 0.6, "step", "fast", rest_until=NOW + 300, rest_kind="step"))
        return book

    def test_the_resting_step_is_cancelled_and_the_position_kept(self):
        book, trader = self.book_with_step(), FakeTrader(open_orders={8: 100})
        with LOG:
            notes = book.end_steps(trader, {KEY: "swap no longer pays"}, NOW, 1)
        self.assertEqual((trader.cancelled, book.orders, book.step_keys()), ([8], {}, set()))
        self.assertIn("the position is kept", notes[-1])

    def test_a_failed_cancel_keeps_the_steps_for_the_next_loop(self):
        book = self.book_with_step()
        with LOG:
            notes = book.end_steps(FakeTrader(cancel_ok=False), {KEY: "swap no longer pays"}, NOW, 1)
        self.assertEqual((list(book.orders), book.step_keys()), ([8], {KEY}))
        self.assertIn("could not cancel", notes[0])

    def test_a_step_with_nothing_resting_just_ends(self):
        book = stepping(last=0.50)
        with LOG:
            book.end_steps(FakeTrader(), {KEY: "swap no longer pays"}, NOW, 1)
        self.assertEqual(book.step_keys(), set())


if __name__ == "__main__":
    unittest.main()
