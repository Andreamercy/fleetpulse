"""Fault-case library: nearest-neighbour retrieval over historical failure cases.

Stored in pgvector in production (`fault_case.embedding vector(13)` + HNSW cosine index); this
numpy implementation has identical semantics and is used for tests and the in-memory demo.
The 'embedding' is the standardised model feature vector (not an LLM embedding) -- deliberate:
distance then means 'similar wear/precursor pattern', which is what a technician wants to see.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from ..sim.daily import FEATURES


class CaseLibrary:
    def __init__(self, df: pd.DataFrame, cases_per_vehicle: int = 1):
        pos = df[df.label == 1].sort_values(["vehicle", "day"]).groupby("vehicle").head(cases_per_vehicle)
        X = pos[FEATURES].to_numpy(dtype=float)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        self.X = self._norm(X)
        self.meta = pos[["vehicle", "day"]].to_numpy()

    def _norm(self, X):
        Z = (X - self.mu) / self.sd
        return Z / (np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9)

    def neighbors(self, x: np.ndarray, k: int = 3):
        """Cosine top-k. O(n*d); in pgvector an HNSW index makes this ~O(log n)."""
        q = self._norm(np.asarray(x, dtype=float).reshape(1, -1))[0]
        sims = self.X @ q
        idx = np.argsort(-sims)[:k]
        return [{"case_vehicle": int(self.meta[i][0]), "case_day": int(self.meta[i][1]),
                 "similarity": round(float(sims[i]), 3)} for i in idx]
