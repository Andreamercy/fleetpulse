"""Production read-side adapters: PostgreSQL (vehicles, risk, alerts) + ClickHouse (telemetry).

Requires the docker-compose stack. Each query is tenant-scoped in SQL *and* Postgres RLS is set per
connection (`SET app.tenant_id`) as defence in depth. Keyset pagination only (no OFFSET); single
joined queries (no N+1). Indexes these rely on: db/optimizations.sql.
"""
from __future__ import annotations
import os

from .repo import enc_cursor


class SqlRepo:  # pragma: no cover - needs Postgres/ClickHouse (covered by tests/integration)
    def __init__(self):
        import clickhouse_connect
        from psycopg_pool import ConnectionPool
        self.pg = ConnectionPool(os.environ["DATABASE_URL"], min_size=2, max_size=int(os.getenv("PG_POOL", "10")))
        self.ch = clickhouse_connect.get_client(host=os.getenv("CLICKHOUSE_HOST", "clickhouse"))

    def _q(self, tenant_id: int, sql: str, params):
        with self.pg.connection() as c, c.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            cur.execute(sql, params)
            return cur.fetchall()

    @staticmethod
    def _tid(tenant: str) -> int:
        return int(tenant.lstrip("t"))

    def list_vehicles(self, tenant, limit, cursor, min_risk=0.0):
        t = self._tid(tenant)
        s, vid = (cursor[0], cursor[1]) if cursor else (2.0, 2**31 - 1)
        rows = self._q(t, """SELECT v.id, v.vin, r.score, r.factors FROM vehicle_risk r JOIN vehicle v ON v.id = r.vehicle_id
                             WHERE r.tenant_id = %s AND r.score >= %s AND (r.score, r.vehicle_id) < (%s, %s)
                             ORDER BY r.score DESC, r.vehicle_id DESC LIMIT %s""", (t, min_risk, s, vid, limit + 1))
        items = [{"vin": r[1].strip(), "risk": r[2], "factors": r[3], "id": r[0]} for r in rows]
        nxt = enc_cursor([items[limit - 1]["risk"], items[limit - 1]["id"]]) if len(items) > limit else None
        return items[:limit], nxt

    def get_vehicle(self, tenant, vin):
        t = self._tid(tenant)
        rows = self._q(t, """SELECT v.vin, r.score, r.factors, m.fuel_type FROM vehicle v JOIN vehicle_model m ON m.id = v.model_id
                             LEFT JOIN vehicle_risk r ON r.vehicle_id = v.id WHERE v.tenant_id = %s AND v.vin = %s""", (t, vin))
        return {"vin": rows[0][0].strip(), "risk": rows[0][1], "factors": rows[0][2], "fuel": rows[0][3]} if rows else None

    def list_alerts(self, tenant, limit, cursor, min_severity=1):
        t = self._tid(tenant)
        rows = self._q(t, """SELECT a.id, v.vin, a.type_code, a.severity, a.raised_at, a.detail FROM alert a JOIN vehicle v ON v.id = a.vehicle_id
                             WHERE a.tenant_id = %s AND a.status = 'OPEN' AND a.severity >= %s AND a.id < %s
                             ORDER BY a.id DESC LIMIT %s""", (t, min_severity, cursor or 2**62, limit + 1))
        items = [{"id": r[0], "vin": r[1].strip(), "type": r[2], "severity": r[3], "ts": r[4].isoformat(), "detail": r[5]} for r in rows]
        return items[:limit], (enc_cursor(items[limit - 1]["id"]) if len(items) > limit else None)

    def telemetry(self, tenant, vin, limit):
        if self.get_vehicle(tenant, vin) is None:           # ownership check against the system of record first
            return []
        r = self.ch.query("SELECT ts, speed_kmh, lat, lon FROM telemetry WHERE tenant = {t:String} AND vin = {v:String} "
                          "ORDER BY ts DESC LIMIT {n:UInt32}", parameters={"t": tenant, "v": vin, "n": limit})
        return [{"ts": x[0].isoformat(), "speed_kmh": x[1], "lat": x[2], "lon": x[3]} for x in r.result_rows]

    def erase_vehicle(self, tenant, vin):
        if self.get_vehicle(tenant, vin) is None:
            return 0
        self.ch.command("ALTER TABLE telemetry DELETE WHERE tenant = {t:String} AND vin = {v:String}", parameters={"t": tenant, "v": vin})
        return 1
