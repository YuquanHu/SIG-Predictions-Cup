"""Remembers when each market was last traded, so the bot does not flip-flop (sell a market it just bought, or buy back
one it just sold). Trading the same way again is never blocked: building a position takes several buys."""
import time


class Cooldown:
    def __init__(self, seconds, clock=time.time):
        self.seconds = seconds
        self.clock = clock
        self._last_order = {}  # (market_id, action) -> time of the latest order

    def record(self, orders):
        now = self.clock()
        for o in orders:
            self._last_order[(o.market_id, o.action)] = now

    def blocks(self, market_id, action):
        """True when the opposite action in this market was ordered less than `seconds` ago."""
        opposite = "sell" if action == "buy" else "buy"
        last = self._last_order.get((market_id, opposite))
        return last is not None and self.clock() - last < self.seconds
