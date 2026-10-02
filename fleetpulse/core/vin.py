"""VIN validation (ISO 3779 / FMVSS 115 check digit).

Complexity: O(1) -- fixed 17 characters.
"""
import re

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # no I, O, Q
_TRANSLIT = {str(d): d for d in range(10)}
_TRANSLIT.update(dict(zip("ABCDEFGH", range(1, 9))))
_TRANSLIT.update(dict(zip("JKLMN", range(1, 6))))
_TRANSLIT["P"] = 7
_TRANSLIT["R"] = 9
_TRANSLIT.update(dict(zip("STUVWXYZ", range(2, 10))))
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def check_digit(vin: str) -> str:
    total = sum(_TRANSLIT[c] * w for c, w in zip(vin, _WEIGHTS))
    r = total % 11
    return "X" if r == 10 else str(r)


def is_valid_vin(vin: str, strict_check_digit: bool = True) -> bool:
    """Structure check always; check digit only when strict (mandatory for North America,
    not for all markets, so Indian-market VINs are validated with strict=False)."""
    if not isinstance(vin, str):
        return False
    vin = vin.upper()
    if not _VIN_RE.match(vin):
        return False
    return (not strict_check_digit) or vin[8] == check_digit(vin)


def make_vin(prefix8: str, serial: int, year_code: str = "N", plant: str = "A") -> str:
    """Build a VIN with a correct check digit (used by the simulator)."""
    body = prefix8 + "0" + year_code + plant + f"{serial:06d}"
    if len(body) != 17:
        raise ValueError("prefix8 must be 8 chars, serial <= 6 digits")
    return body[:8] + check_digit(body) + body[9:]
