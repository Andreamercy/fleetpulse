import json
import pytest
from fleetpulse.core.oem import AdapterRegistry, UnknownFormat
from fleetpulse.core.vin import make_vin
from fleetpulse.ingest.memory import MemSink, MemState, MemAlerts, MemDlq
from fleetpulse.ingest.processor import EventProcessor

VIN = make_vin("MA3EJKD1", 1)


def ev(seq, t, **kw):
    base = {"v": 1, "vin": VIN, "ts": f"2026-09-25T10:{t // 60:02d}:{t % 60:02d}.000Z", "seq": seq, "lat": 13.0, "lon": 80.2,
            "speed_kmh": 50.0, "odo_km": 1000.0, "coolant_c": 90.0, "batt_v": 12.6, "dtc": [], "oem": "canonical"}
    base.update(kw)
    return base


def make(batch=5000):
    s, st, a, d = MemSink(), MemState(), MemAlerts(), MemDlq()
    return EventProcessor(sink=s, state=st, alerts=a, dlq=d, batch_size=batch), s, st, a, d


# ---------------- OEM adapters
def test_adapter_alpha_converts_units():
    raw = {"vehicleIdentificationNumber": VIN, "timestamp": 1_790_000_000_000, "counter": 7,
           "position": {"latitude": 13.0, "longitude": 80.2}, "speedMph": 62.1371, "odometerMiles": 100.0,
           "coolantF": 194.0, "battery": {"stateOfCharge": 0.5}, "troubleCodes": ["p0301"]}
    e = AdapterRegistry().normalise("alpha", raw)
    assert e.speed_kmh == pytest.approx(100.0, rel=1e-3) and e.odo_km == pytest.approx(160.9, rel=1e-3)
    assert e.coolant_c == pytest.approx(90.0) and e.soc_pct == pytest.approx(50.0) and e.dtc == ["P0301"]

def test_adapter_beta_and_hot_registration():
    reg = AdapterRegistry()
    raw = {"vin": VIN, "time": "2026-09-25T10:00:00Z", "msgId": 1, "lat": 1, "lng": 2, "speed_ms": 10, "odo": 5}
    assert reg.normalise("beta", raw).speed_kmh == pytest.approx(36.0)
    with pytest.raises(UnknownFormat):
        reg.normalise("gamma", raw)
    reg.register("gamma", lambda r: {**r, "seq": r["msgId"], "speed_kmh": r["speed_ms"] * 3.6, "odo_km": r["odo"],
                                      "ts": r["time"], "lon": r["lng"]})
    assert reg.normalise("gamma", raw).oem == "gamma"          # onboarded without redeploying the pipeline

def test_adapter_missing_field_is_unknown_format():
    with pytest.raises(UnknownFormat):
        AdapterRegistry().normalise("alpha", {"foo": 1})


# ---------------- Processor
def test_valid_event_flows_to_sink_and_state():
    p, s, st, a, d = make()
    assert p.handle(json.dumps(ev(1, 0))) == "ok"
    assert p.flush() == 1 and len(s.rows) == 1 and VIN in st.latest and not d.items

def test_duplicates_are_dropped_idempotently():
    p, s, *_ = make()
    assert [p.handle(ev(1, 0)), p.handle(ev(1, 0)), p.handle(ev(2, 1))] == ["ok", "duplicate", "ok"]
    p.flush()
    assert len(s.rows) == 2 and p.metrics.duplicates == 1

def test_bloom_false_positive_does_not_drop_new_event():
    p, s, *_ = make()
    class AlwaysSeen:
        def seen_or_add(self, k): return True                     # worst case: Bloom claims everything is a duplicate
    p.bloom = AlwaysSeen()
    assert p.handle(ev(1, 0)) == "ok" and p.handle(ev(2, 1)) == "ok" and p.handle(ev(2, 1)) == "duplicate"

def test_out_of_order_within_lateness_updates_state_but_beyond_goes_to_history_only():
    p, s, st, a, d = make()
    p.handle(ev(10, 120)); p.handle(ev(9, 100))                    # 20 s late -> within 60 s
    assert p.metrics.late == 0
    assert p.handle(ev(1, 0)) == "late"                           # 120 s late -> beyond allowed lateness
    p.flush()
    assert len(s.rows) == 3 and sum(r["late"] for r in s.rows) == 1
    assert st.latest[VIN]["seq"] == 10                            # live state not rewound by late data

def test_invalid_goes_to_dead_letter_not_dropped():
    p, s, st, a, d = make()
    assert p.handle(ev(1, 0, vin=VIN[:-1] + "I")) == "invalid"
    assert p.handle(ev(2, 1, dtc=["ZZZZZ"])) == "invalid"
    assert p.handle(b"{not json") == "invalid"
    assert p.handle(ev(3, 2), oem="unknown-oem") == "invalid"
    assert len(d.items) == 4 and p.metrics.invalid == 4 and not s.rows

def test_critical_dtc_alert_with_cooldown():
    p, s, st, a, d = make()
    p.handle(ev(1, 0, dtc=["P0217"], evt="FAULT"))
    p.handle(ev(2, 10, dtc=["P0217"], evt="FAULT"))                # within 300 s cooldown -> no 2nd alert
    p.handle(ev(3, 400 % 60 + 360, dtc=["P0217"], evt="FAULT"))    # t=366 s: after cooldown
    types = [x["type"] for x in a.items]
    assert types.count("DTC_CRITICAL") == 2 and a.items[0]["severity"] == 5

def test_sustained_overheat_detected_but_single_spike_not():
    p, s, st, a, d = make()
    p.handle(ev(1, 0, coolant_c=130.0))                           # one spike
    assert not [x for x in a.items if x["type"] == "OVERHEAT"]
    p2, _, _, a2, _ = make()
    for i in range(60):
        p2.handle(ev(i, i, coolant_c=112.0))
    assert [x for x in a2.items if x["type"] == "OVERHEAT"]

def test_battery_low_ev_soc_and_harsh_brake_alerts():
    p, s, st, a, d = make()
    for i in range(70):
        p.handle(ev(i, i, batt_v=11.5))
    p.handle(ev(100, 100, soc_pct=5.0, coolant_c=None, speed_kmh=40))
    p.handle(ev(101, 101, evt="HARSH_BRAKE"))
    assert {"BATTERY_12V_LOW", "EV_SOC_LOW", "HARSH_BRAKE"} <= {x["type"] for x in a.items}

def test_batching_flushes_at_batch_size_and_failure_propagates():
    p, s, *_ = make(batch=3)
    for i in range(7):
        p.handle(ev(i, i))
    assert len(s.rows) == 6                                        # two auto-flushes
    class Boom:
        def write_batch(self, rows): raise RuntimeError("clickhouse down")
    p.sink = Boom()
    p.handle(ev(50, 50))
    with pytest.raises(RuntimeError):                              # caller must not commit offsets
        p.flush()
    assert len(p._buf) == 2                                        # nothing lost: buffer retained for retry (1 leftover + 1 new)
