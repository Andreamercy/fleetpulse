"""Streaming data structures: Bloom filter (dedupe), Count-Min Sketch (top-K)."""
import hashlib
import math


def _hashes(key: bytes, k: int, m: int):
    """Kirsch-Mitzenmacher double hashing: k indices from one 128-bit digest. O(k)."""
    d = hashlib.blake2b(key, digest_size=16).digest()
    h1 = int.from_bytes(d[:8], "little")
    h2 = int.from_bytes(d[8:], "little") | 1
    return [(h1 + i * h2) % m for i in range(k)]


class BloomFilter:
    """Space O(m) bits; add/contains O(k). No false negatives; FP rate ~ (1-e^{-kn/m})^k."""

    def __init__(self, capacity: int, fp_rate: float = 0.001):
        self.m = max(8, int(-capacity * math.log(fp_rate) / (math.log(2) ** 2)))
        self.k = max(1, round(self.m / capacity * math.log(2)))
        self.bits = bytearray((self.m + 7) // 8)
        self.n = 0

    def _has(self, idx) -> bool:
        return all(self.bits[i >> 3] & (1 << (i & 7)) for i in idx)

    def add(self, key: bytes) -> bool:
        """Add key; returns True if it was (probably) already present."""
        return self._add_idx(_hashes(key, self.k, self.m))

    def _add_idx(self, idx) -> bool:
        present = True
        for i in idx:
            byte, bit = divmod(i, 8)
            if not self.bits[byte] & (1 << bit):
                present = False
                self.bits[byte] |= 1 << bit
        if not present:
            self.n += 1
        return present

    def __contains__(self, key: bytes) -> bool:
        return all(self.bits[i >> 3] & (1 << (i & 7)) for i in _hashes(key, self.k, self.m))

    def est_fp_rate(self) -> float:
        return (1 - math.exp(-self.k * self.n / self.m)) ** self.k


class RotatingBloom:
    """Two-generation Bloom so memory stays bounded on an unbounded stream.
    A key is a duplicate if present in either generation."""

    def __init__(self, capacity_per_gen: int, fp_rate: float = 0.001):
        self.cap, self.fp = capacity_per_gen, fp_rate
        self.cur, self.old = BloomFilter(self.cap, fp_rate), BloomFilter(self.cap, fp_rate)

    def seen_or_add(self, key: bytes) -> bool:
        idx = _hashes(key, self.cur.k, self.cur.m)          # both generations share (m, k): hash once
        if self.old._has(idx):
            return True
        if self.cur.n >= self.cap:
            self.old, self.cur = self.cur, BloomFilter(self.cap, self.fp)
        return self.cur._add_idx(idx)


class CountMinSketch:
    """Frequency estimates with one-sided error: est >= true, est <= true + eps*N w.p. 1-delta.
    Space O(w*d) with w=ceil(e/eps), d=ceil(ln(1/delta)); update/query O(d)."""

    def __init__(self, eps: float = 0.001, delta: float = 0.01):
        self.w = math.ceil(math.e / eps)
        self.d = math.ceil(math.log(1 / delta))
        self.table = [[0] * self.w for _ in range(self.d)]
        self.total = 0

    def _idx(self, key: bytes):
        return _hashes(key, self.d, self.w)

    def add(self, key: bytes, c: int = 1) -> int:
        self.total += c
        est = None
        for row, i in zip(self.table, self._idx(key)):
            row[i] += c
            est = row[i] if est is None else min(est, row[i])
        return est

    def estimate(self, key: bytes) -> int:
        return min(row[i] for row, i in zip(self.table, self._idx(key)))


class TopK:
    """Approximate heavy hitters: Count-Min + a size-K set. O(d + K) worst-case per update
    (K is small, default 10)."""

    def __init__(self, k: int = 10, eps: float = 0.001, delta: float = 0.01):
        self.k, self.cms = k, CountMinSketch(eps, delta)
        self.members: dict[str, int] = {}

    def add(self, item: str, c: int = 1):
        est = self.cms.add(item.encode(), c)
        if item in self.members or len(self.members) < self.k:
            self.members[item] = est
            return
        worst = min(self.members, key=self.members.get)
        if est > self.members[worst]:
            del self.members[worst]
            self.members[item] = est

    def top(self):
        return sorted(self.members.items(), key=lambda kv: -kv[1])
