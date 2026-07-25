"""Phase 6 verification — the capabilities the hackathon brief requires.

1. EDA is agent-callable, returns metrics, and cites real transactions under a neutral family.
2. Feature engineering is a visible step with a manifest, values identical to build_profiles.
3. Query filters actually narrow the analysis (the bug: they were parsed then dropped).
4. Broad queries return a RANKED findings list with per-item risk, not one case.
5. The plan varies with intent/typology/entities and carries real per-tool telemetry.
6. Charts are derived, and every number in them traces back to the run result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus import charts, screener
from nexus import scope as scope_mod
from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.intent import parse
from nexus.ledger import EvidenceLedger
from nexus.orchestrator import run
from nexus.peers import PeerModel
from nexus.planner import ROSTER, plan, plan_for
from nexus.profiles import CLUSTER_FEATURES, build_profiles
from nexus.risk import RISK_PROFILES
from nexus.schemas import NEUTRAL_FAMILIES
from nexus.tools import eda_profile, feature_builder

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"      # suspicious consolidation hub
M1 = "0900|M1"      # benign merchant lookalike


@pytest.fixture(scope="module")
def engine():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    profiles = build_profiles(con)
    return con, PeerModel(profiles, k=3), profiles


@pytest.fixture()
def settings():
    return Settings(max_investigations=10, max_candidates=50)


# --------------------------------------------------------------------------
# 1. EDA tool
# --------------------------------------------------------------------------

def test_eda_returns_metrics_and_proof_carrying_neutral_evidence(engine):
    con, _, _ = engine
    ledger = EvidenceLedger()
    profile = eda_profile.run(con, None, ledger, Settings())

    assert profile.transactions > 0
    assert profile.accounts > 0
    assert "payment_format" in profile.distributions
    assert profile.amounts is not None and profile.amounts.count > 0
    assert profile.time_span is not None and profile.time_span.span_days >= 1

    # Exactly one record, under a NEUTRAL family, citing real transaction ids.
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.family in NEUTRAL_FAMILIES
    assert record.transactions, "EDA evidence must cite real transactions"

    known = {int(r[0]) for r in con.execute("SELECT tx_id FROM transactions").fetchall()}
    assert set(record.transactions) <= known


def test_eda_neutral_family_cannot_move_a_score():
    """A neutral family appears in no fingerprint and no risk weight, so it is inert."""
    weighted = {f for profile in RISK_PROFILES.values() for f in profile}
    assert NEUTRAL_FAMILIES.isdisjoint(weighted)


def test_eda_never_reads_the_held_out_label():
    source = Path(eda_profile.__file__).read_text(encoding="utf-8")
    for symbol in ("is_laundering", "Patterns.txt", "parse_patterns", "GroundTruth"):
        assert symbol not in source, f"EDA tool must not reference {symbol}"


def test_eda_empty_slice_reports_absent_not_zero(engine):
    con, _, _ = engine
    ledger = EvidenceLedger()
    impossible = scope_mod.from_filters({"payment_format": "NoSuchFormat"})
    profile = eda_profile.run(con, impossible, ledger, Settings())

    assert profile.transactions == 0
    assert profile.amounts is None          # absent, NOT a zero-filled object
    assert profile.time_span is None
    assert ledger.records == []             # no evidence invented for an empty slice


# --------------------------------------------------------------------------
# 2. Feature engineering as a visible step
# --------------------------------------------------------------------------

def test_feature_builder_matches_build_profiles_exactly(engine):
    con, _, profiles = engine
    features, manifest = feature_builder.run(con)

    assert list(features.columns) == list(feature_builder.FEATURES)
    assert set(features.index) == set(profiles.index)
    for column in feature_builder.FEATURES:
        for node in features.index:
            assert features.at[node, column] == pytest.approx(
                profiles.at[node, column], rel=1e-9
            )

    assert len(manifest.features) == 10
    assert manifest.cluster_features == list(CLUSTER_FEATURES)
    assert manifest.source == "built"


def test_feature_builder_reuses_the_warmup_table_without_querying(engine):
    con, _, profiles = engine

    class Boom:
        def execute(self, *_a, **_k):
            raise AssertionError("prebuilt path must issue no database query")

    features, manifest = feature_builder.run(Boom(), prebuilt=profiles)
    assert features is profiles
    assert manifest.source == "warmup"


# --------------------------------------------------------------------------
# 3. Filters actually scope the analysis
# --------------------------------------------------------------------------

def test_inactive_scope_contributes_no_sql():
    """This is what keeps every pre-existing anchor byte-identical."""
    assert scope_mod.from_filters({}) is None
    assert scope_mod.sql(None) == ("", [])
    assert scope_mod.where(None) == ("", [])


def test_filter_narrows_the_analysed_slice(engine):
    con, _, _ = engine
    total = scope_mod.count(con, None)
    cash = scope_mod.count(con, scope_mod.from_filters({"payment_format": "Cash"}))

    assert 0 < cash < total, "a filter must select strictly fewer transactions"
    expected = con.execute(
        "SELECT COUNT(*) FROM transactions WHERE lower(payment_format) = 'cash'"
    ).fetchone()[0]
    assert cash == expected


def test_filtered_and_unfiltered_queries_differ(engine, settings):
    con, peers, profiles = engine
    plain = run("Flag high-risk customers", con, peers, profiles, settings)
    cash = run("Flag high-risk cash customers", con, peers, profiles, settings)

    assert cash.spec.filters.get("payment_format") == "Cash"
    assert cash.execution.filters == {"payment_format": "Cash"}
    assert cash.execution.scoped_transactions is not None
    assert cash.execution.scoped_transactions < cash.execution.total_transactions
    # The unfiltered run reports no scoping at all.
    assert plain.execution.filters == {}
    assert plain.execution.scoped_transactions is None


def test_month_filter_maps_to_a_calendar_month():
    s = scope_mod.from_filters({"month": "March"})
    assert s is not None and s.month_number == 3
    predicate, params = scope_mod.sql(s)
    assert "month(timestamp)" in predicate and params == [3]


# --------------------------------------------------------------------------
# 4. Ranked multi-item findings
# --------------------------------------------------------------------------

def test_broad_query_returns_ranked_findings_with_per_item_risk(engine, settings):
    con, peers, profiles = engine
    res = run("Flag high-risk customers", con, peers, profiles, settings)

    assert res.findings, "a broad query must return a findings list"
    for finding in res.findings:
        assert finding.tier in {"low", "medium", "high"}
        assert finding.escalation in {"monitor", "review", "report"}
        assert finding.explanation and len(finding.explanation) <= 400
        assert finding.evidence and any(r.transactions for r in finding.evidence)

    risks = [f.risk for f in res.findings]
    assert risks == sorted(risks, reverse=True), "findings must rank by risk descending"
    assert [f.rank for f in res.findings] == list(range(1, len(res.findings) + 1))


def test_broad_query_excludes_the_benign_lookalike(engine, settings):
    """The merchant that retains its funds must not pad the triage queue."""
    con, peers, profiles = engine
    res = run("Flag high-risk customers", con, peers, profiles, settings)

    flagged = {f.node for f in res.findings}
    assert C1 in flagged
    assert M1 not in flagged
    assert res.execution.cost.excluded >= 1


def test_named_entity_still_answers_even_when_benign(engine, settings):
    con, peers, profiles = engine
    res = run(f"Is {M1} suspicious?", con, peers, profiles, settings)

    assert [f.node for f in res.findings] == [M1]
    assert res.findings[0].winning_kind == "benign"
    assert res.findings[0].escalation == "monitor"


def test_case_is_the_top_finding(engine, settings):
    """Backward compatibility: the single-case response shape still holds."""
    con, peers, profiles = engine
    res = run(f"Is {C1} suspicious?", con, peers, profiles, settings)
    assert res.case is not None
    assert res.case.seed == res.findings[0].node
    assert res.case.risk == res.findings[0].risk


def test_findings_are_deterministic(engine, settings):
    con, peers, profiles = engine
    a = run("Flag high-risk customers", con, peers, profiles, settings)
    b = run("Flag high-risk customers", con, peers, profiles, settings)
    assert [(f.node, f.risk, f.tier) for f in a.findings] == \
           [(f.node, f.risk, f.tier) for f in b.findings]


def test_screener_is_bounded_and_needs_no_database(engine):
    _, _, profiles = engine
    pool = screener.rank(profiles, parse("flag high risk customers"), max_candidates=2)
    assert len(pool.candidates) <= 2
    ranks = [c.rank for c in pool.candidates]
    assert ranks == sorted(ranks, reverse=True)
    assert screener.rank(profiles, None, 0).candidates == []


# --------------------------------------------------------------------------
# 5. Plan variation + telemetry
# --------------------------------------------------------------------------

def test_four_query_shapes_produce_four_distinct_plans():
    """The plan signature is the invoked tool set plus the traversal depth.

    Depth matters: an `explain` and a `trace` question on the same entity select the same
    tools (graph_motif is already on the smurfing route) but traverse different distances,
    so the executed plan genuinely differs.
    """
    shapes = [
        "Flag high-risk customers",                 # detect, broad, smurfing
        f"Explain why {C1} is suspicious",           # explain, entity-scoped, depth 1
        f"Trace the network around {C1}",            # trace, entity-scoped, depth 2
        "Find structuring patterns",                 # structuring route
    ]
    signatures = set()
    for query in shapes:
        p = plan(parse(query))
        signatures.add((frozenset(p.run), p.trace_depth))
    assert len(signatures) == len(shapes), "each query shape must yield its own plan"


def test_scoring_route_is_unchanged_for_each_typology():
    """The anchors depend on exactly these scoring tools running. Do not widen them."""
    scoring = {t.id for t in ROSTER if t.scoring}
    smurf = set(plan_for(parse(f"Is {C1} suspicious?"))[0]) & scoring
    struct = set(plan_for(parse(f"Find structuring for {C1}"))[0]) & scoring
    assert smurf == {"peer_comparison", "rapid_pass_through", "graph_motif", "benign_signals"}
    assert struct == {"peer_comparison", "near_threshold"}


def test_plan_trace_covers_every_roster_tool_exactly_once(engine, settings):
    con, peers, profiles = engine
    res = run("Flag high-risk customers", con, peers, profiles, settings)

    seen = [e.tool for e in res.plan_trace]
    assert sorted(seen) == sorted(t.id for t in ROSTER)
    for entry in res.plan_trace:
        assert entry.status in {"ran", "skipped", "failed"}
        assert entry.reason, "every decision must carry a reason, including declines"
        assert entry.duration_ms >= 0.0
        if entry.status == "skipped":
            assert entry.duration_ms == 0.0
        if entry.status == "ran":
            assert entry.duration_ms > 0.0


def test_execution_summary_echoes_the_request(engine, settings):
    con, peers, profiles = engine
    query = f"Explain why {C1} is suspicious"
    res = run(query, con, peers, profiles, settings)

    ex = res.execution
    assert ex.query == query
    assert ex.intent == res.spec.intent
    assert ex.typology == res.spec.typology
    assert ex.entities == [C1]
    assert ex.filters_note == "no filter applied"
    assert ex.cost.max_investigations == settings.max_investigations


def test_eda_is_declined_for_an_entity_scoped_question():
    p = plan(parse(f"Explain why {C1} is suspicious"))
    assert not p.selected("eda_profile")
    reason = next(d.reason for d in p.decisions if d.tool == "eda_profile")
    assert "named account" in reason


def test_unrecognized_typology_falls_back_and_says_so():
    spec = parse("look for something odd")
    spec = spec.model_copy(update={"typology": "not-a-typology"})
    p = plan(spec)
    assert not p.typology_recognized
    assert p.notes and "unrecognized" in p.notes[0]


def test_roundtrips_per_candidate_are_bounded(engine, settings):
    con, peers, profiles = engine
    res = run("Flag high-risk customers", con, peers, profiles, settings)
    assert 0 < res.execution.cost.roundtrips_max_per_candidate <= \
        settings.max_roundtrips_per_candidate


# --------------------------------------------------------------------------
# 6. Charts
# --------------------------------------------------------------------------

def _numbers(obj) -> set[float]:
    found: set[float] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        return {round(float(obj), 2)}
    if isinstance(obj, dict):
        for v in obj.values():
            found |= _numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found |= _numbers(v)
    return found


def test_charts_are_produced_and_every_number_traces_to_the_run(engine, settings):
    con, peers, profiles = engine
    res = run("Flag high-risk customers", con, peers, profiles, settings)
    chart_set = res.charts

    assert chart_set is not None
    dumped = chart_set.model_dump()
    assert len(dumped) == 6
    assert dumped["risk_contribution"]["available"]
    assert dumped["findings_table"]["rows"]

    # Every chart number must already exist somewhere in the run result.
    source = _numbers([f.model_dump() for f in res.findings])
    source |= _numbers(res.eda.model_dump() if res.eda else {})
    source |= _numbers([a for a in res.audit.alternatives])
    source |= {round(e.contribution, 2) for e in chart_set.risk_contribution.entries}
    source |= {round(e.score, 2) for e in chart_set.counterfactual.entries}
    # Structural counts: the size of a list the run already returned.
    for finding in res.findings:
        source.add(float(len(finding.evidence)))
        for record in finding.evidence:
            source.add(float(len(record.transactions)))

    for name in ("findings_table", "evidence_table", "risk_contribution", "counterfactual"):
        for value in _numbers(dumped[name]):
            assert value in source, f"{name} invented the number {value}"


def test_charts_degrade_per_payload_without_erroring():
    empty = charts.build([], contributions={}, counterfactuals=[], scores=[], eda=None)
    dumped = empty.model_dump()
    assert len(dumped) == 6
    for name, payload in dumped.items():
        assert payload["available"] is False
        assert payload["reason"], f"{name} must say why it is unavailable"


def test_no_findings_reports_a_reason_and_no_score(engine, settings):
    con, peers, profiles = engine
    res = run("Flag high-risk cheque customers", con, peers, profiles, settings)
    if not res.findings:
        assert res.no_findings_reason
        assert res.case is None
        assert res.charts is not None            # the trace and charts survive
        assert res.plan_trace                    # the analyst still sees what was searched
