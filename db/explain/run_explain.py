"""Reproducible before/after query-plan benchmark on an embedded PostgreSQL 16 (+pgvector).

  pip install pgserver "psycopg[binary]" numpy pandas
  python db/explain/run_explain.py            # writes docs/sql_results.json and docs/query_plans.md

Scale: 20 tenants, 400 fleets, 100,000 vehicles, 1,500,000 alerts, 100,000 risk rows, 50,000 fault-case vectors.
"""
import io
import json
import pathlib
import statistics
import sys
import tempfile
import time
import numpy as np
import pandas as pd
import psycopg
import pgserver  # noqa

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fleetpulse.core.vin import make_vin  # noqa: E402

N_V, N_A, N_T, N_F, N_VEC = 100_000, 1_500_000, 20, 400, 50_000
r = np.random.default_rng(42)


def copy_df(cur, table, df):
    buf = io.StringIO(); df.to_csv(buf, index=False, header=False, na_rep="\\N"); buf.seek(0)
    with cur.copy(f"COPY {table} ({','.join(df.columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')") as cp:
        while chunk := buf.read(1 << 20):
            cp.write(chunk)


def explain(conn, sql, params=None, runs=5):
    times = []
    for _ in range(runs):
        with conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
            times.append(cur.fetchone()[0][0]["Execution Time"])
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
        text = "\n".join(x[0] for x in cur.fetchall())
    return statistics.median(times), text


