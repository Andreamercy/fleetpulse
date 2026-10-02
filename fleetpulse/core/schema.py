"""Canonical telemetry event (the contract every OEM payload is normalised into)."""
from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator

from .vin import is_valid_vin
from .dtc import parse_dtc

SCHEMA_VERSION = 1
EVENT_TYPES = {"HARSH_BRAKE", "HARSH_ACCEL", "IGNITION_ON", "IGNITION_OFF", "CHARGE_START", "CHARGE_END", "FAULT"}


class TelemetryEvent(BaseModel):
    v: Literal[1] = 1
    vin: str
    ts: datetime
    seq: int = Field(ge=0)                       # per-vehicle monotonically increasing
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_kmh: float = Field(ge=0, le=350)
    odo_km: float = Field(ge=0)
    coolant_c: float | None = Field(default=None, ge=-60, le=200)
    batt_v: float | None = Field(default=None, ge=0, le=60)   # 12V aux battery
    soc_pct: float | None = Field(default=None, ge=0, le=100)  # EV/PHEV traction battery
    dtc: list[str] = Field(default_factory=list)
    evt: str | None = None
    oem: str = "unknown"
    tenant: str = "demo"

    @field_validator("vin")
    @classmethod
    def _vin(cls, v: str) -> str:
        v = v.upper()
        if not is_valid_vin(v, strict_check_digit=False):
            raise ValueError("invalid VIN")
        return v

    @field_validator("dtc")
    @classmethod
    def _dtc(cls, v: list[str]) -> list[str]:
        out = []
        for c in v:
            p = parse_dtc(c)
            if p is None:
                raise ValueError(f"invalid DTC {c!r}")
            out.append(p.code)
        return out

    @field_validator("evt")
    @classmethod
    def _evt(cls, v):
        if v is not None and v not in EVENT_TYPES:
            raise ValueError("unknown event type")
        return v

    @property
    def key(self) -> bytes:
        """Idempotency key: a vehicle never emits the same seq twice."""
        return f"{self.vin}:{self.seq}".encode()
