"""Kill switch: STOP file, consecutive errors, and daily loss."""
import json
import os
from dataclasses import dataclass, field
from datetime import date

import config
from books import Ledger
from cooldown import Cooldown
from gapmodel import GapStore
from guard import FairGuard
from liquidity import VolumeStore
from patient import PatientBook


@dataclass
class RunState:
    errors_in_row: int = 0
    orders_may_be_open: bool = True  # cancel-all is only needed after orders went out (and at start-up)
    seen_warnings: set = field(default_factory=set)
    day: date | None = None
    day_start_equity: float | None = None
    cooldown: Cooldown = field(default_factory=lambda: Cooldown(config.COOLDOWN_SECONDS))
    gaps: GapStore = field(default_factory=GapStore)  # two-book strategy: the gap history
    ledger: Ledger = field(default_factory=Ledger)    # two-book strategy: which book owns which shares
    patient: PatientBook = field(default_factory=PatientBook)  # two-book strategy: the fast sells resting on SIG between loops
    guard: FairGuard = field(default_factory=FairGuard)  # the election-night guard on the fair values (REGIME election only)
    volumes: VolumeStore = field(default_factory=VolumeStore)  # two-book strategy: traded volume per market (fast book liquidity)


def new_warnings(state, current):
    """The warnings not already seen last loop, so an outage is logged once, not every loop.
    A warning that goes away and comes back counts as new again."""
    fresh = [w for w in current if w not in state.seen_warnings]
    state.seen_warnings = set(current)
    return fresh


def stop_file_exists():
    return os.path.exists(config.STOP_FILE)


def check_stop(state):
    """Reason to stop before doing anything this loop (STOP file, too many errors), or None."""
    if stop_file_exists():
        return f"STOP file found at {config.STOP_FILE}"
    if state.errors_in_row >= config.MAX_CONSECUTIVE_ERRORS:
        return f"{state.errors_in_row} errors in a row"
    return None


def set_day_start(state, equity):
    """Record `equity` as today's starting equity and keep it on disk, so a restart does not reset the loss limit."""
    state.day, state.day_start_equity = date.today(), equity
    with open(config.DAY_START_FILE, "w") as f:
        json.dump({"date": state.day.isoformat(), "equity": equity}, f)


def load_day_start(state):
    """Reuse today's starting equity from an earlier run, if there is one. Returns True when it did."""
    try:
        with open(config.DAY_START_FILE) as f:
            saved = json.load(f)
        if saved["date"] == date.today().isoformat():
            state.day, state.day_start_equity = date.today(), float(saved["equity"])
            return True
    except (OSError, ValueError, KeyError):
        pass
    return False


def check_loss(state, equity):
    """Reason to stop if total equity (cash + positions at their sell price) is down MAX_DAILY_LOSS from the
    start of the day. The first reading of a day (or the --day-start-equity value) is the baseline."""
    if state.day != date.today():
        set_day_start(state, equity)
    elif equity < state.day_start_equity * (1 - config.MAX_DAILY_LOSS):
        return f"down {1 - equity / state.day_start_equity:.1%} today ({state.day_start_equity:,.0f} -> {equity:,.0f})"
    return None
