"""Offline tests for the ledger of lots. Run: python3 -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest

from books import Ledger, Lot, book_value
from orders import Order
from portfolio import Holding, Portfolio


def position(market, side="no", shares=1000.0, avg=0.9):
    return {"market_id": market, "side": side, "shares": shares, "avg_price": avg}


def lot(market, book="hold", shares=1000.0, side="no", t=1.0, **kw):
    return Lot(book, market, side, shares, 0.9, t, **kw)


def order(action, market="1", book="hold", side="no", shares=100):
    return Order(action, side, "R", market, shares, 0.9, 0.97, "t", book)


class RecordFills(unittest.TestCase):
    def test_a_filled_fast_buy_becomes_a_fast_lot(self):
        ledger = Ledger()
        ledger.record_fills([(order("buy", book="fast"), 80)], now=7.0)
        self.assertEqual([(l.book, l.shares, l.entry_price, l.entry_time, l.bucket, l.grandfathered) for l in ledger.lots],
                         [("fast", 80, 0.9, 7.0, None, False)])

    def test_a_filled_hold_buy_takes_the_bucket_of_its_pick(self):
        ledger = Ledger(picks={"favourite": ("1", "no")})
        ledger.record_fills([(order("buy"), 50)], now=1.0)
        self.assertEqual(ledger.bucket_of("1", "no"), "favourite")

    def test_a_fast_sale_takes_the_fast_lots_before_the_hold_lots(self):
        ledger = Ledger([lot("1", "hold", 1000), lot("1", "fast", 300)])
        ledger.record_fills([(order("sell", book="fast"), 300)])
        self.assertEqual([(l.book, l.shares) for l in ledger.lots], [("hold", 1000)])

    def test_a_hold_sale_takes_the_hold_lots_first_and_a_full_exit_empties_both(self):
        ledger = Ledger([lot("1", "hold", 1000), lot("1", "fast", 300)])
        ledger.record_fills([(order("sell", book="hold"), 400)])
        self.assertEqual(sorted((l.book, l.shares) for l in ledger.lots), [("fast", 300), ("hold", 600)])
        ledger.record_fills([(order("sell", book="hold"), 900)])
        self.assertEqual(ledger.lots, [])

    def test_what_was_booked_leaves_nothing_for_the_next_reconcile_to_adopt(self):
        ledger = Ledger()
        ledger.record_fills([(order("buy", book="fast"), 80)])
        self.assertEqual(ledger.reconcile([position("1", shares=80)]), [])
        self.assertEqual([l.book for l in ledger.lots], ["fast"])


class Reconcile(unittest.TestCase):
    def test_shares_nobody_owns_become_a_grandfathered_hold_lot(self):
        ledger = Ledger()
        notes = ledger.reconcile([position("1", shares=500, avg=0.88)], now=50.0)
        self.assertEqual([(l.book, l.shares, l.entry_price, l.grandfathered) for l in ledger.lots], [("hold", 500, 0.88, True)])
        self.assertIn("adopted", notes[0])

    def test_extra_shares_on_a_known_position_are_adopted_as_a_separate_lot(self):
        ledger = Ledger([lot("1", shares=1000)])
        ledger.reconcile([position("1", shares=1300)], now=5.0)
        self.assertEqual(sorted(l.shares for l in ledger.lots), [300, 1000])
        self.assertEqual(ledger.shares("1", "no"), 1300)

    def test_a_position_you_sold_down_shrinks_the_fast_lot_first_then_the_hold_lot(self):
        ledger = Ledger([lot("1", "hold", 1000), lot("1", "fast", 400)])
        notes = ledger.reconcile([position("1", shares=1100)])
        self.assertEqual((ledger.shares("1", "no", "fast"), ledger.shares("1", "no", "hold")), (100, 1000))
        ledger.reconcile([position("1", shares=800)])
        self.assertEqual((ledger.shares("1", "no", "fast"), ledger.shares("1", "no", "hold")), (0, 800))
        self.assertTrue(notes and "fewer" in notes[0])
        self.assertEqual([l.book for l in ledger.lots], ["hold"])              # the empty fast lot is dropped

    def test_a_position_that_is_gone_drops_its_lots(self):
        ledger = Ledger([lot("1"), lot("2")])
        ledger.reconcile([position("2")])
        self.assertEqual([l.market_id for l in ledger.lots], ["2"])

    def test_yes_and_no_of_one_market_are_separate_positions(self):
        ledger = Ledger([lot("1", side="no", shares=100)])
        ledger.reconcile([position("1", side="no", shares=100), position("1", side="yes", shares=40)])
        self.assertEqual((ledger.shares("1", "no"), ledger.shares("1", "yes")), (100, 40))

    def test_matching_positions_change_nothing(self):
        ledger = Ledger([lot("1")])
        self.assertEqual(ledger.reconcile([position("1")]), [])
        self.assertEqual(len(ledger.lots), 1)


class Storage(unittest.TestCase):
    def test_roundtrip_keeps_lots_and_picks(self):
        ledger = Ledger([lot("1", bucket="sure bet", grandfathered=True)], {"sure bet": ("1", "no")})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.json")
            ledger.save(path)
            loaded = Ledger.load(path)
        self.assertEqual(loaded.lots, ledger.lots)
        self.assertEqual(loaded.picks, {"sure bet": ("1", "no")})
        self.assertEqual(loaded.bucket_of("1", "no"), "sure bet")

    def test_a_missing_file_is_an_empty_ledger(self):
        loaded = Ledger.load("/nonexistent/ledger.json")
        self.assertEqual((loaded.lots, loaded.picks), ([], {}))


class BookValue(unittest.TestCase):
    def test_a_holding_is_split_by_each_books_share_of_its_lots(self):
        holding = Holding("R", "1", "no", "D", 1000, 900.0, 0.9, 1e6, 0.95)
        portfolio = Portfolio(0.0, [holding], 0.0)
        ledger = Ledger([lot("1", "hold", 750), lot("1", "fast", 250)])
        self.assertAlmostEqual(book_value(portfolio.holdings, ledger, "hold"), 675.0)
        self.assertAlmostEqual(book_value(portfolio.holdings, ledger, "fast"), 225.0)
        self.assertEqual(book_value(portfolio.holdings, Ledger(), "hold"), 0.0)


if __name__ == "__main__":
    unittest.main()
