"""The Hypothesis Duel — every evidence record re-scores all hypotheses.

The one rule (design §7.8):
  family in fingerprint & direction matches  -> score += importance * strength
  family in fingerprint & direction clashes  -> score -= importance * strength
  family absent from fingerprint             -> neutral (no change)

Subtracting on mismatch is the false-positive killer: contradicting evidence actively
demolishes the wrong theory instead of merely failing to support it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import EvidenceRecord, Hypothesis

# Normalized-score label bands (design §8.4).
_STRONG = 0.66
_MODERATE = 0.33
_WEAK = 0.10
_CONTRADICTED = -0.50


@dataclass
class HypothesisScore:
    id: str
    label: str
    kind: str
    raw: float
    normalized: float
    band: str
    matched: list[str] = field(default_factory=list)
    contradicted: list[str] = field(default_factory=list)

    @property
    def observed_count(self) -> int:
        """How many fingerprint families this hypothesis actually saw evidence for."""
        return len(self.matched) + len(self.contradicted)


def _band(normalized: float) -> str:
    if normalized >= _STRONG:
        return "strong"
    if normalized >= _MODERATE:
        return "moderate"
    if normalized >= _WEAK:
        return "weak"
    if normalized > -_WEAK:
        return "neutral"
    if normalized > _CONTRADICTED:
        return "weakened"
    return "contradicted"


def score_one(hyp: Hypothesis, records: list[EvidenceRecord]) -> HypothesisScore:
    raw = 0.0
    matched: list[str] = []
    contradicted: list[str] = []
    observed: set[str] = set()
    for r in records:
        fe = hyp.fingerprint.get(r.family)
        if fe is None:
            continue  # neutral
        observed.add(r.family)
        delta = fe.importance * r.strength
        if fe.expects == r.direction:
            raw += delta
            matched.append(r.family)
        else:
            raw -= delta
            contradicted.append(r.family)
    # Normalize by the importances of families actually OBSERVED, so extending a
    # fingerprint with new families never dilutes a score when they aren't tested.
    denom = sum(hyp.fingerprint[f].importance for f in observed)
    normalized = raw / denom if denom else 0.0
    return HypothesisScore(
        id=hyp.id, label=hyp.label, kind=hyp.kind, raw=round(raw, 4),
        normalized=round(normalized, 4), band=_band(normalized),
        matched=matched, contradicted=contradicted,
    )


def score_all(
    hypotheses: list[Hypothesis], records: list[EvidenceRecord]
) -> list[HypothesisScore]:
    """Score every hypothesis, returned sorted by normalized score (winner first)."""
    scores = [score_one(h, records) for h in hypotheses]
    scores.sort(key=lambda s: s.normalized, reverse=True)
    return scores


def winner(scores: list[HypothesisScore]) -> HypothesisScore:
    return scores[0]


def is_indeterminate(scores: list[HypothesisScore]) -> bool:
    """True when the evidence does not separate the theories.

    Without this guard, an all-zero scoreboard (no evidence, or nothing discriminating)
    would let the stable sort hand victory to whichever hypothesis is first in the library
    — labelling an unknown/inactive account 'suspicious' by accident.
    """
    if not scores:
        return True
    if all(s.observed_count == 0 for s in scores):
        return True
    # Nothing rose above the neutral band -> no theory is actually supported.
    return max(s.normalized for s in scores) < _WEAK


def verdict(scores: list[HypothesisScore]) -> tuple[HypothesisScore | None, str]:
    """Return (winning_score_or_None, kind) where kind is suspicious/benign/indeterminate."""
    if is_indeterminate(scores):
        return (scores[0] if scores else None), "indeterminate"
    top = scores[0]
    return top, top.kind


def confidence(scores: list[HypothesisScore]) -> str:
    """Confidence level (not a fake probability): from margin + corroboration.

    Uses the gap between the top two hypotheses and how many families corroborate the
    winner. Returns weak / moderate / strong / high.
    """
    if not scores:
        return "weak"
    top = scores[0]
    margin = top.normalized - (scores[1].normalized if len(scores) > 1 else -1.0)
    corroborating = len(top.matched)
    if margin >= 0.9 and corroborating >= 3:
        return "high"
    if margin >= 0.5 and corroborating >= 2:
        return "strong"
    if margin >= 0.25:
        return "moderate"
    return "weak"
