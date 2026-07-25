"""Orchestrator: the agentic loop end-to-end.

query -> intent spec -> filter scope -> per-query plan -> context tools -> candidate screen
-> investigate (locked engine) -> rank findings -> narrate -> validate -> charts -> receipt.

The LLM lives ONLY at parse-in and narrate-out; everything between is deterministic.

Two-stage funnel, because investigating every account is not an option on ~5M rows:
  Stage 1  rank all accounts in memory (no database work) and keep a bounded pool.
  Stage 2  run the expensive, explainable investigation on the top of that pool only.
A query that names an account skips the screen entirely and investigates what was named.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache

import duckdb
import pandas as pd

from . import anomaly, charts, findings as findings_mod, intent, llm, narrator, planner
from . import scope as scope_mod
from . import screener, validator
from .casebuilder import UNFILTERED_TOOLS, investigate
from .config import Settings
from .duel import score_all
from .hypotheses import load_hypotheses
from .peers import PeerModel
from .risk import counterfactuals, risk_score
from .schemas import (
    AuditReceipt, Case, ChartSet, CostTelemetry, EdaProfile, ExecutionSummary,
    FeatureManifest, Finding, InvestigationSpec, PlanTraceEntry,
)
from .tools import eda_profile, feature_builder
from .trace import CountingConnection, TraceRecorder


@dataclass
class RunResult:
    spec: InvestigationSpec
    tools_run: list[str]
    tools_skipped: list[tuple[str, str]]
    case: Case | None
    narrative: str
    validated: bool
    unsupported: list[str]
    audit: AuditReceipt
    narrator_source: str = "template"   # "llm" or "template"
    intent_source: str = "deterministic"  # "llm" or "deterministic"
    # --- additive (Phase 6) ---
    findings: list[Finding] = field(default_factory=list)
    no_findings_reason: str | None = None
    plan_trace: list[PlanTraceEntry] = field(default_factory=list)
    execution: ExecutionSummary | None = None
    charts: ChartSet | None = None
    eda: EdaProfile | None = None
    feature_manifest: FeatureManifest | None = None


def _parse_spec(query: str) -> tuple[InvestigationSpec, str]:
    """LLM intent if available and valid, else deterministic."""
    if llm.use_llm():
        data = llm.intent_llm(query)
        if data:
            try:
                return InvestigationSpec(**data), "llm"
            except Exception:
                pass
    return intent.parse(query), "deterministic"


def _narrate(case: Case, spec: InvestigationSpec) -> tuple[str, str]:
    """LLM narration if available AND it passes the claim validator, else template."""
    if llm.use_llm():
        text = llm.narrate_llm(narrator.facts(case, spec))
        if text:
            ok, _ = validator.validate(text, case)
            if ok:
                return text, "llm"
    return narrator.narrate_template(case, spec), "template"


@lru_cache(maxsize=1)
def _anomaly_model():
    """Load the persisted IsolationForest once, or None if not trained yet."""
    return anomaly.load()


def _targets(
    spec: InvestigationSpec,
    features: pd.DataFrame | None,
    settings: Settings,
    recorder: TraceRecorder,
) -> tuple[list[str], str | None]:
    """Stage 1: which accounts get the expensive treatment, and why."""
    if spec.entities:
        present, absent = [], []
        for node in spec.entities:
            if features is not None and node in features.index:
                present.append(node)
            else:
                absent.append(node)
        for node in absent:
            recorder.note(f"named account {node} is absent from the profiled population")
        if not present:
            return [], (
                "none of the named accounts appear in the profiled population: "
                + ", ".join(spec.entities)
            )
        return present, None

    if features is None or len(features) == 0:
        raise ValueError("No entity in query and no profiles provided to rank a seed.")

    pool = None
    with recorder.step("candidate_screener") as h:
        pool = screener.rank(features, spec, settings.max_candidates)
        if h is not None:
            h.rows(rows_in=len(features), rows_out=len(pool.candidates))
            h.say(pool.reason)

    # The recorder absorbs a tool failure by design, so the result may be absent.
    if pool is None:
        return [], "the candidate screener failed, so no account could be ranked"

    recorder.counts(
        candidate_pool_size=len(pool.candidates),
        candidates_eligible=pool.eligible,
        candidates_dropped=pool.dropped,
    )
    if not pool.candidates:
        return [], pool.reason
    return [c.node for c in pool.candidates], None


def run(
    query: str,
    con: duckdb.DuckDBPyConnection,
    peers: PeerModel,
    profiles: pd.DataFrame | None = None,
    settings: Settings | None = None,
) -> RunResult:
    settings = settings or Settings()
    started = time.perf_counter()

    spec, intent_source = _parse_spec(query)
    scope = scope_mod.from_spec(spec)
    model = _anomaly_model()

    plan = planner.plan(spec, anomaly_available=model is not None)
    recorder = TraceRecorder(
        plan=plan,
        budget_ms=settings.broad_query_budget_s * 1000.0,
        filters_applied=scope_mod.applied(scope),
        unfiltered_tools=UNFILTERED_TOOLS,
    )

    # ---- Stage 0: context -------------------------------------------------
    eda: EdaProfile | None = None
    manifest: FeatureManifest | None = None
    features = profiles

    if plan.selected("feature_builder"):
        with recorder.step("feature_builder") as h:
            built_features, manifest = feature_builder.run(con, prebuilt=profiles)
            features = built_features
            if h is not None:
                h.rows(rows_out=len(features))
                source = "reused from warmup" if profiles is not None else "built for this run"
                h.say(
                    f"account features {source} ({len(features):,} accounts, "
                    f"{len(feature_builder.FEATURES)} features)"
                )

    scoped_tx: int | None = None
    total_tx: int | None = None
    filtered = scope_mod.is_active(scope)

    if plan.selected("eda_profile"):
        with recorder.step("eda_profile") as h:
            from .ledger import EvidenceLedger
            eda_ledger = EvidenceLedger()
            eda = eda_profile.run(con, scope, eda_ledger, settings)
            if h is not None:
                h.rows(rows_in=eda.transactions, rows_out=len(eda.distributions))
                h.say(
                    f"profiled {eda.transactions:,} transactions across "
                    f"{eda.accounts:,} accounts ({scope_mod.describe(scope)})"
                )

    if filtered:
        # `scoped_transactions` is only meaningful against a total, so report both.
        scoped_tx = eda.transactions if eda is not None else scope_mod.count(con, scope)
        total_tx = scope_mod.count(con, None)
        recorder.note(
            f"filters applied ({scope_mod.describe(scope)}): "
            f"{scoped_tx:,} of {total_tx:,} transactions in scope"
        )
    elif eda is not None:
        total_tx = eda.transactions

    # ---- Stage 1: who is worth investigating ------------------------------
    no_findings_reason: str | None = None
    targets: list[str] = []
    if filtered and scoped_tx == 0:
        no_findings_reason = (
            f"no transaction matched the requested filters ({scope_mod.describe(scope)})"
        )
    else:
        targets, no_findings_reason = _targets(spec, features, settings, recorder)

    # ---- Stage 2: the expensive, explainable stage -------------------------
    cases: list[Case] = []
    excluded = 0
    investigated = 0
    if targets and settings.max_investigations > 0:
        for node in targets[: settings.max_investigations]:
            counting = CountingConnection(con)
            case = investigate(
                counting, peers, node, spec.typology, plan.trace_depth, settings,
                anomaly_model=model, profiles=features,
                scope=scope, recorder=recorder,
            )
            recorder.roundtrips(node, counting.count)
            investigated += 1
            if findings_mod.should_include(case, plan.broad):
                cases.append(case)
            else:
                excluded += 1
    elif targets and settings.max_investigations <= 0:
        no_findings_reason = "max_investigations is 0, so no account was investigated"

    if not cases and no_findings_reason is None:
        no_findings_reason = (
            f"investigated {investigated} candidate(s); none carried weighted suspicious "
            f"evidence under the {spec.typology} typology, so nothing was flagged"
        )

    # ---- findings, narration, validation ----------------------------------
    built: list[Finding] = []
    for case in cases:
        explanation = narrator.explain_finding(case, spec)
        ok, unsupported = validator.validate(explanation, case)
        built.append(findings_mod.to_finding(
            case, spec, explanation, rank=1, explanation_source="template",
            validated=ok, unsupported=unsupported,
        ))
    result_findings = findings_mod.rerank(built)

    recorder.counts(
        investigated=investigated, excluded=excluded, returned=len(result_findings),
    )

    top_case = result_findings[0].case if result_findings else None
    if top_case is not None:
        narrative, narrator_source = _narrate(top_case, spec)
        ok, unsupported = validator.validate(narrative, top_case)
        scores = score_all(load_hypotheses(spec.typology), top_case.evidence)
        risk = risk_score(top_case.evidence, spec.typology)
        cfs = counterfactuals(top_case.evidence, spec.typology)
    else:
        narrative = (
            f"Query intent: {', '.join(spec.intent)} under the {spec.typology} typology. "
            f"No account was flagged: {no_findings_reason}."
        )
        narrator_source = "template"
        ok, unsupported = True, []
        scores, risk, cfs = [], None, []

    chart_set = charts.build(
        result_findings,
        contributions=risk.contributions if risk else {},
        counterfactuals=cfs,
        scores=scores,
        eda=eda,
        max_investigations=settings.max_investigations,
    )

    trace_entries = recorder.entries()
    tools_run = [e.tool for e in trace_entries if e.status == "ran"]
    tools_skipped = [(e.tool, e.reason) for e in trace_entries if e.status != "ran"]

    execution = ExecutionSummary(
        query=query,
        intent=list(spec.intent),
        typology=spec.typology,
        typology_recognized=plan.typology_recognized,
        entities=list(spec.entities),
        entities_note="" if spec.entities else "no account entity detected in the query",
        filters=scope_mod.applied(scope),
        filters_note="" if scope_mod.is_active(scope) else "no filter applied",
        scoped_transactions=scoped_tx,
        total_transactions=total_tx,
        cost=recorder.cost(settings),
        notes=recorder.notes,
    )

    audit = AuditReceipt(
        query=query, typology=spec.typology, intent=spec.intent,
        tools_run=tools_run, tools_skipped=tools_skipped,
        winning_hypothesis=top_case.winning_hypothesis if top_case else "",
        alternatives=[(s.id, s.label, s.band) for s in scores],
        risk=top_case.risk if top_case else 0.0,
        escalation=top_case.escalation if top_case else "monitor",
        evidence_ids=[r.claim_id for r in (top_case.evidence if top_case else [])],
        narrative=narrative,
        plan_trace=trace_entries,
    )

    return RunResult(
        spec=spec,
        tools_run=tools_run,
        tools_skipped=tools_skipped,
        case=top_case,
        narrative=narrative,
        validated=ok,
        unsupported=unsupported,
        audit=audit,
        narrator_source=narrator_source,
        intent_source=intent_source,
        findings=result_findings,
        no_findings_reason=no_findings_reason if not result_findings else None,
        plan_trace=trace_entries,
        execution=execution,
        charts=chart_set,
        eda=eda,
        feature_manifest=manifest,
    )
