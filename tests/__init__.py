import os

os.environ.setdefault("SIG_API_KEY", "test-key")  # config refuses to import without a key; tests never use it

import config  # noqa: E402

# The tests assume these values, whatever settings.json and config.py say today, so changing a limit (or going live)
# never breaks them. They are pinned for the test run only; the bot itself reads the real values.
TEST_CONFIG = {
    "INVEST_CAP": 0.60, "RACE_CAP": 0.30, "POSITION_CAP": 0.25, "HOLD_BOOK_PCT": 0.30, "FAST_BOOK_PCT": 0.30,
    "ORDER_CAP_PCT": 0.02, "MAX_DAILY_LOSS": 0.10, "MAX_CONSECUTIVE_ERRORS": 5,
    "DRY_RUN": True, "STRATEGY": "v1", "MIN_EDGE": 0.05, "HOLD_MIN_EDGE": 0.03, "FAST_MIN_EDGE": 0.03,
    "LIQUIDITY_LOW": 1_000_000, "LIQUIDITY_HIGH": 5_000_000, "MEDIUM_SLICE_FACTOR": 0.5, "THIN_SLICES": ((0.10, 0.25), (0.05, 0.125)), "MIN_RECENT_VOLUME": 5_000,
    "VOLUME_TTL_HOURS": 12,
}
for name, value in TEST_CONFIG.items():
    setattr(config, name, value)
