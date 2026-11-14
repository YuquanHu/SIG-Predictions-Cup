"""Offline tests for patient entries: a fast buy (a new position or a top-up) on a spread wider than one tick bids one tick above the best bid for
PATIENT_ENTRY_MINUTES, then buys at the ask. Run: python3 -m unittest discover -s tests -t ."""
import unittest
from unittest import mock

import config
import display
import fast
import trading
from books import Ledger, Lot
from cooldown import Cooldown
from orders import Order
import patient
from patient import PatientBook
from portfolio import Portfolio
from tests.test_fast import EQUITY, NOW, base, fast_lot, held, opp, races_for, volumes_for
from tests.test_patient_exits import FakeTrader
from tests.test_live_trader import Base

ENTRIES_ON = mock.patch.multiple(config, PATIENT_EXITS=True, PATIENT_ENTRIES=True)
KEY = ("1", "no")


def plan(book, candidates=None, holdings=(), lots=(), spread=0.01):
    holdings = list(holdings)
    candidates = candidates if candidates is not None else [opp("1", size=10)]
    markets = {"1"} | {h.market_id for h in holdings} | {c.market_id for c in candidates}
    portfolio = Portfolio(EQUITY - sum(h.value for h in holdings), holdings, 0.0)
    with ENTRIES_ON:
        return fast.fast_plan(candidates, {m: base(-0.05) for m in markets},
                              races_for(*markets, bid=0.10 - spread, ask=0.10), portfolio, Ledger(list(lots)), Cooldown(0),
                              volumes_for(*markets), NOW, book)


def bid_order(price=0.895, shares=6_000, until=NOW + 600, side="no"):
    return Order("buy", side, "R1", "1", shares, price, 0.985, "entry", "fast", rest_until=until, rest_kind="entry")


def yes_opp(fair):
    return opp("1", fair=fair, price=0.10, side="yes")


class Bids(unittest.TestCase):
    def test_a_new_position_on_a_one_cent_spread_bids_one_tick_above_the_best_bid_for_ten_minutes(self):
        [w] = plan(PatientBook()).wants
        self.assertEqual((w.opp.price, w.rest_until), (0.895, NOW + config.PATIENT_ENTRY_MINUTES * 60))   # ask 0.90, bid 0.89
        [order], _ = trading.size_buys([w], 1e6)
        self.assertEqual((order.price, order.rest_kind, order.resting), (0.895, "entry", True))
        self.assertEqual(order.shares, int(w.want / 0.895))                  # not limited by the 10 shares on offer at the ask

    def test_a_one_tick_spread_buys_at_the_ask(self):
        [w] = plan(PatientBook(), spread=0.005).wants
        self.assertEqual((w.opp.price, w.rest_until), (0.90, None))

    def test_with_the_switch_off_it_buys_at_the_ask(self):
        with mock.patch.object(config, "PATIENT_ENTRIES", False):
            portfolio = Portfolio(EQUITY, [], 0.0)
            with mock.patch.object(config, "PATIENT_EXITS", True):
                [w] = fast.fast_plan([opp("1")], {"1": base(-0.05)}, races_for("1", bid=0.09, ask=0.10), portfolio, Ledger(), Cooldown(0),
                                     volumes_for("1"), NOW, PatientBook()).wants
        self.assertIsNone(w.rest_until)

    def test_a_top_up_of_a_held_position_bids_too(self):
        h = held("1", 2_000)
        [w] = plan(PatientBook(), holdings=[h], lots=[fast_lot("1", h.shares)]).wants
        self.assertEqual((w.opp.price, w.rest_until), (0.895, NOW + config.PATIENT_ENTRY_MINUTES * 60))

    def test_a_top_up_on_a_one_tick_spread_buys_at_the_ask(self):
        h = held("1", 2_000)
        [w] = plan(PatientBook(), holdings=[h], lots=[fast_lot("1", h.shares)], spread=0.005).wants
        self.assertIsNone(w.rest_until)

    def test_a_held_position_with_a_bid_resting_gets_no_second_buy(self):
        h = held("1", 2_000)
        book = PatientBook()
        book.add(9, bid_order())
        p = plan(book, holdings=[h], lots=[fast_lot("1", h.shares)])
        self.assertEqual(p.wants, [])
        self.assertIn("patient entry: 6,000 bid at 0.895", p.rows[0].note)

    def test_the_bid_is_shorter_for_a_big_fast_part_and_skipped_for_a_bigger_one(self):
        def wait(fair):  # a YES at 0.10 (bid 0.09), usual gap 5c on the other side: fast part = fair - 0.10 - 0.05
            [w] = plan(PatientBook(), candidates=[yes_opp(fair)]).wants
            return None if w.rest_until is None else (w.rest_until - NOW) / 60
        self.assertEqual([wait(f) for f in (0.23, 0.24, 0.25, 0.26, 0.35)],
                         [config.PATIENT_ENTRY_MINUTES, 5, 5, None, None])   # 8c, 9c, 10c, 11c, 20c

    def test_a_skipped_bid_buys_at_the_ask(self):
        [w] = plan(PatientBook(), candidates=[yes_opp(0.35)]).wants
        self.assertEqual(w.opp.price, 0.10)
        self.assertNotIn("patient entry", w.reason)

    def test_after_a_patient_entry_ended_the_market_buys_at_the_ask(self):
        book = PatientBook()
        book._entered[KEY] = (1, NOW)
        self.assertIsNone(plan(book).wants[0].rest_until)


