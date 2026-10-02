"""In-memory adapters (tests, local demo without infrastructure)."""
from __future__ import annotations


class MemSink:
    def __init__(self): self.rows: list[dict] = []
    def write_batch(self, rows): self.rows.extend(rows)


class MemState:
    def __init__(self): self.latest: dict[str, dict] = {}
    def put_latest(self, vin, state): self.latest[vin] = state


class MemAlerts:
    def __init__(self): self.items: list[dict] = []
    def publish(self, alert): self.items.append(alert)


class MemDlq:
    def __init__(self): self.items: list[tuple] = []
    def publish(self, raw, reason): self.items.append((raw, reason))
