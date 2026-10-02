"""Infrastructure adapters: Kafka consumer loop, ClickHouse sink, Redis state, Kafka alert/DLQ producers.

NOTE: these need the docker-compose stack (Redpanda, ClickHouse, Redis). Imports are lazy so the
core and unit tests run without those client libraries installed.
"""
from __future__ import annotations
import json
import os
import signal
import time


class ClickHouseSink:
    COLS = ["tenant", "vin", "ts", "seq", "lat", "lon", "speed_kmh", "odo_km", "coolant_c", "batt_v",
            "soc_pct", "dtc", "evt", "oem", "late"]

    def __init__(self, host=None, retries=5):
        import clickhouse_connect
        self.c = clickhouse_connect.get_client(host=host or os.getenv("CLICKHOUSE_HOST", "clickhouse"),
                                               username=os.getenv("CLICKHOUSE_USER", "default"),
                                               password=os.getenv("CLICKHOUSE_PASSWORD", ""))
        self.retries = retries

    def write_batch(self, rows):
        from datetime import datetime
        data = [[(datetime.fromisoformat(r[c]) if c == "ts" else r[c]) for c in self.COLS] for r in rows]
        delay = 0.2
        for attempt in range(self.retries):                      # bounded retry with backoff
            try:
                self.c.insert("telemetry", data, column_names=self.COLS)
                return
            except Exception:
                if attempt == self.retries - 1:
                    raise
                time.sleep(delay); delay *= 2


class RedisState:
    def __init__(self):
        import redis
        self.r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        self.pipe, self.n = self.r.pipeline(transaction=False), 0

    def put_latest(self, vin, state):
        self.pipe.hset(f"veh:{vin}", mapping={k: json.dumps(v) for k, v in state.items()})
        self.pipe.expire(f"veh:{vin}", 3600)
        self.n += 1
        if self.n >= 500:
            self.flush()

    def flush(self):
        self.pipe.execute(); self.n = 0


class KafkaOut:
    def __init__(self, topic):
        from confluent_kafka import Producer
        self.p = Producer({"bootstrap.servers": os.getenv("KAFKA_BROKERS", "redpanda:9092"),
                           "enable.idempotence": True, "linger.ms": 20, "compression.type": "lz4"})
        self.topic = topic

    def publish(self, item, reason=None):
        body = item if reason is None else {"reason": reason, "raw": item}
        key = (item.get("vin") if isinstance(item, dict) else None) or None
        self.p.produce(self.topic, json.dumps(body).encode(), key=key)
        self.p.poll(0)


def run():  # pragma: no cover - exercised via docker compose / load test
    from confluent_kafka import Consumer
    from .processor import EventProcessor
    from prometheus_client import Counter, Gauge, start_http_server

    reg = {k: Counter(f"fp_ingest_{k}_total", k) for k in ("received", "accepted", "duplicates", "late", "invalid", "alerts")}
    lag_g = Gauge("fp_ingest_buffer_rows", "rows buffered awaiting flush")
    start_http_server(int(os.getenv("METRICS_PORT", "9100")))

    state = RedisState()
    proc = EventProcessor(sink=ClickHouseSink(), state=state, alerts=KafkaOut("alerts"), dlq=KafkaOut("telemetry.dlq"),
                          batch_size=int(os.getenv("BATCH_SIZE", "5000")))
    c = Consumer({"bootstrap.servers": os.getenv("KAFKA_BROKERS", "redpanda:9092"),
                  "group.id": "fp-ingest", "enable.auto.commit": False, "auto.offset.reset": "earliest",
                  "fetch.min.bytes": 65536, "max.poll.interval.ms": 300000})
    c.subscribe(["telemetry"])
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    last_flush, prev = time.time(), {k: 0 for k in reg}
    while not stop["v"]:
        msgs = c.consume(num_messages=2000, timeout=0.5)
        for m in msgs:
            if m.error():
                continue
            proc.handle(m.value(), oem=(dict(m.headers() or []).get("oem", b"canonical")).decode())
        # back-pressure: flush (blocking) before pulling more; offsets only committed after durable write
        if msgs and (time.time() - last_flush > 0.5 or len(proc._buf) >= proc.batch_size):
            proc.flush(); state.flush(); c.commit(asynchronous=False); last_flush = time.time()
        lag_g.set(len(proc._buf))
        for k in reg:
            cur = getattr(proc.metrics, k)
            reg[k].inc(cur - prev[k]); prev[k] = cur
    proc.flush(); state.flush(); c.commit(asynchronous=False); c.close()


if __name__ == "__main__":  # pragma: no cover
    run()
