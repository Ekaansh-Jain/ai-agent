"""Anchor pins — the safety net every later change in this feature is measured against.

Hermetic by construction: fixture CSVs under `tests/fixtures/` only, no network, nothing
read from `data/raw/`, LLM already forced off by `conftest.py`.

Three pinned anchors (design D2 — 53.18 is a *real* HI-Small ring number, not a fixture
number, and is handled separately as a gated integration anchor):

  * Phase 2 fixture evidence set through the Risk_Engine .... 86.65
  * `ring_Trans.csv`, hub `0500|C1`, full `investigate()` ... 56.00
  * `case_Trans.csv`, hub `0500|C1`, full `investigate()` ... 45.54

Plus the pinned counterfactual sequence for the Phase 2 fixture (count, order, labels,
scores). Every failure message names the anchor, the expected value and the observed value.

Requirements: 1.1, 1.2, 1.3, 1.7
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from nexus.casebuilder import investigate
from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.peers import PeerModel
from nexus.profiles import build_profiles
from nexus.risk import counterfactuals, risk_score

# Reuse the Phase 2 worked-example evidence set rather than duplicating it, so the anchor
# can never drift away from the fixture the rest of the suite scores.
from test_phase2 import _ring_ledger as phase2_ledger

FIXTURES = Path(__file__).parent / "fixtures"
HUB = "0500|C1"
TOL = 0.01

# ---------------------------------------------------------------------------
# Pinned anchor values (measured on this repository; see design D2)
# ---------------------------------------------------------------------------

ANCHOR_PHASE2_FIXTURE = 86.65
ANCHOR_RING_FIXTURE = 56.00
ANCHOR_CASE_FIXTURE = 45.54

ANCHOR_PHASE2_COUNTERFACTUALS: tuple[tuple[str, float], ...] = (
    ("full", 86.65),
    ("-peer_deviation", 70.25),
    ("-flow_through", 63.90),
    ("-network_convergence", 64.15),
    ("-temporal_coordination", 70.65),
    ("-typology_rule", 77.65),
    ("-flow_through-network_convergence", 41.40),
)


def _fail(anchor: str, expected, observed, detail: str = "") -> str:
    """Anchor failure message: which anchor, what was expected, what was observed."""
    msg = (
        f"ANCHOR DEVIATED: {anchor} — expected {expected} (± {TOL}), "
        f"observed {observed}"
    )
    return f"{msg}. {detail}" if detail else msg


@lru_cache(maxsize=None)
def _investigate_fixture(csv_name: str, hub: str):
    """Same setup pattern as test_phase3a / test_phase3b: ingest -> profiles -> peers."""
    con, _, _ = load_transactions(FIXTURES / csv_name, Settings())
    peers = PeerModel(build_profiles(con), k=3)
    return investigate(con, peers, hub)


# ---------------------------------------------------------------------------
# Anchor examples
# ---------------------------------------------------------------------------


def test_anchor_phase2_fixture_risk_is_86_65():
    """Anchor 1 — Phase 2 fixture evidence set through the Risk_Engine."""
    result = risk_score(phase2_ledger().records)
    assert abs(result.score - ANCHOR_PHASE2_FIXTURE) < TOL, _fail(
        "phase2-fixture risk", ANCHOR_PHASE2_FIXTURE, result.score
    )
    assert result.tier == "high", _fail("phase2-fixture tier", "high", result.tier)
    assert result.escalation == "report", _fail(
        "phase2-fixture escalation", "report", result.escalation
    )


def test_anchor_ring_fixture_hub_risk_is_56_00():
    """Anchor 2 — `ring_Trans.csv` hub through the full `investigate()` path."""
    case = _investigate_fixture("ring_Trans.csv", HUB)
    assert abs(case.risk - ANCHOR_RING_FIXTURE) < TOL, _fail(
        f"ring_Trans.csv {HUB} risk", ANCHOR_RING_FIXTURE, case.risk,
        "design D2: the fixture anchor is 56.00; 53.18 is a real HI-Small ring number.",
    )
    assert case.winning_kind == "suspicious", _fail(
        f"ring_Trans.csv {HUB} winning_kind", "suspicious", case.winning_kind
    )
    assert case.tier == "medium", _fail(
        f"ring_Trans.csv {HUB} tier", "medium", case.tier
    )
    assert case.escalation == "review", _fail(
        f"ring_Trans.csv {HUB} escalation", "review", case.escalation
    )


def test_anchor_case_fixture_hub_risk_is_45_54():
    """Anchor 3 — `case_Trans.csv` hub through the full `investigate()` path."""
    case = _investigate_fixture("case_Trans.csv", HUB)
    assert abs(case.risk - ANCHOR_CASE_FIXTURE) < TOL, _fail(
        f"case_Trans.csv {HUB} risk", ANCHOR_CASE_FIXTURE, case.risk
    )
    assert case.winning_kind == "suspicious", _fail(
        f"case_Trans.csv {HUB} winning_kind", "suspicious", case.winning_kind
    )
    assert case.tier == "medium", _fail(
        f"case_Trans.csv {HUB} tier", "medium", case.tier
    )
    assert case.escalation == "review", _fail(
        f"case_Trans.csv {HUB} escalation", "review", case.escalation
    )


def test_anchor_phase2_counterfactual_sequence_is_pinned():
    """Anchor 4 — counterfactual entry count, order, labels and scores are all pinned."""
    observed = counterfactuals(phase2_ledger().records)

    assert len(observed) == len(ANCHOR_PHASE2_COUNTERFACTUALS), _fail(
        "phase2-fixture counterfactual entry count",
        len(ANCHOR_PHASE2_COUNTERFACTUALS),
        len(observed),
        f"observed sequence: {observed}",
    )

    observed_labels = [label for label, _ in observed]
    expected_labels = [label for label, _ in ANCHOR_PHASE2_COUNTERFACTUALS]
    assert observed_labels == expected_labels, _fail(
        "phase2-fixture counterfactual label order", expected_labels, observed_labels
    )

    for position, ((exp_label, exp_score), (obs_label, obs_score)) in enumerate(
        zip(ANCHOR_PHASE2_COUNTERFACTUALS, observed)
    ):
        assert abs(obs_score - exp_score) < TOL, _fail(
            f"phase2-fixture counterfactual[{position}] '{exp_label}'",
            exp_score,
            obs_score,
            f"observed label at this position: '{obs_label}'",
        )


# ---------------------------------------------------------------------------
# Property tests (tasks 1.2 and 1.3 add Property 1 and Property 2 below)
# ---------------------------------------------------------------------------
