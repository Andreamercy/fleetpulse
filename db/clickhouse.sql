-- Telemetry store (hot + warm tiers). ClickHouse chosen over a B-tree RDBMS: columnar compression,
-- LSM-style batch inserts (no per-row index maintenance), partition pruning by day.
CREATE TABLE IF NOT EXISTS telemetry
(
    tenant     LowCardinality(String),
    vin        FixedString(17),
    ts         DateTime64(3, 'UTC'),
    seq        UInt64,
    lat        Float32, lon Float32,
    speed_kmh  Float32, odo_km Float32,
    coolant_c  Nullable(Float32), batt_v Nullable(Float32), soc_pct Nullable(Float32),
    dtc        Array(LowCardinality(String)),
    evt        LowCardinality(Nullable(String)),
    oem        LowCardinality(String),
    late       UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree              -- idempotent writes: replayed/duplicate (vin,seq) collapse at merge
PARTITION BY toYYYYMMDD(ts)              -- time partitioning: cheap retention (DROP PARTITION) + pruning
ORDER BY (tenant, vin, ts, seq)          -- shard/sort key: tenant+vin spreads load; per-vehicle scans are contiguous
TTL toDateTime(ts) + INTERVAL 90 DAY DELETE              -- cold copy lives in Parquet on object storage
-- Production tiering (needs a 'tiered' storage policy with hot SSD + warm object-store volumes):
--   TTL toDateTime(ts) + INTERVAL 7 DAY TO VOLUME 'warm', toDateTime(ts) + INTERVAL 90 DAY DELETE
--   SETTINGS storage_policy = 'tiered';
;

-- Features for the risk model, computed in the database (batch scoring job reads this).
CREATE MATERIALIZED VIEW IF NOT EXISTS vehicle_day_agg
ENGINE = AggregatingMergeTree PARTITION BY toYYYYMM(day) ORDER BY (tenant, vin, day) AS
SELECT tenant, vin, toDate(ts) AS day,
       avgState(coolant_c) AS coolant_avg, maxState(coolant_c) AS coolant_max, minState(batt_v) AS batt_min,
       countState() AS n, sumState(length(dtc)) AS dtc_n
FROM telemetry WHERE late = 0 GROUP BY tenant, vin, day;
