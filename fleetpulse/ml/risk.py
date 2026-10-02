"""Breakdown-risk model (7-day horizon) + rule baseline + evaluation.

Why ML and not only rules: the baseline thresholds (DTC severity, overdue service, overheating)
either miss silent failures (no DTC precursor) or fire on nuisance alarms; a model combining
trends, battery sag and wear separates them better. We always report against the baseline.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from ..sim.daily import FEATURES, make_daily_dataset


def baseline_score(df: pd.DataFrame) -> np.ndarray:
    """What a fleet manager does today: act on severe DTCs, overheating, overdue service.
    Returned as a score in [0,1] (count of tripped rules / 3) so it is rankable."""
    r1 = (df.max_dtc_sev_7d >= 4).to_numpy()
    r2 = (df.coolant_max > 105).to_numpy()
    r3 = (df.km_since_service > 15000).to_numpy()
    return (r1.astype(float) * 1.0 + r2 * 1.0 + r3 * 0.5) / 2.5


def train(df: pd.DataFrame, train_end_day: int, horizon: int = 7, seed: int = 0):
    """Temporal split with a gap: rows whose label window reaches past train_end_day are excluded
    from training, so no label information leaks from the test period."""
    tr = df[df.day + horizon < train_end_day]
    model = HistGradientBoostingClassifier(max_depth=5, learning_rate=0.08, max_iter=200,
                                           class_weight="balanced", random_state=seed)
    model.fit(tr[FEATURES], tr.label)
    return model


def recall_at_top_frac(y, s, frac=0.05):
    k = max(1, int(len(y) * frac))
    idx = np.argsort(-s, kind="stable")[:k]
    return float(y[idx].sum() / max(1, y.sum())), float(y[idx].mean())


def evaluate(df: pd.DataFrame, train_end_day: int = 85, horizon: int = 7, seed: int = 0) -> dict:
    model = train(df, train_end_day, horizon, seed)
    te = df[df.day >= train_end_day]
    y = te.label.to_numpy()
    p_model = model.predict_proba(te[FEATURES])[:, 1]
    p_base = baseline_score(te)
    out = {"n_test_rows": int(len(te)), "positive_rate": float(y.mean())}
    for name, s in (("baseline", p_base), ("model", p_model)):
        rec, prec = recall_at_top_frac(y, s, 0.05)
        out[name] = {"pr_auc": float(average_precision_score(y, s)), "roc_auc": float(roc_auc_score(y, s)),
                     "recall_at_top5pct": rec, "precision_at_top5pct": prec}
    return out, model


if __name__ == "__main__":  # python -m fleetpulse.ml.risk
    import json
    import time
    t = time.time()
    df = make_daily_dataset()
    res, _ = evaluate(df)
    res["rows"] = int(len(df)); res["seconds"] = round(time.time() - t, 1)
    print(json.dumps(res, indent=2))
