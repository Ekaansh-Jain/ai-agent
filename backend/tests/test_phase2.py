"""Phase 2 verification: the duel + risk engine against the design's worked example.

Uses hand-made fixture evidence (no real tools yet) to prove the math:
  suspicious ring -> H1 wins strongly, risk ~87, counterfactual ~87 -> ~64 -> ~41
  benign lookalike -> benign hypothesis wins, risk stays low.
"""

from __future__ import annotations

from nexus.duel import confidence, score_all, winner
from nexus.hypotheses import load_hypotheses
from nexus.ledger import EvidenceLedger
from nexus.risk import counterfactuals, risk_score
from nexus.schemas import EvidenceRecord


def _rec(ledger: EvidenceLedger, family, value, strength, direction, txs) -> EvidenceRecord:
    return ledger.add(EvidenceRecord(
        claim_id=ledger.mint_id(), family=family, claim=f"{family}={value}",
        calculation="fixture", value=value, direction=direction,
        strength=strength, transactions=txs,
    ))


def _ring_ledger() -> EvidenceLedger:
    lg = EvidenceLedger()
    _rec(lg, "peer_deviation", 4.1, 0.82, "high", [1, 2, 3, 4, 5, 6])
    _rec(lg, "flow_through", 0.91, 0.91, "high", [1, 2, 3, 4, 5, 6, 7])
    _rec(lg, "network_convergence", 0.95, 0.90, "high", [1, 2, 3, 4, 5, 6, 7])
    _rec(lg, "temporal_coordination", 0.82, 0.80, "high", [1, 2, 3, 4, 5, 6])
    _rec(lg, "typology_rule", 0.90, 0.90, "high", [1, 2, 3, 4, 5, 6])
    return lg


def test_hypothesis_library_loads():
    hyps = load_hypotheses("smurfing")
    assert {h.id for h in hyps} == {"H1", "H2", "H3", "H4"}
    h1 = next(h for h in hyps if h.id == "H1")
    assert h1.kind == "suspicious"


def test_duel_suspicious_ring_wins():
    hyps = load_hypotheses("smurfing")
    scores = score_all(hyps, _ring_ledger().records)
    top = winner(scores)
    assert top.id == "H1" and top.kind == "suspicious"
    assert top.band == "strong"
    # Observed-family normalization preserves the design anchor (~0.864).
    assert abs(top.normalized - 0.864) < 0.01
    # No benign theory is positively supported by this evidence.
    benign = [s for s in scores if s.kind == "benign"]
    assert all(s.normalized <= 0.0 for s in benign)
    # Untested benign families sit neutral here, so margin -> strong (not high) until
    # benign_signals evidence is gathered in a real run.
    assert confidence(scores) in {"strong", "high"}


def test_risk_score_matches_worked_example():
    result = risk_score(_ring_ledger().records)
    assert 86.0 <= result.score <= 88.0
    assert result.tier == "high" and result.escalation == "report"


def test_counterfactual_shows_corroboration():
    cfs = dict(counterfactuals(_ring_ledger().records))
    assert 86.0 <= cfs["full"] <= 88.0
    # Removing the two strongest families collapses well below the "high" line.
    both_removed = [v for k, v in cfs.items() if k.count("-") == 2]
    assert both_removed and min(both_removed) < 45.0


def test_benign_lookalike_downgraded():
    lg = EvidenceLedger()
    _rec(lg, "flow_through", 0.08, 0.85, "low", [10, 11, 12])   # keeps its money
    _rec(lg, "peer_deviation", 0.5, 0.60, "low", [10, 11, 12])  # normal for peers
    hyps = load_hypotheses("smurfing")
    scores = score_all(hyps, lg.records)
    top = winner(scores)
    assert top.kind == "benign"                 # innocence wins
    assert risk_score(lg.records).score < 10.0  # nothing suspicious to sum
    assert risk_score(lg.records).escalation == "monitor"
