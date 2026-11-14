"""How much each market has traded (SUSQies), kept in a small cache across restarts, and what that allows the fast book to do.

    unknown volume                       no fast entry
    traded since yesterday < MIN_RECENT_VOLUME   no fast entry (the market has gone quiet)
    all-time volume < LIQUIDITY_LOW      only a big fast part, and then a small slice (THIN_SLICES)
    LIQUIDITY_LOW to LIQUIDITY_HIGH      MEDIUM_SLICE_FACTOR of a fast slice
    LIQUIDITY_HIGH and above             a full slice
"""
import json
import os
from dataclasses import asdict, dataclass

import config
from strategy import EPS


@dataclass(frozen=True)
class Volume:
    total: float       # SUSQies traded in the market, all time (as far back as SIG's daily candles go)
    recent: float      # SUSQies traded since the start of yesterday (UTC)
    fetched_at: float
    week: float | None = None  # SUSQies traded over the last 7 UTC days; None: not read yet (a cache file from before this field)


class VolumeStore:
    """The latest Volume of every market that has been read."""

    def __init__(self, volumes=None):
        self._volumes = dict(volumes or {})

    def get(self, market_id):
        return self._volumes.get(market_id)

    def update(self, volumes):
        self._volumes.update(volumes)

    def stale(self, market_ids, now):
        """The markets with no volume, no week volume yet, or one older than VOLUME_TTL_HOURS: never-read first, then oldest first."""
        age_limit = now - config.VOLUME_TTL_HOURS * 3600
        old = [(v.fetched_at if v else 0.0, m) for m in market_ids
               if (v := self.get(m)) is None or v.week is None or v.fetched_at < age_limit]
        return [m for _, m in sorted(old)]

    def save(self, path=None):
        path = path or config.VOLUME_CACHE_FILE
        with open(path + ".tmp", "w") as f:  # written whole then renamed, so a crash cannot leave half a file
            json.dump({m: asdict(v) for m, v in self._volumes.items()}, f)
        os.replace(path + ".tmp", path)

    @classmethod
    def load(cls, path=None):
        path = path or config.VOLUME_CACHE_FILE
        if not os.path.exists(path):
            return cls()
        with open(path) as f:
            rows = json.load(f)
        # a week of 0 was how an old cache file said "unknown"; a market that really traded nothing is read once more at start-up
        return cls({m: Volume(**{**row, "week": row.get("week") or None}) for m, row in rows.items()})


def millions(x):
    return f"{x / 1e6:.1f}M"


def describe(volume):
    """A market's liquidity condition for a report: unknown, quiet (traded little since yesterday), thin, medium or high,
    with its all-time volume."""
    if volume is None:
        return "unknown"
    if volume.recent < config.MIN_RECENT_VOLUME:
        kind = "quiet"
    elif volume.total < config.LIQUIDITY_LOW:
        kind = "thin"
    else:
        kind = "high" if volume.total >= config.LIQUIDITY_HIGH else "medium"
    return f"{kind} {millions(volume.total)}"


def slice_factor(volume, part):
    """The share of a fast slice a market with this Volume may take for a bet with fast part `part`; 0 when the market is
    too thin for a bet that small."""
    if volume.total >= config.LIQUIDITY_HIGH:
        return 1.0
    if volume.total >= config.LIQUIDITY_LOW:
        return config.MEDIUM_SLICE_FACTOR
    return next((share for floor, share in config.THIN_SLICES if part >= floor - EPS), 0.0)


def liquidity_blocker(volume, part):
    """Why the fast book may not enter a bet with fast part `part` in a market with this Volume (None: not read yet), or None."""
    if volume is None:
        return "volume not known yet"
    if volume.recent < config.MIN_RECENT_VOLUME:
        return f"only {volume.recent:,.0f} traded since yesterday: below {config.MIN_RECENT_VOLUME:,.0f}"
    if slice_factor(volume, part) == 0:
        return (f"volume {millions(volume.total)} is below {millions(config.LIQUIDITY_LOW)}: "
                f"a fast part of {config.THIN_SLICES[-1][0] * 100:.0f}c or more is needed, not {part * 100:+.1f}c")
    return None
