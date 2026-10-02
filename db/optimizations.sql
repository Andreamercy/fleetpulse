-- Applied after the base schema; each statement is justified by a measured before/after plan
-- (docs/query_plans.md, reproduce with: python db/explain/run_explain.py).

-- Q1: open critical alerts per tenant, newest first, keyset-paginated.
-- Partial index: only the rows the hot query reads (OPEN & severity>=4); ordered to avoid a sort.
CREATE INDEX alert_open_critical_idx ON alert (tenant_id, raised_at DESC, id DESC) WHERE status = 'OPEN' AND severity >= 4;

-- Q2: risk-ranked vehicle list per tenant (keyset on (score, vehicle_id)). tenant_id is denormalised on
-- vehicle_risk so the index serves filter + order + cursor in one range scan, with no join/sort.
CREATE INDEX vehicle_risk_rank_idx ON vehicle_risk (tenant_id, score DESC, vehicle_id DESC);

-- Q3: daily alert counts per fleet/type -> materialised view, refreshed every few minutes.
CREATE MATERIALIZED VIEW mv_fleet_alert_daily AS
  SELECT v.fleet_id, a.type_code, date_trunc('day', a.raised_at) AS day, count(*) AS n, max(a.severity) AS max_sev
  FROM alert a JOIN vehicle v ON v.id = a.vehicle_id GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX ON mv_fleet_alert_daily (fleet_id, type_code, day);   -- enables REFRESH ... CONCURRENTLY

-- Vector similarity (fault-case retrieval): HNSW, cosine.
CREATE INDEX fault_case_hnsw ON fault_case USING hnsw (embedding vector_cosine_ops);

-- FK support indexes commonly forgotten (joins + ON DELETE)
CREATE INDEX trip_vehicle_started_idx ON trip (vehicle_id, started_at DESC);
CREATE INDEX vehicle_tenant_fleet_idx ON vehicle (tenant_id, fleet_id);
