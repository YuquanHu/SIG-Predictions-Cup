"""Runs setup and price fetches across all three price sources."""
import kalshi
import polymarket
import sig
from parallel import run_all

SOURCES = (sig, kalshi, polymarket)  # each exposes NAME, setup(races) and fetch_prices(races)


def _run_one(races, label, source, fn):
    """The sources already warn per race; this is the backstop so one crashing source can't stop the others."""
    try:
        fn(races)
    except Exception as e:
        print(f"  WARNING {source.NAME} {label} crashed for all races: {type(e).__name__}: {e}")


def setup_all(races):
    run_all([lambda s=s: _run_one(races, "setup", s, s.setup) for s in SOURCES])


def fetch_all(races):
    """Fetch every source at the same time: each writes to its own part of each race."""
    run_all([lambda s=s: _run_one(races, "fetch", s, s.fetch_prices) for s in SOURCES])
