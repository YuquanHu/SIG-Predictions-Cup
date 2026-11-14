"""Settings. The SIG key is read from SIG_API_KEY in .env (or the environment)."""
import json
import os
import time
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
    "election": Regime(4, 6),    # results coming in, prices moving fast
}
REGIME = "election"  # slow | normal | election
DRY_RUN = True

# Strategy rules (plan.md)
STALE_SECONDS = 60   # drop a Kalshi/Polymarket quote older than this
MAX_SPREAD = 0.03    # drop a Kalshi/Polymarket quote with a wider spread
MAX_DISAGREE = 0.04  # no fair value if the two midpoints differ by more
# Election-night guard (guard.py), active only when REGIME is "election"
ELECTION_STALE_SECONDS = 15    # a source the outcome is meant to have must have been fetched this recently, else the race is skipped
ELECTION_BREAKER_MOVE = 0.15   # a fair that moved more than this within ELECTION_BREAKER_SECONDS...
ELECTION_BREAKER_SECONDS = 60
ELECTION_BREAKER_MINUTES = 3   # ...without both sources following pauses the race this long (a real call moves both sources)
ELECTION_PERSIST_LOOPS = 2     # a changed fair is used only after it held this many loops in a row...
ELECTION_PERSIST_BAND = 0.03   # ...within this
ELECTION_K = 1.0               # z_adj = (edge - K x s) / sqrt(p (1 - p)), s = the fair value's uncertainty (guard.uncertainty)
ELECTION_MOVE_WEIGHT = 0.5     # the share of a 60s move that counts towards s
ELECTION_UNCALLED_SIZE = 0.5   # a buy in a race not in calls.json gets this share of the single-order cap
ELECTION_CALL_FAIR = 0.99      # the fair of an outcome you put in calls.json...
ELECTION_CALL_MIN_MARKET = 0.90  # ...if at least one source has it at this or more (so a typo cannot become a full-size buy)
CALLS_FILE = os.path.join(RACES_DIR, "calls.json")   # {"RI-SEN": "D"}: race id -> the outcome that won; read every loop
PAUSE_FILE = os.path.join(RACES_DIR, "pause.json")   # ["TX-15-HOUSE"]: races to leave alone; read every loop
PROBABILITY_TOLERANCE = 0.05  # a race whose outcome fair values add up to 1 +/- this is scaled to exactly 1; further off, a candidate is probably missing
MIN_EDGE = 0.07     # smallest edge worth trading
TOP_N = 4            # trades kept, ranked by Kelly

# Sizing and swaps
SINGLE_SOURCE_RACE_CAP = 0.15  # the cap when the race's fair value rests on only one price source
SWAP_MARGIN = 0.03      # v1: a new bet must beat the worst held one by this much expected return
FAST_SWAP_Z = 0.25       # v2 fast book: a new entry must have this much more z (fast part / sqrt(p (1 - p)), as the hold book's z) than the worst held
                         # position. A swap sells at the bid and buys at the ask (~2.5c) and each fair has ~1.5c of noise: that is 0.13 z on a coinflip, 0.3 on a 0.95 bet
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
FAST_MAX_HOURS = 48         # time stop: a fast position is sold after this long even if SIG has not caught up


def _local_time(text):
    """Epoch seconds of a local "YYYY-MM-DD HH:MM", or None for None."""
    return None if text is None else time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


# Election window (2026-11-02): SIG marks favourites down while Kalshi/Polymarket stay put, an edge earned at resolution
BASELINE_FROZEN_AT = _local_time("2026-11-02 00:00")  # the usual gap is taken from the 48h before this (before the shift), so a gap that opened since stays a fast part; None: the last 48h
FAST_HOLD_UNTIL = _local_time("2026-11-05 12:00")     # until then the fast book sells only on a negative edge or a swap: no time stop, no caught-up, patient or early exit; None: off
FAST_MAX_SPREAD = 0.02      # a fast entry needs a SIG spread no wider than this (the way out costs the spread too)
LIQUIDITY_LOW = 1_000_000    # a fast entry needs at least this much traded volume (SUSQies, all time); below it: no entry
LIQUIDITY_HIGH = 5_000_000   # from here the fast slice is full size; between the two it is MEDIUM_SLICE_FACTOR of a slice
MEDIUM_SLICE_FACTOR = 0.5
THIN_SLICES = ((0.10, 0.25), (0.05, 0.125))  # under LIQUIDITY_LOW: (fast part of at least, share of a slice) best first; less than the last: no entry
MIN_RECENT_VOLUME = 5_000    # and at least this much traded since the start of yesterday (UTC): a dead market is no fast market
VOLUME_TTL_HOURS = 12        # a market's volume is read again after this long
VOLUME_REFRESH_PER_LOOP = 2  # stale volumes re-read per loop while trading (each is one SIG read)

