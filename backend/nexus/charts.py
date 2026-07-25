"""Chart_Builder — supporting charts, tables and metrics for reviewer confidence.

Hard rule, enforced by construction: **this module copies numbers, it never computes them.**
The only arithmetic permitted is `round(x, 2)`. Every value traces back to something the run
already produced (risk contributions, counterfactuals, duel scores, findings, evidence
records, the EDA profile), which is what lets the proof-carrying claim extend to the visuals.

No supervised model is loaded and no feature-attribution library is imported — the risk
engine is additive, so its own contributions ARE the explanation.
"""

from __future__ import annotations

from .duel import HypothesisScore
from .schemas import (
    AmountSummary, ChartSet, CounterfactualChart, CounterfactualEntry, DataProfileChart,
    EdaProfile, EvidenceTableChart, EvidenceTableRow, Finding, FindingsTableChart,
    FindingsTableRow, RiskContributionChart, RiskContributionEntry, ScoreboardChart,
    ScoreboardEntry,
)

MAX_EVIDENCE_ROWS = 50
MAX_TX_IDS_PER_ROW = 25


def _r2(x: float) -> float:
    return round(float(x), 2)


def build(
    findings: list[Finding],
    contributions: dict[str, float] | None = None,
    counterfactuals: list[tuple[str, float]] | None = None,
    scores: list[HypothesisScore] | None = None,
    eda: EdaProfile | None = None,
    max_investigations: int = 25,
) -> ChartSet:
    """Assemble the six payloads. Any unavailable source degrades that payload alone."""
    top = findings[0] if findings else None

    # --- risk contribution -------------------------------------------------
    contributions = contributions or {}
    if contributions:
        entries = [
            RiskContributionEntry(family=f, contribution=_r2(v))
            for f, v in sorted(contributions.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        risk_chart = RiskContributionChart(
            id="risk_contribution", title="Risk contribution by evidence family",
            entries=entries,
        )
    else:
        risk_chart = RiskContributionChart(
            id="risk_contribution", title="Risk contribution by evidence family",
            available=False,
            reason="no evidence family contributed to the score for this item",
        )

    # --- counterfactual ----------------------------------------------------
    if counterfactuals:
        cf_chart = CounterfactualChart(
            id="counterfactual", title="Risk with each family removed",
            entries=[
                CounterfactualEntry(label=label, score=_r2(score))
                for label, score in counterfactuals
            ],
        )
    else:
        cf_chart = CounterfactualChart(
            id="counterfactual", title="Risk with each family removed",
            available=False, reason="no contributing family to remove",
        )

    # --- hypothesis scoreboard ---------------------------------------------
    if scores:
        board = ScoreboardChart(
            id="hypothesis_scoreboard", title="Competing explanations",
            entries=[
                ScoreboardEntry(
                    id=s.id, label=s.label, kind=s.kind, raw=_r2(s.raw),
                    normalized=_r2(s.normalized), band=s.band,
                    matched=list(s.matched), contradicted=list(s.contradicted),
                )
                for s in sorted(scores, key=lambda s: (-round(s.normalized, 4), s.id))
            ],
        )
    else:
        board = ScoreboardChart(
            id="hypothesis_scoreboard", title="Competing explanations",
            available=False, reason="no hypothesis was scored for this run",
        )

    # --- findings table ----------------------------------------------------
    if findings:
        findings_chart = FindingsTableChart(
            id="findings_table", title="Flagged accounts",
            rows=[
                FindingsTableRow(
                    node=f.node, risk=_r2(f.risk), tier=f.tier, escalation=f.escalation,
                )
                for f in findings[:max_investigations]
            ],
        )
    else:
        findings_chart = FindingsTableChart(
            id="findings_table", title="Flagged accounts",
            available=False, reason="the query returned no findings",
        )

    # --- evidence table ----------------------------------------------------
    if top is not None and top.evidence:
        rows = []
        for record in top.evidence[:MAX_EVIDENCE_ROWS]:
            rows.append(EvidenceTableRow(
                family=record.family, claim=record.claim, calculation=record.calculation,
                value=_r2(record.value), direction=record.direction,
                strength=_r2(record.strength),
                tx_ids=[int(t) for t in record.transactions[:MAX_TX_IDS_PER_ROW]],
                tx_count=len(record.transactions),
            ))
        evidence_chart = EvidenceTableChart(
            id="evidence_table", title=f"Evidence for {top.node}", rows=rows,
            total_records=len(top.evidence),
            rows_omitted=max(len(top.evidence) - MAX_EVIDENCE_ROWS, 0),
        )
    else:
        evidence_chart = EvidenceTableChart(
            id="evidence_table", title="Evidence",
            available=False, reason="the query returned no findings to evidence",
        )

    # --- data profile ------------------------------------------------------
    if eda is not None and eda.transactions > 0:
        profile_chart = DataProfileChart(
            id="data_profile", title="Profiled slice",
            payment_format=eda.distributions.get("payment_format"),
            amounts=_round_amounts(eda.amounts),
        )
    else:
        profile_chart = DataProfileChart(
            id="data_profile", title="Profiled slice", available=False,
            reason=(
                "the data profile tool was declined for this query"
                if eda is None else "the profiled slice held no transactions"
            ),
        )

    return ChartSet(
        risk_contribution=risk_chart,
        counterfactual=cf_chart,
        hypothesis_scoreboard=board,
        findings_table=findings_chart,
        evidence_table=evidence_chart,
        data_profile=profile_chart,
    )


def _round_amounts(a: AmountSummary | None) -> AmountSummary | None:
    if a is None:
        return None
    return AmountSummary(
        count=a.count, min=_r2(a.min), max=_r2(a.max), mean=_r2(a.mean),
        median=_r2(a.median), p95=_r2(a.p95), sum=_r2(a.sum),
    )
