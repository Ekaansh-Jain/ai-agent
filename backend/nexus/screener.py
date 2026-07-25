"""Candidate_Screener — the cheap first stage of the broad-query funnel.

Before this module, a broad query like "flag high-risk customers" investigated exactly ONE
account (the highest in_degree) and returned one case. Investigating every account is not an
option: each full investigation costs several DuckDB round-trips against ~5M rows.

So: rank all accounts in memory (measured ~137 ms over 515k rows, zero database queries),
keep a bounded pool, and spend the expensive explainable stage only on the top of it.

Honest positioning: on AMLworld, `in_degree` alone is near-optimal BY CONSTRUCTION, because
the ground truth labels rings by fan-in topology (docs/plan.md, Phase 3d). This composite is
therefore NOT a precision claim. It is a recall funnel — its only job is to put real hubs
inside the investigation cap. in_degree keeps half the weight so the order stays close to
that near-optimal order; velocity and io_ratio give a small lift to accounts that consolidate
fast or retain nothing without having extreme degree.

Deterministic, feature-only, label-free. No LLM, no ground truth, no database.
"""

from __future__ import annotations

import pandas as pd

from .schemas import Candidate, CandidatePool, InvestigationSpec

# Percentile-rank weights. Sum to 1.0 so `rank` reads as a 0..1 score.
RANK_WEIGHTS: dict[str, float] = {
    "in_degree": 0.50,
    "in_count": 0.20,
    "in_sum": 0.15,
    "velocity": 0.10,
    "io_ratio": 0.05,
}

# An account with fewer than two payers cannot be a consolidation hub.
MIN_IN_COUNT = 1
MIN_IN_DEGREE = 2

_FEATURE_SNAPSHOT = ("in_degree", "in_count", "in_sum", "velocity", "io_ratio")


def rank(
    features: pd.DataFrame,
    spec: InvestigationSpec | None = None,
    max_candidates: int = 500,
) -> CandidatePool:
    """Rank accounts into a bounded candidate pool. Issues zero database queries."""
    signal = dict(RANK_WEIGHTS)

    if features is None or len(features) == 0:
        return CandidatePool(
            candidates=[], eligible=0, dropped=0, max_candidates=max_candidates,
            signal=signal, reason="no engineered account features available to rank",
        )

    eligible = features[
        (features["in_count"] >= MIN_IN_COUNT) & (features["in_degree"] >= MIN_IN_DEGREE)
    ]
    n_eligible = int(len(eligible))
    n_dropped = int(len(features) - n_eligible)

    if n_eligible == 0:
        return CandidatePool(
            candidates=[], eligible=0, dropped=n_dropped, max_candidates=max_candidates,
            signal=signal,
            reason=(
                f"no account met the screening floor "
                f"(in_count >= {MIN_IN_COUNT}, in_degree >= {MIN_IN_DEGREE})"
            ),
        )

    if max_candidates <= 0:
        return CandidatePool(
            candidates=[], eligible=n_eligible, dropped=n_dropped,
            max_candidates=max_candidates, signal=signal,
            reason="max_candidates is 0, so no candidate was screened",
        )

    # Percentile ranks make the signal scale-free, so no thresholds are needed.
    score = pd.Series(0.0, index=eligible.index)
    for column, weight in RANK_WEIGHTS.items():
        score = score + weight * eligible[column].rank(pct=True, method="average")

    # The feature index is itself named "node", so the tie-break column gets a distinct
    # name to keep pandas from reporting an ambiguous label.
    ordered = pd.DataFrame({"rank": score})
    ordered["_node_id"] = [str(i) for i in ordered.index]
    # Descending rank, ties broken by ascending node id -> fully deterministic.
    ordered = ordered.sort_values(["rank", "_node_id"], ascending=[False, True])
    top = ordered.head(max_candidates)

    candidates = [
        Candidate(
            node=str(node),
            rank=round(float(row_rank), 6),
            features={
                f: round(float(eligible.at[node, f]), 4)
                for f in _FEATURE_SNAPSHOT if f in eligible.columns
            },
        )
        for node, row_rank in zip(top.index, top["rank"])
    ]

    return CandidatePool(
        candidates=candidates,
        eligible=n_eligible,
        dropped=n_dropped,
        max_candidates=max_candidates,
        signal=signal,
        reason=(
            f"ranked {n_eligible:,} eligible accounts by fan-in composite, "
            f"kept the top {len(candidates):,}"
        ),
    )
