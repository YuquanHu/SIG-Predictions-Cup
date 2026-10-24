"""Append-only CSV logs in logs/, one file per kind. They record only what matters: orders, bets that clear the
minimum edge, loops that placed orders or stopped, and new errors (not the same warning every loop)."""
import csv
import os
import time

import config

COLUMNS = {
    "opportunities": ["time", "loop", "race", "market", "side", "price", "fair", "edge", "kelly",
                      "half_kelly", "avail_shares", "decision"],
    "orders": ["time", "loop", "action", "race", "market", "side", "price", "fair", "edge", "shares",
               "value", "reason", "dry_run"],
    "errors": ["time", "loop", "where", "error"],
    "loops": ["time", "loop", "regime", "cash", "invested", "bankroll", "opportunities", "orders", "sig_reads", "sig_writes", "seconds", "status"],
}


def _path_for(kind):
    """logs/<kind>.csv, ready to append to. A file whose columns changed is renamed, not overwritten."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    path = os.path.join(config.LOG_DIR, f"{kind}.csv")
    if os.path.exists(path):
        with open(path, newline="") as f:
            if next(csv.reader(f), None) == COLUMNS[kind]:
                return path
        os.rename(path, path.replace(".csv", f".old-{int(time.time())}.csv"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerow(COLUMNS[kind])
    return path


def init():
    """Create logs/ and every log file with its header row, so they exist before anything is written."""
    for kind in COLUMNS:
        _path_for(kind)


def log(kind, loop_no, rows):
    """Write dict rows to logs/<kind>.csv; `time` and `loop` are filled in here."""
    if not rows:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(_path_for(kind), "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS[kind])
        for row in rows:
            writer.writerow({"time": stamp, "loop": loop_no, **row})


def opportunity_row(c):
    return {"race": c.race, "market": c.market_id, "side": c.side, "price": c.price, "fair": c.fair,
            "edge": c.edge, "kelly": c.kelly, "half_kelly": c.half_kelly, "avail_shares": c.size,
            "decision": c.decision}


def order_row(o):
    return {"action": o.action, "race": o.race, "market": o.market_id, "side": o.side, "price": o.price,
            "fair": o.fair, "edge": o.edge, "shares": o.shares, "value": o.value, "reason": o.reason,
            "dry_run": config.DRY_RUN}
