"""Offline tests for the patient-exit helpers. Run: python3 -m unittest discover -s tests -t ."""
import unittest

import bigorder
import config


class WallPrice(unittest.TestCase):
    """Early-exit listings: at most 3 ticks above the best ask, in front of a liquidity wall when that clears entry + 1c."""
    def price(self, asks, structural=0.99, floor=0.0):
        return bigorder.wall_price(asks, structural, floor)

    def test_market_151_lists_in_front_of_the_million_at_0_89(self):
        self.assertEqual(self.price([(0.885, 10_200), (0.89, 1_130_000)]), 0.885)          # best ask 9k, then about 1M SUSQ

    def test_a_level_under_1k_is_skipped_so_it_neither_is_a_wall_nor_makes_one(self):
        self.assertEqual(self.price([(0.88, 500), (0.885, 10_200), (0.89, 1_130_000)]), 0.885)  # the 440 at 0.88 does not count
        book_at_1529 = [(0.875, 3_638), (0.88, 500), (0.885, 31_899), (0.89, 1_240_165)]      # 0.88 is under 1k: 0.875 is no wall
        self.assertEqual(self.price(book_at_1529, floor=0.875), 0.88)
        self.assertEqual(self.price([(0.875, 3_638), (0.88, 3_355), (0.885, 31_899), (0.89, 1_240_165)], floor=0.875), 0.88)   # 151 now

    def test_the_first_wall_up_the_book_wins(self):
        self.assertEqual(self.price([(0.88, 1_200), (0.885, 10_200), (0.89, 1_130_000)]), 0.88)   # 9k is 3x the 1,056 below it

    def test_without_a_wall_it_lists_three_ticks_above_the_best_ask_or_at_the_structural_value(self):
        asks = [(0.88, 2_000), (0.885, 2_000), (0.89, 2_000), (0.895, 2_000), (0.90, 50_000)]   # the wall is 4 ticks up: too far
        self.assertEqual(self.price(asks), 0.895)
        self.assertEqual(self.price(asks, structural=0.887), 0.887)

    def test_a_small_level_is_no_wall_however_it_compares(self):
        self.assertEqual(self.price([(0.88, 100), (0.885, 1_000), (0.89, 100)]), 0.895)          # 885 SUSQ: under 1,000

    def test_empty_ticks_are_skipped_when_comparing(self):
        asks = [(0.88, 3_000), (0.89, 6_000)]                         # 0.885 empty: 0.89 is 2x 0.88, not a wall
        self.assertEqual(self.price(asks), 0.895)
        self.assertEqual(self.price([(0.88, 3_000), (0.89, 12_000)]), 0.885)   # 4x: a wall; in front of it is the empty 0.885

    def test_a_wall_too_close_to_the_entry_is_ignored(self):
        asks = [(0.88, 2_000), (0.885, 50_000)]                      # in front of it is 0.88, under entry 0.875 + 1c
        self.assertEqual(self.price(asks, floor=0.885), 0.895)
        self.assertEqual(self.price(asks, floor=0.88), 0.88)

    def test_a_best_ask_wall_lists_one_tick_below_it_else_at_it_else_at_the_cap(self):
        asks = [(0.88, 50_000), (0.885, 2_000)]
        self.assertEqual(self.price(asks, floor=0.875), 0.875)        # one tick below: the bid on a one-tick spread, sold at once
        self.assertEqual(self.price(asks, floor=0.88), 0.88)          # joins the wall
        self.assertEqual(self.price(asks, floor=0.885), 0.895)        # neither clears entry + 1c: the cap
        self.assertEqual(self.price([(0.88, 50_000)], floor=0.0), 0.895)   # nothing to compare it with: no wall

    def test_no_asks_lists_at_the_structural_value(self):
        self.assertEqual(self.price([], structural=0.93), 0.93)


class BigOrder(unittest.TestCase):
    def test_a_big_order_is_four_percent_of_the_week_between_the_floor_and_the_cap(self):
        self.assertAlmostEqual(bigorder.big_order_size(1_000_000), 40_000)
        self.assertEqual(bigorder.big_order_size(5_000_000), config.BIG_ORDER_CAP)       # capped at 50k
        self.assertEqual(bigorder.big_order_size(20_000), 800)                        # 4% of 20k, above the floor
        self.assertEqual(bigorder.big_order_size(5_000), config.BIG_ORDER_MIN)               # 4% would be 200


class RestPrice(unittest.TestCase):
    ladder = [(0.095, 1_000), (0.10, 2_000), (0.105, 4_000)]   # asks of the held side, best first

    def test_it_rests_at_the_farthest_level_one_big_order_still_fills_completely(self):
        # selling 500 shares: at 0.10 the asks at or below it are worth 95 + 200 = 295 and mine 50 -> 345; at 0.105: 715 + 52.5 -> 767.5
        self.assertEqual(bigorder.rest_price(self.ladder, 500, 0.09, big=400), 0.10)
        self.assertEqual(bigorder.rest_price(self.ladder, 500, 0.09, big=769), 0.105)
        # above the last level nothing else queues ahead: 715 + 500 x 0.17 = 800 still fits, 0.175 does not
        self.assertEqual(bigorder.rest_price(self.ladder, 500, 0.09, big=800), 0.17)

    def test_an_order_too_big_for_the_big_order_rests_one_tick_above_the_bid(self):
        self.assertEqual(bigorder.rest_price(self.ladder, 100_000, 0.09, big=400), 0.095)
        self.assertEqual(bigorder.rest_price([], 100_000, 0.09, big=400), 0.095)

    def test_a_wide_spread_lets_it_rest_inside_the_spread(self):
        wide = [(0.12, 1_000)]
        self.assertEqual(bigorder.rest_price(wide, 100, 0.09, big=9), 0.095)              # nothing fits: one tick above the bid
        self.assertEqual(bigorder.rest_price(wide, 100, 0.09, big=60), 0.115)             # 100 x 0.115 = 11.5 fits, 0.12 would queue behind 120

    def test_it_never_goes_above_the_top_tick(self):
        self.assertEqual(bigorder.rest_price([], 10, 0.98, big=1e9), bigorder.MAX_PRICE)

    def test_a_truncated_book_limits_the_price_to_the_levels_that_can_be_seen(self):
        full = [(0.095 + 0.005 * i, 100) for i in range(config.DEPTH_LEVELS)]            # ten visible levels: more may hide behind them
        self.assertEqual(bigorder.rest_price(full, 10, 0.09, big=1e9), full[-1][0])


class ExitFill(unittest.TestCase):
    bids = [(0.90, 100), (0.895, 300), (0.89, 1_000), (0.80, 5_000)]

    def test_it_sells_into_the_bids_within_the_band_at_their_average_price(self):
        shares, average, worst = bigorder.exit_fill(self.bids, 10_000, 0.01)
        self.assertEqual((shares, worst), (1_400, 0.89))
        self.assertAlmostEqual(average, (100 * 0.90 + 300 * 0.895 + 1_000 * 0.89) / 1_400)

    def test_it_stops_when_the_size_is_filled(self):
        self.assertEqual(bigorder.exit_fill(self.bids, 250, 0.01)[0], 250)
        self.assertEqual(bigorder.exit_fill(self.bids, 250, 0.01)[2], 0.895)

    def test_no_bids_sell_nothing(self):
        self.assertEqual(bigorder.exit_fill([], 100, 0.01), (0, None, None))
        

if __name__ == "__main__":
    unittest.main()
