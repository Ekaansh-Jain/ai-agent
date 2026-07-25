"""Tests for the IsolationForest anomaly model + its neutral integration."""

from __future__ import annotations

from pathlib import Path

from nexus import anomaly
from nexus.casebuilder import investigate
from nexus.config import Settings
from nexus.eval.baseline import generic_ai_baseline, rules_baseline
from nexus.ingest import load_transactions
from nexus.peers import PeerModel
from nexus.profiles import build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"


def _prepare():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    profiles = build_profiles(con)
    return con, PeerModel(profiles, k=3), profiles


def test_anomaly_scores_in_unit_interval():
    _, _, profiles = _prepare()
    model = anomaly.train(profiles, contamination=0.05)
    scores = model.score_frame(profiles)
    assert scores.between(0.0, 1.0).all()
    assert 0.0 <= model.score_row(profiles.loc[C1]) <= 1.0


def test_anomaly_is_neutral_to_the_verdict():
    con, peers, profiles = _prepare()
    model = anomaly.train(profiles, contamination=0.05)

    without = investigate(con, peers, C1, "smurfing")
    with_ml = investigate(con, peers, C1, "smurfing", anomaly_model=model, profiles=profiles)

    # ML adds an evidence record but must NOT change the verdict or risk (not in
    # fingerprints or risk weights -> anchors preserved).
    assert "anomaly" in {r.family for r in with_ml.evidence}
    assert "anomaly" not in {r.family for r in without.evidence}
    assert with_ml.winning_hypothesis == without.winning_hypothesis
    assert with_ml.winning_kind == without.winning_kind
    assert with_ml.risk == without.risk
    assert with_ml.escalation == without.escalation


def test_baselines_return_metrics():
    _, _, profiles = _prepare()
    model = anomaly.train(profiles, contamination=0.05)
    nodes = list(profiles.index)
    indeg = profiles["in_degree"].to_dict()
    labels = [n == C1 for n in nodes]  # pretend only C1 is positive (smoke)

    r = rules_baseline(nodes, indeg, labels, k=5)
    g = generic_ai_baseline(nodes, model, profiles, labels, tau=0.5)
    assert 0.0 <= r.precision <= 1.0 and 0.0 <= r.recall <= 1.0
    assert 0.0 <= g.precision <= 1.0 and 0.0 <= g.recall <= 1.0
