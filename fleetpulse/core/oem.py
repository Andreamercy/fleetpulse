"""Anti-corruption layer: one Adapter per OEM payload format -> canonical TelemetryEvent.

Onboarding a new OEM = write one adapter + register it (Strategy + Registry); no change to
the pipeline and no downtime. Unknown formats are routed to a dead-letter topic, never dropped.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Protocol

from .schema import TelemetryEvent

MPH_TO_KMH = 1.609344
MI_TO_KM = 1.609344


class UnknownFormat(Exception):
    pass


class OemAdapter(Protocol):
    def __call__(self, raw: dict) -> dict: ...


def _iso(ts) -> datetime:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def canonical(raw: dict) -> dict:
    """Already canonical (our own simulator / reference OEM)."""
    return raw


def oem_alpha(raw: dict) -> dict:
    """Imperial units, nested position, epoch-millis, SoC as 0..1 fraction."""
    soc = raw.get("battery", {}).get("stateOfCharge")
    return {
        "vin": raw["vehicleIdentificationNumber"], "ts": _iso(raw["timestamp"]), "seq": raw["counter"],
        "lat": raw["position"]["latitude"], "lon": raw["position"]["longitude"],
        "speed_kmh": raw["speedMph"] * MPH_TO_KMH, "odo_km": raw["odometerMiles"] * MI_TO_KM,
        "coolant_c": (raw["coolantF"] - 32) / 1.8 if "coolantF" in raw else None,
        "batt_v": raw.get("auxBatteryVolts"), "soc_pct": None if soc is None else soc * 100,
        "dtc": raw.get("troubleCodes", []), "evt": raw.get("event"), "oem": "alpha",
    }


def oem_beta(raw: dict) -> dict:
    """Metric, flat keys, ISO timestamps, 'speed' in m/s."""
    return {
        "vin": raw["vin"], "ts": _iso(raw["time"]), "seq": raw["msgId"],
        "lat": raw["lat"], "lon": raw["lng"], "speed_kmh": raw["speed_ms"] * 3.6,
        "odo_km": raw["odo"], "coolant_c": raw.get("engineTemp"), "batt_v": raw.get("v12"),
        "soc_pct": raw.get("soc"), "dtc": raw.get("dtcs", []), "evt": raw.get("evt"), "oem": "beta",
    }


class AdapterRegistry:
    def __init__(self):
        self._by_oem: dict[str, OemAdapter] = {"canonical": canonical, "alpha": oem_alpha, "beta": oem_beta}

    def register(self, oem: str, fn: Callable[[dict], dict]) -> None:
        """Hot-register at runtime -> zero-downtime OEM onboarding."""
        self._by_oem[oem] = fn

    def normalise(self, oem: str, raw: dict, tenant: str = "demo") -> TelemetryEvent:
        fn = self._by_oem.get(oem)
        if fn is None:
            raise UnknownFormat(oem)
        try:
            d = dict(fn(raw))
        except (KeyError, TypeError) as e:
            raise UnknownFormat(f"{oem}: missing field {e}") from e
        d.setdefault("tenant", tenant)
        d.setdefault("oem", oem)
        return TelemetryEvent(**d)
