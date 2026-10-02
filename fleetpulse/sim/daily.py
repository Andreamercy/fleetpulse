"""Vehicle-day feature generator used to train/evaluate the breakdown-risk model.

Ground truth is generated *causally*: a latent wear process drives (noisy) observable signals and,
separately, a failure day. Signals are deliberately imperfect: ~35% of failures show no early DTC,
and ~8% of healthy vehicles show transient overheating or benign DTCs (nuisance alarms), so a
single-threshold rule cannot be perfect. This is SYNTHETIC: the numbers it yields validate the
pipeline and the evaluation method, not real-world accuracy.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

FEATURES = ["coolant_mean", "coolant_max", "batt_v_min", "dtc_count_7d", "max_dtc_sev_7d",
            "harsh_per_100km", "km_since_service", "age_years", "idle_ratio", "km_day",
            "coolant_trend_7d", "batt_trend_7d", "is_ev"]


def make_daily_dataset(n_vehicles: int = 20_000, days: int = 120, horizon: int = 7,
                       fail_frac: float = 0.06, seed: int = 11) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    n = n_vehicles
    age = r.gamma(3.0, 1.2, n).clip(0.2, 12)
    is_ev = r.random(n) < 0.3
    base_wear = (age / 12) * 0.3 + r.beta(2, 6, n) * 0.3
    last_service = r.integers(-200, 0, n).astype(float)           # day offsets
    km_day0 = r.gamma(6, 12, n)
    fails = r.random(n) < (fail_frac * (0.5 + 3 * base_wear))     # older / worn fail more
    fail_day = np.where(fails, r.integers(horizon + 3, days, n), 10**6)
    lead = r.integers(4, 21, n)                                    # days of visible degradation
    silent = r.random(n) < 0.35                                    # no DTC precursor
    nuisance = r.random(n) < 0.08

    rows = []
    d = np.arange(days)
    for day in d:
        to_fail = fail_day - day
        ramp = np.clip(1 - to_fail / lead, 0, 1) * (to_fail > 0)    # 0 .. 1 in the lead window
        ramp = np.where(to_fail <= 0, 0, ramp)                       # failed vehicles leave the pool
        neu = nuisance & (r.random(n) < 0.15)
        cool_base = 88 + 6 * base_wear
        coolant_mean = np.where(is_ev, np.nan, cool_base + 14 * ramp + 5 * neu + r.normal(0, 1.5, n))
        coolant_max = coolant_mean + np.abs(r.normal(4, 1.5, n)) + 8 * ramp * (r.random(n) < 0.6)
        batt = 12.55 - 0.4 * base_wear - 0.9 * ramp * (~silent | (r.random(n) < 0.3)) + r.normal(0, 0.08, n)
        dtc_p = 0.01 + 0.35 * ramp * (~silent) + 0.04 * neu
        dtc_cnt = r.poisson(dtc_p * 7)
        sev = np.where(dtc_cnt > 0, np.where(r.random(n) < (0.2 + 0.7 * ramp), r.integers(3, 6, n), r.integers(1, 3, n)), 0)
        kmd = km_day0 * r.uniform(0.6, 1.3, n)
        harsh = r.gamma(2, 0.6, n) * (1 + 0.5 * ramp)
        since = (day - last_service) * km_day0 * 0.9
        idle = np.clip(r.normal(0.18, 0.05, n) + 0.08 * ramp, 0, 1)
        rows.append(pd.DataFrame({
            "vehicle": np.arange(n), "day": day, "coolant_mean": coolant_mean, "coolant_max": coolant_max,
            "batt_v_min": batt, "dtc_count_7d": dtc_cnt, "max_dtc_sev_7d": sev, "harsh_per_100km": harsh,
            "km_since_service": since, "age_years": age, "idle_ratio": idle, "km_day": kmd,
            "is_ev": is_ev.astype(int),
            "alive": to_fail > 0,
            "label": ((to_fail > 0) & (to_fail <= horizon)).astype(int),
        }))
    df = pd.concat(rows, ignore_index=True)
    df = df[df.alive].drop(columns="alive")
    df = df.sort_values(["vehicle", "day"]).reset_index(drop=True)
    g = df.groupby("vehicle")
    df["coolant_trend_7d"] = (df["coolant_mean"] - g["coolant_mean"].shift(7)).fillna(0)
    df["batt_trend_7d"] = (df["batt_v_min"] - g["batt_v_min"].shift(7)).fillna(0)
    df["coolant_mean"] = df["coolant_mean"].fillna(-1)       # EV sentinel (is_ev disambiguates)
    df["coolant_max"] = df["coolant_max"].fillna(-1)
    return df
