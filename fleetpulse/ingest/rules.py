"""Real-time detection rules (stateless functions over per-vehicle windows)."""
from __future__ import annotations
from ..core.dtc import parse_many, max_severity

OVERHEAT_C = 105.0
BATT_LOW_V = 11.9
SOC_LOW = 10.0


def evaluate(ev, win) -> list[tuple[str, int, str]]:
    """Returns [(alert_type, severity 1-5, detail)] for one event given its VehicleWindows."""
    out = []
    sev = max_severity(parse_many(ev.dtc)) if ev.dtc else 0
    if sev >= 4:
        out.append(("DTC_CRITICAL", 5 if sev == 5 else 4, ",".join(ev.dtc)))
    if win.coolant.mean is not None and len(win.coolant) >= 30 and win.coolant.mean > OVERHEAT_C:
        out.append(("OVERHEAT", 5, f"mean {win.coolant.mean:.1f}C over 3 min"))
    if win.batt.min is not None and len(win.batt) >= 60 and win.batt.max < BATT_LOW_V:
        out.append(("BATTERY_12V_LOW", 3, f"max {win.batt.max:.2f}V over 5 min"))
    if ev.soc_pct is not None and ev.soc_pct < SOC_LOW and ev.speed_kmh > 0:
        out.append(("EV_SOC_LOW", 3, f"{ev.soc_pct:.0f}%"))
    if ev.evt == "HARSH_BRAKE":
        out.append(("HARSH_BRAKE", 2, f"{ev.speed_kmh:.0f} km/h"))
    return out
