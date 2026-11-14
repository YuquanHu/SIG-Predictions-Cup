import os

os.environ.setdefault("SIG_API_KEY", "test-key")  # config refuses to import without a key; tests never use it

import config  # noqa: E402

# The tests assume these values, whatever settings.json and config.py say today, so changing a limit (or going live)
# never breaks them. They are pinned for the test run only; the bot itself reads the real values.
TEST_CONFIG = {
    "INVEST_CAP": 0.60, "RACE_CAP": 0.30, "POSITION_CAP": 0.25, "HOLD_BOOK_PCT": 0.30, "FAST_BOOK_PCT": 0.30,
    "ORDER_CAP_PCT": 0.02, "MAX_DAILY_LOSS": 0.10, "MAX_CONSECUTIVE_ERRORS": 5,
    "DRY_RUN": True, "STRATEGY": "v1", "MIN_EDGE": 0.05, "HOLD_MIN_EDGE": 0.03, "HOLD_BIG_EDGE": 0.10, "TOPUP_FREEZE_DROP": 0.10, "TOPUP_FREEZE_HOURS": 6, "HOLD_MIN_Z": 0.0, "ELECTION_STALE_SECONDS": 15, "ELECTION_BREAKER_MOVE": 0.15, "ELECTION_BREAKER_SECONDS": 60, "ELECTION_BREAKER_MINUTES": 3,
    "ELECTION_PERSIST_LOOPS": 2, "ELECTION_PERSIST_BAND": 0.03, "ELECTION_K": 1.0, "ELECTION_MOVE_WEIGHT": 0.5, "ELECTION_UNCALLED_SIZE": 0.5,
    "ELECTION_CALL_FAIR": 0.99, "ELECTION_CALL_MIN_MARKET": 0.90, "HOLD_SWAP_Z": 0.30, "HOLD_SWITCH_Z": 0.10, "HOLD_SLOTS": {"sure bet": 1, "favourite": 1, "coinflip": 1, "unlikely": 1, "longshot": 1}, "FAST_MIN_EDGE": 0.03, "FAST_MAX_POSITIONS": 5, "FAST_MAX_SPREAD": 0.03, "HOLD_MAX_SPREAD": 0.03,
    "LIQUIDITY_LOW": 1_000_000, "LIQUIDITY_HIGH": 5_000_000, "MEDIUM_SLICE_FACTOR": 0.5, "THIN_SLICES": ((0.10, 0.25), (0.05, 0.125)), "MIN_RECENT_VOLUME": 5_000,
    "VOLUME_TTL_HOURS": 12,
    "PATIENT_EXITS": False, "PATIENT_TRIGGER": 0.03, "PATIENT_MAX_MINUTES": 60, "BIG_ORDER_WEEK_SHARE": 0.04, "EARLY_EXITS": False, "EARLY_EXIT_HOURS": 24, "EARLY_EXIT_BAND": 0.01, "EARLY_EXIT_MAX_TICKS": 3, "EARLY_EXIT_MIN_PROFIT": 0.01, "WALL_RATIO": 3, "WALL_MIN_VALUE": 1_000, "PATIENT_ENTRIES": False, "PATIENT_ENTRY_MINUTES": 10, "PATIENT_ENTRY_SHORT_PART": 0.08, "PATIENT_ENTRY_SHORT_MINUTES": 5, "PATIENT_ENTRY_MAX_PART": 0.10, "PATIENT_REBID_SPREAD": 0.015, "PATIENT_MAX_BIDS": 3, "BIG_ORDER_CAP": 50_000,
    "BIG_ORDER_MIN": 500, "SWAP_BID_BAND": 0.01, "DEPTH_LEVELS": 10,
    "SWAP_MARGIN": 0.01, "FAST_SWAP_Z": 0.40, "BASELINE_FROZEN_AT": None, "FAST_HOLD_UNTIL": None,
}
missing = [name for name in TEST_CONFIG if not hasattr(config, name)]
if missing:  # pinning would add them and hide that the bot itself would stop on them
    raise ImportError(f"config.py does not define {', '.join(missing)}")
for name, value in TEST_CONFIG.items():
    setattr(config, name, value)
