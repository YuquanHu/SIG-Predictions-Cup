"""Append-only CSV logs in logs/: one file per kind, header written when the file is created."""
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
    "loops": ["time", "loop", "regime", "cash", "invested", "bankroll", "opportunities", "orders", "status"],
}


def log(kind, loop_no, rows):
    """Write dict rows to logs/<kind>.csv; `time` and `loop` are filled in here."""
    if not rows:
        return
    os.makedirs(config.LOG_DIR, exist_ok=True)
    path = os.path.join(config.LOG_DIR, f"{kind}.csv")
    is_new = not os.path.exists(path)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS[kind])
        if is_new:
            writer.writeheader()
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
