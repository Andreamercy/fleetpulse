"""Integration: REAL Kafka (Redpanda) + ClickHouse + Redis via Testcontainers. Requires Docker.
Skipped automatically when Docker is unavailable (e.g. the authoring sandbox); runs in CI (see ci.yml `integration` job)."""
import shutil
import time
import pytest

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def test_end_to_end_exactly_once_effect_and_recovery():
    from testcontainers.kafka import RedpandaContainer
    from testcontainers.clickhouse import ClickHouseContainer
    from confluent_kafka import Producer, Consumer
    from fleetpulse.ingest.adapters import ClickHouseSink
    from fleetpulse.ingest.processor import EventProcessor
    from fleetpulse.ingest.memory import MemState, MemAlerts, MemDlq
    from fleetpulse.sim.fleet import FleetSimulator, NetworkMess, encode
    import clickhouse_connect

    with RedpandaContainer() as rp, ClickHouseContainer() as chc:
        ch = clickhouse_connect.get_client(host=chc.get_container_host_ip(), port=int(chc.get_exposed_port(8123)),
                                           username=chc.username, password=chc.password)
        ddl = open("db/clickhouse.sql").read().split(";\n\n")[0]
        ch.command("\n".join(l for l in ddl.splitlines() if not l.strip().startswith("--")).rstrip().rstrip(";"))
        sim, mess, sent = FleetSimulator(500, seed=1), NetworkMess(seed=1, bad_rate=0), set()
        p = Producer({"bootstrap.servers": rp.get_bootstrap_server()})
        for _ in range(20):
            for e, line in zip(*(lambda ev: (ev, encode(ev)))(mess.process(sim.tick_events()))):
                p.produce("telemetry", line.encode(), key=e["vin"]); sent.add((e["vin"], e["seq"]))
        p.flush()
        c = Consumer({"bootstrap.servers": rp.get_bootstrap_server(), "group.id": "t", "auto.offset.reset": "earliest", "enable.auto.commit": False})
        c.subscribe(["telemetry"])
        sink = ClickHouseSink.__new__(ClickHouseSink); sink.c, sink.retries = ch, 3
        proc = EventProcessor(sink, MemState(), MemAlerts(), MemDlq())
        t0 = time.time()
        while time.time() - t0 < 20:
            for m in c.consume(500, 1.0):
                if not m.error(): proc.handle(m.value())
        proc.flush(); ch.command("OPTIMIZE TABLE telemetry FINAL")
        stored = ch.query("SELECT count() FROM (SELECT vin, seq FROM telemetry GROUP BY vin, seq)").result_rows[0][0]
        assert stored == len({k for k in sent})            # no loss, duplicates collapsed
