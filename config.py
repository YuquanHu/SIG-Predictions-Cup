"""Settings. The SIG key is read from SIG_API_KEY in .env (or the environment)."""
import os

from dotenv import load_dotenv

RACES_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(RACES_DIR, ".env"))

SIG_API_KEY = os.environ.get("SIG_API_KEY", "").strip()
if not SIG_API_KEY:
    raise SystemExit("SIG_API_KEY is missing or empty. Set it in .env (SIG_API_KEY=your_key) and rerun.")

SIG_BASE = "https://www.thesuper.market/api/v1"
SIG_TOURNAMENT_ID = None  # no tournaments
SIG_TOURNAMENT_SLUG = None  # set together with the ID: positions are then read from the tournament
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE = "https://clob.polymarket.com"

RACES_FILE = os.path.join(RACES_DIR, "races.json")

REGIME = "normal"  # slow | normal | election
DRY_RUN = True

# Strategy rules (plan.md)
STALE_SECONDS = 60   # drop a Kalshi/Polymarket quote older than this
MAX_SPREAD = 0.03    # drop a Kalshi/Polymarket quote with a wider spread
MAX_DISAGREE = 0.04  # no fair value if the two midpoints differ by more
MIN_EDGE = 0.04      # smallest edge worth trading
TOP_N = 4            # trades kept, ranked by Kelly

HTTP_TIMEOUT = 10  # seconds
