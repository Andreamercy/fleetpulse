"""Ports (hexagonal architecture): the domain/application layer depends only on these."""
from __future__ import annotations
from typing import Protocol


class TelemetrySink(Protocol):
    def write_batch(self, rows: list[dict]) -> None: ...


class StateStore(Protocol):
    def put_latest(self, vin: str, state: dict) -> None: ...


class AlertPublisher(Protocol):
    def publish(self, alert: dict) -> None: ...


class DeadLetter(Protocol):
    def publish(self, raw: object, reason: str) -> None: ...
