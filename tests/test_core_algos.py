import random
import numpy as np
import pytest

from fleetpulse.core.vin import is_valid_vin, make_vin, check_digit
from fleetpulse.core.dtc import parse_dtc, parse_many, max_severity
from fleetpulse.core.sketches import BloomFilter, RotatingBloom, CountMinSketch, TopK
from fleetpulse.core.geo import geohash_encode, haversine_km, mask_location
from fleetpulse.core.windows import SlidingWindow
from fleetpulse.core.trips import segment_states, segments, MOVING, STOPPED


# ---------------- VIN
def test_vin_known_valid():
    assert is_valid_vin("1HGCM82633A004352")                    # canonical example, check digit 3

def test_vin_rejects_ioq_length_and_bad_check_digit():
    assert not is_valid_vin("1HGCM82633A00435I")                # I not allowed
    assert not is_valid_vin("1HGCM82633A00435")                 # 16 chars
    assert not is_valid_vin("1HGCM82643A004352")                # wrong check digit
    assert is_valid_vin("1HGCM82643A004352", strict_check_digit=False)
    assert not is_valid_vin(None) and not is_valid_vin(12345678901234567)

def test_make_vin_roundtrip():
    for i in range(200):
        assert is_valid_vin(make_vin("MA3EJKD1", i))
    assert check_digit("1HGCM82633A004352") == "3"


# ---------------- DTC
@pytest.mark.parametrize("raw,ok", [("P0301", True), ("p0301", True), (" U0100 ", True), ("Z0301", False),
                                    ("P4301", False), ("P030", False), ("P03011", False), (None, False), ("", False)])
def test_dtc_parse(raw, ok):
    assert (parse_dtc(raw) is not None) == ok

def test_dtc_severity():
    d = parse_many(["P0301", "BAD", "P0217"])
    assert [x.code for x in d] == ["P0301", "P0217"] and max_severity(d) == 5
    assert max_severity([]) == 0


# ---------------- Bloom
def test_bloom_no_false_negatives_and_fp_bound():
    bf = BloomFilter(20_000, 0.01)
    keys = [f"v{i}:{i}".encode() for i in range(20_000)]
    for k in keys:
        bf.add(k)
    assert all(k in bf for k in keys)
    fp = sum(f"x{i}".encode() in bf for i in range(20_000)) / 20_000
    assert fp < 0.02                                             # configured 1%, allow slack

def test_bloom_add_returns_presence():
    bf = BloomFilter(100)
    assert bf.add(b"a") is False and bf.add(b"a") is True
    assert 0 < bf.est_fp_rate() < 1

def test_rotating_bloom_bounded_and_remembers_previous_generation():
    rb = RotatingBloom(100, 0.001)
    for i in range(150):
        rb.seen_or_add(str(i).encode())                          # rotates once at 100
    assert rb.seen_or_add(b"120") is True                         # in current gen
    assert rb.seen_or_add(b"5") is True                           # still in old gen
    for i in range(1000, 1250):
        rb.seen_or_add(str(i).encode())
    assert rb.seen_or_add(b"5") is False                          # aged out: memory is bounded


# ---------------- Count-Min / TopK
def test_cms_never_underestimates_and_error_bound():
    cms, truth, rnd = CountMinSketch(0.001, 0.01), {}, random.Random(1)
    items = [f"k{int(rnd.paretovariate(1.2))}" for _ in range(20_000)]
    for it in items:
        cms.add(it.encode()); truth[it] = truth.get(it, 0) + 1
    over = [cms.estimate(k.encode()) - c for k, c in truth.items()]
    assert min(over) >= 0
    assert np.mean([o <= 0.001 * len(items) * 1.0 for o in over]) > 0.98

def test_topk_finds_heavy_hitters():
    tk = TopK(3)
    for _ in range(500): tk.add("P0301")
    for _ in range(300): tk.add("P0420")
    for _ in range(200): tk.add("P0128")
    for i in range(400): tk.add(f"noise{i}")
    assert [k for k, _ in tk.top()] == ["P0301", "P0420", "P0128"]


# ---------------- Geo
def test_geohash_known_vector():
    assert geohash_encode(57.64911, 10.40744, 11) == "u4pruydqqvj"

def test_haversine_and_masking():
    assert abs(haversine_km(13.0827, 80.2707, 12.9716, 77.5946) - 290) < 10   # Chennai-Bengaluru ~290 km
    lat, lon = 13.082712, 80.270718
    mlat, mlon = mask_location(lat, lon, 5)
    assert 0 < haversine_km(lat, lon, mlat, mlon) < 5
    assert mask_location(lat, lon, 5) == mask_location(lat + 1e-4, lon + 1e-4, 5)   # same cell -> same output


# ---------------- Sliding window vs brute force
def test_sliding_window_matches_bruteforce():
    rnd, w, pts = random.Random(5), SlidingWindow(60), []
    t = 0.0
    for _ in range(3000):
        t += rnd.random() * 3
        v = rnd.uniform(0, 100)
        w.add(t, v); pts.append((t, v))
        win = [x for ts, x in pts if ts >= t - 60]
        assert len(w) == len(win)
        assert w.max == pytest.approx(max(win)) and w.min == pytest.approx(min(win))
        assert w.mean == pytest.approx(sum(win) / len(win), rel=1e-6, abs=1e-6)

def test_sliding_window_ignores_too_old():
    w = SlidingWindow(10)
    w.add(100, 1)
    assert w.add(50, 9) is False and w.max == 1


# ---------------- Trip segmentation (DP)
def test_trip_segmentation_is_robust_to_noise():
    rnd = random.Random(2)
    speeds = [rnd.uniform(0, 3) for _ in range(60)] + [rnd.uniform(30, 70) for _ in range(120)] \
        + [rnd.uniform(0, 3) for _ in range(60)]
    speeds[100] = 0.0; speeds[130] = 1.0                           # GPS dropouts mid-trip
    segs = segments(segment_states(speeds))
    assert [s[0] for s in segs] == [STOPPED, MOVING, STOPPED]
    assert segs[1][1] == 60 and segs[1][2] == 179

def test_trip_segmentation_edges():
    assert segment_states([]) == []
    assert segments(segment_states([0, 0, 0])) == [(STOPPED, 0, 2)]
