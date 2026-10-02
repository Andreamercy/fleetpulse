"""FleetPulse API (FastAPI). Presentation layer only: auth, validation, pagination, DTO mapping."""
from __future__ import annotations
import os
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from ..core.geo import mask_location
from ..core.vin import is_valid_vin
from .agent import AgentService
from .audit import AuditLog
from .auth import Principal, issue_dev_token, require
from .ratelimit import TokenBucket
from .repo import InMemoryRepo, dec_cursor

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app(repo=None, audit: AuditLog | None = None, limiter: TokenBucket | None = None) -> FastAPI:
    app = FastAPI(title="FleetPulse API", version="1.0.0", docs_url="/docs")
    app.state.repo = repo or InMemoryRepo()
    app.state.audit = audit or AuditLog()
    app.state.agent = AgentService(app.state.repo, app.state.audit)
    app.state.limiter = limiter or TokenBucket(rate_per_s=float(os.getenv("RATE_PER_S", "50")), burst=int(os.getenv("RATE_BURST", "100")))

    # ---------------------------------------------------------------- cross-cutting
    @app.middleware("http")
    async def mw(request: Request, call_next):
        rid = request.headers.get("x-request-id", str(uuid.uuid4()))
        t0 = time.perf_counter()
        resp = await call_next(request)
        resp.headers.update({"x-request-id": rid, "x-content-type-options": "nosniff", "cache-control": "no-store",
                             "strict-transport-security": "max-age=63072000; includeSubDomains",
                             "content-security-policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
                             "x-response-ms": f"{(time.perf_counter() - t0) * 1000:.1f}"})
        return resp

    @app.exception_handler(HTTPException)
    async def http_err(_, exc: HTTPException):
        return JSONResponse({"error": {"code": exc.status_code, "message": exc.detail}}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    def limited(p: Principal = Depends(require("admin", "fleet_manager", "analyst", "viewer"))) -> Principal:
        ok, retry = app.state.limiter.allow(p.sub)
        if not ok:
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(int(retry) + 1)})
        return p

    def _cursor(c):
        try:
            return dec_cursor(c)
        except ValueError:
            raise HTTPException(400, "invalid cursor")

    def _mask(p: Principal, row: dict) -> dict:
        if not p.sees_exact_location and "lat" in row:
            row = {**row}
            row["lat"], row["lon"] = mask_location(row["lat"], row["lon"])
            row["location_masked"] = True
        return row

    def _audit(p, action, res, **d):
        app.state.audit.record(p.sub, p.tenant, action, res, d)

    # ---------------------------------------------------------------- routes
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/v1/vehicles")
    def vehicles(limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                 min_risk: float = Query(0.0, ge=0, le=1), p: Principal = Depends(limited)):
        rows, nxt = app.state.repo.list_vehicles(p.tenant, limit, _cursor(cursor), min_risk)
        _audit(p, "vehicles.list", "vehicles", n=len(rows))
        return {"items": [_mask(p, r) for r in rows], "next_cursor": nxt}

    @app.get("/v1/vehicles/{vin}")
    def vehicle(vin: str, p: Principal = Depends(limited)):
        if not is_valid_vin(vin, strict_check_digit=False):
            raise HTTPException(422, "invalid VIN")
        v = app.state.repo.get_vehicle(p.tenant, vin.upper())
        if v is None:  # same response for 'other tenant' and 'does not exist' (no enumeration)
            raise HTTPException(404, "vehicle not found")
        _audit(p, "vehicle.read", vin)
        return _mask(p, v)

    @app.get("/v1/vehicles/{vin}/telemetry")
    def telemetry(vin: str, limit: int = Query(60, ge=1, le=500), p: Principal = Depends(limited)):
        if not is_valid_vin(vin, strict_check_digit=False):
            raise HTTPException(422, "invalid VIN")
        rows = app.state.repo.telemetry(p.tenant, vin.upper(), limit)
        if not rows:
            raise HTTPException(404, "vehicle not found")
        _audit(p, "telemetry.read", vin, n=len(rows))
        return {"items": [_mask(p, r) for r in rows]}

    @app.get("/v1/alerts")
    def alerts(limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
               min_severity: int = Query(1, ge=1, le=5), p: Principal = Depends(limited)):
        rows, nxt = app.state.repo.list_alerts(p.tenant, limit, _cursor(cursor), min_severity)
        _audit(p, "alerts.list", "alerts", n=len(rows))
        return {"items": rows, "next_cursor": nxt}

    @app.post("/v1/agent/ask")
    def ask(body: dict, p: Principal = Depends(require("admin", "fleet_manager", "analyst", "viewer"))):
        q = body.get("question")
        if not isinstance(q, str) or not q.strip():
            raise HTTPException(422, "question required")
        return app.state.agent.ask(p, q)

    @app.post("/v1/work-orders/{wid}/approve")
    def approve(wid: str, p: Principal = Depends(require("admin", "fleet_manager"))):
        w = app.state.agent.approve(p, wid)
        if w is None:
            raise HTTPException(404, "work order not found")
        return w

    @app.delete("/v1/privacy/vehicles/{vin}", status_code=202)
    def erase(vin: str, p: Principal = Depends(require("admin"))):
        if not is_valid_vin(vin, strict_check_digit=False):
            raise HTTPException(422, "invalid VIN")
        n = app.state.repo.erase_vehicle(p.tenant, vin.upper())
        _audit(p, "privacy.erase", vin, found=bool(n))
        if not n:
            raise HTTPException(404, "vehicle not found")
        return {"status": "erasure_scheduled", "vin": vin.upper()}

    @app.get("/v1/audit")
    def audit_log(limit: int = Query(50, ge=1, le=500), p: Principal = Depends(require("admin"))):
        rows = [r for r in app.state.audit.records if r["tenant"] == p.tenant][-limit:]
        return {"items": rows, "chain_valid": app.state.audit.verify()}

    if os.getenv("ENV", "dev") == "dev":
        @app.get("/dev/token")
        def dev_token(role: str = "fleet_manager", tenant: str = "t00"):
            """Dev-only convenience so the UI demo works without an IdP. Disabled outside ENV=dev."""
            return {"token": issue_dev_token(f"demo-{role}", tenant, [role])}

    @app.get("/")
    @app.get("/ui")
    def ui():
        return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