def main():
    srv = pgserver.get_server(tempfile.mkdtemp())
    conn = psycopg.connect(srv.get_uri(), autocommit=True)
    cur = conn.cursor()
    cur.execute((ROOT / "db/schema.sql").read_text())
    cur.execute("INSERT INTO tenant SELECT g,'tenant'||g FROM generate_series(0,%s) g", (N_T - 1,))
    cur.execute("INSERT INTO fleet SELECT g, g %% %s, 'fleet'||g FROM generate_series(0,%s) g", (N_T, N_F - 1))
    cur.execute("INSERT INTO vehicle_model VALUES (1,'OEM-A','Sedan',2022,'ICE'),(2,'OEM-B','SUV',2023,'EV'),(3,'OEM-C','Van',2021,'HYBRID')")
    cur.execute("INSERT INTO alert_type VALUES ('OVERHEAT',5,'x'),('DTC_CRITICAL',5,'x'),('BATTERY_12V_LOW',3,'x'),('EV_SOC_LOW',3,'x'),('HARSH_BRAKE',2,'x')")
    ids = np.arange(N_V); fleet = ids % N_F
    veh = pd.DataFrame({"id": ids, "vin": [make_vin("MA3EJKD1", int(i)) for i in ids], "tenant_id": fleet % N_T, "fleet_id": fleet,
                        "model_id": r.integers(1, 4, N_V), "registered_on": "2022-01-01", "status": "ACTIVE"})
    copy_df(cur, "vehicle", veh)
    risk = pd.DataFrame({"vehicle_id": ids, "tenant_id": veh.tenant_id, "score": np.clip(r.beta(1, 14, N_V), 0, 1).round(5),
                         "model_version": "hgb-1", "computed_at": "2026-10-02 09:00:00+00", "factors": "[]"})
    copy_df(cur, "vehicle_risk", risk)
    types = np.array(["OVERHEAT", "DTC_CRITICAL", "BATTERY_12V_LOW", "EV_SOC_LOW", "HARSH_BRAKE"])
    t = r.choice(len(types), N_A, p=[.05, .05, .15, .1, .65]); sev = np.array([5, 5, 3, 3, 2])[t]
    vid = r.integers(0, N_V, N_A)
    ts = pd.Timestamp("2026-10-02") - pd.to_timedelta(r.integers(0, 90 * 86400, N_A), unit="s")
    alert = pd.DataFrame({"id": np.arange(N_A), "vehicle_id": vid, "tenant_id": (vid % N_F) % N_T, "type_code": types[t], "severity": sev,
                          "raised_at": ts, "status": r.choice(["OPEN", "ACKED", "CLOSED"], N_A, p=[.4, .2, .4]), "detail": "x"})
    copy_df(cur, "alert", alert)
    cur.execute("INSERT INTO fault_case (vehicle_id, observed_day, embedding) SELECT 0, g, ('['||array_to_string(ARRAY(SELECT random()::float4 FROM generate_series(1,13)),',')||']')::vector FROM generate_series(1,%s) g", (N_VEC,))
    cur.execute("ANALYZE")

    QS = {
        "Q1 open critical alerts (page 1)": ("SELECT id, vehicle_id, type_code, severity, raised_at FROM alert WHERE tenant_id=3 AND status='OPEN' AND severity>=4 ORDER BY raised_at DESC, id DESC LIMIT 25", None),
        "Q2 risk-ranked vehicles (keyset page)": ("SELECT v.vin, r.score FROM vehicle_risk r JOIN vehicle v ON v.id=r.vehicle_id WHERE r.tenant_id=3 AND (r.score, r.vehicle_id) < (0.08, 60000) ORDER BY r.score DESC, r.vehicle_id DESC LIMIT 25", None),
        "Q3 alerts per fleet/type, last 30 days": ("SELECT fleet_id, type_code, sum(n) FROM mv_fleet_alert_daily WHERE fleet_id IN (3,23,43,63) AND day >= date '2026-09-02' GROUP BY 1,2", None),
        "Q5 nearest 5 fault cases (cosine)": ("SELECT id, embedding <=> '[0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5,0.5]' AS d FROM fault_case ORDER BY d LIMIT 5", None),
    }
    Q3_BEFORE = "SELECT v.fleet_id, a.type_code, count(*) FROM alert a JOIN vehicle v ON v.id=a.vehicle_id WHERE v.fleet_id IN (3,23,43,63) AND a.raised_at >= timestamptz '2026-09-02' GROUP BY 1,2"
    Q1_OFFSET = "SELECT id, vehicle_id, type_code, severity, raised_at FROM alert WHERE tenant_id=3 AND status='OPEN' AND severity>=4 ORDER BY raised_at DESC, id DESC LIMIT 25 OFFSET 1500"
    cur.execute("SELECT raised_at, id FROM alert WHERE tenant_id=3 AND status='OPEN' AND severity>=4 ORDER BY raised_at DESC, id DESC OFFSET 1500 LIMIT 1")
    cut_ts, cut_id = cur.fetchone()
    Q1_KEYSET = f"SELECT id, vehicle_id, type_code, severity, raised_at FROM alert WHERE tenant_id=3 AND status='OPEN' AND severity>=4 AND (raised_at, id) < ('{cut_ts.isoformat()}', {cut_id}) ORDER BY raised_at DESC, id DESC LIMIT 25"

    before = {}
    before["Q1"] = explain(conn, QS["Q1 open critical alerts (page 1)"][0])
    before["Q2"] = explain(conn, QS["Q2 risk-ranked vehicles (keyset page)"][0])
    before["Q3"] = explain(conn, Q3_BEFORE)
    before["Q5"] = explain(conn, QS["Q5 nearest 5 fault cases (cosine)"][0])
    before["Q1_offset_deep"] = None  # measured after index (isolates pagination style from indexing)

    t0 = time.time(); cur.execute((ROOT / "db/optimizations.sql").read_text()); cur.execute("ANALYZE"); build_s = time.time() - t0

    after = {"Q1": explain(conn, QS["Q1 open critical alerts (page 1)"][0]), "Q2": explain(conn, QS["Q2 risk-ranked vehicles (keyset page)"][0]),
             "Q3": explain(conn, QS["Q3 alerts per fleet/type, last 30 days"][0]), "Q5": explain(conn, QS["Q5 nearest 5 fault cases (cosine)"][0])}
    off, off_plan = explain(conn, Q1_OFFSET); key, key_plan = explain(conn, Q1_KEYSET)

    # ORM N+1 vs one JOIN (wall-clock, includes round trips)
    ids25 = list(range(0, 25 * 400, 400))
    def wall(fn, n=20):
        xs = []
        for _ in range(n):
            t = time.perf_counter(); fn(); xs.append((time.perf_counter() - t) * 1000)
        return statistics.median(xs)
    def n_plus_1():
        cur.execute("SELECT id, vin FROM vehicle WHERE id = ANY(%s)", (ids25,)); rows = cur.fetchall()
        for vid, _ in rows:
            cur.execute("SELECT score FROM vehicle_risk WHERE vehicle_id=%s", (vid,)); cur.fetchone()
    def one_join():
        cur.execute("SELECT v.id, v.vin, r.score FROM vehicle v JOIN vehicle_risk r ON r.vehicle_id=v.id WHERE v.id = ANY(%s)", (ids25,)); cur.fetchall()
    nplus1_ms, join_ms = wall(n_plus_1), wall(one_join)

    res = {"scale": {"vehicles": N_V, "alerts": N_A, "risk_rows": N_V, "vectors": N_VEC, "postgres": "16.2 embedded (pgserver), 1 vCPU sandbox"},
           "index_build_seconds": round(build_s, 1),
           "queries": {k: {"before_ms": round(before[k][0], 3), "after_ms": round(after[k][0], 3)} for k in ("Q1", "Q2", "Q3", "Q5")},
           "pagination_deep_page": {"offset_1500_ms": round(off, 3), "keyset_ms": round(key, 3)},
           "orm_n_plus_1": {"n_plus_1_ms": round(nplus1_ms, 3), "single_join_ms": round(join_ms, 3), "queries_issued": "26 vs 1"}}
    (ROOT / "docs/sql_results.json").write_text(json.dumps(res, indent=2))
    def trim(t): return "\n".join(t.splitlines()[:14])
    md = ["# Query plans (measured)\n", f"Scale: {json.dumps(res['scale'])}\n"]
    for k in ("Q1", "Q2", "Q3", "Q5"):
        md += [f"## {k}\n", f"**Before: {before[k][0]:.3f} ms**\n```\n{trim(before[k][1])}\n```\n", f"**After: {after[k][0]:.3f} ms**\n```\n{trim(after[k][1])}\n```\n"]
    md += [f"## Deep pagination\nOFFSET 1500: **{off:.3f} ms**\n```\n{trim(off_plan)}\n```\nKeyset: **{key:.3f} ms**\n```\n{trim(key_plan)}\n```\n"]
    (ROOT / "docs/query_plans.md").write_text("\n".join(md))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
