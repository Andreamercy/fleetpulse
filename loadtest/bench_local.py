"""In-sandbox micro-benchmarks (single vCPU, no Kafka/ClickHouse). These measure OUR code's CPU cost
per event so we can size consumers; they are NOT the end-to-end 100K ev/s cluster result
(that is produced by loadtest/k6 + simulator against `docker compose` / Kubernetes).

  python loadtest/bench_local.py
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np
from fleetpulse.sim.fleet import FleetSimulator, NetworkMess, encode
from fleetpulse.ingest.processor import EventProcessor
from fleetpulse.ingest.memory import MemSink, MemState, MemAlerts, MemDlq

out = {"env": f"{os.cpu_count()} vCPU sandbox, Python {sys.version.split()[0]}"}

# 1) simulator: generate + network mess + JSON encode, 100,000 vehicles, 3 ticks
sim, mess = FleetSimulator(100_000), NetworkMess(seed=1)
t = time.perf_counter(); n = 0; payloads = []
for _ in range(3):
    evs = mess.process(sim.tick_events()); payloads += encode(evs); n += len(evs)
dt = time.perf_counter() - t
out["simulator_100k_vehicles"] = {"events": n, "events_per_sec_single_core": round(n / dt), "avg_event_bytes": round(sum(map(len, payloads)) / len(payloads), 1)}

# 2) processor: validate (pydantic) + dedupe + windows + rules + batch, in-memory ports
p = EventProcessor(MemSink(), MemState(), MemAlerts(), MemDlq(), batch_size=20000)
sample = payloads[:250_000]
t = time.perf_counter()
for m in sample: p.handle(m)
p.flush(); dt = time.perf_counter() - t
m = p.metrics
out["processor_single_core"] = {"messages": len(sample), "msgs_per_sec": round(len(sample) / dt), "accepted": m.accepted, "duplicates": m.duplicates,
                                 "late": m.late, "invalid_to_dlq": m.invalid, "alerts": m.alerts, "bloom_checks": m.bloom_false_positive_checks,
                                 "bloom_est_fp_rate": round(p.bloom.cur.est_fp_rate(), 6)}
rate = out["processor_single_core"]["msgs_per_sec"]
out["capacity_plan"] = {"target_ev_s": 300_000, "note": "100K ev/s sustained x 3 burst", "consumer_cores_needed_at_70pct_util": int(np.ceil(300_000 / (rate * 0.7)))}

# 3) API over real HTTP (uvicorn + httpx, 20 concurrent clients)
env = {**os.environ, "ENV": "dev", "RATE_PER_S": "100000", "RATE_BURST": "100000"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "fleetpulse.api.app:app", "--port", "8765", "--log-level", "warning"], env=env, cwd=str(pathlib.Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402
async def api_bench():
    res = {}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8765", limits=httpx.Limits(max_connections=200)) as c:
        for _ in range(50):
            try:
                if (await c.get("/healthz")).status_code == 200: break
            except Exception: await asyncio.sleep(0.2)
        tok = (await c.get("/dev/token?role=fleet_manager&tenant=t00")).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        async def call(i, lat):
            t = time.perf_counter()
            r = await c.get("/v1/vehicles?limit=25" if i % 2 else "/v1/alerts?limit=25&min_severity=4", headers=h)
            assert r.status_code == 200
            lat.append((time.perf_counter() - t) * 1000)
        # (a) closed loop, 20 concurrent clients: measures saturation throughput of ONE vCPU (shared with the client)
        lat, sem = [], asyncio.Semaphore(20)
        async def one(i):
            async with sem: await call(i, lat)
        t0 = time.perf_counter(); await asyncio.gather(*[one(i) for i in range(3000)]); dt = time.perf_counter() - t0
        q = np.percentile(lat, [50, 95, 99])
        res["saturation_closed_loop_c20"] = {"requests": 3000, "rps": round(3000 / dt), "p50_ms": round(q[0], 1), "p95_ms": round(q[1], 1), "p99_ms": round(q[2], 1)}
        # (b) open loop at fixed arrival rate (what a latency SLO is defined against)
        for rate in (60, 100):
            lat, tasks, n, t0 = [], [], rate * 15, time.perf_counter()
            for i in range(n):
                tasks.append(asyncio.create_task(call(i, lat)))
                await asyncio.sleep(max(0, t0 + (i + 1) / rate - time.perf_counter()))
            await asyncio.gather(*tasks)
            q = np.percentile(lat, [50, 95, 99])
            res[f"open_loop_{rate}rps"] = {"requests": n, "p50_ms": round(q[0], 1), "p95_ms": round(q[1], 1), "p99_ms": round(q[2], 1)}
    return res
try:
    out["api_http_in_memory_repo"] = asyncio.run(api_bench())
finally:
    srv.terminate()
pathlib.Path(__file__).resolve().parents[1].joinpath("docs/bench_local.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
