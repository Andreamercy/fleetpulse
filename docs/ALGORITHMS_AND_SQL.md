# Algorithms and SQL write-up

## Algorithms (all in `fleetpulse/core`, unit-tested)
| Algorithm | Where used | Time | Space | Notes / test |
|---|---|---|---|---|
| VIN validation (regex + ISO 3779 check digit) | schema validation | O(1) | O(1) | known-valid VIN, I/O/Q, length, bad digit; check digit optional (not mandatory outside North America) |
| DTC parsing (regex `^[PCBU][0-3][0-9A-F]{3}$`) | schema, rules | O(1) | O(1) | 9 parametrised cases |
| Bloom filter + rotating generations | duplicate fast-path | O(k) per op, k~10 | O(m) bits, bounded by rotation | no false negatives; FP < 2% at 1% config; a Bloom *hit* is confirmed against an exact per-vehicle recent-seq set, so a false positive can never drop real data (test with an always-"seen" Bloom) |
| Count-Min Sketch + Top-K | heavy-hitter DTCs (fleet-wide) | O(d) / O(d+K) | O(w*d) | never underestimates; error bound checked on a Pareto stream |
| Sliding window with monotonic deques | per-vehicle coolant/battery windows | amortised O(1) add, O(1) mean/max/min | O(samples in window) | verified against brute force on 3,000 random points |
| 2-state Viterbi DP | trip/stop segmentation of noisy speed | O(n) | O(n) | GPS dropouts mid-trip do not split the trip (a threshold would) |
| Geohash + haversine + masking | location privacy, geo-joins | O(precision) | O(1) | known test vector `u4pruydqqvj`; masked point <5 km from truth |
| Token bucket | rate limiting | O(1) | O(principals) | refill + isolation tested |
| Hash-chained audit | tamper evidence | O(1) append, O(n) verify | O(n) | tamper detected |
| Cosine k-NN (pgvector HNSW in prod) | similar past failures | O(n d) exact; ~O(log n) with HNSW | O(n d) | Q5: 15.3 ms seq-scan -> 0.08 ms HNSW (approximate) |
Not implemented (candidates): Dijkstra/A* nearest-charger routing, DP charging schedule - outside the chosen problem space (predictive maintenance).

## ML (fleetpulse/ml, sim/daily.py)
Task: P(breakdown within 7 days) per vehicle-day. Baseline = rules a fleet manager uses today (severe DTC, coolant > 105 C, service overdue). Model = HistGradientBoosting on 13 features. Temporal split (train days < 85 with a 7-day gap so labels cannot leak). Data is **synthetic with causal ground truth** (35% of failures have no DTC precursor; 8% of healthy vehicles raise nuisance alarms), so results validate the method and pipeline, **not real-world accuracy**.

SQL results: see `sql_results.json` and `query_plans.md`. Reproduce: `python db/explain/run_explain.py`.