PATIENT_EXITS = False       # True: a fast position whose remaining fast part drops below PATIENT_TRIGGER rests its sell above the bid; False: every exit sells at the bid, as before
PATIENT_TRIGGER = 0.03       # the remaining fast part below which the patient exit starts (instead of the 1c caught-up rule)
PATIENT_MAX_MINUTES = 60     # a patient exit rests until the position's time stop (FAST_MAX_HOURS from its first entry) plus this long, then is cancelled and sold at the bid
EARLY_EXITS = False         # True (needs PATIENT_EXITS): from its first entry a fast position lists all its fast shares at the structural value (fair - usual gap) for EARLY_EXIT_HOURS, then rests a patient exit until the time stop
EARLY_EXIT_HOURS = 24        # how long the early-exit listing lasts from the first fast entry (at most FAST_MAX_HOURS)
EARLY_EXIT_BAND = 0.01       # the listing is cancelled and re-listed when the structural value moves more than this from its value at listing
EARLY_EXIT_MAX_TICKS = 3     # the listing is at most this many ticks above the best ask (and never above the structural value)...
EARLY_EXIT_MIN_PROFIT = 0.01 # ...and goes in front of a liquidity wall only at this much over the average fast entry price
WALL_RATIO = 3               # an ask level is a liquidity wall when its value is this many times the neighbouring non-empty level's...
WALL_MIN_VALUE = 1_000       # ...and at least this many SUSQies
PATIENT_ENTRIES = True     # True (needs PATIENT_EXITS): a fast or hold buy (a new position or a top-up) on a SIG spread wider than one tick first rests its buy one tick above the best bid for PATIENT_ENTRY_MINUTES, then enters at the ask as usual
PATIENT_ENTRY_MINUTES = 10   # how long a patient entry's bid rests before what is left buys at the ask
PATIENT_ENTRY_SHORT_PART = 0.10    # a fast part (a hold buy: its edge) above this bids only PATIENT_ENTRY_SHORT_MINUTES...
PATIENT_ENTRY_SHORT_MINUTES = 5    # ...so a big gap is not lost while SIG catches up...
PATIENT_ENTRY_MAX_PART = 0.15      # ...and above this there is no patient entry: it buys at the ask at once (a resting bid is cancelled for it)
PATIENT_REBID_SPREAD = 0.015       # a bid that ended without filling in full is bid again, not bought at the ask, while the SIG spread is wider than this...
PATIENT_MAX_BIDS = 3               # ...and the bet has had fewer than this many bids in a row (then it buys at the ask, and bids no more for an hour; a full fill starts over). The gap must be under PATIENT_ENTRY_MAX_PART as for any bid
SWAP_STEP_MINUTES = 5        # with patient exits on, a swapped-out position sells by steps: a limit at the best bid rests this long, then what is left moves to the next bid level down
BIG_ORDER_WEEK_SHARE = 0.03  # a big order is this share of the market's volume of the last 7 days...
BIG_ORDER_CAP = 50_000       # but at most this many SUSQies...
BIG_ORDER_MIN = 500          # and at least this many
SWAP_BID_BAND = 0.01         # a swap sells into the bids this close to the best bid, and is priced at their average
DEPTH_LEVELS = 10            # order-book levels read per market
BUCKETS = (                  # (name, lowest fair probability, highest, share of the hold book); one position per bucket
    ("longshot", 0.00, 0.20, 0.02),
    ("unlikely", 0.20, 0.40, 0.10),
    ("coinflip", 0.40, 0.60, 0.15),
    ("favourite", 0.60, 0.90, 0.33),
    ("sure bet", 0.90, 1.00, 0.40),
)
HOLD_MIN_Z = 0.25            # a hold pick needs at least this z = edge / sqrt(p (1 - p)), its ROI over the SD of its return (the per-bet Sharpe):
                             # the same edge per unit of risk in every bucket, so the minimum ROI climbs steeply as the fair p falls
                             # (6% at p 0.95, 17% at 0.75, 33% at 0.5, 62% at 0.3 with the 5c edge floor; hold.roi_floor). It was 0.30 for part of 2026-11-03, a bucket table of 6-25% ROI before that
