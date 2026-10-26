"""Offline tests for the volume cache and the liquidity rules. Run: python3 -m unittest discover -s tests -t ."""
import os
import tempfile
import unittest

import config
from liquidity import Volume, VolumeStore, describe, liquidity_blocker, slice_factor

NOW = 1_700_000_000.0
HOUR = 3600


def vol(total=10e6, recent=50_000, age_hours=0.0):
    return Volume(total, recent, NOW - age_hours * HOUR)


class Rules(unittest.TestCase):
    def test_tiers_follow_all_time_volume(self):
        self.assertEqual(slice_factor(vol(total=1e6), 0.03), config.MEDIUM_SLICE_FACTOR)
        self.assertEqual(slice_factor(vol(total=4_999_999), 0.03), config.MEDIUM_SLICE_FACTOR)
        self.assertEqual(slice_factor(vol(total=5e6), 0.03), 1.0)
        self.assertIsNone(liquidity_blocker(vol(total=1e6), 0.03))

    def test_a_thin_market_needs_a_big_fast_part_and_gets_a_small_slice(self):
        thin = vol(total=999_999)
        self.assertIn("below 1.0M", liquidity_blocker(thin, 0.049))
        self.assertEqual(slice_factor(thin, 0.049), 0.0)
        self.assertEqual((slice_factor(thin, 0.05), slice_factor(thin, 0.099)), (0.125, 0.125))
        self.assertEqual((slice_factor(thin, 0.10), slice_factor(thin, 0.30)), (0.25, 0.25))
        self.assertIsNone(liquidity_blocker(thin, 0.05))

    def test_a_busy_market_gone_quiet_is_blocked(self):
        self.assertIn("since yesterday", liquidity_blocker(vol(total=30e6, recent=4_999), 0.5))
        self.assertIsNone(liquidity_blocker(vol(total=30e6, recent=5_000), 0.5))

    def test_a_big_fast_part_does_not_open_a_quiet_market(self):
        self.assertIn("since yesterday", liquidity_blocker(vol(total=0.5e6, recent=100), 0.30))

    def test_describe_names_the_condition_and_the_volume(self):
        self.assertEqual(describe(vol(total=12.4e6)), "high 12.4M")
        self.assertEqual(describe(vol(total=2.6e6)), "medium 2.6M")
        self.assertEqual(describe(vol(total=0.7e6)), "thin 0.7M")
        self.assertEqual(describe(vol(total=3e6, recent=100)), "quiet 3.0M")
        self.assertEqual(describe(None), "unknown")

    def test_unknown_volume_is_blocked(self):
        self.assertEqual(liquidity_blocker(None, 0.5), "volume not known yet")


class Store(unittest.TestCase):
    def test_stale_lists_missing_first_then_oldest_and_skips_fresh_ones(self):
        store = VolumeStore({"fresh": vol(age_hours=1), "old": vol(age_hours=13), "older": vol(age_hours=30)})
        self.assertEqual(store.stale(["fresh", "old", "older", "new"], NOW), ["new", "older", "old"])

    def test_it_survives_a_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "volumes.json")
            VolumeStore({"7": vol(2e6, 9_000)}).save(path)
            self.assertEqual(VolumeStore.load(path).get("7"), vol(2e6, 9_000))
            self.assertIsNone(VolumeStore.load(os.path.join(folder, "none.json")).get("7"))

    def test_an_update_replaces_only_the_markets_it_has(self):
        store = VolumeStore({"1": vol(1e6), "2": vol(2e6)})
        store.update({"2": vol(3e6)})
        self.assertEqual((store.get("1").total, store.get("2").total), (1e6, 3e6))


if __name__ == "__main__":
    unittest.main()
