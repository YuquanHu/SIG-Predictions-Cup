"""Gap model: how far SIG sits from fair value, how long that has lasted, and which part of it is new. Pure logic.

The gap of a contract is fair_yes - SIG yes mid, sampled every SAMPLE_MINUTES on the clock. A market is structural when
its usual gap (the median over HISTORY_HOURS) is big and has been there nearly all the time; whatever an executable edge
shows beyond that usual gap is the fast part:

    M          = |median(gap_48h)|, on the side the gap favours
    hurdle     = min(0.8 * M, M - 0.01)
    structural = M >= 3c and p10(gap_48h) >= hurdle and median(gap_last_2h) >= hurdle
    fast part  = executable edge now - usual gap on that side (never below 0: no credit for a gap pointing the other way)
"""
import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

import config
from strategy import EPS

SAMPLE_SECONDS = config.SAMPLE_MINUTES * 60


def quarter(ts):
    """The sample time (clock-aligned) that `ts` falls in."""
    return ts - ts % SAMPLE_SECONDS


def sample_due(last_sample, now):
    return last_sample is None or quarter(now) > last_sample


def percentile(values, pct):
    """Linear-interpolated percentile (pct from 0 to 100) of a non-empty list."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass(frozen=True)
class Baseline:
    signed: float | None  # median gap over the window in Yes space: positive means SIG Yes is cheap; None: unclassified
    side: str | None      # the side the usual gap favours ("yes" or "no")
    size: float           # M: the usual gap on that side (0 when unclassified)
    hurdle: float
    structural: bool
    samples: int          # samples inside the window
    p10: float | None = None     # 10th percentile of the gap on that side over the window
    recent: float | None = None  # median gap on that side over the last RECENT_HOURS

    def for_side(self, side):
        """The usual gap on `side`'s own terms, or None when unclassified. A gap inside the noise tolerance is no gap."""
        if self.signed is None:
            return None
        if abs(self.signed) < config.NOISE_TOLERANCE - EPS:
            return 0.0
        return self.signed if side == "yes" else -self.signed


UNCLASSIFIED = Baseline(None, None, 0.0, 0.0, False, 0)


def classify(samples, now):
    """Baseline from [(time, gap)] samples (gap in Yes space). Fewer than MIN_HISTORY_SAMPLES in the window: unclassified."""
    window = [(t, g) for t, g in samples if now - t <= config.HISTORY_HOURS * 3600]
    if len(window) < config.MIN_HISTORY_SAMPLES:
        return Baseline(None, None, 0.0, 0.0, False, len(window))
    signed = median(g for _, g in window)
    sign = 1 if signed >= 0 else -1
    size = abs(signed)
    hurdle = min(0.8 * size, size - 0.01)
    recent = [sign * g for t, g in window if now - t <= config.RECENT_HOURS * 3600]
    p10 = percentile([sign * g for _, g in window], 10)
    recent_median = median(recent) if recent else None
    structural = (size >= config.STRUCTURAL_MIN_GAP - EPS and p10 >= hurdle - EPS
                  and recent_median is not None and recent_median >= hurdle - EPS)
    return Baseline(signed, "yes" if sign > 0 else "no", size, hurdle, structural, len(window), p10, recent_median)


def fast_part(baseline, side, edge):
    """The part of an executable edge on `side` beyond the usual gap on that side. None when unclassified (no fast entry).
    A usual gap that points the other way is no credit: it counts as 0, so a swing back from it is not a fast part."""
    usual = baseline.for_side(side)
    return None if usual is None else edge - max(usual, 0.0)


def current_gaps(races, fair_values):
    """{market_id: fair_yes - SIG yes mid} for every contract that has a fair value and a two-sided SIG quote."""
    gaps = {}
    for race_id, race in races.items():
        for c in race["contracts"]:
            fair = fair_values[(race_id, c["pays_if"])].value
            yes = c["yes"]
            if fair is not None and yes["bid"] is not None and yes["ask"] is not None:
                gaps[c["market_id"]] = fair - (yes["bid"] + yes["ask"]) / 2
    return gaps


class GapStore:
    """The gap samples of every contract, one per clock-aligned sample time, kept across restarts in a CSV file."""

    def __init__(self):
        self._samples = defaultdict(dict)  # market_id -> {sample time: gap}

    def add(self, market_id, ts, gap):
        self._samples[market_id][quarter(ts)] = gap

    def add_all(self, gaps, ts):
        for market_id, gap in gaps.items():
            self.add(market_id, ts, gap)

    def samples(self, market_id):
        return sorted(self._samples[market_id].items())

    def count(self, market_id, now):
        return sum(1 for t in self._samples[market_id] if now - t <= config.HISTORY_HOURS * 3600)

    def latest(self, market_id):
        """Time of the newest sample of one market, or None."""
        return max(self._samples[market_id], default=None)

    def last_sample(self):
        return max((max(s) for s in self._samples.values() if s), default=None)

    def prune(self, now):
        for market_id, samples in self._samples.items():
            for t in [t for t in samples if now - t > config.HISTORY_HOURS * 3600]:
                del samples[t]

    def baselines(self, market_ids, now):
        return {m: classify(self.samples(m), now) for m in market_ids}

    def save(self, path=None):
        path = path or config.GAP_HISTORY_FILE
        with open(path + ".tmp", "w", newline="") as f:  # written whole then renamed, so a crash cannot leave half a file
            writer = csv.writer(f)
            writer.writerow(["market_id", "time", "gap"])
            for market_id in sorted(self._samples):
                writer.writerows((market_id, int(t), round(g, 6)) for t, g in sorted(self._samples[market_id].items()))
        os.replace(path + ".tmp", path)

    @classmethod
    def load(cls, path=None):
        store = cls()
        path = path or config.GAP_HISTORY_FILE
        if os.path.exists(path):
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    store._samples[row["market_id"]][int(row["time"])] = float(row["gap"])
        return store
