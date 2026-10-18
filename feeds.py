"""Runs setup and price fetches across all three price sources."""
import kalshi
import polymarket
import sig

SOURCES = (sig, kalshi, polymarket)  # each exposes NAME, setup(races) and fetch_prices(races)


def _run_each(races, label, pick):
    """The sources already warn per race; this is the backstop so one crashing source can't stop the others."""
    for source in SOURCES:
        try:
            pick(source)(races)
        except Exception as e:
            print(f"  WARNING {source.NAME} {label} crashed for all races: {type(e).__name__}: {e}")


def setup_all(races):
    _run_each(races, "setup", lambda source: source.setup)


def fetch_all(races):
    _run_each(races, "fetch", lambda source: source.fetch_prices)
