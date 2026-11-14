"""Offline tests for the election-night guard: freshness, the breaker, persistence, hand calls, pauses, the z haircut and the order size.
Every case is built from the config values (and run at several), so changing a setting does not break them.
Run: python3 -m unittest discover -s tests -t ."""
import json
import os
import tempfile
import unittest
from unittest import mock

import config
from guard import FairGuard
from orders import Order
from strategy import Opportunity, compute_fair_values, find_opportunities, z_score
from trading import cap_order_sizes

NOW = 1_700_000_000.0


def quote(mid, now, age=0.0, spread=0.01):
    return {"bid": mid - spread / 2, "ask": mid + spread / 2, "bid_size": 1e6, "ask_size": 1e6, "updated": now - age}


def outcome(kalshi, poly, now, kalshi_age=0.0, poly_age=0.0):
    """One outcome with a Kalshi and a Polymarket quote at these mids (a source left out has no id and no quote)."""
    empty = {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "updated": None}
    return {"kalshi_id": "K" if kalshi is not None else None, "poly_id": "P" if poly is not None else None,
            "kalshi": quote(kalshi, now, kalshi_age) if kalshi is not None else empty,
            "poly": quote(poly, now, poly_age) if poly is not None else empty}


def races(d_kalshi, d_poly="same", now=NOW, **ages):
    """A two-way race R1 with D at these mids and R at the complement, fetched at `now`."""
    d_poly = d_kalshi if d_poly == "same" else d_poly
    other = lambda mid: None if mid is None else 1 - mid
    return {"R1": {"outcomes": {"D": outcome(d_kalshi, d_poly, now, **ages), "R": outcome(other(d_kalshi), other(d_poly), now, **ages)},
                   "contracts": [{"market_id": "1", "pays_if": "D", "yes": quote(d_kalshi if d_kalshi is not None else d_poly, now)}]}}


def step(guard, seconds, d_kalshi, d_poly="same", **ages):
    """One loop `seconds` after NOW with the quotes fetched just then: (fair values the guard allows, notes)."""
    rs = races(d_kalshi, d_poly, now=NOW + seconds, **ages)
    return guard.apply(rs, compute_fair_values(rs, NOW + seconds), NOW + seconds)


def value(fairs, code="D"):
    return fairs[("R1", code)].value


