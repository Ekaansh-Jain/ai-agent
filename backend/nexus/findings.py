"""Findings — turn investigated cases into a ranked, per-item triage queue.

The brief asks for "top suspicious transactions or customers" with "risk level for each
flagged item". Before this module a query returned exactly one case, so there was no list to
rank. Ranking rules here are fully deterministic: risk descending compared at two decimals,
ties broken by ascending account id.

Verdict handling differs by query shape, on purpose:
  * broad sweep  -> benign and indeterminate candidates are dropped; a triage queue should
                    not be padded with accounts the duel already cleared.
  * named entity -> every named account comes back regardless of verdict, because "is 4521
                    suspicious?" deserves an answer even when the answer is "no".
"""

from __future__ import annotations

from .duel import HypothesisScore
from .hypotheses import load_hypotheses
from .schemas import Case, Finding, InvestigationSpec

EXCLUDED_KINDS = ("benign", "indeterminate")


def _label(typology: str, hyp_id: str) -> str:
    try:
        for h in load_hypotheses(typology):
            if h.id == hyp_id:
                return h.label
    except KeyError:
        pass
    return hyp_id


def to_finding(
    case: Case,
    spec: InvestigationSpec,
    explanation: str,
    rank: int,
    explanation_source: str = "template",
    validated: bool = True,
    unsupported: list[str] | None = None,
) -> Finding:
    return Finding(
        rank=rank,
        node=case.seed,
        risk=round(case.risk, 2),
        tier=case.tier,                      # type: ignore[arg-type]
        escalation=case.escalation,          # type: ignore[arg-type]
        winning_kind=case.winning_kind,
        winning_hypothesis=case.winning_hypothesis,
        hypothesis_label=_label(spec.typology, case.winning_hypothesis),
        confidence=case.confidence,
        explanation=explanation,
        explanation_source=explanation_source,  # type: ignore[arg-type]
        validated=validated,
        unsupported=list(unsupported or []),
        evidence=list(case.evidence),
        case=case,
    )


def should_include(case: Case, broad: bool) -> bool:
    """Broad sweeps keep only accounts the duel found suspicious AND scored above zero.

    The risk floor matters. A hypothesis can win on a family that its typology's risk profile
    does not weight — e.g. structuring scores only `typology_rule`, so an account with no
    near-threshold deposits can come back `suspicious` at risk 0.0. Those are not findings;
    listing them would pad the queue with rows carrying no weighted evidence at all. They are
    counted as cleared, so the totals still reconcile.

    A query that names an account is exempt: "is 4521 suspicious?" gets an answer either way.
    """
    if not broad:
        return True
    if case.winning_kind in EXCLUDED_KINDS:
        return False
    return round(case.risk, 2) > 0.0


def sort_key(finding: Finding) -> tuple[float, str]:
    # Negative risk for descending, node ascending as the deterministic tie-break.
    return (-round(finding.risk, 2), finding.node)


def rerank(findings: list[Finding]) -> list[Finding]:
    """Sort and (re)assign 1-based ranks after sorting."""
    ordered = sorted(findings, key=sort_key)
    return [f.model_copy(update={"rank": i + 1}) for i, f in enumerate(ordered)]


def scoreboard_of(scores: list[HypothesisScore]) -> list[HypothesisScore]:
    """Winner-first, deterministic ordering for the hypothesis scoreboard payload."""
    return sorted(scores, key=lambda s: (-round(s.normalized, 4), s.id))
