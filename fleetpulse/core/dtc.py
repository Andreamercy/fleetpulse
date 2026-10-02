"""OBD-II Diagnostic Trouble Code parsing."""
import re
from dataclasses import dataclass

_DTC_RE = re.compile(r"^([PCBU])([0-3])([0-9A-F])([0-9A-F]{2})$")
_SYSTEM = {"P": "powertrain", "C": "chassis", "B": "body", "U": "network"}

# Severity 1 (info) .. 5 (stop-driving). Small illustrative catalogue.
_SEVERITY = {
    "P0300": 4, "P0301": 4, "P0302": 4, "P0303": 4, "P0304": 4,   # misfires
    "P0217": 5,                                                    # engine overtemp
    "P0128": 2, "P0420": 2, "P0171": 2, "P0562": 3,                # coolant/cat/fuel/volt low
    "P0A80": 5, "P0A0F": 4, "P0AFA": 4,                            # hybrid/EV battery
    "C0035": 3, "U0100": 3,
}


@dataclass(frozen=True)
class Dtc:
    code: str
    system: str
    generic: bool
    severity: int


def parse_dtc(raw: str) -> Dtc | None:
    """Parse e.g. 'p0301' -> Dtc. Returns None if malformed. O(1)."""
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    m = _DTC_RE.match(code)
    if not m:
        return None
    sys_, kind, _, _ = m.groups()
    return Dtc(code, _SYSTEM[sys_], kind in "02", _SEVERITY.get(code, 2))


def parse_many(raws) -> list[Dtc]:
    out = []
    for r in raws or []:
        d = parse_dtc(r)
        if d:
            out.append(d)
    return out


def max_severity(dtcs) -> int:
    return max((d.severity for d in dtcs), default=0)
