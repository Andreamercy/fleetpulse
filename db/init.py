"""One-shot initialiser (compose `init` service): topics, Postgres schema + indexes, ClickHouse DDL, seeded fleet.
Idempotent: safe to re-run."""
import io
import os
import pathlib
import numpy as np
import pandas as pd
import psycopg
import clickhouse_connect
from confluent_kafka.admin import AdminClient, NewTopic
from fleetpulse.core.vin import make_vin

HERE = pathlib.Path(__file__).parent
N_V = int(os.getenv("VEHICLES", "100000")); N_T, N_F = 20, 400


def topics():
    a = AdminClient({"bootstrap.servers": os.getenv("KAFKA_BROKERS", "redpanda:9092")})
    for f in a.create_topics([NewTopic("telemetry", 48, 1), NewTopic("alerts", 12, 1), NewTopic("telemetry.dlq", 6, 1)]).values():
        try: f.result()
        except Exception: pass                          # already exists


def pg():
    c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True); cur = c.cursor()
    cur.execute("SELECT to_regclass('public.vehicle')")
    if cur.fetchone()[0]: return
    cur.execute((HERE / "schema.sql").read_text())
    r = np.random.default_rng(42)
    cur.execute("INSERT INTO tenant SELECT g,'tenant'||g FROM generate_series(0,%s) g", (N_T - 1,))
    cur.execute("INSERT INTO fleet SELECT g, g %% %s, 'fleet'||g FROM generate_series(0,%s) g", (N_T, N_F - 1))
    cur.execute("INSERT INTO vehicle_model VALUES (1,'OEM-A','Sedan',2022,'ICE'),(2,'OEM-B','SUV',2023,'EV'),(3,'OEM-C','Van',2021,'HYBRID')")
    cur.execute("INSERT INTO alert_type VALUES ('OVERHEAT',5,'Sustained engine overheating'),('DTC_CRITICAL',5,'Severe fault code'),('BATTERY_12V_LOW',3,'Aux battery low'),('EV_SOC_LOW',3,'EV state of charge low'),('HARSH_BRAKE',2,'Harsh braking')")
    ids = np.arange(N_V); fleet = ids % N_F
    veh = pd.DataFrame({"id": ids, "vin": [make_vin("MA3EJKD1", int(i)) for i in ids], "tenant_id": fleet % N_T, "fleet_id": fleet,
                        "model_id": r.integers(1, 4, N_V), "registered_on": "2022-01-01", "status": "ACTIVE"})
    risk = pd.DataFrame({"vehicle_id": ids, "tenant_id": veh.tenant_id, "score": np.clip(r.beta(1, 14, N_V), 0, 1).round(5),
                         "model_version": "seed", "computed_at": "2026-10-02 09:00:00+00", "factors": "[]"})
    for table, df in (("vehicle", veh), ("vehicle_risk", risk)):
        buf = io.StringIO(); df.to_csv(buf, index=False, header=False); buf.seek(0)
        with cur.copy(f"COPY {table} ({','.join(df.columns)}) FROM STDIN WITH (FORMAT csv)") as cp: cp.write(buf.read())
    cur.execute((HERE / "optimizations.sql").read_text()); cur.execute("ANALYZE")


def ch():
    c = clickhouse_connect.get_client(host=os.getenv("CLICKHOUSE_HOST", "clickhouse"))
    for stmt in (HERE / "clickhouse.sql").read_text().split(";\n\n"):
        s = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip().rstrip(";")
        if s: c.command(s)


if __name__ == "__main__":
    topics(); pg(); ch(); print("init complete")
