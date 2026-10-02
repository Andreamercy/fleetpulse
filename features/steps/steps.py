from behave import given, when, then
from fastapi.testclient import TestClient
from fleetpulse.api.app import create_app
from fleetpulse.api.audit import AuditLog
from fleetpulse.api.auth import issue_dev_token
from fleetpulse.api.ratelimit import TokenBucket
from fleetpulse.api.repo import InMemoryRepo
from fleetpulse.core.vin import make_vin
from fleetpulse.ingest.memory import MemSink, MemState, MemAlerts, MemDlq
from fleetpulse.ingest.processor import EventProcessor

VIN = make_vin("MA3EJKD1", 1)


def ev(seq, t, **kw):
    e = {"v": 1, "vin": VIN, "ts": f"2026-09-25T10:{t // 60:02d}:{t % 60:02d}.000Z", "seq": seq, "lat": 13.0, "lon": 80.2, "speed_kmh": 50.0,
         "odo_km": 1000.0, "coolant_c": 90.0, "batt_v": 12.6, "dtc": [], "oem": "canonical"}
    e.update(kw); return e


@given("a vehicle streaming normal telemetry")
def _(ctx):
    ctx.sink, ctx.alerts, ctx.dlq = MemSink(), MemAlerts(), MemDlq()
    ctx.proc = EventProcessor(ctx.sink, MemState(), ctx.alerts, ctx.dlq)
    for i in range(5): ctx.proc.handle(ev(i, i))

@when("its coolant temperature stays above {c:d} C for one minute")
def _(ctx, c):
    for i in range(5, 66): ctx.proc.handle(ev(i, i, coolant_c=float(c) + 2))

@then("an OVERHEAT alert with severity {s:d} is raised exactly once")
def _(ctx, s):
    a = [x for x in ctx.alerts.items if x["type"] == "OVERHEAT"]
    assert len(a) == 1 and a[0]["severity"] == s, a

@when("the same event is delivered twice and a message with an invalid VIN arrives")
def _(ctx):
    ctx.proc.handle(ev(100, 100)); ctx.proc.handle(ev(100, 100)); ctx.proc.handle(ev(101, 101, vin=VIN[:-1] + "I")); ctx.proc.flush()

@then("only one copy is stored and the bad message is in the dead-letter queue")
def _(ctx):
    assert sum(r["seq"] == 100 for r in ctx.sink.rows) == 1 and len(ctx.dlq.items) == 1

@given('a fleet manager of tenant "{t}"')
def _(ctx, t):
    ctx.repo = InMemoryRepo(n=300)
    ctx.client = TestClient(create_app(ctx.repo, AuditLog(), TokenBucket(1000, 1000)))
    ctx.h = {"Authorization": f"Bearer {issue_dev_token('mgr', t, ['fleet_manager'])}"}
    ctx.tenant = t

@when('they request the vehicle of tenant "{other}"')
def _(ctx, other):
    vin = next(v for v in ctx.repo.vehicles.values() if v["tenant"] == other)["vin"]
    ctx.r = ctx.client.get(f"/v1/vehicles/{vin}", headers=ctx.h)
    ctx.ghost = ctx.client.get("/v1/vehicles/MA3EJKD10NA999999", headers=ctx.h)

@then("the API answers 404 exactly as for a vehicle that does not exist")
def _(ctx):
    assert ctx.r.status_code == 404 and ctx.r.json() == ctx.ghost.json()

@when("they ask the agent to book a work order for one of their vehicles")
def _(ctx):
    vin = next(v for v in ctx.repo.vehicles.values() if v["tenant"] == ctx.tenant)["vin"]
    ctx.res = ctx.client.post("/v1/agent/ask", json={"question": f"book a work order for {vin}"}, headers=ctx.h).json()["steps"][0]["result"]

@then("the work order is PENDING_APPROVAL until a manager approves it")
def _(ctx):
    assert ctx.res["status"] == "PENDING_APPROVAL"
    assert ctx.client.post(f"/v1/work-orders/{ctx.res['work_order_id']}/approve", headers=ctx.h).json()["status"] == "APPROVED"
