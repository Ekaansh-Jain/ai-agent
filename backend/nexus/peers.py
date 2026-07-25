"""Behavioral peer grouping + robust deviation lookup.

No demographics exist, so peers are derived by clustering behavioral profiles
(MiniBatchKMeans on log-scaled, standardized features). Deviation uses robust stats
(median + MAD) so a launderer inside a cluster can't inflate the spread and hide itself.

Guards against blow-up:
  - MIN_CLUSTER_SIZE: below this, fall back to the global population.
  - zero-spread (MAD ~ 0): fall back to global; if still zero, return neutral 0-strength.
  - strength = clamp(|z| / 5, 0, 1): a real deviation saturates instead of exploding.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import StandardScaler

from .profiles import CLUSTER_FEATURES

MIN_CLUSTER_SIZE = 30
DEFAULT_K = 10
EPS = 1e-9
_MAD_SCALE = 1.4826  # makes MAD a consistent estimator of std for normal data

# Features whose upward deviation flags a suspicious collector.
SCORE_FEATURES = ["in_degree", "in_sum", "in_count"]


@dataclass
class Deviation:
    z: float
    direction: str          # high / low
    feature: str | None
    peer_set: str           # "cluster N" / "global" / "global(zero-spread)" / "none"
    peer_count: int


def _mad(frame: pd.DataFrame) -> pd.Series:
    return (frame - frame.median()).abs().median()


class PeerModel:
    def __init__(self, profiles: pd.DataFrame, k: int = DEFAULT_K, seed: int = 0):
        self.profiles = profiles
        x = np.log1p(profiles[CLUSTER_FEATURES].to_numpy(dtype=float))
        self._scaler = StandardScaler().fit(x)
        km = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=3)
        labels = km.fit_predict(self._scaler.transform(x))

        # Log-scaled scoring features + cluster label, for robust z lookups.
        self._logf = pd.DataFrame(
            np.log1p(profiles[SCORE_FEATURES].to_numpy(dtype=float)),
            index=profiles.index, columns=SCORE_FEATURES,
        )
        self._logf["_cluster"] = labels
        self._cluster_of = pd.Series(labels, index=profiles.index)
        self._size = pd.Series(labels).value_counts().to_dict()

        grp = self._logf.groupby("_cluster")[SCORE_FEATURES]
        self._cl_med = grp.median()
        self._cl_mad = grp.apply(_mad)
        self._g_med = self._logf[SCORE_FEATURES].median()
        self._g_mad = _mad(self._logf[SCORE_FEATURES])

    def deviation(self, node: str) -> Deviation:
        if node not in self._cluster_of.index:
            return Deviation(0.0, "low", None, "none", 0)

        cluster = int(self._cluster_of.loc[node])
        use_global = self._size.get(cluster, 0) < MIN_CLUSTER_SIZE

        best = Deviation(0.0, "low", None, "none", 0)
        for feat in SCORE_FEATURES:
            x = float(self._logf.at[node, feat])
            if use_global:
                med, mad, peer, n = self._g_med[feat], self._g_mad[feat], "global", len(self.profiles)
            else:
                med, mad = self._cl_med.at[cluster, feat], self._cl_mad.at[cluster, feat]
                peer, n = f"cluster {cluster}", int(self._size[cluster])

            denom = _MAD_SCALE * mad
            if denom < EPS:  # zero-spread -> global fallback
                med, mad = self._g_med[feat], self._g_mad[feat]
                denom = _MAD_SCALE * mad
                peer, n = "global(zero-spread)", len(self.profiles)
                if denom < EPS:
                    continue  # truly degenerate: skip this feature

            z = (x - med) / denom
            if abs(z) > abs(best.z):
                best = Deviation(z, "high" if z > 0 else "low", feat, peer, n)
        return best
