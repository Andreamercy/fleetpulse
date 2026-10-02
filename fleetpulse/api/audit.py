"""Tamper-evident audit log: each record carries SHA-256(prev_hash || record). Verifiable offline.
Production sink: append-only Postgres table (REVOKE UPDATE/DELETE) + WORM object storage export."""
from __future__ import annotations
import hashlib
import json
import threading
from datetime import datetime, timezone


class AuditLog:
    def __init__(self):
        self.records: list[dict] = []
        self._lock = threading.Lock()

    def record(self, actor: str, tenant: str, action: str, resource: str, detail: dict | None = None) -> dict:
        with self._lock:
            prev = self.records[-1]["hash"] if self.records else "0" * 64
            body = {"id": len(self.records) + 1, "ts": datetime.now(timezone.utc).isoformat(), "actor": actor,
                    "tenant": tenant, "action": action, "resource": resource, "detail": detail or {}, "prev": prev}
            body["hash"] = hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()
            self.records.append(body)
            return body

    def verify(self) -> bool:
        prev = "0" * 64
        for r in self.records:
            b = {k: v for k, v in r.items() if k != "hash"}
            if b["prev"] != prev or hashlib.sha256((prev + json.dumps(b, sort_keys=True, default=str)).encode()).hexdigest() != r["hash"]:
                return False
            prev = r["hash"]
        return True
