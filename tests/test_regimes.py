"""The regime timings must keep SIG's write budget (30 a minute) with room to spare."""
import unittest

import config

MEASURED_FETCH_SECONDS = 7.4  # one fetch + plan over ~115 races, measured live
SIG_WRITES_PER_MINUTE = 30


def writes_per_minute(regime, writes_per_loop):
    cycle = MEASURED_FETCH_SECONDS + regime.order_timeout + regime.loop_pause
    return 60 / cycle * writes_per_loop


class RegimeBudgets(unittest.TestCase):
    def test_typical_writes_stay_well_under_the_limit(self):
        typical = 1 + config.TOP_N  # one cancel-all plus a buy for each top bet
        for name, regime in config.REGIMES.items():
            self.assertLess(writes_per_minute(regime, typical), SIG_WRITES_PER_MINUTE * 0.75, name)

    def test_order_waits_for_the_data_cache_and_gets_slower_from_election_to_slow(self):
        timeouts = [config.REGIMES[n].order_timeout for n in ("election", "normal", "slow")]
        pauses = [config.REGIMES[n].loop_pause for n in ("election", "normal", "slow")]
        self.assertEqual(timeouts, sorted(timeouts))
        self.assertEqual(pauses, sorted(pauses))
        self.assertGreater(config.REGIMES["election"].order_timeout, 2)  # SIG data is cached for up to 2 s


if __name__ == "__main__":
    unittest.main()
