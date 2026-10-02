"""Application service: validate -> dedupe -> order -> detect -> persist.

Delivery semantics: at-least-once from Kafka + idempotent processing (dedupe on (vin, seq) and an
idempotent ReplacingMergeTree sink) => effectively-once results. Kafka partitions by VIN so one
consumer owns all events of a vehicle; per-vehicle state needs no cross-node coordination.
"""
from __future__ import annotations
import json
from collections import deque
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..core.oem import AdapterRegistry, UnknownFormat
from ..core.schema import TelemetryEvent
from ..core.sketches import RotatingBloom
from ..core.windows import SlidingWindow
from . import rules

ALLOWED_LATENESS_S = 60.0
ALERT_COOLDOWN_S = 300.0
RECENT_SEQ = 128


class VehicleWindows:
    __slots__ = ("coolant", "batt", "watermark", "recent", "last_alert")

    def __init__(self):
        self.coolant = SlidingWindow(180)
        self.batt = SlidingWindow(300)
        self.watermark = float("-inf")
        self.recent: deque[int] = deque(maxlen=RECENT_SEQ)
        self.last_alert: dict[str, float] = {}


@dataclass
class Metrics:
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    late: int = 0
    invalid: int = 0
    alerts: int = 0
    bloom_false_positive_checks: int = 0


@dataclass
class EventProcessor:
    sink: object
    state: object
    alerts: object
    dlq: object
    registry: AdapterRegistry = field(default_factory=AdapterRegistry)
    bloom: RotatingBloom = field(default_factory=lambda: RotatingBloom(2_000_000, 0.001))
    batch_size: int = 5000
    metrics: Metrics = field(default_factory=Metrics)
    _vehicles: dict[str, VehicleWindows] = field(default_factory=dict)
    _buf: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ public
    def handle(self, raw: bytes | str | dict, oem: str = "canonical") -> str:
        """Process one message; returns outcome label (useful for tests/metrics)."""
        self.metrics.received += 1
        try:
            if oem == "canonical" and not isinstance(raw, dict):
                ev = TelemetryEvent.model_validate_json(raw)          # fast path: single parse+validate pass
            else:
                obj = raw if isinstance(raw, dict) else json.loads(raw)
                ev = self.registry.normalise(oem, obj)
        except (ValidationError, UnknownFormat, ValueError, KeyError, TypeError) as e:
            self.metrics.invalid += 1
            self.dlq.publish(raw if not isinstance(raw, bytes) else raw.decode("utf-8", "replace"), type(e).__name__)
            return "invalid"

        vw = self._vehicles.get(ev.vin)
        if vw is None:
            vw = self._vehicles[ev.vin] = VehicleWindows()

        # --- idempotency: Bloom fast path, exact check on a hit (Bloom FP must not drop real data)
        if self.bloom.seen_or_add(ev.key):
            self.metrics.bloom_false_positive_checks += 1
            if ev.seq in vw.recent:
                self.metrics.duplicates += 1
                return "duplicate"
        vw.recent.append(ev.seq)

        t = ev.ts.timestamp()
        row = self._row(ev)
        if t < vw.watermark - ALLOWED_LATENESS_S:
            # too late for live state/rules, still valuable for history (batch analytics)
            self.metrics.late += 1
            row["late"] = 1
            self._emit(row)
            return "late"

        is_newest = t >= vw.watermark          # slightly-late events feed windows but must not rewind 'latest'
        vw.watermark = max(vw.watermark, t)
        if ev.coolant_c is not None:
            vw.coolant.add(t, ev.coolant_c)
        if ev.batt_v is not None:
            vw.batt.add(t, ev.batt_v)
        if is_newest:
            self.state.put_latest(ev.vin, {**row, "tenant": ev.tenant})
        for typ, sev, detail in rules.evaluate(ev, vw):
            if t - vw.last_alert.get(typ, float("-inf")) >= ALERT_COOLDOWN_S:
                vw.last_alert[typ] = t
                self.metrics.alerts += 1
                self.alerts.publish({"tenant": ev.tenant, "vin": ev.vin, "type": typ, "severity": sev,
                                     "ts": ev.ts.isoformat(), "detail": detail})
        self.metrics.accepted += 1
        self._emit(row)
        return "ok"

    def flush(self) -> int:
        n = len(self._buf)
        if n:
            self.sink.write_batch(self._buf)  # raises on failure -> caller must NOT commit offsets
            self._buf = []
        return n

    # ------------------------------------------------------------------ internals
    def _emit(self, row: dict):
        self._buf.append(row)
        if len(self._buf) >= self.batch_size:
            self.flush()

    @staticmethod
    def _row(ev: TelemetryEvent) -> dict:
        return {"tenant": ev.tenant, "vin": ev.vin, "ts": ev.ts.isoformat(), "seq": ev.seq, "lat": ev.lat,
                "lon": ev.lon, "speed_kmh": ev.speed_kmh, "odo_km": ev.odo_km, "coolant_c": ev.coolant_c,
                "batt_v": ev.batt_v, "soc_pct": ev.soc_pct, "dtc": ev.dtc, "evt": ev.evt, "oem": ev.oem,
                "late": 0}
