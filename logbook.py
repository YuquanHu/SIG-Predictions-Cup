"""Append-only CSV logs in logs/, one file per kind. They record only what matters: orders, bets that clear the
minimum edge, loops that placed orders or stopped, and new errors (not the same warning every loop)."""
import csv
import os
import time

import config

COLUMNS = {
    "opportunities": ["time", "loop", "race", "market", "side", "price", "fair", "edge", "kelly",
                      "half_kelly", "avail_shares", "bucket", "usual_gap", "fast_part", "decision"],
    "orders": ["time", "loop", "event", "order_id", "book", "action", "race", "market", "side", "price", "shares",
               "filled", "value", "fair", "edge", "reason", "dry_run", "detail"],
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


def opportunity_row(c, bucket="", usual_gap="", fast_part=""):
    return {"race": c.race, "market": c.market_id, "side": c.side, "price": c.price, "fair": c.fair,
            "edge": c.edge, "kelly": c.kelly, "half_kelly": c.half_kelly, "avail_shares": c.size,
            "bucket": bucket, "usual_gap": usual_gap, "fast_part": fast_part, "decision": c.decision}


ORDER_EVENTS = ("would_place", "placed", "filled", "partial_fill", "cancelled", "rejected", "unconfirmed", "cancel_failed",
                "cancel_all")


def order_event(event, o=None, order_id="", filled="", detail=""):
    """One orders.csv row. `event` is one of ORDER_EVENTS; `o` is the Order (None for an account-wide cancel-all)."""
    assert event in ORDER_EVENTS, event
    row = {"event": event, "order_id": order_id, "filled": filled, "detail": detail, "dry_run": config.DRY_RUN}
    if o is not None:
        row.update(book=o.book, action=o.action, race=o.race, market=o.market_id, side=o.side, price=o.price, shares=o.shares,
                   value=o.value, fair=o.fair, edge=o.edge, reason=o.reason)
    return row
