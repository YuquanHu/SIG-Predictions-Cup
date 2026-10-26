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
    "max_position_pct": (0.01, 1.0),        # most total equity in any one position (market and side)
    "hold_book_pct": (0.0, 1.0),            # share of total equity the structural hold book may hold
    "fast_book_pct": (0.0, 1.0),            # share of total equity the fast opportunity book may hold
    "max_order_pct_of_equity": (0.0001, 1.0),  # hard cap on a single buy, as a share of total equity (cash + positions)
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
    if settings["hold_book_pct"] + settings["fast_book_pct"] > settings["max_invested"] + 1e-9:
        raise SystemExit(f"{path}: hold_book_pct + fast_book_pct must not be more than max_invested")
    return settings


_SETTINGS = _load_settings(SETTINGS_FILE)
INVEST_CAP = _SETTINGS["max_invested"]
RACE_CAP = _SETTINGS["max_per_race"]
ORDER_CAP_PCT = _SETTINGS["max_order_pct_of_equity"]
MAX_DAILY_LOSS = _SETTINGS["max_daily_loss"]
MAX_CONSECUTIVE_ERRORS = int(_SETTINGS["max_consecutive_errors"])
POSITION_CAP = _SETTINGS["max_position_pct"]
HOLD_BOOK_PCT = _SETTINGS["hold_book_pct"]
FAST_BOOK_PCT = _SETTINGS["fast_book_pct"]

SIG_BASE = "https://www.thesuper.market/api/v1"
SIG_TOURNAMENT_ID = None  # no tournaments
SIG_TOURNAMENT_SLUG = None  # set together with the ID: positions are then read from the tournament
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE = "https://clob.polymarket.com"

RACES_FILE = os.path.join(RACES_DIR, "races.json")

Regime = namedtuple("Regime", "order_timeout loop_pause")  # seconds
REGIMES = {
    "slow": Regime(30, 30),      # overnight US time, weekends, nothing happening (a fast move is noticed within about a minute)
    "normal": Regime(10, 20),    # most days, polls and news coming out
    "election": Regime(3, 5),    # results coming in, prices moving fast
}
REGIME = "normal"  # slow | normal | election
DRY_RUN = False

# Strategy rules (plan.md)
STALE_SECONDS = 60   # drop a Kalshi/Polymarket quote older than this
MAX_SPREAD = 0.03    # drop a Kalshi/Polymarket quote with a wider spread
MAX_DISAGREE = 0.04  # no fair value if the two midpoints differ by more
PROBABILITY_TOLERANCE = 0.05  # a race whose outcome fair values add up to 1 +/- this is scaled to exactly 1; further off, a candidate is probably missing
MIN_EDGE = 0.05      # smallest edge worth trading
TOP_N = 4            # trades kept, ranked by Kelly

# Sizing and swaps
SINGLE_SOURCE_RACE_CAP = 0.15  # the cap when the race's fair value rests on only one price source
SWAP_MARGIN = 0.01      # a new bet must beat the worst held one by this much expected return
MIN_ORDER_VALUE = 10.0  # ignore buys smaller than this; "no investable cash" means less than this
MIN_SWAP_PCT = 0.01     # a swap must sell at least this share of total equity; smaller ones cost more in spread than they earn
COOLDOWN_SECONDS = 300  # after trading a market, do not trade it the opposite way for this long (stops flip-flopping)

