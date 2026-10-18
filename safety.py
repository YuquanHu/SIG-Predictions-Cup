"""Kill switch: STOP file, consecutive errors, and daily loss."""
import os
from dataclasses import dataclass
from datetime import date

import config


@dataclass
class RunState:
    errors_in_row: int = 0
    day: date | None = None
    day_start_equity: float | None = None


def stop_file_exists():
    return os.path.exists(config.STOP_FILE)


def check_stop(state):
    """Reason to stop before doing anything this loop (STOP file, too many errors), or None."""
    if stop_file_exists():
        return f"STOP file found at {config.STOP_FILE}"
    if state.errors_in_row >= config.MAX_CONSECUTIVE_ERRORS:
        return f"{state.errors_in_row} errors in a row"
    return None


def check_loss(state, equity):
    """Reason to stop if equity (cash + positions) is down MAX_DAILY_LOSS since the day's first reading."""
    today = date.today()
    if state.day != today:
        state.day, state.day_start_equity = today, equity
    elif equity < state.day_start_equity * (1 - config.MAX_DAILY_LOSS):
        return f"down {1 - equity / state.day_start_equity:.1%} today ({state.day_start_equity:,.0f} -> {equity:,.0f})"
    return None
