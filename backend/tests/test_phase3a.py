"""Phase 3a verification: the three tools on a controlled ring fixture.

Confirms each tool emits a sensible EvidenceRecord and the locked Phase 2 duel/risk pick
the suspicious hypothesis. Uses a small in-repo fixture (not the 5M-row dataset).
"""

from __future__ import annotations

from pathlib import Path

from nexus.config import Settings
from nexus.duel import confidence, score_all, winner
from nexus.hypotheses import load_hypotheses
from nexus.ingest import load_transactions
from nexus.ledger import EvidenceLedger
from nexus.peers import PeerModel
from nexus.profiles import build_profiles
from nexus.risk import risk_score
from nexus.tools import graph_motif, peer_comparison, rapid_pass_through

FIXTURES = Path(__file__).parent / "fixtures"
HUB = "0500|C1"


def _prepare():
    con, _, _ = load_transactions(FIXTURES / "ring_Trans.csv", Settings())
    profiles = build_profiles(con)
    peers = PeerModel(profiles, k=3)
    return con, peers


def test_tools_emit_expected_evidence():
    con, peers = _prepare()
    lg = EvidenceLedger()
    peer_comparison.run(con, peers, HUB, lg)
    rapid_pass_through.run(con, HUB, lg)
    graph_motif.run(con, HUB, lg)

    fams = {r.family: r for r in lg.records}
    assert set(fams) == {"peer_deviation", "flow_through", "network_convergence"}

    # Flow-through: 51,400 of 56,500 left within the window -> ratio ~0.91,
    # strength = |0.91-0.5|/0.5 ~ 0.82, high.
    assert fams["flow_through"].direction == "high"
    assert fams["flow_through"].strength > 0.8

    # Network: 6 feeders, 1 exit -> strong convergence.
    assert fams["network_convergence"].direction == "high"
    assert fams["network_convergence"].strength > 0.8

    # Peer deviation: hub in_degree=6 vs single-degree background -> high.
    assert fams["peer_deviation"].direction == "high"

    # Every record carries transaction proof.
    assert all(r.transactions for r in lg.records)


def test_duel_and_risk_on_ring():
    con, peers = _prepare()
    lg = EvidenceLedger()
    peer_comparison.run(con, peers, HUB, lg)
    rapid_pass_through.run(con, HUB, lg)
    graph_motif.run(con, HUB, lg)

    scores = score_all(load_hypotheses("smurfing"), lg.records)
    top = winner(scores)
    assert top.id == "H1" and top.kind == "suspicious"
    assert top.band in {"moderate", "strong"}
    assert confidence(scores) in {"strong", "high"}

    risk = risk_score(lg.records)
    # 3-family ceiling is 70; a clean ring should reach the escalation range.
    assert risk.score >= 40.0
    assert risk.escalation in {"review", "report"}
