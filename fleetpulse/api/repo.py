"""Read-side repository port + in-memory implementation (CQRS read model).

Postgres/ClickHouse implementation lives in `sqlrepo.py`; this one backs tests and the no-infra demo.
Every method takes `tenant` -- tenant isolation is enforced at the repository boundary, not trusted
to callers.
"""
from __future__ import annotations
import base64
import bisect
import zlib
import json
from typing import Protocol

import numpy as np



def enc_cursor(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()


def dec_cursor(c: str | None):
    if not c:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(c.encode()))
    except Exception as e:  # noqa: BLE001
        raise ValueError("bad cursor") from e


class FleetRepo(Protocol):
    def list_vehicles(self, tenant: str, limit: int, cursor, min_risk: float) -> tuple[list[dict], object]: ...
    def get_vehicle(self, tenant: str, vin: str) -> dict | None: ...
    def list_alerts(self, tenant: str, limit: int, cursor, min_severity: int) -> tuple[list[dict], object]: ...
    def telemetry(self, tenant: str, vin: str, limit: int) -> list[dict]: ...
    def erase_vehicle(self, tenant: str, vin: str) -> int: ...


class InMemoryRepo:
    def __init__(self, n: int = 3000, tenants=("t00", "t01", "t02"), seed: int = 3):
        from ..core.vin import make_vin
        r = np.random.default_rng(seed)
        self.vehicles: dict[str, dict] = {}
        for i in range(n):
            vin = make_vin("MA3EJKD1", i)
            risk = float(np.clip(r.beta(1, 14), 0, 1))
            self.vehicles[vin] = {
                "vin": vin, "tenant": tenants[i % len(tenants)], "risk": round(risk, 4),
                "factors": ["coolant_trend_7d", "batt_v_min", "dtc_count_7d"][: 1 + int(risk * 3)],
                "lat": 13.08 + float(r.normal(0, .05)), "lon": 80.27 + float(r.normal(0, .05)),
                "odo_km": round(float(r.uniform(5e3, 1.2e5)), 1), "fuel": "EV" if r.random() < .3 else "ICE"}
        self.alerts = [{"id": k + 1, "vin": v["vin"], "tenant": v["tenant"], "type": "OVERHEAT" if v["risk"] > .3 else "HARSH_BRAKE",
                        "severity": 5 if v["risk"] > .3 else 2, "ts": f"2026-10-02T09:{k % 60:02d}:00Z", "detail": "demo"}
                       for k, v in enumerate(sorted(self.vehicles.values(), key=lambda x: -x["risk"])[:400])]
        self.erased: set[str] = set()
        # pre-sorted read model per tenant (what the DB index (tenant_id, score DESC, vehicle_id DESC) gives us)
        self._rank: dict[str, list[tuple[float, str]]] = {}
        for v in self.vehicles.values():
            self._rank.setdefault(v["tenant"], []).append((-v["risk"], v["vin"]))
        for lst in self._rank.values():
            lst.sort()
        self._alerts_by_tenant: dict[str, list[dict]] = {}
        for a in sorted(self.alerts, key=lambda a: -a["id"]):
            self._alerts_by_tenant.setdefault(a["tenant"], []).append(a)

    # --- helpers
    def _present(self, v):  # drop internal fields
        return {k: v[k] for k in ("vin", "risk", "factors", "lat", "lon", "odo_km", "fuel")}

    def list_vehicles(self, tenant, limit, cursor, min_risk=0.0):
        keys = self._rank.get(tenant, [])
        i = bisect.bisect_right(keys, (-cursor[0], cursor[1])) if cursor else 0   # keyset: seek, don't scan
        page = []
        while i < len(keys) and len(page) <= limit:
            neg, vin = keys[i]; i += 1
            if -neg < min_risk:
                break                                                    # sorted desc: nothing further qualifies
            if vin not in self.erased:
                page.append(self.vehicles[vin])
        nxt = enc_cursor([page[limit - 1]["risk"], page[limit - 1]["vin"]]) if len(page) > limit else None
        return [self._present(v) for v in page[:limit]], nxt

    def get_vehicle(self, tenant, vin):
        v = self.vehicles.get(vin)
        return self._present(v) if v and v["tenant"] == tenant and vin not in self.erased else None

    def list_alerts(self, tenant, limit, cursor, min_severity=1):
        page = []
        for a in self._alerts_by_tenant.get(tenant, []):
            if cursor and a["id"] >= cursor:
                continue
            if a["severity"] >= min_severity and a["vin"] not in self.erased:
                page.append(a)
                if len(page) > limit:
                    break
        return page[:limit], (enc_cursor(page[limit - 1]["id"]) if len(page) > limit else None)

    def telemetry(self, tenant, vin, limit):
        v = self.get_vehicle(tenant, vin)
        if not v:
            return []
        r = np.random.default_rng(zlib.crc32(vin.encode()))
        return [{"ts": f"2026-10-02T09:{59 - i // 60:02d}:{59 - i % 60:02d}Z", "speed_kmh": round(float(abs(r.normal(40, 15))), 1),
                 "lat": v["lat"], "lon": v["lon"]} for i in range(min(limit, 120))]

    def erase_vehicle(self, tenant, vin):
        if self.get_vehicle(tenant, vin):
            self.erased.add(vin)
            return 1
        return 0