class Rebids(unittest.TestCase):
    """A bid that ended unfilled is bid again while the spread is over PATIENT_REBID_SPREAD, up to PATIENT_MAX_BIDS bids in a row."""
    WIDE = config.PATIENT_REBID_SPREAD + 0.005

    def after(self, ended, spread=None, now=NOW):
        book = PatientBook()
        if ended:
            book._entered[KEY] = (ended, now)
        [w] = plan(book, spread=spread or self.WIDE).wants
        return w

    def test_a_wide_spread_is_bid_again_until_the_limit_then_buys_at_the_ask(self):
        self.assertEqual([self.after(n).rest_until is not None for n in range(config.PATIENT_MAX_BIDS + 1)],
                         [True] * config.PATIENT_MAX_BIDS + [False])

    def test_a_spread_no_wider_than_the_rebid_spread_buys_at_the_ask_after_a_bid(self):
        self.assertIsNone(self.after(1, spread=config.PATIENT_REBID_SPREAD).rest_until)
        self.assertIsNotNone(self.after(0, spread=config.PATIENT_REBID_SPREAD).rest_until)          # the first bid needs only a spread over a tick

    def test_a_gap_too_big_to_wait_is_never_bid_again(self):
        book = PatientBook()
        book._entered[KEY] = (1, NOW)
        [w] = plan(book, candidates=[yes_opp(0.35)], spread=self.WIDE).wants
        self.assertIsNone(w.rest_until)

    def test_each_bid_that_ends_without_a_full_fill_counts_and_a_full_fill_starts_over(self):
        book = PatientBook()
        book.add(9, bid_order())
        order = book.orders[9]
        for n in range(1, config.PATIENT_MAX_BIDS + 1):
            book._ended(order, NOW + n)
            self.assertEqual(book.ended_bids(*KEY), n)
        book._ended(order, NOW + 99, filled=True)
        self.assertEqual(book.ended_bids(*KEY), 0)

    def test_sync_counts_a_bid_that_closed_part_filled_and_resets_on_one_that_closed_in_full(self):
        for filled, count in ((1_000, 2), (bid_order().shares, 0)):
            with self.subTest(filled=filled), mock.patch("patient.logbook.log"):
                book = PatientBook()
                book._entered[KEY] = (1, NOW)
                book.add(9, bid_order())
                book.sync(FakeTrader(fills={9: filled}), 1)
                self.assertEqual(book.ended_bids(*KEY), count)

    def test_the_count_is_forgotten_an_hour_after_the_last_bid_ended(self):
        book = PatientBook()
        book._entered[KEY] = (config.PATIENT_MAX_BIDS, NOW)
        with mock.patch.object(config, "PATIENT_ENTRIES", True):
            book.review(FakeTrader(), Portfolio(EQUITY, [], 0.0), Ledger(), {}, NOW + patient.ENTRY_MEMORY - 1, 1)
            self.assertEqual(book.ended_bids(*KEY), config.PATIENT_MAX_BIDS)
            book.review(FakeTrader(), Portfolio(EQUITY, [], 0.0), Ledger(), {}, NOW + patient.ENTRY_MEMORY, 1)
        self.assertEqual(book.ended_bids(*KEY), 0)


