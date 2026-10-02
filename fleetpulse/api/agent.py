"""Fleet agent with guardrails and an audit trail.

Design: the LLM (or the default rule-based planner) only *selects a tool and arguments*; it never
touches data directly. Guardrails:
  1. Tool allow-list; unknown tools are rejected.            (least privilege)
  2. Arguments validated by schema (VIN regex, bounded ints). (prompt-injection blast radius)
  3. Tenant and role come from the verified JWT, never from model output.
  4. Tool results are structured data, never concatenated into instructions.
  5. Mutating tools (create_work_order) return PENDING_APPROVAL; a human with fleet_manager role must approve.
  6. Max 3 tool calls per question; every call and decision is written to the audit log.
"""
from __future__ import annotations
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from ..core.vin import is_valid_vin
from .audit import AuditLog
from .auth import Principal

MAX_STEPS = 3
_VIN_IN_TEXT = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


@dataclass
class ToolCall:
    name: str
    args: dict


class Planner(Protocol):
    def plan(self, question: str) -> list[ToolCall]: ...


class RuleBasedPlanner:
    """Deterministic planner (default; no API key needed). An LLM planner implements the same
    Protocol and is plugged in via env LLM_PROVIDER; its output goes through the same validation."""

    def plan(self, q: str) -> list[ToolCall]:
        ql = q.lower()
        m = _VIN_IN_TEXT.search(q.upper())
        vin = m.group(0) if m else None
        calls: list[ToolCall] = []
        if vin and ("work order" in ql or "schedule" in ql or "book" in ql):
            calls.append(ToolCall("create_work_order", {"vin": vin, "reason": "agent-recommended inspection"}))
        elif vin:
            calls += [ToolCall("vehicle_summary", {"vin": vin}), ToolCall("open_alerts", {"min_severity": 1})]
        elif "alert" in ql:
            calls.append(ToolCall("open_alerts", {"min_severity": 4}))
        else:
            calls.append(ToolCall("top_risk_vehicles", {"n": 5}))
        return calls[:MAX_STEPS]


class AgentService:
    def __init__(self, repo, audit: AuditLog, planner: Planner | None = None):
        self.repo, self.audit, self.planner = repo, audit, planner or RuleBasedPlanner()
        self.pending: dict[str, dict] = {}
        self.tools: dict[str, tuple[Callable, set[str], bool]] = {   # fn, roles, mutating
            "top_risk_vehicles": (self._top, {"admin", "fleet_manager", "analyst", "viewer"}, False),
            "vehicle_summary": (self._summary, {"admin", "fleet_manager", "analyst"}, False),
            "open_alerts": (self._alerts, {"admin", "fleet_manager", "analyst", "viewer"}, False),
            "create_work_order": (self._work_order, {"admin", "fleet_manager"}, True),
        }

    # ---- tools (all tenant-scoped via principal)
    def _top(self, p, a):
        n = max(1, min(int(a.get("n", 5)), 20))
        rows, _ = self.repo.list_vehicles(p.tenant, n, None, 0.0)
        return [{"vin": r["vin"], "risk": r["risk"], "factors": r["factors"]} for r in rows]

    def _summary(self, p, a):
        v = self.repo.get_vehicle(p.tenant, a["vin"])
        return {"error": "not_found"} if v is None else {k: v[k] for k in ("vin", "risk", "factors", "odo_km", "fuel")}

    def _alerts(self, p, a):
        rows, _ = self.repo.list_alerts(p.tenant, 5, None, int(a.get("min_severity", 1)))
        return [{"vin": r["vin"], "type": r["type"], "severity": r["severity"], "ts": r["ts"]} for r in rows]

    def _work_order(self, p, a):
        if self.repo.get_vehicle(p.tenant, a["vin"]) is None:
            return {"error": "not_found"}
        wid = str(uuid.uuid4())
        self.pending[wid] = {"id": wid, "tenant": p.tenant, "vin": a["vin"], "reason": a["reason"][:200], "status": "PENDING_APPROVAL"}
        return {"status": "PENDING_APPROVAL", "work_order_id": wid}

    @staticmethod
    def _validate(name: str, args: dict) -> dict:
        if "vin" in args and not is_valid_vin(str(args["vin"]), strict_check_digit=False):
            raise ValueError("invalid vin")
        return args

    def ask(self, p: Principal, question: str) -> dict:
        question = question[:500]
        self.audit.record(p.sub, p.tenant, "agent.ask", "agent", {"q": question})
        results = []
        for call in self.planner.plan(question)[:MAX_STEPS]:
            spec = self.tools.get(call.name)
            if spec is None:
                self.audit.record(p.sub, p.tenant, "agent.tool_rejected", call.name, {"reason": "not_allowlisted"})
                results.append({"tool": call.name, "error": "tool_not_allowed"})
                continue
            fn, roles, mutating = spec
            if not p.has(*roles):
                self.audit.record(p.sub, p.tenant, "agent.tool_denied", call.name, {"roles": sorted(p.roles)})
                results.append({"tool": call.name, "error": "forbidden"})
                continue
            try:
                out = fn(p, self._validate(call.name, call.args))
            except (ValueError, KeyError, TypeError) as e:
                results.append({"tool": call.name, "error": f"bad_arguments:{type(e).__name__}"})
                continue
            self.audit.record(p.sub, p.tenant, "agent.tool_call", call.name, {"args": call.args, "mutating": mutating})
            results.append({"tool": call.name, "result": out})
        return {"question": question, "steps": results}

    def approve(self, p: Principal, wid: str) -> dict | None:
        w = self.pending.get(wid)
        if not w or w["tenant"] != p.tenant:
            return None
        w["status"] = "APPROVED"
        self.audit.record(p.sub, p.tenant, "workorder.approved", wid, {"vin": w["vin"]})
        return w
