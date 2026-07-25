"""Phase 3c verification — minimal structuring + typology routing.

- structuring routing fires the near-threshold tool and escalates a threshold-shaped account
- a non-threshold-shaped account stays benign/monitor under the structuring lens
- the consolidation anchor (Phase 2 worked example = 86.65) is UNCHANGED by the typology
  profile addition (default profile == old weights)
"""

from __future__ import annotations

from pathlib import Path

from nexus.casebuilder import investigate
from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.ledger import EvidenceLedger
from nexus.peers import PeerModel
from nexus.profiles import build_profiles
from nexus.risk import risk_score
from nexus.schemas import EvidenceRecord

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"   # receives 6 deposits in [9000,10000) -> threshold-shaped
M1 = "0900|M1"   # ordinary merchant deposits, not near-threshold


def _prepare():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    peers = PeerModel(build_profiles(con), k=3)
    return con, peers


def test_structuring_routing_escalates():
    con, peers = _prepare()
    case = investigate(con, peers, C1, typology="structuring")
    assert case.winning_kind == "suspicious"
    assert case.escalation in {"review", "report"}
    families = {r.family for r in case.evidence}
    assert "typology_rule" in families          # near_threshold ran (routing worked)
    assert "flow_through" not in families        # smurfing-only tools did NOT run


def test_structuring_benign_when_not_threshold_shaped():
    con, peers = _prepare()
    case = investigate(con, peers, M1, typology="structuring")
    assert case.winning_kind == "benign"
    assert case.escalation == "monitor"


def test_consolidation_anchor_unchanged():
    lg = EvidenceLedger()
    for fam, s in [
        ("peer_deviation", 0.82), ("flow_through", 0.91), ("network_convergence", 0.90),
        ("temporal_coordination", 0.80), ("typology_rule", 0.90),
    ]:
        lg.add(EvidenceRecord(claim_id=lg.mint_id(), family=fam, claim="", calculation="",
                              value=s, direction="high", strength=s, transactions=[1]))
    # Default typology == consolidation profile == old weights.
    assert risk_score(lg.records).score == 86.65
    assert risk_score(lg.records, "smurfing").score == 86.65
