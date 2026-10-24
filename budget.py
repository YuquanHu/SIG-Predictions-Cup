"""Sliding-window request budgets. SIG allows 100 reads and 30 writes a minute per account, so every SIG
request goes through one of these and waits instead of being refused."""
import threading
import time
from collections import deque

import config


class Budget:
    def __init__(self, limit_setting, kind):
        self.limit_setting = limit_setting  # name of the config value holding the limit per minute
        self.kind = kind
        self._times = deque()               # when each request in the last 60 s was sent
        self._lock = threading.Lock()       # requests come from several threads at once
        self._count = 0

    def acquire(self, block=True):
        """Record one request. If the last 60 s already hold the limit: sleep until it fits (block=True),
        or, for a dry run, just report how long a live run would have waited (block=False).
        Returns those seconds."""
        waited = 0.0
        with self._lock:
            while True:
                now = time.time()
                while self._times and now - self._times[0] >= 60:
                    self._times.popleft()
                if len(self._times) < getattr(config, self.limit_setting):
                    break
                pause = 60 - (now - self._times[0]) + 0.05
                if not block:
                    waited = pause
                    break
                print(f"  (SIG {self.kind} budget used up: waiting {pause:.0f}s)")
                time.sleep(pause)
                waited += pause
            self._times.append(time.time())
            self._count += 1
        return waited

    def take_count(self):
        """Requests recorded since the last call (for per-loop logging)."""
        with self._lock:
            count, self._count = self._count, 0
        return count


READS = Budget("SIG_READS_PER_MINUTE", "read")
WRITES = Budget("SIG_WRITES_PER_MINUTE", "write")
