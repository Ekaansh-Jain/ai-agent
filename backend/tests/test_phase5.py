"""Phase 5 verification — eval metrics, LLM fallback, and the FastAPI service."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from nexus.api.app import create_app
from nexus.api.state import state_from_parts
from nexus.config import Settings
from nexus.eval.metrics import confusion
from nexus.ingest import load_transactions
from nexus.orchestrator import run
from nexus.peers import PeerModel
from nexus.profiles import build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"


def _state():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    profiles = build_profiles(con)
    peers = PeerModel(profiles, k=3)
    return state_from_parts(SimpleNamespace(con=con, n_transactions=len(profiles)),
                            profiles, peers)


def _client(state):
    return TestClient(create_app(state=state, warm=False))


def test_metrics_confusion():
    c = confusion([True, True, False, False], [True, False, True, False])
    assert (c.tp, c.fp, c.fn, c.tn) == (1, 1, 1, 1)
    assert c.precision == 0.5 and c.recall == 0.5 and c.f1 == 0.5


def test_llm_fallback_without_key(monkeypatch):
    # No GEMINI_API_KEY in the test env -> deterministic path.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = _state()
    res = run(f"find and trace the smurfing ring at {C1}", s.ds.con, s.peers,
              s.profiles, s.settings)
    assert res.intent_source == "deterministic"
    assert res.narrator_source == "template"
    assert res.validated is True


def test_api_investigate_endpoint(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = _client(_state())

    assert client.get("/health").json()["status"] == "ready"

    r = client.post("/investigate", json={"query": f"trace the smurfing ring at {C1}"})
    assert r.status_code == 200
    body = r.json()
    assert body["case"]["winning_kind"] == "suspicious"
    assert body["case"]["escalation"] in {"review", "report"}
    assert body["validated"] is True and body["unsupported"] == []
    assert "near_threshold" in [t for t, _ in body["plan"]["skipped"]]
    assert body["case"]["evidence"]           # proof-carrying evidence returned


def test_api_structuring_plan_differs(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = _client(_state())
    body = client.post("/investigate", json={"query": f"look for structuring at {C1}"}).json()
    assert "near_threshold" in body["plan"]["run"]
    assert body["case"]["escalation"] == "report"