HOLD_SLOTS = {               # positions per bucket, each an equal slot of the bucket's share (an empty slot stays cash). The counts give every
    "sure bet": 3,           # bucket about the same dollar risk: slots ~ (the bucket's SD with one bet / the sure bet's)^2, the SD per SUSQie
    "favourite": 5,          # being sqrt(p (1 - p)) / price at the minimum-z price of the bucket's midpoint (favourite 5.2, coinflip 4.2,
    "coinflip": 4,           # unlikely 6.4, longshot 6.0; the sure bet would be 1 and gets 3 for single-race jump risk). One per bucket
    "unlikely": 6,           # until 2026-11-03
    "longshot": 6,
}
HOLD_MIN_EDGE = 0.05         # a hold entry needs at least this executable edge, whatever its z: it binds only above p ~0.95, where z lets 2.5-4c through (was 3c until 2026-11-03)
TOPUP_FREEZE_DROP = 0.10     # a bet held this far below its average entry (at its sell price) is not topped up by either book; nothing is sold
TOPUP_FREEZE_HOURS = 6       # until this long after the last loop it was that far down (a falling price whose fair did not fall may be a fair value gone stale)
HOLD_BIG_EDGE = 0.10         # a market that is not structural is still a hold entry when its executable edge is this big (other traders moved the price fast; held to resolution the whole edge is earned)
HOLD_MAX_SPREAD = 0.05      # and a SIG spread no wider than this
HOLD_SWAP_Z = 0.25           # a held bet is sold for a new one when the new bet has this much more z than the held bet at its sell price (the round trip costs the spread twice, as in the fast book); loop 1 builds the book from the held bets alone
HOLD_SWITCH_Z = 0.10         # a new bet takes the slot of another new bet (one not held yet, so nothing is sold) only with this much more z. Less than HOLD_SWAP_Z: no sale, so no spread to cover,
                             # only fair-value noise (and a half-built position left behind)
BUCKET_BAND = 0.02           # a held position keeps its bucket until its fair probability is this far outside it
STRATEGY = "v1"              # v1: top 4 by Kelly with swaps (plan.md steps 0-6). v2: two books (end of plan.md); main.py --strategy overrides
MIN_HISTORY_SAMPLES = int(MIN_HISTORY_FRACTION * HISTORY_HOURS * 60 / SAMPLE_MINUTES)  # 96 of the 192 samples in 48h

# Kill switch
STOP_FILE = os.path.join(RACES_DIR, "STOP")  # create this file to stop the bot
DAY_START_FILE = os.path.join(RACES_DIR, ".day_start.json")  # equity at the start of the day, kept across restarts
GAP_HISTORY_FILE = os.path.join(RACES_DIR, ".gap_history_norm.csv")  # the 15-minute gap samples (fair values scaled to add up to 1), kept across restarts
VOLUME_CACHE_FILE = os.path.join(RACES_DIR, ".volume_cache.json")  # traded volume per market, kept across restarts
LEDGER_FILE = os.path.join(RACES_DIR, ".ledger.json")  # which book owns how many shares of each positio
RESTING_FILE = os.path.join(RACES_DIR, ".resting.json")  # the orders resting on SIG (live runs), so a restart keeps following them
LOG_DIR = os.path.join(RACES_DIR, "logs")

# SIG allows 100 reads a minute per account; stay under it
SIG_READS_PER_MINUTE = 95
MAX_DEPTH_READS_PER_LOOP = 12  # order-book reads per loop, for the contracts about to trade (and the held fast positions a swap would price)
SIG_WRITES_PER_MINUTE = 27    # SIG allows 30 writes (orders and cancels) a minute; stay under it
MAX_ORDERS_PER_LOOP = 6    # sells first, then buys in Kelly order; the rest wait for the next loop

HTTP_TIMEOUT = 10  # seconds
