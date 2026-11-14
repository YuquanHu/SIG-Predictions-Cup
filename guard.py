"""Election-night guard on the fair values (REGIME = "election"). Run after strategy.compute_fair_values, it turns fair values into the
ones the bot may trade on, per race and in this order:
  pause.json    a race you put there (a list of race ids) gets no fair value: no buys, swaps or automated sells in it
  calls.json    {"RI-SEN": "D"}: the outcome that won gets ELECTION_CALL_FAIR and the others share the rest, if a source agrees with it
  freshness     Polymarket (the source the fair rests on) must be fetched within ELECTION_STALE_SECONDS, else the race is skipped. Kalshi is never
                needed (it settles early and drops out): with no Kalshi quote the race trades on Polymarket alone, and without Polymarket it
                is skipped even if Kalshi is fresh. An outcome with no Polymarket market needs the sources it has
  breaker       a fair that moved more than ELECTION_BREAKER_MOVE in ELECTION_BREAKER_SECONDS with only a Kalshi quote behind it pauses the race
                for ELECTION_BREAKER_MINUTES. With two sources a one-source spike already has no fair value (they must agree within
                MAX_DISAGREE), and Polymarket alone is trusted, so this covers an outcome that has only Kalshi and nothing to confirm it
  persistence   a changed fair is used only after it held (within ELECTION_PERSIST_BAND) for ELECTION_PERSIST_LOOPS loops in a row
and gives each fair its uncertainty s (strategy.Opportunity.adj_edge takes ELECTION_K x s off the edge). A called race skips the breaker
and persistence and trades at full size; every other race's buys are cut to ELECTION_UNCALLED_SIZE of the single-order cap.
Quote ages here are fetch ages: neither Kalshi nor Polymarket gives a usable quote timestamp (kalshi.py, polymarket.py)."""
import json
import os
from collections import defaultdict, deque
from dataclasses import replace

import config
from races import quote_available
from strategy import Fair

SOURCES = (("Kalshi", "kalshi", "kalshi_id"), ("Poly", "poly", "poly_id"))
TRUSTED = "Poly"  # the source the fair rests on: it is needed when the outcome has a market there, Kalshi never is
FLOOR = 0.01  # the smallest uncertainty: a fair value is never exact to better than a cent


