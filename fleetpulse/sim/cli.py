"""Simulator CLI. Shard across processes/pods to reach 100k+ events/s:

  python -m fleetpulse.sim.cli --vehicles 100000 --shards 8 --shard 0 --sink kafka --burst-at 60 --burst-secs 300
  python -m fleetpulse.sim.cli --vehicles 1000 --sink file --out /tmp/events.ndjson --seconds 10
"""
import argparse
import os
import time

from .fleet import FleetSimulator, NetworkMess, encode


def main(argv=None):  # pragma: no cover - needs a broker for --sink kafka
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=100_000, help="TOTAL fleet size across all shards")
    ap.add_argument("--shards", type=int, default=1); ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--sink", choices=["stdout", "file", "kafka"], default="stdout")
    ap.add_argument("--out", default="events.ndjson"); ap.add_argument("--seconds", type=int, default=0, help="0 = run forever")
    ap.add_argument("--burst-at", type=int, default=-1, help="second at which a 3x burst starts"); ap.add_argument("--burst-secs", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)

    per = a.vehicles // a.shards
    sim = FleetSimulator(per, seed=a.seed, offset=a.shard * per)
    mess = NetworkMess(seed=a.seed + a.shard)
    producer = None
    if a.sink == "kafka":
        from confluent_kafka import Producer
        producer = Producer({"bootstrap.servers": os.getenv("KAFKA_BROKERS", "redpanda:9092"), "linger.ms": 50, "batch.num.messages": 20000,
                             "compression.type": "lz4", "enable.idempotence": True, "queue.buffering.max.messages": 1_000_000})
    fh = open(a.out, "w") if a.sink == "file" else None
    sec = 0
    while a.seconds == 0 or sec < a.seconds:
        t0 = time.time()
        burst = a.burst_at >= 0 and a.burst_at <= sec < a.burst_at + a.burst_secs
        for _ in range(3 if burst else 1):
            evs = mess.process(sim.tick_events())
            for e, line in zip(evs, encode(evs)):
                if producer:
                    while True:
                        try:
                            producer.produce("telemetry", line.encode(), key=e["vin"]); break
                        except BufferError:        # back-pressure from the broker: wait, don't drop
                            producer.poll(0.05)
                elif fh:
                    fh.write(line + "\n")
                else:
                    print(line)
            if producer:
                producer.poll(0)
        sec += 1
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
    if producer:
        producer.flush()
    if fh:
        fh.close()


if __name__ == "__main__":
    main()
