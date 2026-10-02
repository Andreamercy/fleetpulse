"""Token-bucket rate limiter (per principal). In-memory here; the Redis variant (atomic Lua
script, same algorithm) is used when API replicas > 1 -- see docs/adr/ADR-005 note."""
from __future__ import annotations
import time


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: int, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate_per_s, burst, clock
        self.state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds). O(1)."""
        now = self.clock()
        tokens, last = self.state.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens >= cost:
            self.state[key] = (tokens - cost, now)
            return True, 0.0
        self.state[key] = (tokens, now)
        return False, (cost - tokens) / self.rate
