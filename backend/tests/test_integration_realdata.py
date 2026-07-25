"""Real-data integration suite: run the whole engine over cases pulled from ground truth.

Asserts the INVARIANTS that must always hold (0% unsupported, valid escalation,
proof-carrying evidence, correct per-query routing, determinism) and reports the honest
metrics (recall/precision/F1) without asserting precision superiority.

Gated: set NEXUS_RUN_INTEGRATION=1 and have data/raw populated. Forces the deterministic
path (NEXUS_USE_LLM=0) for reproducibility and to avoid burning LLM quota.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nexus.config import Settings, paths_for

_DATA = paths_for("HI-Small").trans
pytestmark = pytest.mark.skipif(
    not (os.getenv("NEXUS_RUN_INTEGRATION") and _DATA.is_file()),
    reason="integration test: set NEXUS_RUN_INTEGRATION=1 and provide data/raw/HI-Small_*",
)

CASES_FILE = Path(__file__).parent / "cases" / "real_cases.json"
ANCHORS_FILE = Path(__file__).parent / "cases" / "anchors.json"
ANCHOR_TOL = 0.01


def _query(case: dict) -> str:
    if case["typology"] == "structuring":
        return f"look for structuring at {case['node']}"
    return f"find and trace the smurfing ring at {case['node']}"


@pytest.fixture(scope="module")
def engine():
    os.environ["NEXUS_USE_LLM"] = "0"  # deterministic + reproducible
    from nexus.ingest import load_dataset
    from nexus.orchestrator import run
    from nexus.peers import PeerModel
    from nexus.profiles import build_profiles

    ds = load_dataset(Settings(variant="HI-Small"))
    profiles = build_profiles(ds.con)
    peers = PeerModel(profiles)
    cases = json.loads(CASES_FILE.read_text())
    results = [(c, run(_query(c), ds.con, peers, profiles)) for c in cases]
    return {"con": ds.con, "peers": peers, "profiles": profiles, "results": results}


def test_zero_unsupported_claims_everywhere(engine):
    """The core guarantee: no narrative cites a number absent from the ledger."""
    offenders = [(c["node"], r.unsupported) for c, r in engine["results"] if not r.validated]
    assert offenders == []


def test_every_case_is_well_formed(engine):
    for c, r in engine["results"]:
        assert r.case.escalation in {"monitor", "review", "report"}
        assert r.case.winning_kind in {"suspicious", "benign"}
        assert r.case.evidence, f"no evidence for {c['node']}"
        # Proof-carrying: at least one cited real transaction id.
        assert any(rec.transactions for rec in r.case.evidence), c["node"]


def test_intent_and_plan_route_by_typology(engine):
    for c, r in engine["results"]:
        if c["typology"] == "structuring":
            assert r.spec.typology == "structuring"
            assert "near_threshold" in r.tools_run
            assert "rapid_pass_through" in {t for t, _ in r.tools_skipped}
        else:
            assert r.spec.typology == "smurfing"
            assert "near_threshold" in {t for t, _ in r.tools_skipped}
            assert "rapid_pass_through" in r.tools_run


def test_determinism(engine):
    from nexus.orchestrator import run
    con, peers, profiles = engine["con"], engine["peers"], engine["profiles"]
    c, _ = engine["results"][0]
    a = run(_query(c), con, peers, profiles)
    b = run(_query(c), con, peers, profiles)
    assert a.case.escalation == b.case.escalation
    assert a.case.winning_hypothesis == b.case.winning_hypothesis
    assert a.narrative == b.narrative  # deterministic path


def test_structuring_true_positive_escalates(engine):
    struct = [(c, r) for c, r in engine["results"] if c["group"] == "structuring"]
    assert struct, "expected a structuring case"
    for c, r in struct:
        assert r.case.escalation in {"review", "report"}, c["node"]


def test_metrics_report(engine):
    """Report honest confusion/precision/recall. Assert only sanity + the 0% invariant."""
    from nexus.eval.metrics import confusion

    preds, labels = [], []
    for c, r in engine["results"]:
        preds.append(r.case.escalation in {"review", "report"})
        labels.append(c["label"] == "laundering")
    conf = confusion(preds, labels)

    print("\n" + "=" * 56)
    print("REAL-DATA INTEGRATION METRICS (n =", len(preds), ")")
    print("=" * 56)
    print(f"  {conf.as_dict()}")
    print("  NOTE: NEXUS ~parity with a fan-in threshold on AMLworld;")
    print("  differentiator is explainability + benign duel, not this number.")

    unsupported_total = sum(0 if r.validated else 1 for _, r in engine["results"])
    assert unsupported_total == 0            # hard invariant
    assert conf.tp + conf.fn > 0             # sanity: positives exist
    assert conf.recall > 0.0                 # sanity: we catch at least some


# ---------------------------------------------------------------------------
# Real-data anchor guard (tests/cases/anchors.json)
#
# The 53.18 anchor was recorded in Phase 3a against a real HI-Small ring whose identity
# was never written down. It is node 0048309|811C599A0 (GATHER-SCATTER), which scores
# 53.18 under both the current full `investigate()` path and the reconstructed Phase 3a
# three-tool subset. `anchors.json` is now the single anchor record.
#
# This test ASSERTS the re-measured value against what anchors.json records (± 0.01) —
# that is the forward-looking regression guard. The *historical* 53.18 value is only
# REPORTED (before/after via print), never asserted, because its provenance predates the
# repo's record-keeping.
# ---------------------------------------------------------------------------


def _measure_anchor(anchor: dict, engine) -> float:
    """Re-measure one real-data anchor under the scoring path it records."""
    from nexus.casebuilder import investigate
    from nexus.ledger import EvidenceLedger
    from nexus.risk import risk_score
    from nexus.tools import graph_motif, peer_comparison, rapid_pass_through

    con, peers = engine["con"], engine["peers"]
    node, typology = anchor["node"], anchor.get("typology", "smurfing")
    path = anchor["scoring_path"]

    if path == "investigate_full":
        return investigate(con, peers, node, typology).risk
    if path == "phase3a_three_tool":
        # Phase 3a conditions: three weighted families only, no benign_signals.
        ledger = EvidenceLedger()
        peer_comparison.run(con, peers, node, ledger)
        rapid_pass_through.run(con, node, ledger)
        graph_motif.run(con, node, ledger)
        return risk_score(ledger.records, typology).score
    raise AssertionError(f"unknown scoring_path {path!r} in anchors.json")


def test_real_data_anchors_are_stable_across_peer_model_rebuilds(engine):
    """Rebuild profiles + PeerModel three times; every anchor must land on one value.

    This is the assertion that was flaky. `build_profiles` ran an unordered FULL OUTER JOIN,
    so DuckDB returned the 515,088 accounts in a different row order per call; that order
    reached MiniBatchKMeans (order-sensitive despite a fixed random_state), moving the
    per-cluster median/MAD, the peer_deviation z and therefore the risk. Node
    0048309|811C599A0 was observed at 53.18 / 52.86 / 52.08 across rebuilds in one process.
    Existing determinism tests reused ONE PeerModel, so none of them could see it.
    """
    from nexus.peers import PeerModel
    from nexus.profiles import build_profiles

    record = json.loads(ANCHORS_FILE.read_text())
    real = [a for a in record["anchors"] if a["kind"] == "real_data"]
    con = engine["con"]

    rebuilds = []
    orders = []
    for _ in range(3):
        profiles = build_profiles(con)
        orders.append(list(profiles.index))
        rebuilt = {"con": con, "peers": PeerModel(profiles)}
        rebuilds.append({a["id"]: round(_measure_anchor(a, rebuilt), 2) for a in real})

    for order in orders[1:]:
        assert order == orders[0], (
            "build_profiles returned the same accounts in a different row order across "
            "rebuilds; the peer clustering input must be deterministic"
        )

    print("\n" + "=" * 56)
    print("ANCHOR STABILITY ACROSS 3 PROFILE + PEER-MODEL REBUILDS")
    print("=" * 56)
    for anchor in real:
        values = [r[anchor["id"]] for r in rebuilds]
        print(f"  {anchor['id']}: {values}")
        assert len(set(values)) == 1, (
            f"ANCHOR UNSTABLE: {anchor['id']} (node {anchor['node']}) scored {values} "
            f"across three rebuilds of profiles + PeerModel — rebuild determinism is broken"
        )
        assert abs(values[0] - anchor["measured"]) < ANCHOR_TOL, (
            f"ANCHOR DEVIATED under rebuild: {anchor['id']} expected "
            f"{anchor['measured']} (± {ANCHOR_TOL}), observed {values[0]}"
        )


def test_real_data_anchors_match_recorded_values(engine):
    """Re-measure every real-data anchor in anchors.json and pin it (± 0.01)."""
    record = json.loads(ANCHORS_FILE.read_text())
    real = [a for a in record["anchors"] if a["kind"] == "real_data"]
    assert real, "anchors.json records no real-data anchor"

    print("\n" + "=" * 56)
    print("REAL-DATA ANCHOR RE-MEASUREMENT")
    print("=" * 56)

    for anchor in real:
        assert anchor["dataset"] == "HI-Small", anchor["id"]
        observed = _measure_anchor(anchor, engine)
        recorded = anchor["measured"]
        print(
            f"  {anchor['id']}: node {anchor['node']} via {anchor['scoring_path']} "
            f"-> recorded {recorded:.2f}, measured {observed:.2f}"
        )

        # Historical value: report a divergence, never fail on it (unverified provenance).
        historical = anchor.get("historical_expected")
        if historical is not None:
            delta = observed - historical
            if abs(delta) >= ANCHOR_TOL:
                print(
                    f"    HISTORICAL DIVERGENCE (reported, not failed) {anchor['id']}: "
                    f"before {historical:.2f} -> after {observed:.2f} "
                    f"(delta {delta:+.2f}). Provenance of the historical value is "
                    f"unverified; anchors.json 'measured' is the guard."
                )
            else:
                print(
                    f"    historical {historical:.2f} reproduced "
                    f"(delta {delta:+.2f})"
                )

        # The actual regression guard.
        assert abs(observed - recorded) < ANCHOR_TOL, (
            f"ANCHOR DEVIATED: {anchor['id']} (node {anchor['node']}, "
            f"{anchor['scoring_path']}) — expected {recorded} (± {ANCHOR_TOL}), "
            f"observed {round(observed, 2)}. Update tests/cases/anchors.json only with a "
            f"recorded before/after justification."
        )