def read_json(path, default, notes=None):
    """A hand-edited file, or `default` when it is missing (silently) or unreadable or of the wrong shape (with a note in `notes`: a typo must
    never stop the bot, but you must not think a race is paused when it is not). The default's type is the shape: a dict of strings or a list."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        data = e
    items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else None
    if type(data) is type(default) and all(isinstance(x, str) for x in items):
        return data
    if notes is not None:
        notes.append(f"guard: {os.path.basename(path)} ignored: expected {'{race id: outcome}' if isinstance(default, dict) else 'a list of race ids'}")
    return default


def fresh_sources(outcome, now):
    """({source: (mid, spread)} of the two-sided quotes fetched within ELECTION_STALE_SECONDS, the sources the outcome needs that are not among them:
    Polymarket when it has a market there, else every source it has)."""
    fresh = {}
    for name, key, _ in SOURCES:
        q = outcome[key]
        if quote_available(q) and q["updated"] is not None and now - q["updated"] <= config.ELECTION_STALE_SECONDS:
            fresh[name] = ((q["bid"] + q["ask"]) / 2, q["ask"] - q["bid"])
    has = [name for name, _, id_key in SOURCES if outcome.get(id_key)]
    return fresh, [name for name in ([TRUSTED] if TRUSTED in has else has) if name not in fresh]


def uncertainty(sources, move):
    """The s of z_adj: the cent floor, how far the two sources disagree, half the widest source spread, and ELECTION_MOVE_WEIGHT x the move in the window."""
    mids = [mid for mid, _ in sources.values()]
    gap = max(mids) - min(mids) if len(mids) == 2 else 0.0
    return max(FLOOR, gap, max((spread for _, spread in sources.values()), default=0.0) / 2, config.ELECTION_MOVE_WEIGHT * move)


def blank(race, why):
    return {code: Fair(None, why) for code in race["outcomes"]}


class FairGuard:
    def __init__(self):
        self.history = defaultdict(deque)  # (race, outcome) -> [(time, fair)] over the breaker window
        self.paused_until = {}             # race -> when the breaker releases it
        self.confirmed = {}                # (race, outcome) -> the fair in use
        self.pending = {}                  # (race, outcome) -> [a changed fair, loops it has held]
        self.called = set()                # races called this loop (full size, no breaker)

    def size_scale(self, order):
        """How much of the single-order cap a buy gets: all of it in a called race, ELECTION_UNCALLED_SIZE elsewhere."""
        return 1.0 if order.race in self.called else config.ELECTION_UNCALLED_SIZE

    def apply(self, races, fair_values, now):
        """(fair values the bot may trade on, notes) for this loop. Fair values it cannot vouch for become Fair(None, why)."""
        out, notes, waiting = dict(fair_values), [], 0
        calls, by_hand = read_json(config.CALLS_FILE, {}, notes), set(read_json(config.PAUSE_FILE, [], notes))
        self.called = set()
        for race_id, race in races.items():
            if race_id in by_hand:
                result = blank(race, "paused by hand (pause.json)")
            elif race_id in calls and (called := self.call(race_id, race, calls[race_id], now, notes)):
                result = called
            else:
                result, held_back = self.guard_race(race_id, race, {c: fair_values[(race_id, c)] for c in race["outcomes"]}, now, notes)
                waiting += held_back
            out.update({(race_id, code): fair for code, fair in result.items()})
        if waiting:
            notes.append(f"guard: {waiting} fair value(s) changed and are used once they hold {config.ELECTION_PERSIST_LOOPS} loops")
        return out, notes

    def call(self, race_id, race, won, now, notes):
        """The fair values of a race called by hand, or None (with a note) when the call is unknown or no source backs it."""
        if won not in race["outcomes"]:
            notes.append(f"guard: {race_id} call ignored: no outcome {won!r}")
            return None
        sources, _ = fresh_sources(race["outcomes"][won], now)
        if not any(mid >= config.ELECTION_CALL_MIN_MARKET for mid, _ in sources.values()):
            notes.append(f"guard: {race_id} call for {won} ignored: no source has it at {config.ELECTION_CALL_MIN_MARKET:.2f} or more")
            return None
        self.called.add(race_id)
        rest = (1 - config.ELECTION_CALL_FAIR) / (len(race["outcomes"]) - 1)
        return {code: Fair(config.ELECTION_CALL_FAIR if code == won else rest, f"called for {won} (calls.json)", 2, uncertainty=FLOOR)
                for code in race["outcomes"]}

    def guard_race(self, race_id, race, fair, now, notes):
        """(the race's fair values, how many are waiting to hold) after freshness, the breaker and persistence."""
        until = self.paused_until.get(race_id)
        if until and now < until:
            return blank(race, f"breaker: paused for {int(until - now)}s more"), 0
        views = {code: fresh_sources(race["outcomes"][code], now) for code in race["outcomes"]}
        if any(missing for _, missing in views.values()):
            return blank(race, f"{'/'.join(sorted({n for _, missing in views.values() for n in missing}))} is older than "
                               f"{config.ELECTION_STALE_SECONDS}s or missing: race skipped"), 0
        moves, tripped = {}, False
        for code, f in fair.items():
            if f.value is None:
                continue
            history = self.history[(race_id, code)]
            while history and now - history[0][0] > config.ELECTION_BREAKER_SECONDS:
                history.popleft()
            moves[code] = abs(f.value - history[0][1]) if history else 0.0
            if moves[code] > config.ELECTION_BREAKER_MOVE and TRUSTED not in views[code][0]:   # Polymarket is trusted, or two sources agree: a real call moves both
                tripped = True
            history.append((now, f.value))
        if tripped:
            self.paused_until[race_id] = now + config.ELECTION_BREAKER_MINUTES * 60
            notes.append(f"guard: {race_id} paused {config.ELECTION_BREAKER_MINUTES} min: a fair moved more than "
                         f"{config.ELECTION_BREAKER_MOVE:.2f} on a single source")
            return blank(race, "breaker: a one-source move"), 0
        out, held_back = {}, 0
        for code, f in fair.items():
            if f.value is None:
                out[code] = f
                continue
            used = self.persist((race_id, code), f.value)
            held_back += used != f.value
            out[code] = replace(f, value=used, uncertainty=uncertainty(views[code][0], moves[code]),
                                note=f.note + (f" [raw {f.value:.3f} not held yet]" if used != f.value else ""))
        return out, held_back

    def persist(self, key, value):
        """The fair in use for this outcome: its last confirmed value until a changed one has held ELECTION_PERSIST_LOOPS loops in a row."""
        confirmed = self.confirmed.get(key)
        if confirmed is None or abs(value - confirmed) <= config.ELECTION_PERSIST_BAND:
            self.pending.pop(key, None)
            self.confirmed[key] = value
            return value
        pending = self.pending.get(key)
        loops = pending[1] + 1 if pending and abs(value - pending[0]) <= config.ELECTION_PERSIST_BAND else 1
        if loops >= config.ELECTION_PERSIST_LOOPS:
            self.pending.pop(key, None)
            self.confirmed[key] = value
            return value
        self.pending[key] = [value, loops]
        return confirmed
