"""The two-book strategy's state between loops: the gap history (sampled on the quarter hour) and the ledger of lots."""
import time

import config
import gapmodel
import logbook
import sig
from backfill import backfill
from feeds import fetch_all
from hold import bucket_of
from liquidity import VolumeStore
from races import iter_contracts
from strategy import EPS, compute_fair_values


def prepare_history(races, state):
    """Before trading: load the saved gap history, back-fill what is missing from the three price histories, and add the
    current gap. Needs live prices, so it fetches them once."""
    fetch_all(races)
    now = time.time()
    state.gaps = gapmodel.GapStore.load()
    filled = backfill(races, state.gaps, now)
    state.gaps.add_all(gapmodel.current_gaps(races, compute_fair_values(races, now)), now)
    state.gaps.prune(now)
    state.gaps.save()
    print(f"  gap history ready: {filled} contracts back-filled")
    state.volumes = VolumeStore.load()
    read = refresh_volumes(races, state.volumes, now, limit=None)
    print(f"  volumes ready: {read} markets read")


def refresh_volumes(races, volumes, now, limit):
    """Read the traded volume of the markets with none or an old one (the stalest `limit`, or all of them) and save the cache.
    Returns how many were read."""
    stale = volumes.stale([c["market_id"] for _, c in iter_contracts(races)], now)[:limit]
    if stale:
        volumes.update(sig.fetch_volumes(races, stale))
        volumes.save()
    return len(stale)


def opportunity_rows(candidates, baselines, min_edge):
    """orders-log rows for the candidates worth keeping (edge at least min_edge), each with its bucket, usual gap and fast
    part when the market has a baseline (v2); v1 passes no baselines and gets blank columns."""
    rows = []
    for c in candidates:
        if c.edge < min_edge - EPS:
            continue
        b = baselines.get(c.market_id)
        if b is None or b.signed is None:
            rows.append(logbook.opportunity_row(c))
        else:
            rows.append(logbook.opportunity_row(c, bucket_of(c.fair)[0], b.for_side(c.side), gapmodel.fast_part(b, c.side, c.edge)))
    return rows


def refresh(races, fair_values, positions, state, now):
    """Each loop: take the 15-minute gap sample when one is due, re-read a few stale volumes, make the ledger agree with the SIG positions, and return
    (baselines by market id, notes about what the ledger changed). Only markets in races.json are tracked."""
    if gapmodel.sample_due(state.gaps.last_sample(), now):
        state.gaps.add_all(gapmodel.current_gaps(races, fair_values), now)
        state.gaps.prune(now)
        state.gaps.save()
    refresh_volumes(races, state.volumes, now, config.VOLUME_REFRESH_PER_LOOP)
    market_ids = {c["market_id"] for _, c in iter_contracts(races)}
    notes = state.ledger.reconcile([p for p in positions if p["market_id"] in market_ids], now)
    return state.gaps.baselines(market_ids, now), notes
