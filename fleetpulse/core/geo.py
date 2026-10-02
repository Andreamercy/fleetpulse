"""Geohash encoding, haversine distance, and location masking (privacy)."""
import math

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_BITS = (16, 8, 4, 2, 1)


def geohash_encode(lat: float, lon: float, precision: int = 7) -> str:
    """O(precision). precision 5 ~ 4.9 km cell, 7 ~ 150 m."""
    lat_r, lon_r = [-90.0, 90.0], [-180.0, 180.0]
    bit, ch, even, out = 0, 0, True, []
    while len(out) < precision:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            ch |= _BITS[bit]
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_B32[ch])
            bit, ch = 0, 0
    return "".join(out)


def geohash_decode_center(h: str) -> tuple[float, float]:
    lat_r, lon_r, even = [-90.0, 90.0], [-180.0, 180.0], True
    for c in h:
        v = _B32.index(c)
        for mask in _BITS:
            rng = lon_r if even else lat_r
            mid = (rng[0] + rng[1]) / 2
            if v & mask:
                rng[0] = mid
            else:
                rng[1] = mid
            even = not even
    return sum(lat_r) / 2, sum(lon_r) / 2


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def mask_location(lat: float, lon: float, precision: int = 5) -> tuple[float, float]:
    """Coarsen a coordinate to the centre of its geohash cell (~5 km) for roles that must
    not see exact location (data minimisation under DPDP / GDPR)."""
    la, lo = geohash_decode_center(geohash_encode(lat, lon, precision))
    return round(la, 5), round(lo, 5)
