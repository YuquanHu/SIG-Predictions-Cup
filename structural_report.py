#!/usr/bin/env python3
"""Stage 1 of the two-book plan: for every SIG contract, the usual gap to fair value over 48h, whether it is structural,
its probability bucket, and how much of today's edge is the fast part. Read-only: no orders.

    python3 structural_report.py            # back-fills missing history first (a few minutes on the first run)
    python3 structural_report.py --all      # also list contracts with no mispricing (usual gap under 1c)
"""
import argparse
import time

import config
import gapmodel
import sig
from backfill import backfill
from bookkeeping import refresh_volumes
from display import print_table
from feeds import fetch_all, setup_all
from hold import bucket_of
from liquidity import VolumeStore, describe
from races import iter_contracts, load_races
from strategy import EPS, compute_fair_values, find_opportunities

COLUMNS = ("race", "mkt", "side", "48h median", "p10(48h)", "2h median", "hurdle (80%)", "samples", "structural",
           "chance", "bucket", "edge now (ask/bid)", "fast part", "liquidity")
LEGEND = """
  usual      median gap over 48h on the side it favours: the baseline (M). The gap is fair value minus the SIG mid, sampled every 15 min.
  p10        10th percentile of the same 48h: the gap was at or above this 90% of the time (a near-worst case). Must be >= hurdle.
  last 2h    median gap over the last 2 hours: is the gap still there now? Must be >= hurdle.
  hurdle     min(0.8 x usual, usual - 1c). A market is STRUCTURAL when usual >= 3c and p10 and last 2h are both >= hurdle.
  edge now   fair minus the SIG ask (Yes) or the SIG bid minus fair (No): what you could trade at today.
  fast part  edge now - usual. Usual is measured on mids and edge now on the ask/bid, so in a steady market it sits about half a spread below 0.
             FAST means it is at least {x:.0f}c.
  liquidity  all-time traded volume in SUSQies, and the fast book's view of it: high ({high:.0f}M+, full slice), medium ({low:.0f}M+, half slice),
             thin (under {low:.0f}M: only a fast part of 5c+, small slice), quiet (under {recent:,} traded since yesterday: no fast entry), unknown."""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="also list contracts whose usual gap is under 1c")
    args = parser.parse_args()

    races = load_races(config.RACES_FILE)
    print(f"Loaded {len(races)} races. Fetching live prices...")
    setup_all(races)
    fetch_all(races)
    now = time.time()
    fair_values = compute_fair_values(races, now)

    store = gapmodel.GapStore.load()
    print(f"History on disk: {sum(store.count(c['market_id'], now) >= config.MIN_HISTORY_SAMPLES for _, c in iter_contracts(races))} "
          f"contracts have enough samples. Back-filling the rest (SIG reads are limited to {config.SIG_READS_PER_MINUTE} a minute)...")
    filled = backfill(races, store, now)
    store.add_all(gapmodel.current_gaps(races, fair_values), now)
    store.prune(now)
    store.save()
    volumes = VolumeStore.load()
    refresh_volumes(races, volumes, now, limit=None)
    print(f"Back-filled {filled} contracts; {sig.take_read_count()} SIG reads.")

    contracts = {c["market_id"]: (race_id, c) for race_id, c in iter_contracts(races)}
    baselines = store.baselines(contracts, now)
    edge = {(o.market_id, o.side): o for o in find_opportunities(races, fair_values)}

    rows, counts = [], {"structural": 0, "not structural": 0, "no mispricing": 0, "unclassified": 0}
    for market_id, b in baselines.items():
        race_id, _ = contracts[market_id]
        if b.signed is None:
            counts["unclassified"] += 1
            continue
        kind = "no mispricing" if b.size < config.NOISE_TOLERANCE else "structural" if b.structural else "not structural"
        counts[kind] += 1
        if kind == "no mispricing" and not args.all:
            continue
        o = edge.get((market_id, b.side))
        part = gapmodel.fast_part(b, o.edge) if o else None
        fast = "-" if part is None else f"{part * 100:+.1f}c" + ("  FAST" if part >= config.FAST_MIN_EDGE - EPS else "")
        rows.append((b.size, (race_id, market_id, b.side.upper(), f"{b.size * 100:.1f}c", f"{b.p10 * 100:.1f}c",
                              f"{b.recent * 100:.1f}c" if b.recent is not None else "-", f"{b.hurdle * 100:.1f}c", b.samples,
                              "STRUCTURAL" if b.structural else "no",
                              f"{o.fair:.0%}" if o else "-", bucket_of(o.fair)[0] if o else "-",
                              f"{o.edge * 100:+.1f}c" if o else "-", fast, describe(volumes.get(market_id)))))
    rows.sort(key=lambda r: -r[0])
    print(f"\nUsual gap = median over {config.HISTORY_HOURS}h on the side it favours; structural needs {config.STRUCTURAL_MIN_GAP * 100:.0f}c+, "
          f"a 10th percentile and a last-{config.RECENT_HOURS}h median above the hurdle. FAST = fast part >= {config.FAST_MIN_EDGE * 100:.0f}c.")
    print_table(COLUMNS, [r[1] for r in rows])
    print(LEGEND.format(x=config.FAST_MIN_EDGE * 100, high=config.LIQUIDITY_HIGH / 1e6, low=config.LIQUIDITY_LOW / 1e6,
                       recent=config.MIN_RECENT_VOLUME))
    print(f"\n{counts['structural']} structural, {counts['not structural']} gaps of 1c+ that are not structural, "
          f"{counts['no mispricing']} with no mispricing (under 1c), {counts['unclassified']} unclassified (too little history).")


if __name__ == "__main__":
    main()
