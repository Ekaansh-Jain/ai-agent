"""Phase 3b verification — the demo money shot.

1. Benign lookalike (high-volume merchant that RETAINS funds) is downgraded to monitor.
2. Ring expansion pulls in the mule feeders + beneficiary but EXCLUDES a connected but
   benign salary payer (high out-degree).
"""

from __future__ import annotations

from pathlib import Path

from nexus.casebuilder import investigate
from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.peers import PeerModel
from nexus.profiles import build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"          # smurfing collector (suspicious)
M1 = "0900|M1"          # benign merchant lookalike
E1 = "0801|E1"          # salary payer connected to C1 (benign, should be excluded)
FEEDERS = [f"050{i}|A{i}" for i in range(1, 7)]
B1 = "0600|B1"


def _prepare():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    peers = PeerModel(build_profiles(con), k=3)
    return con, peers


def test_benign_lookalike_downgraded_to_monitor():
    con, peers = _prepare()
    case = investigate(con, peers, M1)
    assert case.winning_kind == "benign"       # innocence wins the duel
    assert case.escalation == "monitor"        # duel gates the risk
    assert case.members == [M1]                # no ring expanded around a legit hub


def test_ring_expands_and_excludes_salary_payer():
    con, peers = _prepare()
    case = investigate(con, peers, C1)
    assert case.winning_kind == "suspicious"
    assert case.escalation in {"review", "report"}

    members = set(case.members)
    for feeder in FEEDERS:                     # mule feeders earn their flag
        assert feeder in members
    assert B1 in members                       # beneficiary traced

    excluded_nodes = {n for n, _ in case.excluded}
    assert E1 in excluded_nodes                # salary payer connected but excluded
    assert E1 not in members