class WhileItRests(unittest.TestCase):
    def resting_book(self):
        book = PatientBook()
        book.add(9, bid_order())
        return book

    def test_a_resting_bid_is_not_a_sell_and_reserves_no_shares(self):
        book = self.resting_book()
        self.assertEqual((book.keys(), book.reserved(*KEY), book.resting_of(*KEY)), (set(), 0, None))
        self.assertEqual(set(book.entries()), {KEY})
        self.assertAlmostEqual(book.entry_value(), 6_000 * 0.895)

    def test_it_gets_no_second_buy_and_the_report_shows_its_timer(self):
        p = plan(self.resting_book())
        self.assertEqual(p.wants, [])
        row = p.rows[0]
        self.assertIn("patient entry: 6,000 bid at 0.895", row.note)
        self.assertEqual(display.status(row), "patient entry, 10m left")

    def test_it_takes_a_slot(self):
        holdings = [held(str(m), 2_000) for m in range(2, 2 + config.FAST_MAX_POSITIONS - 1)]   # one slot left, the bid takes it
        lots = [fast_lot(h.market_id, h.shares) for h in holdings]
        p = plan(self.resting_book(), candidates=[opp("1"), opp("9")], holdings=holdings, lots=lots)
        self.assertEqual([w.opp.market_id for w in p.wants], [])
        self.assertIn("book full", next(r.note for r in p.rows if "9" in r.label))

    def test_a_candidate_that_stops_qualifying_cancels_its_bid(self):
        book = self.resting_book()
        p = plan(book, candidates=[opp("1", fair=0.92)])                    # 2c edge: no fast entry any more
        self.assertEqual(p.stale_entries, {KEY: "no longer a fast entry"})
        trader = FakeTrader()
        with mock.patch("patient.logbook.log"):
            notes = book.cancel_kind("entry", trader, p.stale_entries, NOW, 1)
        self.assertEqual(trader.cancelled, [9])
        self.assertIn("no longer a fast entry", notes[0])
        self.assertEqual(book.ended_bids(*KEY), 1)

    def test_a_resting_bid_whose_fast_part_rises_above_ten_cents_is_cancelled_to_buy_at_the_ask(self):
        book = PatientBook()
        book.add(9, bid_order(price=0.095, side="yes"))
        self.assertEqual(plan(book, candidates=[yes_opp(0.24)]).stale_entries, {})               # 9c: it keeps bidding
        p = plan(book, candidates=[yes_opp(0.35)])                                                 # 20c
        self.assertEqual(p.stale_entries, {("1", "yes"): "fast part 20.0c: buying at the ask"})
        self.assertIn("cancelled to buy at the ask", p.rows[0].note)
        with mock.patch("patient.logbook.log"):
            book.cancel_kind("entry", FakeTrader(), p.stale_entries, NOW, 1)
        [w] = plan(book, candidates=[yes_opp(0.35)]).wants
        self.assertEqual((w.opp.price, w.rest_until), (0.10, None))


class Review(Base):
    def review(self, book, now):
        with mock.patch.object(config, "PATIENT_ENTRIES", True):
            return book.review(FakeTrader(), Portfolio(EQUITY, [], 0.0), Ledger(), {}, now, 1)

    def test_it_rests_its_ten_minutes_even_with_no_shares_yet_then_the_rest_buys_at_the_ask(self):
        book = PatientBook()
        book.add(9, bid_order())
        self.assertEqual(self.review(book, NOW + 300), [])
        notes = self.review(book, NOW + 601)
        self.assertIn("bought at the ask", notes[0])
        self.assertEqual(book.ended_bids(*KEY), 1)
        self.assertEqual(book.entries(), {})

    def test_its_fills_are_booked_to_the_fast_book_while_it_rests_and_once_after_it_ends(self):
        book = PatientBook()
        book.add(9, bid_order())
        self.assertEqual(book.take_entry_buys(), {KEY: (0.895, "fast")})
        self.review(book, NOW + 601)
        self.assertEqual(book.take_entry_buys(), {KEY: (0.895, "fast")})                # the fills that came in just before the cancel
        self.assertEqual(book.take_entry_buys(), {})

    def test_shutdown_cancels_it(self):
        book, trader = PatientBook(), FakeTrader()
        book.add(9, bid_order())
        book.cancel_all(trader, 1)
        self.assertEqual(trader.cancelled, [9])


class Ledgers(unittest.TestCase):
    def test_new_shares_in_a_market_with_a_patient_entry_are_a_fast_lot_at_the_bid(self):
        ledger = Ledger()
        notes = ledger.reconcile([{"market_id": "1", "side": "no", "shares": 1_500, "avg_price": 0.9}], NOW, {KEY: (0.895, "fast")})
        [lot] = ledger.lots
        self.assertEqual((lot.book, lot.shares, lot.entry_price, lot.grandfathered), ("fast", 1_500, 0.895, False))
        self.assertIn("patient entry", notes[0])

    def test_elsewhere_they_are_still_a_grandfathered_hold_lot(self):
        ledger = Ledger([Lot("fast", "2", "no", 10, 0.9, NOW)])
        ledger.reconcile([{"market_id": "1", "side": "no", "shares": 1_500, "avg_price": 0.9},
                          {"market_id": "2", "side": "no", "shares": 10, "avg_price": 0.9}], NOW, {("2", "no"): (0.9, "fast")})
        self.assertEqual([(l.market_id, l.book, l.grandfathered) for l in ledger.lots if l.market_id == "1"], [("1", "hold", True)])


if __name__ == "__main__":
    unittest.main()
