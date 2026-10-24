"""Regime timings against SIG's write budget (30 a minute; the guard holds the bot to SIG_WRITES_PER_MINUTE)."""
import unittest

import config

MEASURED_FETCH_SECONDS = 1.2  # one fetch + plan over ~115 races with parallel fetching, measured live
SIG_WRITES_PER_MINUTE = 30


def writes_per_minute(regime, writes_per_loop):
    cycle = MEASURED_FETCH_SECONDS + regime.order_timeout + regime.loop_pause
    return 60 / cycle * writes_per_loop


class RegimeBudgets(unittest.TestCase):
    worst_loop = 1 + config.MAX_ORDERS_PER_LOOP  # one cancel-all plus a full set of orders

    def test_the_guard_sits_below_the_sig_limit_and_one_loop_always_fits(self):
        self.assertLessEqual(config.SIG_WRITES_PER_MINUTE, SIG_WRITES_PER_MINUTE - 3)
        self.assertLessEqual(self.worst_loop, config.SIG_WRITES_PER_MINUTE)

    def test_slow_and_normal_stay_under_the_guard_even_if_every_loop_is_full(self):
        for name in ("slow", "normal"):
            self.assertLess(writes_per_minute(config.REGIMES[name], self.worst_loop),
                            config.SIG_WRITES_PER_MINUTE, name)

    def test_election_relies_on_the_guard_when_every_loop_is_full(self):
        # Full loops every cycle would be ~45 writes a minute; the guard slows the bot to its limit instead.
        self.assertGreater(writes_per_minute(config.REGIMES["election"], self.worst_loop), config.SIG_WRITES_PER_MINUTE)

    def test_order_timeout_and_pause_grow_from_election_to_slow_and_clear_the_data_cache(self):
        order = ("election", "normal", "slow")
        timeouts = [config.REGIMES[n].order_timeout for n in order]
        pauses = [config.REGIMES[n].loop_pause for n in order]
        self.assertEqual(timeouts, sorted(timeouts))
        self.assertEqual(pauses, sorted(pauses))
        self.assertGreater(config.REGIMES["election"].order_timeout, 2)  # SIG data is cached for up to 2 s


if __name__ == "__main__":
    unittest.main()
