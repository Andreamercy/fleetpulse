"""Streaming fleet simulator: N vehicles, 1 Hz telemetry, realistic mess.

Vectorised with numpy (state for 100k vehicles advances in a few ms per tick); only the
final JSON encoding is per-event. Injected realism:
  * trips and parking, speed random-walk, GPS jitter, mixed ICE/EV
  * ~2% of vehicles are 'degrading': coolant creeps up, 12V battery sags, DTCs appear, then fail
  * duplicates (default 1%), out-of-order delivery (2%, up to 30 s late), 3x bursts
  * a few malformed events (bad VIN / bad DTC) to exercise validation + dead-letter path
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from ..core.vin import make_vin

try:  # orjson is ~5-8x faster than json for this workload; optional.
    import orjson
    _dumps = lambda o: orjson.dumps(o).decode()  # noqa: E731
except ImportError:  # pragma: no cover
    _dumps = lambda o: json.dumps(o, separators=(",", ":"))  # noqa: E731

CITIES = [(13.0827, 80.2707), (12.9716, 77.5946), (19.0760, 72.8777), (21.1702, 72.8311), (28.6139, 77.2090)]
VIN_PREFIXES = ["MA3EJKD1", "MALAA51G", "MBJBA3FS", "1HGCM826", "5YJ3E1EA", "JTDKB20U"]
DTC_POOL = ["P0301", "P0420", "P0128", "P0171", "P0562", "P0217", "P0A80"]


class FleetSimulator:
    def __init__(self, n_vehicles: int = 100_000, seed: int = 7, degrading_frac: float = 0.02,
                 ev_frac: float = 0.3, tenants: int = 20, start: datetime | None = None,
                 offset: int = 0):
        r = self.r = np.random.default_rng(seed + offset)
        n = n_vehicles
        self.n = n
        self.vins = np.array([make_vin(VIN_PREFIXES[(offset + i) % len(VIN_PREFIXES)], offset + i) for i in range(n)])
        self.tenant = np.array([f"t{((offset + i) % tenants):02d}" for i in range(n)])
        c = r.integers(0, len(CITIES), n)
        base = np.array(CITIES)[c]
        self.lat = base[:, 0] + r.normal(0, 0.08, n)
        self.lon = base[:, 1] + r.normal(0, 0.08, n)
        self.heading = r.uniform(0, 2 * np.pi, n)
        self.speed = np.zeros(n)
        self.moving = r.random(n) < 0.35
        self.odo = r.uniform(2_000, 120_000, n)
        self.is_ev = r.random(n) < ev_frac
        self.coolant = np.where(self.is_ev, np.nan, 88 + r.normal(0, 2, n))
        self.batt_v = 12.6 + r.normal(0, 0.1, n)
        self.soc = np.where(self.is_ev, r.uniform(20, 95, n), np.nan)
        self.seq = np.zeros(n, dtype=np.int64)
        self.degrading = r.random(n) < degrading_frac
        self.fail_tick = np.where(self.degrading, r.integers(600, 3600, n), 10**9)
        self.t = 0
        self.start = start or datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)

    # ------------------------------------------------------------------ physics
    def step(self):
        r, n = self.r, self.n
        flip = r.random(n)
        self.moving = np.where(flip < 0.002, ~self.moving, self.moving)       # start/stop trips
        target = np.where(self.moving, np.clip(self.speed + r.normal(0, 4, n), 0, 130), 0.0)
        self.speed = 0.7 * self.speed + 0.3 * target
        self.heading += r.normal(0, 0.05, n)
        d_km = self.speed / 3600.0
        self.lat += d_km * np.cos(self.heading) / 111.0
        self.lon += d_km * np.sin(self.heading) / (111.0 * np.cos(np.radians(self.lat)))
        self.odo += d_km
        load = np.clip(self.speed / 100, 0, 1)
        # ICE coolant relaxes to load-dependent equilibrium; degrading vehicles drift upward
        eq = 86 + 8 * load
        drift = np.where(self.degrading, np.minimum((self.t / 3600.0) * 40, 40), 0)
        self.coolant += 0.05 * (eq + drift - self.coolant) + r.normal(0, 0.15, n)
        sag = np.where(self.degrading, np.minimum(self.t / 3600.0 * 1.4, 1.4), 0)
        self.batt_v = 12.6 - sag + r.normal(0, 0.03, n)
        self.soc = np.where(self.is_ev, np.clip(self.soc - d_km * 0.18, 5, 100), np.nan)
        self.seq += 1
        self.t += 1

    # ------------------------------------------------------------------ events
    def tick_events(self) -> list[dict]:
        """Advance 1 s and return one canonical event per vehicle."""
        self.step()
        r = self.r
        ts = (self.start + timedelta(seconds=self.t)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(r.integers(0, 999)):03d}Z"
        deg_active = self.degrading & ((self.fail_tick - self.t) < 1800)
        dtc_trig = deg_active & (r.random(self.n) < 0.02)
        failed = self.t >= self.fail_tick
        harsh = (self.speed > 5) & (r.random(self.n) < 0.0008)
        out = []
        la = np.round(self.lat + r.normal(0, 0.00003, self.n), 5).tolist()
        lo = np.round(self.lon + r.normal(0, 0.00003, self.n), 5).tolist()
        sp, od = np.round(self.speed, 1).tolist(), np.round(self.odo, 1).tolist()
        bv, cl, sc = np.round(self.batt_v, 2).tolist(), np.round(self.coolant, 1).tolist(), np.round(self.soc, 1).tolist()
        sq, vins, ten, ev = self.seq.tolist(), self.vins.tolist(), self.tenant.tolist(), self.is_ev.tolist()
        trig, fl, hh = (dtc_trig | failed).tolist(), failed.tolist(), harsh.tolist()
        pick = r.integers(0, len(DTC_POOL), self.n).tolist()
        for i in range(self.n):
            e = {"v": 1, "vin": vins[i], "ts": ts, "seq": sq[i], "lat": la[i], "lon": lo[i],
                 "speed_kmh": sp[i], "odo_km": od[i], "batt_v": bv[i], "dtc": [], "oem": "canonical",
                 "tenant": ten[i]}
            if ev[i]:
                e["soc_pct"] = sc[i]
            else:
                e["coolant_c"] = cl[i]
            if trig[i]:
                e["dtc"] = ["P0217"] if fl[i] else [DTC_POOL[pick[i]]]
                e["evt"] = "FAULT"
            elif hh[i]:
                e["evt"] = "HARSH_BRAKE"
            out.append(e)
        return out


class NetworkMess:
    """Stateful network realism: duplicates, delayed (genuinely out-of-order) delivery, corruption.
    Delayed events are held for 1..max_late_ticks ticks and released with a later tick's batch."""

    def __init__(self, seed=1, dup_rate=0.01, late_rate=0.02, max_late_ticks=30, bad_rate=0.0005):
        self.rng = np.random.default_rng(seed)
        self.dup, self.late, self.maxl, self.bad = dup_rate, late_rate, max_late_ticks, bad_rate
        self.held: dict[int, list[dict]] = {}
        self.tick = 0

    def process(self, events: list[dict]) -> list[dict]:
        r, out = self.rng, []
        u = r.random((len(events), 3))
        delays = r.integers(1, self.maxl + 1, len(events))
        for e, (a, b, c), d in zip(events, u, delays):
            if a < self.bad:
                e = {**e, "vin": e["vin"][:-1] + "I"} if b < 0.5 else {**e, "dtc": ["ZZZZZ"]}
            if b < self.late:
                self.held.setdefault(self.tick + int(d), []).append(e)
                continue
            out.append(e)
            if c < self.dup:
                out.append(e)
        self.tick += 1
        out.extend(self.held.pop(self.tick, []))
        return out


def encode(events: list[dict]) -> list[str]:
    return [_dumps(e) for e in events]
