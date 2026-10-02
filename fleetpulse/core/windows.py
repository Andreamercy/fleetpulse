"""Sliding-window aggregation with amortised O(1) updates (monotonic deque for max/min)."""
from collections import deque


class SlidingWindow:
    """Time-based window over (ts_seconds, value). add: amortised O(1); mean/max/min: O(1).
    Space O(n) for n samples inside the horizon. Late (out-of-order) samples older than the
    window are ignored; slightly late samples inside the window are accepted for count/sum
    but only appended to the monotonic deques in arrival order (documented trade-off)."""

    def __init__(self, horizon_s: float):
        self.h = horizon_s
        self.q: deque[tuple[float, float]] = deque()
        self.maxq: deque[tuple[float, float]] = deque()
        self.minq: deque[tuple[float, float]] = deque()
        self.sum = 0.0
        self.latest = float("-inf")

    def add(self, ts: float, v: float) -> bool:
        if ts < self.latest - self.h:
            return False
        self.latest = max(self.latest, ts)
        self.q.append((ts, v))
        self.sum += v
        while self.maxq and self.maxq[-1][1] <= v:
            self.maxq.pop()
        self.maxq.append((ts, v))
        while self.minq and self.minq[-1][1] >= v:
            self.minq.pop()
        self.minq.append((ts, v))
        self._evict()
        return True

    def _evict(self):
        cutoff = self.latest - self.h
        while self.q and self.q[0][0] < cutoff:
            _, v = self.q.popleft()
            self.sum -= v
        while self.maxq and self.maxq[0][0] < cutoff:
            self.maxq.popleft()
        while self.minq and self.minq[0][0] < cutoff:
            self.minq.popleft()

    def __len__(self):
        return len(self.q)

    @property
    def mean(self):
        return self.sum / len(self.q) if self.q else None

    @property
    def max(self):
        return self.maxq[0][1] if self.maxq else None

    @property
    def min(self):
        return self.minq[0][1] if self.minq else None
