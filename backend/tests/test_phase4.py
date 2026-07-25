"""Phase 4 verification — agentic orchestration end-to-end (deterministic, no LLM)."""

from __future__ import annotations

from pathlib import Path

from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.intent import parse
from nexus.orchestrator import run
from nexus.peers import PeerModel
from nexus.profiles import build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"


def _prepare():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    profiles = build_profiles(con)
    return con, PeerModel(profiles, k=3), profiles


def test_intent_parser():
    spec = parse("Find and trace the smurfing ring at 0500|C1 in cash deposits")
    assert spec.typology == "smurfing"
    assert "0500|C1" in spec.entities
    assert "trace" in spec.intent and "detect" in spec.intent
    assert spec.filters.get("payment_format") == "Cash"
    assert spec.trace_depth == 2

    s2 = parse("Explain the structuring at 0500|C1")
    assert s2.typology == "structuring"
    assert "explain" in s2.intent


def test_plan_is_per_query_not_fixed():
    con, peers, profiles = _prepare()
    smurf = run("trace the ring at 0500|C1", con, peers, profiles)
    struct = run("explain structuring at 0500|C1", con, peers, profiles)

    # smurfing plan runs pass-through/graph, skips near_threshold ...
    assert "near_threshold" in {t for t, _ in smurf.tools_skipped}
    assert "rapid_pass_through" in smurf.tools_run
    # ... structuring plan does the opposite -> proof it's per-query, not fixed.
    assert "near_threshold" in struct.tools_run
    assert "rapid_pass_through" in {t for t, _ in struct.tools_skipped}


def test_orchestration_suspicious_ring_validated():
    con, peers, profiles = _prepare()
    res = run("find and trace the smurfing ring at 0500|C1", con, peers, profiles)
    assert res.case.winning_kind == "suspicious"
    assert res.case.escalation in {"review", "report"}
    # Proof-carrying: every number in the narrative traces to the ledger.
    assert res.validated is True
    assert res.unsupported == []
    # Audit captured the plan + alternatives.
    assert res.audit.tools_run and res.audit.alternatives
    assert res.audit.winning_hypothesis == "H1"


def test_narrative_zero_unsupported_on_benign():
    con, peers, profiles = _prepare()
    res = run("explain 0900|M1", con, peers, profiles)   # benign merchant
    assert res.validated is True
    assert res.unsupported == []
