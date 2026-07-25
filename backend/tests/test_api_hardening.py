"""Tests for the five demo-blocking fixes (B1-B5)."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from nexus.api.app import create_app
from nexus.api.state import EngineState, state_from_parts
from nexus.config import Settings
from nexus.duel import is_indeterminate, score_all, verdict
from nexus.hypotheses import load_hypotheses
from nexus.ingest import load_transactions
from nexus.peers import PeerModel
from nexus.profiles import build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
C1 = "0500|C1"


def _state():
    con, _, _ = load_transactions(FIXTURES / "case_Trans.csv", Settings())
    profiles = build_profiles(con)
    return state_from_parts(SimpleNamespace(con=con, n_transactions=len(profiles)),
                            profiles, PeerModel(profiles, k=3))


def _client(state=None, warm=False):
    return TestClient(create_app(state=state or _state(), warm=warm))


# ---------- B1: CORS ----------

def test_cors_preflight_allowed():
    c = _client()
    r = c.options("/investigate", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in {"*", "http://localhost:3000"}


def test_cors_header_on_real_request():
    c = _client()
    r = c.post("/investigate", json={"query": f"trace ring at {C1}"},
               headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") is not None


# ---------- B2 / M4: warmup + readiness ----------

def test_health_reports_ready_state():
    body = _client().get("/health").json()
    assert body["status"] == "ready"
    assert body["data_loaded"] is True
    assert body["accounts"] > 0
    assert "llm_enabled" in body and "anomaly_model" in body


def test_health_reports_warming_and_investigate_returns_503():
    engine = EngineState()          # never warmed
    c = TestClient(create_app(state=engine, warm=False))
    h = c.get("/health").json()
    assert h["status"] == "warming" and h["data_loaded"] is False

    r = c.post("/investigate", json={"query": f"trace ring at {C1}"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "WARMING_UP"


def test_background_warmup_reaches_ready():
    engine = EngineState(root=FIXTURES)
    engine.settings = Settings(variant="HI-Small")
    engine.warm()                   # synchronous for the test
    assert engine.status == "ready", engine.error
    assert engine.stats()["transactions"] == 10


# ---------- B3: indeterminate + 404 ----------

def test_no_evidence_is_indeterminate_not_suspicious():
    scores = score_all(load_hypotheses("smurfing"), [])   # zero evidence
    assert is_indeterminate(scores) is True
    top, kind = verdict(scores)
    assert kind == "indeterminate"                        # NOT "suspicious"


def test_unknown_account_returns_404():
    r = _client().post("/investigate", json={"query": "trace ring at 9999|NOPE"})
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert body["error"]["detail"]["node"] == "9999|NOPE"


def test_known_account_still_works():
    r = _client().post("/investigate", json={"query": f"trace ring at {C1}"})
    assert r.status_code == 200
    assert r.json()["case"]["winning_kind"] == "suspicious"


# ---------- B4: error envelope ----------

def test_validation_error_uses_envelope():
    r = _client().post("/investigate", json={"nope": 1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_blank_query_rejected():
    r = _client().post("/investigate", json={"query": "   "})
    assert r.status_code in {400, 422}
    assert "error" in r.json()


def test_error_envelope_shape_is_consistent():
    for payload, expected in [({"nope": 1}, 422),
                              ({"query": "trace ring at 9999|NOPE"}, 404)]:
        body = _client().post("/investigate", json=payload).json()
        assert set(body) == {"error"}
        assert {"code", "message"} <= set(body["error"])


# ---------- B5: concurrency ----------

def test_concurrent_requests_are_serialized_safely():
    client = _client()
    results: list[int] = []
    errors: list[str] = []

    def hit():
        try:
            r = client.post("/investigate", json={"query": f"trace ring at {C1}"})
            results.append(r.status_code)
        except Exception as exc:            # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert results == [200] * 8


def test_state_exposes_lock():
    assert isinstance(_state().lock, type(threading.Lock()))
