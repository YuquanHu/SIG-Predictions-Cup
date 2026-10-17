"""Order placement. Dry run only: nothing here can reach SIG until the live stage is built."""
from dataclasses import dataclass

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


def place_order(order, timeout):
    """Limit order at `order.price`; anything unfilled after `timeout` seconds would be cancelled."""
    _require_dry_run()
    print(f"  [DRY RUN] would {order.action.upper()} {order.side.upper()} market {order.market_id} "
          f"({order.race}) x{order.shares:,.0f} @ {order.price:.3f} limit, "
          f"cancel unfilled after {timeout}s  | {order.reason}")


def cancel_all():
    _require_dry_run()
    print("  [DRY RUN] would cancel all open orders")
