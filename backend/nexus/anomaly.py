"""IsolationForest anomaly model — the 'generic AI' detector.

Unsupervised: trained on behavioral account profiles only (NO labels — no leakage). Produces
a per-account anomaly score in [0,1]. Used two ways:
  - as the generic-AI baseline in eval (Baseline 2),
  - as a neutral, proof-carrying `anomaly` evidence family in the live ledger (kept OUT of
    the risk weights and hypothesis fingerprints, so the locked anchors are unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from .config import REPO_ROOT
from .profiles import CLUSTER_FEATURES

MODEL_PATH = REPO_ROOT / "models" / "isoforest.joblib"
FEATURES = CLUSTER_FEATURES


@dataclass
class AnomalyModel:
    iso: IsolationForest
    scaler: StandardScaler
    p_low: float
    p_high: float

    def _raw(self, x: np.ndarray) -> np.ndarray:
        # Higher raw = more anomalous (decision_function is higher for normal points).
        return -self.iso.decision_function(self.scaler.transform(x))

    def score_row(self, feat: pd.Series) -> float:
        x = np.log1p(feat[FEATURES].to_numpy(dtype=float)).reshape(1, -1)
        raw = float(self._raw(x)[0])
        span = self.p_high - self.p_low
        return float(np.clip((raw - self.p_low) / span, 0.0, 1.0)) if span > 0 else 0.0

    def score_frame(self, profiles: pd.DataFrame) -> pd.Series:
        x = np.log1p(profiles[FEATURES].to_numpy(dtype=float))
        raw = self._raw(x)
        span = self.p_high - self.p_low
        norm = np.clip((raw - self.p_low) / span, 0.0, 1.0) if span > 0 else raw * 0.0
        return pd.Series(norm, index=profiles.index)


def train(profiles: pd.DataFrame, seed: int = 0, contamination: float = 0.01) -> AnomalyModel:
    x = np.log1p(profiles[FEATURES].to_numpy(dtype=float))
    scaler = StandardScaler().fit(x)
    iso = IsolationForest(
        n_estimators=100, contamination=contamination, random_state=seed, n_jobs=-1
    ).fit(scaler.transform(x))
    raw = -iso.decision_function(scaler.transform(x))
    p_low, p_high = float(np.percentile(raw, 1)), float(np.percentile(raw, 99))
    return AnomalyModel(iso=iso, scaler=scaler, p_low=p_low, p_high=p_high)


def save(model: AnomalyModel, path: Path = MODEL_PATH) -> None:
    import joblib
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load(path: Path = MODEL_PATH) -> AnomalyModel | None:
    """Load the persisted model, or None if it hasn't been trained yet."""
    if not Path(path).is_file():
        return None
    try:
        import joblib
        return joblib.load(path)
    except Exception:
        return None
