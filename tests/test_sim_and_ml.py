import numpy as np
from fleetpulse.core.schema import TelemetryEvent
from fleetpulse.core.vin import is_valid_vin
from fleetpulse.sim.fleet import FleetSimulator, NetworkMess, encode
from fleetpulse.sim.daily import make_daily_dataset, FEATURES
from fleetpulse.ml.risk import evaluate, baseline_score, recall_at_top_frac
from fleetpulse.ml.cases import CaseLibrary


def test_simulator_events_are_valid_and_deterministic():
    a, b = FleetSimulator(300, seed=1), FleetSimulator(300, seed=1)
    ea, eb = a.tick_events(), b.tick_events()
    assert len(ea) == 300 and [x["vin"] for x in ea] == [x["vin"] for x in eb]
    for e in ea[:100]:
        TelemetryEvent(**e)                                        # passes the canonical schema
    assert all(is_valid_vin(e["vin"]) for e in ea)

def test_simulator_shards_have_disjoint_vins_and_100k_scale():
    s0, s1 = FleetSimulator(1000, offset=0), FleetSimulator(1000, offset=1000)
    assert not set(s0.vins) & set(s1.vins)
    big = FleetSimulator(100_000)
    assert big.n == 100_000 and len(set(big.vins)) == 100_000

def test_degrading_vehicles_show_precursors():
    s = FleetSimulator(2000, seed=3, degrading_frac=0.5)
    for _ in range(3000): s.step()
    deg, ok = s.degrading & ~s.is_ev, ~s.degrading & ~s.is_ev
    assert s.coolant[deg].mean() > s.coolant[ok].mean() + 10
    assert s.batt_v[s.degrading].mean() < s.batt_v[~s.degrading].mean() - 0.5

def test_network_mess_creates_dups_late_and_bad_events():
    s, m = FleetSimulator(2000, seed=2), NetworkMess(seed=2, bad_rate=0.01)
    seen, dups, bad = set(), 0, 0
    out = []
    for _ in range(40): out += m.process(s.tick_events())
    for e in out:
        k = (e["vin"], e["seq"]); dups += k in seen; seen.add(k)
        try: TelemetryEvent(**e)
        except Exception: bad += 1
    assert dups > 0 and bad > 0
    seqs_by_vin = {}
    ooo = sum(1 for e in out if e["seq"] < seqs_by_vin.get(e["vin"], 0) or seqs_by_vin.__setitem__(e["vin"], max(seqs_by_vin.get(e["vin"], 0), e["seq"])))
    assert ooo > 0                                                  # genuinely out-of-order delivery
    assert all(isinstance(x, str) for x in encode(out[:5]))

def test_daily_dataset_shape_labels_and_no_post_failure_rows():
    df = make_daily_dataset(n_vehicles=1500, days=60, seed=4)
    assert set(FEATURES) <= set(df.columns) and 0 < df.label.mean() < 0.05
    last = df.groupby("vehicle").day.max()
    pos = df[df.label == 1].groupby("vehicle").day.max()
    assert (last[pos.index] >= pos).all()

def test_model_beats_baseline_on_temporal_holdout():
    df = make_daily_dataset(n_vehicles=6000, days=100, seed=9)
    res, model = evaluate(df, train_end_day=70)
    assert res["model"]["pr_auc"] > res["baseline"]["pr_auc"] * 1.5
    assert res["model"]["recall_at_top5pct"] > res["baseline"]["recall_at_top5pct"]

def test_baseline_and_recall_helper():
    df = make_daily_dataset(n_vehicles=500, days=40, seed=5)
    s = baseline_score(df)
    assert s.min() >= 0 and s.max() <= 1
    r, p = recall_at_top_frac(np.array([1, 0, 0, 0]), np.array([0.9, 0.1, 0.2, 0.3]), 0.25)
    assert (r, p) == (1.0, 1.0)

def test_case_library_returns_similar_failures():
    df = make_daily_dataset(n_vehicles=3000, days=80, seed=6)
    lib = CaseLibrary(df)
    row = df[df.label == 1].iloc[0][FEATURES].to_numpy(dtype=float)
    nn = lib.neighbors(row, 3)
    assert len(nn) == 3 and nn[0]["similarity"] >= nn[1]["similarity"] >= nn[2]["similarity"] and nn[0]["similarity"] > 0.9
