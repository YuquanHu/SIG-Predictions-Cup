"""Order placement. Dry run only: nothing here can reach SIG until the live stage is built."""
from dataclasses import dataclass

import budget
import config


@dataclass(frozen=True)
class Order:
    action: str    # "buy" or "sell"
    side: str      # "yes" or "no"
    race: str
    market_id: str
    shares: float
    price: float   # limit price per share of `side`
    fair: float    # fair probability that `side` wins
    reason: str

    @property
    def edge(self):
        return self.fair - self.price

    @property
    def value(self):
        return self.shares * self.price


def _require_dry_run():
    if not config.DRY_RUN:
        raise NotImplementedError("live orders are not built yet; set DRY_RUN = True")


def _count_write():
    """Counts one SIG write. A dry run only reports the wait a live run would have had."""
    wait = budget.WRITES.acquire(block=False)
    if wait > 0:
        print(f"    (live: the SIG write budget would make this wait {wait:.0f}s)")


def place_order(order, timeout):
    """Limit order at `order.price`; anything unfilled after `timeout` seconds would be cancelled."""
    _require_dry_run()
    _count_write()
    print(f"  [DRY RUN] would {order.action.upper()} {order.side.upper()} market {order.market_id} "
          f"({order.race}) x{order.shares:,.0f} @ {order.price:.3f} limit, "
          f"cancel unfilled after {timeout}s  | {order.reason}")


def cancel_all():
    _require_dry_run()
    _count_write()
    print("  [DRY RUN] would cancel all open orders")
