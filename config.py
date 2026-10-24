"""Settings. The SIG key is read from SIG_API_KEY in .env (or the environment)."""
import json
import os
from collections import namedtuple

from dotenv import load_dotenv

RACES_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(RACES_DIR, ".env"))

SIG_API_KEY = os.environ.get("SIG_API_KEY", "").strip()
if not SIG_API_KEY:
    raise SystemExit("SIG_API_KEY is missing or empty. Set it in .env (SIG_API_KEY=your_key) and rerun.")

# Risk limits live in settings.json so they are easy to see and change. Unknown keys and out-of-range values stop the bot.
SETTINGS_FILE = os.path.join(RACES_DIR, "settings.json")
_SETTINGS_RULES = {  # name -> (lowest allowed, highest allowed)
    "max_invested": (0.01, 1.0),            # share of total equity that may be invested
    "max_per_race": (0.01, 1.0),            # share of total equity in one race
    "max_order_pct_of_balance": (0.0001, 1.0),  # hard cap on a single buy, as a share of the cash balance
    "max_daily_loss": (0.01, 1.0),          # kill switch: fall in total equity since the start of the day
    "max_consecutive_errors": (1, 100),     # kill switch: loops in a row that failed
}


def _load_settings(path):
    try:
        with open(path) as f:
            settings = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"Cannot read {path}: {e}")
    unknown = sorted(set(settings) - set(_SETTINGS_RULES))
    missing = sorted(set(_SETTINGS_RULES) - set(settings))
    if unknown or missing:
        raise SystemExit(f"{path}: unknown settings {unknown}, missing settings {missing}")
    for name, (low, high) in _SETTINGS_RULES.items():
        value = settings[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise SystemExit(f"{path}: {name} must be a number from {low} to {high}, got {value!r}")
    return settings


_SETTINGS = _load_settings(SETTINGS_FILE)
INVEST_CAP = _SETTINGS["max_invested"]
RACE_CAP = _SETTINGS["max_per_race"]
ORDER_CAP_PCT = _SETTINGS["max_order_pct_of_balance"]
MAX_DAILY_LOSS = _SETTINGS["max_daily_loss"]
MAX_CONSECUTIVE_ERRORS = int(_SETTINGS["max_consecutive_errors"])

SIG_BASE = "https://www.thesuper.market/api/v1"
SIG_TOURNAMENT_ID = None  # no tournaments
SIG_TOURNAMENT_SLUG = None  # set together with the ID: positions are then read from the tournament
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE = "https://clob.polymarket.com"

RACES_FILE = os.path.join(RACES_DIR, "races.json")

Regime = namedtuple("Regime", "order_timeout loop_pause")  # seconds
REGIMES = {
    "slow": Regime(30, 60),      # overnight US time, weekends, nothing happening
    "normal": Regime(10, 20),    # most days, polls and news coming out
    "election": Regime(3, 5),    # results coming in, prices moving fast
}
REGIME = "election"  # slow | normal | election
DRY_RUN = True

# Strategy rules (plan.md)
STALE_SECONDS = 60   # drop a Kalshi/Polymarket quote older than this
MAX_SPREAD = 0.03    # drop a Kalshi/Polymarket quote with a wider spread
MAX_DISAGREE = 0.04  # no fair value if the two midpoints differ by more
MIN_EDGE = 0.05      # smallest edge worth trading
TOP_N = 4            # trades kept, ranked by Kelly

# Sizing and swaps
SINGLE_SOURCE_RACE_CAP = 0.15  # the cap when the race's fair value rests on only one price source
SWAP_MARGIN = 0.01      # a new bet must beat the worst held one by this much expected return
MIN_ORDER_VALUE = 10.0  # ignore buys smaller than this; "no investable cash" means less than this

# Kill switch
STOP_FILE = os.path.join(RACES_DIR, "STOP")  # create this file to stop the bot
DAY_START_FILE = os.path.join(RACES_DIR, ".day_start.json")  # equity at the start of the day, kept across restarts

LOG_DIR = os.path.join(RACES_DIR, "logs")

# SIG allows 100 reads a minute per account; stay under it
SIG_READS_PER_MINUTE = 90
MAX_DEPTH_READS_PER_LOOP = 6  # order-book reads per loop, for the contracts about to trade
SIG_WRITES_PER_MINUTE = 25    # SIG allows 30 writes (orders and cancels) a minute; stay under it
MAX_ORDERS_PER_LOOP = 6    # sells first, then buys in Kelly order; the rest wait for the next loop

HTTP_TIMEOUT = 10  # seconds
