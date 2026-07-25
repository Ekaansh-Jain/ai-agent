"""Risk Engine — severity of the winning suspicious story, as a transparent sum.

risk = Σ (family_weight × family_strength) over independent families, ×100  -> 0..100.
Only suspicious-direction ("high") evidence contributes, so when a benign theory wins
(flow_through low, peer low, ...) there is little to sum and risk stays low — the duel
gates the risk.

Because risk is additive, counterfactuals ("what if we drop this family?") are just the
same sum with a family zeroed out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import EvidenceRecord

# Global family weights (sum to 1.0 so the max risk is 100). Distinct from the
# per-hypothesis importances used in the duel. This is the CONSOLIDATION profile and the
# default — it preserves the locked anchors: Phase 2 fixture = 86.65, and HI-Small node
# 0048309|811C599A0 = 53.18 (the "real ring" anchor; stable per rebuild only since the
# profile row order was pinned — before that it drew from [52.08, 52.86, 53.18]).
# All anchors and their provenance live in tests/cases/anchors.json.
RISK_WEIGHTS: dict[str, float] = {
    "peer_deviation": 0.20,
    "flow_through": 0.25,
    "network_convergence": 0.25,
    "temporal_coordination": 0.20,
    "typology_rule": 0.10,
}

# Per-typology risk profiles. `smurfing` == RISK_WEIGHTS (default), so consolidation numbers
# are unchanged. `structuring` weights the near-threshold rule + peer deviation, since
# structuring targets need not show fan-in / rapid pass-through.
RISK_PROFILES: dict[str, dict[str, float]] = {
    "smurfing": RISK_WEIGHTS,
    # Structuring severity rides ONLY on the near-threshold rule — deliberately NOT on
    # peer_deviation, so a high-fan-in merchant can't be flagged as structuring by volume.
    "structuring": {"typology_rule": 1.0},
}


def weights_for(typology: str) -> dict[str, float]:
    return RISK_PROFILES.get(typology, RISK_WEIGHTS)

# Tier thresholds -> escalation action.
_TIERS = [
    (70.0, "high", "report"),
    (40.0, "medium", "review"),
    (0.0, "low", "monitor"),
]


@dataclass
class RiskResult:
    score: float          # 0..100
    tier: str             # low / medium / high
    escalation: str       # monitor / review / report
    contributions: dict[str, float]  # family -> weighted contribution (0..100 scale)


def family_strengths(
    records: list[EvidenceRecord], weights: dict[str, float] | None = None
) -> dict[str, float]:
    """Max suspicious-direction (high) strength per weighted family."""
    weights = weights if weights is not None else RISK_WEIGHTS
    out: dict[str, float] = {}
    for r in records:
        if r.family in weights and r.direction == "high":
            out[r.family] = max(out.get(r.family, 0.0), r.strength)
    return out


def _tier_for(score: float) -> tuple[str, str]:
    for threshold, tier, action in _TIERS:
        if score >= threshold:
            return tier, action
    return "low", "monitor"


def risk_score(
    records: list[EvidenceRecord],
    typology: str = "smurfing",
    exclude: set[str] | None = None,
) -> RiskResult:
    """Compute risk under a typology profile. `exclude` zeroes families (counterfactuals)."""
    exclude = exclude or set()
    weights = weights_for(typology)
    strengths = family_strengths(records, weights)
    contributions: dict[str, float] = {}
    total = 0.0
    for family, weight in weights.items():
        if family in exclude:
            continue
        s = strengths.get(family, 0.0)
        contrib = weight * s * 100.0
        if contrib:
            contributions[family] = round(contrib, 2)
        total += contrib
    score = round(total, 2)
    tier, action = _tier_for(score)
    return RiskResult(score=score, tier=tier, escalation=action, contributions=contributions)


def counterfactuals(
    records: list[EvidenceRecord], typology: str = "smurfing"
) -> list[tuple[str, float]]:
    """Load-bearing analysis: risk with each present family removed, then the two
    strongest removed together. Returns (label, score) pairs, full first.
    """
    weights = weights_for(typology)
    present = [f for f in weights if f in family_strengths(records, weights)]
    out: list[tuple[str, float]] = [("full", risk_score(records, typology).score)]
    for family in present:
        out.append((f"-{family}", risk_score(records, typology, exclude={family}).score))
    contribs = risk_score(records, typology).contributions
    top2 = sorted(contribs, key=contribs.get, reverse=True)[:2]
    if len(top2) == 2:
        out.append(
            (f"-{top2[0]}-{top2[1]}", risk_score(records, typology, exclude=set(top2)).score)
        )
    return out
