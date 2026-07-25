"""Baselines NEXUS is measured against, using held-out ground-truth labels.

- Baseline 1 (rules): flag accounts with in_degree >= k (topological fan-in threshold).
- Baseline 2 (generic AI): flag accounts whose IsolationForest anomaly score >= tau.

Both are evaluated on the SAME node set as NEXUS so comparisons are apples-to-apples.
"""

from __future__ import annotations

import pandas as pd

from ..anomaly import AnomalyModel
from .metrics import Confusion, confusion


def rules_baseline(nodes: list[str], indeg: dict[str, float], labels: list[bool],
                   k: int) -> Confusion:
    preds = [indeg.get(n, 0) >= k for n in nodes]
    return confusion(preds, labels)


def generic_ai_baseline(nodes: list[str], model: AnomalyModel, profiles: pd.DataFrame,
                        labels: list[bool], tau: float) -> Confusion:
    scores = model.score_frame(profiles.loc[[n for n in nodes if n in profiles.index]])
    preds = [float(scores.get(n, 0.0)) >= tau for n in nodes]
    return confusion(preds, labels)