class WithFiles(unittest.TestCase):
    """Each test gets its own calls.json and pause.json."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls, self.pause = os.path.join(self.tmp.name, "calls.json"), os.path.join(self.tmp.name, "pause.json")
        for patch in (mock.patch.object(config, "CALLS_FILE", self.calls), mock.patch.object(config, "PAUSE_FILE", self.pause)):
            patch.start()
            self.addCleanup(patch.stop)

    def write(self, path, data):
        with open(path, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))


class Freshness(WithFiles):
    def test_a_source_over_the_limit_skips_the_race_and_one_just_under_does_not(self):
        for limit in (5, config.ELECTION_STALE_SECONDS, 40):
            with self.subTest(limit=limit), mock.patch.object(config, "ELECTION_STALE_SECONDS", limit):
                fairs, _ = step(FairGuard(), 0, 0.60, poly_age=limit - 1)
                self.assertIsNotNone(value(fairs))
                fairs, _ = step(FairGuard(), 0, 0.60, poly_age=limit + 1)
                self.assertEqual((value(fairs), value(fairs, "R")), (None, None))
                self.assertIn("race skipped", fairs[("R1", "D")].note)

    def test_an_outcome_meant_to_have_one_source_trades_on_it(self):
        fairs, _ = step(FairGuard(), 0, 0.60, None)
        self.assertAlmostEqual(value(fairs), 0.60)


def without_kalshi(rs, **fields):
    """The same races with every Kalshi quote gone (a settled market) or changed by `fields` (the ids stay: the outcomes are still meant to have Kalshi)."""
    for o in rs["R1"]["outcomes"].values():
        o["kalshi"] = dict(o["kalshi"], **fields) if fields else {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "updated": None}
    return rs


class KalshiDropsOut(WithFiles):
    def apply(self, guard, rs, seconds=0):
        return guard.apply(rs, compute_fair_values(rs, NOW + seconds), NOW + seconds)

    def test_a_race_whose_kalshi_quote_is_gone_trades_on_polymarket_alone(self):
        fairs, notes = self.apply(FairGuard(), without_kalshi(races(0.60)))
        self.assertAlmostEqual(value(fairs), 0.60)
        self.assertEqual((fairs[("R1", "D")].sources, notes), (1, []))

    def test_a_kalshi_quote_older_than_the_limit_is_not_a_reason_to_skip(self):
        fairs, _ = self.apply(FairGuard(), races(0.60, 0.62, kalshi_age=config.ELECTION_STALE_SECONDS + 1))
        self.assertIsNotNone(value(fairs))

    def test_polymarket_missing_or_stale_still_skips_the_race_whatever_kalshi_says(self):
        missing = races(0.60)
        for o in missing["R1"]["outcomes"].values():
            o["poly"] = {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "updated": None}
        for why, rs in (("stale", races(0.60, 0.60, poly_age=config.ELECTION_STALE_SECONDS + 1)), ("missing", missing)):
            with self.subTest(why):
                fairs, _ = self.apply(FairGuard(), rs)
                self.assertEqual((value(fairs), value(fairs, "R")), (None, None))
                self.assertIn("Poly is older than", fairs[("R1", "D")].note)

    def test_a_big_polymarket_move_with_kalshi_gone_is_not_paused_but_still_waits_to_hold(self):
        guard = FairGuard()
        self.apply(guard, without_kalshi(races(0.50)))
        fairs, notes = self.apply(guard, without_kalshi(races(Breaker.BIG, now=NOW + 30)), 30)
        self.assertFalse(any("paused" in n for n in notes), notes)
        self.assertAlmostEqual(value(fairs), 0.50)                                    # persistence still holds the old value for a loop

    def test_a_kalshi_only_outcome_is_still_paused_on_a_big_move(self):
        guard = FairGuard()
        step(guard, 0, 0.50, None)
        self.assertIsNone(value(step(guard, 30, Breaker.BIG, None)[0]))


class Breaker(WithFiles):
    BIG = 0.50 + config.ELECTION_BREAKER_MOVE + 0.05

    def test_a_big_move_on_a_single_source_pauses_the_race_for_the_configured_minutes(self):
        for minutes in (1, config.ELECTION_BREAKER_MINUTES, 10):
            with self.subTest(minutes=minutes), mock.patch.object(config, "ELECTION_BREAKER_MINUTES", minutes):
                guard = FairGuard()
                step(guard, 0, 0.50, None)
                fairs, notes = step(guard, 30, self.BIG, None)
                self.assertEqual(value(fairs), None)
                self.assertTrue(any("paused" in n for n in notes), notes)
                self.assertEqual(value(step(guard, 30 + minutes * 60 - 1, 0.50, None)[0]), None)       # still paused
                self.assertIsNotNone(value(step(guard, 30 + minutes * 60 + 1, 0.50, None)[0]))         # released

    def test_a_move_under_the_limit_does_not_pause(self):
        guard = FairGuard()
        step(guard, 0, 0.50, None)
        self.assertIsNotNone(value(step(guard, 30, 0.50 + config.ELECTION_BREAKER_MOVE - 0.03, None)[0]))

    def test_a_move_both_sources_confirm_is_not_paused(self):
        guard = FairGuard()
        step(guard, 0, 0.50)
        self.assertIsNotNone(value(step(guard, 30, self.BIG)[0]))                    # held back by persistence, not paused

    def test_the_move_limit_and_the_window_come_from_config(self):
        for move, window in ((0.05, 30), (0.15, 60), (0.30, 120)):
            with self.subTest(move=move, window=window), mock.patch.object(config, "ELECTION_BREAKER_MOVE", move), \
                    mock.patch.object(config, "ELECTION_BREAKER_SECONDS", window):
                for seconds, jump, paused in ((window - 1, move + 0.03, True), (window - 1, move - 0.03, False), (window + 1, move + 0.03, False)):
                    guard = FairGuard()
                    step(guard, 0, 0.40, None)
                    self.assertEqual(value(step(guard, seconds, 0.40 + jump, None)[0]) is None, paused, (seconds, jump))

    def test_a_move_older_than_the_window_does_not_count(self):
        guard = FairGuard()
        step(guard, 0, 0.50, None)
        self.assertIsNotNone(value(step(guard, config.ELECTION_BREAKER_SECONDS + 1, self.BIG, None)[0]))


class Persistence(WithFiles):
    def test_a_changed_fair_is_used_only_after_it_holds_the_configured_loops(self):
        for loops in (2, config.ELECTION_PERSIST_LOOPS, 4):
            with self.subTest(loops=loops), mock.patch.object(config, "ELECTION_PERSIST_LOOPS", loops):
                guard, jump = FairGuard(), 0.50 + config.ELECTION_PERSIST_BAND + 0.10
                self.assertAlmostEqual(value(step(guard, 0, 0.50)[0]), 0.50)
                for i in range(1, loops):
                    self.assertAlmostEqual(value(step(guard, 10 * i, jump)[0]), 0.50, msg=f"loop {i}")
                self.assertAlmostEqual(value(step(guard, 10 * loops, jump)[0]), jump)

    def test_the_band_comes_from_config(self):
        for band in (0.01, 0.03, 0.08):
            with self.subTest(band=band), mock.patch.object(config, "ELECTION_PERSIST_BAND", band):
                guard = FairGuard()
                step(guard, 0, 0.50)
                self.assertAlmostEqual(value(step(guard, 10, 0.50 + band + 0.01)[0]), 0.50)             # outside the band: waits
                guard = FairGuard()
                step(guard, 0, 0.50)
                self.assertAlmostEqual(value(step(guard, 10, 0.50 + band - 0.005)[0]), 0.50 + band - 0.005)   # inside: followed at once

    def test_a_spike_that_comes_back_is_never_used(self):
        guard = FairGuard()
        step(guard, 0, 0.50)
        self.assertAlmostEqual(value(step(guard, 10, 0.80)[0]), 0.50)
        self.assertAlmostEqual(value(step(guard, 20, 0.51)[0]), 0.51)
        self.assertAlmostEqual(value(step(guard, 30, 0.80)[0]), 0.51)                   # the count starts again

    def test_a_drift_inside_the_band_is_followed_at_once_and_a_first_sight_is_accepted(self):
        guard, drift = FairGuard(), 0.50 + config.ELECTION_PERSIST_BAND - 0.005
        self.assertAlmostEqual(value(step(guard, 0, 0.50)[0]), 0.50)
        self.assertAlmostEqual(value(step(guard, 10, drift)[0]), drift)

    def test_the_note_says_a_value_is_not_held_yet(self):
        guard = FairGuard()
        step(guard, 0, 0.50)
        fairs, notes = step(guard, 10, 0.90)
        self.assertIn("not held yet", fairs[("R1", "D")].note)
        self.assertTrue(any("used once they hold" in n for n in notes))


class Calls(WithFiles):
    def test_a_called_outcome_gets_the_call_fair_the_others_the_rest_and_the_race_is_called(self):
        self.write(self.calls, {"R1": "D"})
        guard = FairGuard()
        fairs, _ = step(guard, 0, 0.95)
        self.assertAlmostEqual(value(fairs), config.ELECTION_CALL_FAIR)
        self.assertAlmostEqual(value(fairs, "R"), 1 - config.ELECTION_CALL_FAIR)
        self.assertEqual((guard.called, fairs[("R1", "D")].sources), ({"R1"}, 2))

    def test_a_call_no_source_backs_is_ignored_with_a_note(self):
        self.write(self.calls, {"R1": "D"})
        for mid in (config.ELECTION_CALL_MIN_MARKET - 0.05, 0.10):
            guard = FairGuard()
            fairs, notes = step(guard, 0, mid)
            self.assertAlmostEqual(value(fairs), mid)                                  # the market's own value stands
            self.assertEqual(guard.called, set())
            self.assertTrue(any("ignored" in n for n in notes), notes)

    def test_a_call_for_an_unknown_outcome_is_ignored(self):
        self.write(self.calls, {"R1": "X"})
        guard = FairGuard()
        fairs, notes = step(guard, 0, 0.95)
        self.assertEqual(guard.called, set())
        self.assertTrue(any("no outcome" in n for n in notes))

    def test_a_call_skips_the_breaker_and_persistence(self):
        guard = FairGuard()
        step(guard, 0, 0.30, None)
        self.write(self.calls, {"R1": "D"})
        fairs, _ = step(guard, 10, 0.95, None)                                         # a 65c jump on a single source
        self.assertAlmostEqual(value(fairs), config.ELECTION_CALL_FAIR)


class Pause(WithFiles):
    def test_a_paused_race_has_no_fair_value_at_all(self):
        self.write(self.pause, ["R1"])
        fairs, _ = step(FairGuard(), 0, 0.60)
        self.assertEqual((value(fairs), value(fairs, "R")), (None, None))
        self.assertEqual(find_opportunities(races(0.60), fairs), [])

    def test_a_missing_file_is_silent_and_a_broken_or_wrongly_shaped_one_pauses_nothing_and_says_so(self):
        fairs, notes = step(FairGuard(), 0, 0.60)                                      # no file
        self.assertEqual((value(fairs) is not None, notes), (True, []))
        for text in ("{not json", '{"R1": 1}', '"R1"', "", '["R1", 2]'):
            self.write(self.pause, text)
            fairs, notes = step(FairGuard(), 0, 0.60)
            self.assertIsNotNone(value(fairs), text)
            self.assertTrue(any("pause.json ignored" in n for n in notes), (text, notes))
        self.write(self.calls, ["R1"])
        self.assertTrue(any("calls.json ignored" in n for n in step(FairGuard(), 0, 0.60)[1]))


class Score(unittest.TestCase):
    def opp(self, uncertainty):
        return Opportunity("R1", "1", "D", "no", 0.80, 0.95, 1e6, "D", 2, uncertainty=uncertainty)

    def test_z_falls_by_the_haircut_over_the_risk_and_is_unchanged_without_uncertainty(self):
        sd = (0.95 * 0.05) ** 0.5
        self.assertAlmostEqual(self.opp(0.0).z, 0.15 / sd)
        for k in (0.5, config.ELECTION_K, 2.0):
            with mock.patch.object(config, "ELECTION_K", k):
                o = self.opp(0.02)
                self.assertAlmostEqual(o.z, (0.15 - k * 0.02) / sd)
                self.assertAlmostEqual(o.adj_edge, 0.15 - k * 0.02)
        self.assertEqual(z_score(0.95, 0.80), self.opp(0.0).z)

    def test_the_uncertainty_is_the_largest_of_its_parts(self):
        self.assertAlmostEqual(step(FairGuard(), 0, 0.60)[0][("R1", "D")].uncertainty, 0.01)             # the cent floor
        self.assertAlmostEqual(step(FairGuard(), 0, 0.60, 0.63)[0][("R1", "D")].uncertainty, 0.03)       # the sources disagree by 3c
        guard = FairGuard()
        step(guard, 0, 0.50)
        for weight in (0.25, config.ELECTION_MOVE_WEIGHT, 1.0):
            with mock.patch.object(config, "ELECTION_MOVE_WEIGHT", weight):
                guard = FairGuard()
                step(guard, 0, 0.50)
                self.assertAlmostEqual(step(guard, 10, 0.62)[0][("R1", "D")].uncertainty, weight * 0.12)

    def test_it_reaches_the_opportunities(self):
        fairs, _ = step(FairGuard(), 0, 0.60, 0.63)
        yes = next(o for o in find_opportunities(races(0.60, 0.63), fairs) if o.side == "yes")
        self.assertAlmostEqual(yes.uncertainty, 0.03)


class Size(unittest.TestCase):
    def order(self, race, action="buy", shares=10_000):
        return Order(action, "no", race, "1", shares, 0.80, 0.95, "t", "hold")

    def test_an_uncalled_buy_is_cut_to_the_factor_and_a_called_one_and_every_sell_is_not(self):
        equity = 100_000.0
        cap = config.ORDER_CAP_PCT * equity
        for factor in (0.25, config.ELECTION_UNCALLED_SIZE, 1.0):
            with self.subTest(factor=factor), mock.patch.object(config, "ELECTION_UNCALLED_SIZE", factor):
                guard = FairGuard()
                guard.called = {"R1"}
                kept, _ = cap_order_sizes([self.order("R1"), self.order("R2"), self.order("R2", "sell")], equity, guard.size_scale)
                by = {(o.race, o.action): o.shares for o in kept}
                self.assertEqual(by[("R1", "buy")], int(cap / 0.80))                  # a called race: the full cap
                self.assertEqual(by[("R2", "buy")], int(cap * factor / 0.80))
                self.assertEqual(by[("R2", "sell")], 10_000)

    def test_without_a_scale_the_cap_is_as_before(self):
        kept, _ = cap_order_sizes([self.order("R1", shares=100_000)], 100_000.0)
        self.assertEqual(kept[0].shares, int(config.ORDER_CAP_PCT * 100_000.0 / 0.80))


class Wiring(unittest.TestCase):
    """main.run_loop applies the guard in the election regime and in no other."""
    def run_regime(self, regime):
        import main
        from safety import RunState
        state = RunState(orders_may_be_open=False)
        with mock.patch.object(config, "REGIME", regime), mock.patch.object(main, "check_stop", return_value=None), \
                mock.patch.object(main, "run_all", return_value=(None, {"balance": 1_000.0}, [])), \
                mock.patch.object(main, "compute_fair_values", return_value={}), mock.patch.object(main, "check_loss", return_value="stop here"), \
                mock.patch.object(state.guard, "apply", return_value=({}, ["guard: a note"])) as apply:
            self.assertEqual(main.run_loop(1, {}, state, config.REGIMES[regime], trader=None), "stop here")
        return apply.called

    def test_only_the_election_regime_runs_it(self):
        self.assertEqual([self.run_regime(r) for r in ("slow", "normal", "election")], [False, False, True])


if __name__ == "__main__":
    unittest.main()