# Two books: structural hold + fast opportunity (gap model: gapmodel.py)
SAMPLE_MINUTES = 15          # the gap is sampled on the clock: :00, :15, :30, :45
HISTORY_HOURS = 48           # window for the usual gap (median) and its 10th percentile
RECENT_HOURS = 2             # the gap must also still be there over the last 2 hours
MIN_HISTORY_FRACTION = 0.5   # share of the window's samples needed to classify a market; below it: unclassified, no fast entry
NOISE_TOLERANCE = 0.01       # a gap of 0-1c counts as no mispricing
STRUCTURAL_MIN_GAP = 0.03    # the usual gap must be at least this for a market to be structural
FAST_MIN_EDGE = 0.04        # x: a fast entry needs this much edge over the structural baseline
FAST_MAX_POSITIONS = 5       # the fast book holds at most this many positions
FAST_POSITION_PCT = 0.20     # one fast position is at most this share of the fast book (a fixed slice)
FAST_MAX_HOURS = 24         # time stop: a fast position is sold after this long even if SIG has not caught up
FAST_MAX_SPREAD = 0.03       # a fast entry needs a SIG spread no wider than this (the way out costs the spread too)
LIQUIDITY_LOW = 1_000_000    # a fast entry needs at least this much traded volume (SUSQies, all time); below it: no entry
LIQUIDITY_HIGH = 5_000_000   # from here the fast slice is full size; between the two it is MEDIUM_SLICE_FACTOR of a slice
MEDIUM_SLICE_FACTOR = 0.5
THIN_SLICES = ((0.10, 0.25), (0.05, 0.125))  # under LIQUIDITY_LOW: (fast part of at least, share of a slice) best first; less than the last: no entry
MIN_RECENT_VOLUME = 5_000    # and at least this much traded since the start of yesterday (UTC): a dead market is no fast market
VOLUME_TTL_HOURS = 12        # a market's volume is read again after this long
VOLUME_REFRESH_PER_LOOP = 2  # stale volumes re-read per loop while trading (each is one SIG read)
BUCKETS = (                  # (name, lowest fair probability, highest, share of the hold book); one position per bucket
    ("longshot", 0.00, 0.20, 0.02),
    ("unlikely", 0.20, 0.40, 0.10),
    ("coinflip", 0.40, 0.60, 0.15),
    ("favourite", 0.60, 0.90, 0.33),
    ("sure bet", 0.90, 1.00, 0.40),
)
HOLD_MIN_EDGE = 0.03         # a hold entry needs at least this executable edge (the market is structural from 3c)
HOLD_MAX_SPREAD = 0.03       # and a SIG spread no wider than this
HOLD_SWITCH_MARGIN = 0.03    # a bucket's held pick is replaced only by one with this much more ROI
BUCKET_BAND = 0.02           # a held position keeps its bucket until its fair probability is this far outside it
STRATEGY = "v1"              # v1: top 4 by Kelly with swaps (plan.md steps 0-6). v2: two books (end of plan.md); main.py --strategy overrides
MIN_HISTORY_SAMPLES = int(MIN_HISTORY_FRACTION * HISTORY_HOURS * 60 / SAMPLE_MINUTES)  # 96 of the 192 samples in 48h

# Kill switch
STOP_FILE = os.path.join(RACES_DIR, "STOP")  # create this file to stop the bot
DAY_START_FILE = os.path.join(RACES_DIR, ".day_start.json")  # equity at the start of the day, kept across restarts
GAP_HISTORY_FILE = os.path.join(RACES_DIR, ".gap_history_norm.csv")  # the 15-minute gap samples (fair values scaled to add up to 1), kept across restarts
VOLUME_CACHE_FILE = os.path.join(RACES_DIR, ".volume_cache.json")  # traded volume per market, kept across restarts
LEDGER_FILE = os.path.join(RACES_DIR, ".ledger.json")  # which book owns how many shares of each position

LOG_DIR = os.path.join(RACES_DIR, "logs")

# SIG allows 100 reads a minute per account; stay under it
SIG_READS_PER_MINUTE = 90
MAX_DEPTH_READS_PER_LOOP = 6  # order-book reads per loop, for the contracts about to trade
SIG_WRITES_PER_MINUTE = 25    # SIG allows 30 writes (orders and cancels) a minute; stay under it
MAX_ORDERS_PER_LOOP = 6    # sells first, then buys in Kelly order; the rest wait for the next loop

HTTP_TIMEOUT = 10  # seconds
