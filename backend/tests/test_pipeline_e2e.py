"""Query-driven end-to-end pipeline suite — cross-stage coherence, not per-tool behaviour.

`test_phase6.py` checks each component in isolation. This suite is the complement: it drives
a MATRIX of representative natural-language queries through the whole agent once each, then
walks the pipeline stage by stage and asserts that every stage's output agrees with its
neighbours FOR THE SAME QUERY.

The stages, in the order the orchestrator executes them:

    1.  intent          query -> InvestigationSpec
    2.  scope           spec.filters -> FilterScope -> SQL predicate
    3.  plan            spec -> per-tool select/decline decisions
    4.  plan vs trace   what was planned vs what actually ran (telemetry agreement)
    5.  scoring route   the locked per-typology scoring tool set
    6.  eda             profile of the scoped slice
    7.  features        engineered account feature table + manifest
    8.  screener        bounded candidate pool (broad queries only)
    9.  investigation   locked duel/risk engine per candidate
    10. findings        ranked, deduplicated triage queue
    11. explanation     per-finding prose, claim-validated against its own case
    12. charts          six derived payloads, every number copied not computed
    13. execution       query-aware execution summary + cost telemetry
    14. audit           the receipt handed to a reviewer
    15. empty contract  what a query that legitimately finds nothing must still return
    16. determinism     same query twice -> same verdicts, same prose, same decisions
    17. api             the same run over HTTP, serialised

Hermetic: fixtures only, LLM forced off by conftest, no network, nothing from data/raw/.
Every scenario runs ONCE and is memoised for the whole module, so 17 stage tests over 9
query shapes cost 9 orchestrator runs, not 150.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nexus import intent as intent_mod
from nexus import planner
from nexus import scope as scope_mod
from nexus import validator
from nexus.api.app import create_app
from nexus.api.state import state_from_parts
from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.orchestrator import RunResult, _anomaly_model, run
from nexus.peers import PeerModel
from nexus.planner import ROSTER
from nexus.profiles import CLUSTER_FEATURES, build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"      # suspicious consolidation hub
M1 = "0900|M1"      # benign merchant lookalike
ABSENT = "4242|ZZZ9"  # syntactically valid node id, absent from the fixture population

# Caps kept small so nine full runs stay fast; both are still above the fixture population.
SETTINGS = Settings(max_investigations=10, max_candidates=50)

KNOWN_INTENTS = {"detect", "trace", "explain", "monitor"}
KNOWN_TYPOLOGIES = {"smurfing", "structuring"}
ROSTER_IDS = {t.id for t in ROSTER}
SCORING_IDS = {t.id for t in ROSTER if t.scoring}
LOCKED_SCORING_ROUTE = {
    "smurfing": {"peer_comparison", "rapid_pass_through", "graph_motif", "benign_signals"},
    "structuring": {"peer_comparison", "near_threshold"},
}
NODE_RE = re.compile(r"^\d{2,7}\|[0-9A-Za-z]+$")
RISK_BANDS = ((70.0, "high", "report"), (40.0, "medium", "review"), (0.0, "low", "monitor"))
MAX_DISTRIBUTION_ENTRIES = 20


# ---------------------------------------------------------------------------
# The query matrix. Each row is one query shape and the observable run it must produce.
# Facts (formats, months) were read out of tests/fixtures/case_Trans.csv, which holds
# payment formats {Credit Card, ACH, Cash, Wire, Cheque} and months {July, August,
# September}. `Bitcoin` is deliberately a format the fixture does NOT contain.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    id: str
    query: str
    typology: str
    intent: tuple[str, ...]
    entities: tuple[str, ...] = ()
    filters: dict[str, str] = field(default_factory=dict)
    trace_depth: int = 1
    findings_expected: bool = True          # does this query legitimately return findings?
    present_entities: tuple[str, ...] = ()  # named entities that exist in the fixture

    @property
    def broad(self) -> bool:
        return not self.entities


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="broad_detect_smurfing",
        query="Flag high-risk customers",
        typology="smurfing", intent=("detect",),
    ),
    Scenario(
        id="entity_explain",
        query=f"Explain why {C1} is suspicious",
        typology="smurfing", intent=("explain", "detect"),
        entities=(C1,), present_entities=(C1,),
    ),
    Scenario(
        id="entity_trace",
        query=f"Trace the network around {C1}",
        typology="smurfing", intent=("trace",),
        entities=(C1,), present_entities=(C1,), trace_depth=2,
    ),
    Scenario(
        id="structuring_route",
        query="Find structuring patterns",
        typology="structuring", intent=("detect",),
    ),
    Scenario(
        id="filtered_broad_cash",
        query="Flag high-risk cash customers",
        typology="smurfing", intent=("detect",), filters={"payment_format": "Cash"},
    ),
    Scenario(
        id="filtered_empty_slice",
        query="Flag high-risk bitcoin customers",
        typology="smurfing", intent=("detect",), filters={"payment_format": "Bitcoin"},
        findings_expected=False,
    ),
    Scenario(
        id="entity_benign_lookalike",
        query=f"Is {M1} suspicious?",
        typology="smurfing", intent=("detect",),
        entities=(M1,), present_entities=(M1,),
    ),
    Scenario(
        id="entity_absent_from_population",
        query=f"Explain why {ABSENT} is suspicious",
        typology="smurfing", intent=("explain", "detect"),
        entities=(ABSENT,), findings_expected=False,
    ),
    Scenario(
        id="month_filter_september",
        query="Find smurfing in September",
        typology="smurfing", intent=("detect",), filters={"month": "September"},
    ),
)

BY_SCENARIO_ID = {s.id: s for s in SCENARIOS}
EMPTY_SCENARIOS = tuple(s for s in SCENARIOS if not s.findings_expected)


def _params(scenarios=SCENARIOS):
    return pytest.mark.parametrize(
        "sc", scenarios, ids=[s.id for s in scenarios]
    )


# ---------------------------------------------------------------------------
# Engine + memoised runs
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", SETTINGS)
    profiles = build_profiles(con)
    tx_ids = {int(r[0]) for r in con.execute("SELECT tx_id FROM transactions").fetchall()}
    return SimpleNamespace(
        con=con, profiles=profiles, peers=PeerModel(profiles, k=3), tx_ids=tx_ids,
        # The orchestrator's own model handle, so plan expectations match what actually ran.
        anomaly_available=_anomaly_model() is not None,
        cache={},
    )


def result_for(engine, sc: Scenario) -> RunResult:
    """Run a scenario once per module and reuse it across every stage test."""
    if sc.id not in engine.cache:
        engine.cache[sc.id] = run(
            sc.query, engine.con, engine.peers, engine.profiles, SETTINGS
        )
    return engine.cache[sc.id]


def plan_for(engine, res: RunResult) -> planner.Plan:
    return planner.plan(res.spec, anomaly_available=engine.anomaly_available)


def _trace(res: RunResult) -> dict[str, object]:
    return {e.tool: e for e in res.plan_trace}


def _band(risk: float) -> tuple[str, str]:
    for threshold, tier, escalation in RISK_BANDS:
        if round(risk, 2) >= threshold:
            return tier, escalation
    return "low", "monitor"


# ---------------------------------------------------------------------------
# 1. Intent stage
# ---------------------------------------------------------------------------

@_params()
def test_stage01_intent_spec_is_well_formed(engine, sc: Scenario):
    res = result_for(engine, sc)
    spec = res.spec

    assert res.intent_source == "deterministic", (
        f"[{sc.id}] the hermetic suite must parse deterministically, "
        f"got intent_source={res.intent_source}"
    )
    assert spec.query == sc.query, f"[{sc.id}] spec.query dropped the original text"
    assert isinstance(spec.intent, list) and spec.intent, (
        f"[{sc.id}] intent must be a non-empty list, got {spec.intent!r}"
    )
    assert set(spec.intent) <= KNOWN_INTENTS, (
        f"[{sc.id}] unknown intent values {sorted(set(spec.intent) - KNOWN_INTENTS)}"
    )
    assert list(spec.intent) == list(sc.intent), (
        f"[{sc.id}] expected intent {list(sc.intent)}, got {spec.intent}"
    )
    assert spec.typology in KNOWN_TYPOLOGIES, (
        f"[{sc.id}] typology {spec.typology!r} is outside {sorted(KNOWN_TYPOLOGIES)}"
    )
    assert spec.typology == sc.typology, (
        f"[{sc.id}] expected typology {sc.typology}, got {spec.typology}"
    )
    assert list(spec.entities) == list(sc.entities), (
        f"[{sc.id}] expected entities {list(sc.entities)}, got {spec.entities}"
    )
    for node in spec.entities:
        assert NODE_RE.match(node), f"[{sc.id}] entity {node!r} is not a valid node id"
    assert 1 <= spec.trace_depth <= 3, (
        f"[{sc.id}] trace_depth {spec.trace_depth} outside 1..3"
    )
    assert spec.trace_depth == sc.trace_depth, (
        f"[{sc.id}] expected trace_depth {sc.trace_depth}, got {spec.trace_depth}"
    )
    assert spec == intent_mod.parse(sc.query), (
        f"[{sc.id}] run() spec disagrees with intent.parse() while the LLM is off"
    )


# ---------------------------------------------------------------------------
# 2. Scope stage
# ---------------------------------------------------------------------------

@_params()
def test_stage02_scope_matches_the_parsed_filters(engine, sc: Scenario):
    res = result_for(engine, sc)
    scope = scope_mod.from_spec(res.spec)

    assert res.spec.filters == sc.filters, (
        f"[{sc.id}] expected filters {sc.filters}, got {res.spec.filters}"
    )
    assert (scope is None) == (not res.spec.filters), (
        f"[{sc.id}] scope presence ({scope is not None}) disagrees with "
        f"filters {res.spec.filters}"
    )

    applied = scope_mod.applied(scope)
    assert set(applied) <= {"payment_format", "month"}, (
        f"[{sc.id}] scope applied unknown keys {sorted(set(applied) - {'payment_format', 'month'})}"
    )
    assert applied == sc.filters, (
        f"[{sc.id}] applied filters {applied} disagree with parsed filters {sc.filters}"
    )

    total = scope_mod.count(engine.con, None)
    scoped = scope_mod.count(engine.con, scope)
    assert scoped <= total, (
        f"[{sc.id}] scoped slice {scoped} exceeds the unfiltered population {total}"
    )
    if scope_mod.is_active(scope):
        assert scoped < total, (
            f"[{sc.id}] filter {applied} did not narrow anything ({scoped} of {total})"
        )
    else:
        assert scoped == total, f"[{sc.id}] inactive scope changed the row count"


# ---------------------------------------------------------------------------
# 3. Plan stage
# ---------------------------------------------------------------------------

@_params()
def test_stage03_plan_decides_every_roster_tool_with_a_reason(engine, sc: Scenario):
    res = result_for(engine, sc)
    plan = plan_for(engine, res)

    decided = [d.tool for d in plan.decisions]
    assert sorted(decided) == sorted(ROSTER_IDS), (
        f"[{sc.id}] plan decisions do not cover the roster exactly once: {decided}"
    )
    assert set(plan.run).isdisjoint({t for t, _ in plan.skipped}), (
        f"[{sc.id}] a tool is both run and skipped by the plan"
    )
    assert set(plan.run) | {t for t, _ in plan.skipped} == ROSTER_IDS, (
        f"[{sc.id}] run+skipped does not partition the roster"
    )
    for decision in plan.decisions:
        assert decision.reason, f"[{sc.id}] {decision.tool} carries no decision reason"
        assert len(decision.reason) <= 200, (
            f"[{sc.id}] {decision.tool} reason is {len(decision.reason)} chars (>200)"
        )
    assert 1 <= plan.trace_depth <= 3, (
        f"[{sc.id}] plan.trace_depth {plan.trace_depth} is not clamped to 1..3"
    )
    assert plan.trace_depth == max(1, min(3, res.spec.trace_depth)), (
        f"[{sc.id}] plan depth {plan.trace_depth} disagrees with spec depth "
        f"{res.spec.trace_depth}"
    )
    assert plan.broad == sc.broad, (
        f"[{sc.id}] plan.broad={plan.broad} but the query "
        f"{'names' if sc.entities else 'does not name'} an entity"
    )
    assert plan.typology_recognized, f"[{sc.id}] typology {res.spec.typology} unrecognized"


# ---------------------------------------------------------------------------
# 4. Plan / trace agreement (cross-stage)
# ---------------------------------------------------------------------------

@_params()
def test_stage04_trace_agrees_with_the_plan(engine, sc: Scenario):
    res = result_for(engine, sc)
    plan = plan_for(engine, res)
    entries = _trace(res)

    assert sorted(entries) == sorted(ROSTER_IDS), (
        f"[{sc.id}] plan_trace covers {sorted(entries)}, expected the whole roster"
    )
    selected = set(plan.run)
    for tool, entry in entries.items():
        if entry.status == "ran":
            assert tool in selected, (
                f"[{sc.id}] {tool} ran but the plan never selected it"
            )
            assert entry.duration_ms > 0.0, (
                f"[{sc.id}] {tool} ran with duration {entry.duration_ms}"
            )
        if tool not in selected:
            assert entry.status == "skipped", (
                f"[{sc.id}] {tool} was declined by the plan but its trace status is "
                f"{entry.status}"
            )
            assert entry.duration_ms == 0.0, (
                f"[{sc.id}] declined tool {tool} reports duration {entry.duration_ms}, "
                f"expected exactly 0.0"
            )
        assert entry.reason, f"[{sc.id}] {tool} trace entry carries no reason"

    assert res.tools_run == [e.tool for e in res.plan_trace if e.status == "ran"], (
        f"[{sc.id}] tools_run disagrees with the ran entries of the trace"
    )
    assert [t for t, _ in res.tools_skipped] == [
        e.tool for e in res.plan_trace if e.status != "ran"
    ], f"[{sc.id}] tools_skipped disagrees with the non-ran entries of the trace"


# ---------------------------------------------------------------------------
# 5. Scoring-route invariance (anchor safety at the query level)
# ---------------------------------------------------------------------------

@_params()
def test_stage05_scoring_tools_stay_inside_the_locked_route(engine, sc: Scenario):
    res = result_for(engine, sc)
    locked = LOCKED_SCORING_ROUTE[res.spec.typology]
    ran_scoring = set(res.tools_run) & SCORING_IDS

    assert ran_scoring <= locked, (
        f"[{sc.id}] scoring tools {sorted(ran_scoring - locked)} ran outside the locked "
        f"{res.spec.typology} route {sorted(locked)}"
    )
    if res.findings:
        assert ran_scoring, (
            f"[{sc.id}] findings were returned with no scoring tool in tools_run"
        )


# ---------------------------------------------------------------------------
# 6. EDA stage
# ---------------------------------------------------------------------------

@_params()
def test_stage06_eda_profiles_exactly_the_scoped_slice(engine, sc: Scenario):
    res = result_for(engine, sc)
    ran = "eda_profile" in res.tools_run

    if not ran:
        assert res.eda is None, (
            f"[{sc.id}] eda_profile did not run yet a profile was returned"
        )
        assert not sc.broad, (
            f"[{sc.id}] a broad query must profile its slice, but eda_profile was skipped"
        )
        return

    assert res.eda is not None, f"[{sc.id}] eda_profile ran but returned no profile"
    scope = scope_mod.from_spec(res.spec)
    expected = scope_mod.count(engine.con, scope)
    assert res.eda.transactions == expected, (
        f"[{sc.id}] EDA counted {res.eda.transactions} transactions, the scope "
        f"({scope_mod.describe(scope)}) holds {expected}"
    )
    assert res.eda.scope == sc.filters, (
        f"[{sc.id}] EDA scope {res.eda.scope} disagrees with filters {sc.filters}"
    )

    for column, dist in res.eda.distributions.items():
        counts = [e.count for e in dist.entries]
        assert counts == sorted(counts, reverse=True), (
            f"[{sc.id}] {column} distribution is not ordered by count descending: {counts}"
        )
        assert len(dist.entries) <= MAX_DISTRIBUTION_ENTRIES, (
            f"[{sc.id}] {column} distribution returned {len(dist.entries)} entries (>20)"
        )
        assert sum(counts) + dist.remainder_count == res.eda.transactions, (
            f"[{sc.id}] {column} distribution does not reconcile to "
            f"{res.eda.transactions} transactions"
        )

    if res.eda.transactions == 0:
        assert res.eda.amounts is None, (
            f"[{sc.id}] empty slice reported zero-filled amounts instead of None"
        )
        assert res.eda.time_span is None, (
            f"[{sc.id}] empty slice reported a time span instead of None"
        )
        assert res.eda.distributions == {}, (
            f"[{sc.id}] empty slice invented distributions"
        )
    else:
        assert res.eda.amounts is not None and res.eda.amounts.count > 0, (
            f"[{sc.id}] non-empty slice returned no amount summary"
        )
        assert res.eda.time_span is not None, (
            f"[{sc.id}] non-empty slice returned no time span"
        )


# ---------------------------------------------------------------------------
# 7. Feature stage
# ---------------------------------------------------------------------------

@_params()
def test_stage07_feature_manifest_matches_the_cluster_contract(engine, sc: Scenario):
    res = result_for(engine, sc)
    ran = "feature_builder" in res.tools_run

    if not ran:
        assert res.feature_manifest is None, (
            f"[{sc.id}] feature_builder did not run yet a manifest was returned"
        )
        return

    manifest = res.feature_manifest
    assert manifest is not None, f"[{sc.id}] feature_builder ran but returned no manifest"
    assert len(manifest.features) == 10, (
        f"[{sc.id}] manifest declares {len(manifest.features)} features, expected 10"
    )
    assert manifest.cluster_features == list(CLUSTER_FEATURES), (
        f"[{sc.id}] manifest cluster features {manifest.cluster_features} disagree with "
        f"profiles.CLUSTER_FEATURES {list(CLUSTER_FEATURES)}"
    )
    assert manifest.accounts == len(engine.profiles), (
        f"[{sc.id}] manifest covers {manifest.accounts} accounts, the profile table has "
        f"{len(engine.profiles)}"
    )


# ---------------------------------------------------------------------------
# 8. Screener stage
# ---------------------------------------------------------------------------

@_params()
def test_stage08_screener_runs_only_for_broad_queries_and_stays_bounded(engine, sc: Scenario):
    res = result_for(engine, sc)
    plan = plan_for(engine, res)
    cost = res.execution.cost

    if sc.broad:
        assert plan.selected("candidate_screener"), (
            f"[{sc.id}] a broad query must select the candidate screener"
        )
        assert cost.candidate_pool_size <= SETTINGS.max_candidates, (
            f"[{sc.id}] candidate pool {cost.candidate_pool_size} exceeds the cap "
            f"{SETTINGS.max_candidates}"
        )
        assert cost.investigated <= SETTINGS.max_investigations, (
            f"[{sc.id}] investigated {cost.investigated} accounts over the cap "
            f"{SETTINGS.max_investigations}"
        )
        if res.findings:
            assert "candidate_screener" in res.tools_run, (
                f"[{sc.id}] findings were returned without the screener ever running"
            )
            assert cost.candidate_pool_size > 0, (
                f"[{sc.id}] findings were returned from an empty candidate pool"
            )
    else:
        assert "candidate_screener" not in res.tools_run, (
            f"[{sc.id}] entity-scoped query still ran the candidate screener"
        )
        assert not plan.selected("candidate_screener"), (
            f"[{sc.id}] entity-scoped query planned a candidate screen"
        )
        assert cost.candidate_pool_size == 0, (
            f"[{sc.id}] entity-scoped query reported a candidate pool of "
            f"{cost.candidate_pool_size}"
        )


# ---------------------------------------------------------------------------
# 9. Investigation stage
# ---------------------------------------------------------------------------

@_params()
def test_stage09_every_case_is_proof_carrying_and_internally_consistent(engine, sc: Scenario):
    res = result_for(engine, sc)
    if not res.findings:
        assert not sc.findings_expected, f"[{sc.id}] expected findings, got none"
        return

    for finding in res.findings:
        case = finding.case
        assert case.seed == finding.node, (
            f"[{sc.id}] finding node {finding.node} disagrees with case seed {case.seed}"
        )
        assert case.evidence, f"[{sc.id}] {finding.node} was flagged with no evidence"
        cited = [r for r in case.evidence if r.transactions]
        assert cited, (
            f"[{sc.id}] {finding.node} carries no evidence record citing a transaction"
        )
        for record in cited:
            unknown = set(record.transactions) - engine.tx_ids
            assert not unknown, (
                f"[{sc.id}] {finding.node} evidence {record.claim_id} cites tx_ids "
                f"{sorted(unknown)} that are absent from the fixture"
            )

        assert case.risk == finding.risk, (
            f"[{sc.id}] {finding.node} case risk {case.risk} != finding risk {finding.risk}"
        )
        tier, escalation = _band(case.risk)
        if case.winning_kind == "suspicious":
            assert (case.tier, case.escalation) == (tier, escalation), (
                f"[{sc.id}] {finding.node} risk {case.risk} maps to {tier}/{escalation}, "
                f"case says {case.tier}/{case.escalation}"
            )
        else:
            # Documented duel gate (casebuilder): a benign or indeterminate verdict is
            # pinned to low/monitor regardless of the additive score.
            assert (case.tier, case.escalation) == ("low", "monitor"), (
                f"[{sc.id}] {finding.node} verdict {case.winning_kind} must be gated to "
                f"low/monitor, case says {case.tier}/{case.escalation}"
            )
        assert finding.tier == case.tier and finding.escalation == case.escalation, (
            f"[{sc.id}] {finding.node} finding tier/escalation disagrees with its case"
        )
        assert case.typology == res.spec.typology, (
            f"[{sc.id}] {finding.node} case typology {case.typology} disagrees with the "
            f"spec typology {res.spec.typology}"
        )
        assert finding.node in case.members, (
            f"[{sc.id}] {finding.node} is not a member of its own case"
        )


# ---------------------------------------------------------------------------
# 10. Findings stage
# ---------------------------------------------------------------------------

@_params()
def test_stage10_findings_are_ranked_and_shaped_by_query_kind(engine, sc: Scenario):
    res = result_for(engine, sc)
    findings = res.findings

    if not findings:
        assert not sc.findings_expected, f"[{sc.id}] expected findings, got none"
        return

    risks = [round(f.risk, 2) for f in findings]
    assert risks == sorted(risks, reverse=True), (
        f"[{sc.id}] findings are not ranked by risk descending: {risks}"
    )
    assert [f.rank for f in findings] == list(range(1, len(findings) + 1)), (
        f"[{sc.id}] ranks are not contiguous 1..n: {[f.rank for f in findings]}"
    )
    nodes = [f.node for f in findings]
    assert len(set(nodes)) == len(nodes), f"[{sc.id}] duplicate nodes in findings: {nodes}"
    for a, b in zip(findings, findings[1:]):
        if round(a.risk, 2) == round(b.risk, 2):
            assert a.node < b.node, (
                f"[{sc.id}] tie at risk {a.risk} is not broken by ascending node "
                f"({a.node} before {b.node})"
            )

    assert res.case is not None, f"[{sc.id}] findings exist but case is None"
    assert res.case.seed == findings[0].node, (
        f"[{sc.id}] case seed {res.case.seed} is not the top finding {findings[0].node}"
    )

    if sc.broad:
        offenders = [f.node for f in findings if f.winning_kind in {"benign", "indeterminate"}]
        assert not offenders, (
            f"[{sc.id}] broad sweep padded the queue with cleared accounts {offenders}"
        )
    else:
        for node in sc.present_entities:
            matched = [f for f in findings if f.node == node]
            assert len(matched) == 1, (
                f"[{sc.id}] named entity {node} yielded {len(matched)} findings, expected "
                f"exactly one regardless of verdict"
            )
        assert set(nodes) <= set(sc.present_entities), (
            f"[{sc.id}] entity-scoped query returned unrequested nodes "
            f"{sorted(set(nodes) - set(sc.present_entities))}"
        )


# ---------------------------------------------------------------------------
# 11. Explanation stage
# ---------------------------------------------------------------------------

@_params()
def test_stage11_explanations_are_bounded_and_claim_validated(engine, sc: Scenario):
    res = result_for(engine, sc)
    if not res.findings:
        assert not sc.findings_expected, f"[{sc.id}] expected findings, got none"
        return

    for finding in res.findings:
        text = finding.explanation
        assert text, f"[{sc.id}] {finding.node} has an empty explanation"
        assert len(text) <= 400, (
            f"[{sc.id}] {finding.node} explanation is {len(text)} chars (>400)"
        )
        assert finding.tier in text, (
            f"[{sc.id}] {finding.node} explanation never names its tier "
            f"{finding.tier!r}: {text!r}"
        )
        assert finding.escalation in text, (
            f"[{sc.id}] {finding.node} explanation never names its escalation "
            f"{finding.escalation!r}: {text!r}"
        )
        assert any(term in text for term in res.spec.intent), (
            f"[{sc.id}] {finding.node} explanation restates none of the intent terms "
            f"{res.spec.intent}: {text!r}"
        )

        ok, unsupported = validator.validate(text, finding.case)
        assert ok, (
            f"[{sc.id}] {finding.node} explanation carries unsupported numbers "
            f"{unsupported}"
        )
        assert finding.validated is True and finding.unsupported == [], (
            f"[{sc.id}] {finding.node} was returned with unsupported claims "
            f"{finding.unsupported}"
        )

    ok, unsupported = validator.validate(res.narrative, res.case)
    assert ok, f"[{sc.id}] the run narrative carries unsupported numbers {unsupported}"
    assert res.validated is True and res.unsupported == [], (
        f"[{sc.id}] run reported unsupported narrative claims {res.unsupported}"
    )
    assert res.narrator_source == "template", (
        f"[{sc.id}] narration must be templated with the LLM off, got "
        f"{res.narrator_source}"
    )


# ---------------------------------------------------------------------------
# 12. Charts stage
# ---------------------------------------------------------------------------

def _numbers(obj) -> set[float]:
    """Every numeric leaf in a payload, rounded to the chart's own two decimals."""
    found: set[float] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        return {round(float(obj), 2)}
    if isinstance(obj, dict):
        for value in obj.values():
            found |= _numbers(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found |= _numbers(value)
    return found


def _traceable_numbers(res: RunResult) -> set[float]:
    source = _numbers([f.model_dump() for f in res.findings])
    source |= _numbers(res.eda.model_dump() if res.eda else {})
    source |= {round(e.contribution, 2) for e in res.charts.risk_contribution.entries}
    source |= {round(e.score, 2) for e in res.charts.counterfactual.entries}
    # Structural counts: the length of a list the run already returned.
    for finding in res.findings:
        source.add(float(len(finding.evidence)))
        for record in finding.evidence:
            source.add(float(len(record.transactions)))
    return source


@_params()
def test_stage12_charts_are_complete_and_every_number_traces_to_the_run(engine, sc: Scenario):
    res = result_for(engine, sc)
    assert res.charts is not None, f"[{sc.id}] no chart set was returned"
    dumped = res.charts.model_dump()
    assert len(dumped) == 6, (
        f"[{sc.id}] chart set carries {len(dumped)} payloads, expected exactly six"
    )

    for name, payload in dumped.items():
        assert "available" in payload, f"[{sc.id}] {name} has no availability flag"
        if not payload["available"]:
            assert payload.get("reason"), (
                f"[{sc.id}] {name} is unavailable without saying why"
            )

    if res.findings:
        for name in ("findings_table", "evidence_table", "risk_contribution",
                     "counterfactual"):
            assert dumped[name]["available"], (
                f"[{sc.id}] {name} is unavailable although the run produced findings"
            )
        assert len(dumped["findings_table"]["rows"]) == len(res.findings), (
            f"[{sc.id}] findings table has {len(dumped['findings_table']['rows'])} rows "
            f"for {len(res.findings)} findings"
        )
    else:
        for name, payload in dumped.items():
            assert payload["available"] is False, (
                f"[{sc.id}] {name} claims to be available with no findings"
            )

    source = _traceable_numbers(res)
    for name in ("findings_table", "evidence_table", "risk_contribution",
                 "counterfactual", "data_profile"):
        if not dumped[name]["available"]:
            # An unavailable payload carries no data, only its schema defaults.
            continue
        for value in _numbers(dumped[name]):
            assert value in source, (
                f"[{sc.id}] {name} invented the number {value}; it is reachable from no "
                f"value in the run result"
            )


# ---------------------------------------------------------------------------
# 13. Execution summary stage
# ---------------------------------------------------------------------------

@_params()
def test_stage13_execution_summary_reconciles_with_the_run(engine, sc: Scenario):
    res = result_for(engine, sc)
    ex = res.execution
    assert ex is not None, f"[{sc.id}] no execution summary was returned"

    assert ex.query == sc.query, (
        f"[{sc.id}] execution echoed {ex.query!r} instead of the query verbatim"
    )
    assert ex.intent == list(res.spec.intent), (
        f"[{sc.id}] execution intent {ex.intent} disagrees with spec {res.spec.intent}"
    )
    assert ex.typology == res.spec.typology, (
        f"[{sc.id}] execution typology {ex.typology} disagrees with spec"
    )
    assert ex.entities == list(res.spec.entities), (
        f"[{sc.id}] execution entities {ex.entities} disagree with spec {res.spec.entities}"
    )
    assert ex.filters == scope_mod.applied(scope_mod.from_spec(res.spec)), (
        f"[{sc.id}] execution filters {ex.filters} disagree with the applied scope"
    )
    if sc.filters:
        assert ex.scoped_transactions is not None and ex.total_transactions is not None, (
            f"[{sc.id}] a filtered run must report both scoped and total transactions"
        )
        assert ex.scoped_transactions <= ex.total_transactions, (
            f"[{sc.id}] scoped {ex.scoped_transactions} exceeds total "
            f"{ex.total_transactions}"
        )
    else:
        assert ex.scoped_transactions is None, (
            f"[{sc.id}] unfiltered run reported a scoped count "
            f"{ex.scoped_transactions}"
        )
        assert ex.filters_note == "no filter applied", (
            f"[{sc.id}] unfiltered run note is {ex.filters_note!r}"
        )

    cost = ex.cost
    assert cost.investigated == cost.returned + cost.excluded, (
        f"[{sc.id}] cost does not reconcile: investigated {cost.investigated} != "
        f"returned {cost.returned} + excluded {cost.excluded}"
    )
    assert cost.returned == len(res.findings), (
        f"[{sc.id}] cost.returned {cost.returned} != {len(res.findings)} findings"
    )
    assert cost.roundtrips_max_per_candidate <= SETTINGS.max_roundtrips_per_candidate, (
        f"[{sc.id}] a candidate used {cost.roundtrips_max_per_candidate} round-trips, "
        f"over the cap {SETTINGS.max_roundtrips_per_candidate}"
    )
    assert cost.max_candidates == SETTINGS.max_candidates, (
        f"[{sc.id}] cost echoes the wrong candidate cap {cost.max_candidates}"
    )
    assert cost.max_investigations == SETTINGS.max_investigations, (
        f"[{sc.id}] cost echoes the wrong investigation cap {cost.max_investigations}"
    )
    if res.findings:
        assert cost.roundtrips_max_per_candidate > 0, (
            f"[{sc.id}] findings were produced with zero measured database round-trips"
        )


# ---------------------------------------------------------------------------
# 14. Audit receipt stage
# ---------------------------------------------------------------------------

@_params()
def test_stage14_audit_receipt_matches_the_run(engine, sc: Scenario):
    res = result_for(engine, sc)
    audit = res.audit

    assert audit.query == sc.query, (
        f"[{sc.id}] audit recorded query {audit.query!r}"
    )
    assert audit.typology == res.spec.typology, (
        f"[{sc.id}] audit typology {audit.typology} disagrees with spec"
    )
    assert audit.intent == list(res.spec.intent), (
        f"[{sc.id}] audit intent {audit.intent} disagrees with spec {res.spec.intent}"
    )
    assert audit.tools_run == [e.tool for e in res.plan_trace if e.status == "ran"], (
        f"[{sc.id}] audit tools_run {audit.tools_run} disagrees with the trace"
    )
    assert audit.plan_trace == res.plan_trace, (
        f"[{sc.id}] audit plan_trace is not the run's plan trace"
    )
    assert audit.narrative == res.narrative, (
        f"[{sc.id}] audit narrative differs from the returned narrative"
    )

    if res.case is not None:
        top = res.findings[0]
        assert audit.risk == top.risk, (
            f"[{sc.id}] audit risk {audit.risk} != top finding risk {top.risk}"
        )
        assert audit.escalation == top.escalation, (
            f"[{sc.id}] audit escalation {audit.escalation} != top finding "
            f"{top.escalation}"
        )
        assert audit.winning_hypothesis == res.case.winning_hypothesis, (
            f"[{sc.id}] audit winning hypothesis disagrees with the case"
        )
        assert audit.evidence_ids == [r.claim_id for r in res.case.evidence], (
            f"[{sc.id}] audit evidence ids do not match the case ledger"
        )
    else:
        assert audit.risk == 0.0 and audit.escalation == "monitor", (
            f"[{sc.id}] no case, yet audit reports risk {audit.risk} / "
            f"{audit.escalation}"
        )
        assert audit.evidence_ids == [], (
            f"[{sc.id}] no case, yet audit cites evidence {audit.evidence_ids}"
        )


# ---------------------------------------------------------------------------
# 15. Empty-findings contract
# ---------------------------------------------------------------------------

@_params(EMPTY_SCENARIOS)
def test_stage15_empty_findings_still_show_what_was_searched(engine, sc: Scenario):
    res = result_for(engine, sc)

    assert res.findings == [], (
        f"[{sc.id}] expected an empty result, got {[f.node for f in res.findings]}"
    )
    assert res.case is None, f"[{sc.id}] case must be None when nothing was flagged"
    assert res.no_findings_reason, f"[{sc.id}] no reason was given for the empty result"
    assert res.plan_trace, f"[{sc.id}] the analyst lost the plan trace"
    assert res.execution is not None, f"[{sc.id}] the analyst lost the execution summary"
    assert res.charts is not None, f"[{sc.id}] the analyst lost the chart set"
    assert res.narrative, f"[{sc.id}] no narrative explained the empty result"

    if sc.filters:
        for value in sc.filters.values():
            assert value.lower() in res.no_findings_reason.lower(), (
                f"[{sc.id}] the reason {res.no_findings_reason!r} never names the filter "
                f"value {value!r}"
            )
    for node in sc.entities:
        if node not in sc.present_entities:
            assert node in res.no_findings_reason, (
                f"[{sc.id}] the reason {res.no_findings_reason!r} never names the absent "
                f"account {node}"
            )


# ---------------------------------------------------------------------------
# 16. Determinism
# ---------------------------------------------------------------------------

@_params()
def test_stage16_same_query_twice_is_identical(engine, sc: Scenario):
    def verdicts(res: RunResult):
        return [(f.node, f.risk, f.tier, f.escalation, f.rank) for f in res.findings]

    def decisions(res: RunResult):
        plan = plan_for(engine, res)
        return [(d.tool, d.selected, d.reason) for d in plan.decisions]

    first = result_for(engine, sc)
    second = run(sc.query, engine.con, engine.peers, engine.profiles, SETTINGS)

    assert verdicts(first) == verdicts(second), (
        f"[{sc.id}] verdicts changed between identical runs:\n"
        f"  first  {verdicts(first)}\n  second {verdicts(second)}"
    )
    assert first.narrative == second.narrative, (
        f"[{sc.id}] the narrative changed between identical runs"
    )
    assert decisions(first) == decisions(second), (
        f"[{sc.id}] plan decisions changed between identical runs"
    )
    assert first.tools_run == second.tools_run, (
        f"[{sc.id}] tools_run changed between identical runs: {first.tools_run} vs "
        f"{second.tools_run}"
    )
    assert first.no_findings_reason == second.no_findings_reason, (
        f"[{sc.id}] the no-findings reason changed between identical runs"
    )
    assert [f.explanation for f in first.findings] == \
        [f.explanation for f in second.findings], (
            f"[{sc.id}] per-finding explanations changed between identical runs"
        )


# ---------------------------------------------------------------------------
# Matrix coverage: the nine shapes together must exercise the whole roster.
# ---------------------------------------------------------------------------

def test_matrix_exercises_every_roster_tool(engine):
    ran: set[str] = set()
    for sc in SCENARIOS:
        ran |= set(result_for(engine, sc).tools_run)

    expected = set(ROSTER_IDS)
    if not engine.anomaly_available:
        expected.discard("isolation_forest")  # no model artifact in models/
    assert expected <= ran, (
        f"the query matrix never exercises {sorted(expected - ran)}; add a query shape "
        f"that selects it"
    )


# ---------------------------------------------------------------------------
# 17. API end to end
# ---------------------------------------------------------------------------

RETAINED_KEYS = {
    "spec", "plan", "case", "narrative", "validated", "unsupported", "sources", "audit",
}
ADDITIVE_KEYS = {
    "plan_trace", "findings", "charts", "execution", "eda", "feature_manifest",
    "no_findings_reason",
}

API_SCENARIO_IDS = (
    "broad_detect_smurfing",
    "entity_explain",
    "filtered_empty_slice",
)


@pytest.fixture(scope="module")
def client(engine):
    state = state_from_parts(
        SimpleNamespace(con=engine.con, n_transactions=len(engine.profiles)),
        engine.profiles, engine.peers, SETTINGS,
    )
    return TestClient(create_app(state=state, warm=False))


@pytest.mark.parametrize("scenario_id", API_SCENARIO_IDS)
def test_stage17_investigate_endpoint_returns_the_full_contract(client, scenario_id):
    sc = BY_SCENARIO_ID[scenario_id]
    response = client.post("/investigate", json={"query": sc.query})

    assert response.status_code == 200, (
        f"[{sc.id}] /investigate returned {response.status_code}: {response.text[:300]}"
    )
    body = response.json()          # JSON serialisation of the whole run must succeed
    assert set(body) == RETAINED_KEYS | ADDITIVE_KEYS, (
        f"[{sc.id}] response keys disagree with the contract; missing "
        f"{sorted((RETAINED_KEYS | ADDITIVE_KEYS) - set(body))}, extra "
        f"{sorted(set(body) - (RETAINED_KEYS | ADDITIVE_KEYS))}"
    )

    assert body["spec"]["typology"] == sc.typology, (
        f"[{sc.id}] API spec typology {body['spec']['typology']} != {sc.typology}"
    )
    assert body["spec"]["filters"] == sc.filters, (
        f"[{sc.id}] API spec filters {body['spec']['filters']} != {sc.filters}"
    )
    assert body["sources"]["intent"] == "deterministic", (
        f"[{sc.id}] API used a non-deterministic parser"
    )
    assert len(body["plan_trace"]) == len(ROSTER_IDS), (
        f"[{sc.id}] API plan_trace covers {len(body['plan_trace'])} tools, expected "
        f"{len(ROSTER_IDS)}"
    )
    assert body["execution"] is not None and body["charts"] is not None, (
        f"[{sc.id}] API dropped the execution summary or the charts"
    )
    assert body["audit"]["query"] == sc.query, f"[{sc.id}] API audit lost the query"

    if sc.findings_expected:
        assert body["findings"], f"[{sc.id}] API returned no findings"
        assert body["case"] is not None, f"[{sc.id}] API returned findings but no case"
        assert body["case"]["seed"] == body["findings"][0]["node"], (
            f"[{sc.id}] API case is not the top finding"
        )
        assert body["no_findings_reason"] is None, (
            f"[{sc.id}] API gave a no-findings reason alongside findings"
        )
    else:
        assert body["findings"] == [], f"[{sc.id}] API returned unexpected findings"
        assert body["case"] is None, (
            f"[{sc.id}] an empty result must carry case: null, got "
            f"{body['case']}"
        )
        assert body["no_findings_reason"], (
            f"[{sc.id}] an empty result must explain itself"
        )


def test_stage17_empty_slice_is_200_not_404(client):
    """404 is reserved for an account absent from the dataset, not an empty filter slice."""
    sc = BY_SCENARIO_ID["filtered_empty_slice"]
    response = client.post("/investigate", json={"query": sc.query})
    assert response.status_code == 200, (
        f"[{sc.id}] an empty filter slice must be a 200 with case: null, got "
        f"{response.status_code}"
    )
    body = response.json()
    assert body["case"] is None, f"[{sc.id}] expected case: null"
    assert body["no_findings_reason"] is not None, f"[{sc.id}] expected a reason"

    absent = BY_SCENARIO_ID["entity_absent_from_population"]
    other = client.post("/investigate", json={"query": absent.query})
    assert other.status_code == 404, (
        f"[{absent.id}] an account absent from the dataset must be a 404, got "
        f"{other.status_code}"
    )
    assert other.json()["error"]["code"] == "ACCOUNT_NOT_FOUND", (
        f"[{absent.id}] unexpected error code {other.json()['error']}"
    )
