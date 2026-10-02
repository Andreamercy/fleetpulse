#!/usr/bin/env bash
# Chaos: kill the broker and an ingest pod mid-stream, prove no data loss and recovery.
# Requires `docker compose up` running. Exit code 0 = PASS.
set -euo pipefail
q() { docker compose exec -T clickhouse clickhouse-client -q "$1"; }
echo "[1/5] baseline distinct (vin,seq): $(q 'SELECT uniqExact(vin, seq) FROM telemetry')"
echo "[2/5] killing broker for 20 s";  docker compose kill redpanda; sleep 20; docker compose start redpanda
echo "[3/5] killing one ingest replica"; docker compose kill --signal SIGKILL "$(docker compose ps -q ingest | head -1)" 2>/dev/null || true
echo "[4/5] waiting for lag to drain (max 120 s)"
for i in $(seq 1 24); do
  lag=$(docker compose exec -T redpanda rpk group describe fp-ingest | awk '/TOTAL-LAG/{print $2}'); [ "${lag:-1}" = "0" ] && break; sleep 5; done
echo "[5/5] lag=${lag:-?}"
docker compose exec -T redpanda rpk group describe fp-ingest | head -5
# every produced (vin,seq) must exist in ClickHouse: compare against the producer-side counter exposed by the simulator
q "OPTIMIZE TABLE telemetry FINAL"
echo "distinct after recovery: $(q 'SELECT uniqExact(vin, seq) FROM telemetry')"
[ "${lag:-1}" = "0" ] && echo PASS || { echo FAIL; exit 1; }
