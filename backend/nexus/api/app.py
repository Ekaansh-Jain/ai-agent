"""FastAPI app: POST a natural-language query, get a validated, fact-checked case.

Demo-hardened:
  B1 CORS so a browser frontend can call it.
  B2/M4 eager background warmup + /health readiness reporting.
  B3 404 for unknown accounts (no fabricated cases).
  B4 structured error envelope + guarded endpoint.
  B5 lock around the shared DuckDB connection.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .. import llm
from ..config import Settings
from ..orchestrator import RunResult, run
from . import errors
from .errors import ApiError
from .state import EngineState

# Comma-separated origins; "*" allows any (fine for a local demo).
ALLOWED_ORIGINS = os.getenv("NEXUS_CORS_ORIGINS", "*")


class Query(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


def _serialize(res: RunResult) -> dict:
    """The eight original keys keep their meaning and type; everything else is additive.

    `case` is the highest-ranked finding, so a query that names an account returns exactly
    what it returned before this feature. It is null only when nothing was flagged, in which
    case `no_findings_reason` says why and the plan trace still shows what was searched.
    """
    return {
        # --- original contract, unchanged ---
        "spec": res.spec.model_dump(),
        "plan": {"run": res.tools_run, "skipped": res.tools_skipped},
        "case": res.case.model_dump() if res.case is not None else None,
        "narrative": res.narrative,
        "validated": res.validated,
        "unsupported": res.unsupported,
        "sources": {"intent": res.intent_source, "narrator": res.narrator_source},
        "audit": res.audit.model_dump(),
        # --- additive ---
        "findings": [f.model_dump() for f in res.findings],
        "no_findings_reason": res.no_findings_reason,
        "plan_trace": [e.model_dump() for e in res.plan_trace],
        "execution": res.execution.model_dump() if res.execution else None,
        "charts": res.charts.model_dump() if res.charts else None,
        "eda": res.eda.model_dump() if res.eda else None,
        "feature_manifest": (
            res.feature_manifest.model_dump() if res.feature_manifest else None
        ),
    }


def create_app(
    state: EngineState | None = None,
    warm: bool = True,
    settings: Settings | None = None,
    root: Path | None = None,
) -> FastAPI:
    engine = state or EngineState(settings=settings or Settings(), root=root)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # B2: start loading immediately, in the background, so /health responds at once.
        if warm and not engine.ready:
            engine.warm_in_background()
        yield

    app = FastAPI(title="NEXUS-AML", version="0.1.0", lifespan=lifespan)

    # B1: without this a browser blocks every cross-origin call.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if ALLOWED_ORIGINS == "*" else
        [o.strip() for o in ALLOWED_ORIGINS.split(",")],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    errors.install(app)  # B4
    app.state.engine = engine

    def _require_ready() -> EngineState:
        if engine.status == "error":
            raise ApiError(503, "ENGINE_ERROR",
                           "The engine failed to load its dataset.", engine.error)
        if not engine.ready:
            raise ApiError(503, "WARMING_UP",
                           "Dataset is still loading. Poll /health until status is 'ready'.")
        return engine

    @app.get("/health")
    def health() -> dict:
        s = engine.stats()
        return {
            "status": engine.status,
            "data_loaded": engine.ready,
            "error": engine.error,
            "variant": engine.settings.variant,
            "transactions": s["transactions"],
            "accounts": s["accounts"],
            "llm_enabled": llm.use_llm(),
            "llm_model": os.getenv("GEMINI_MODEL") if llm.use_llm() else None,
            "anomaly_model": _anomaly_present(),
            # Capability presence indicators for the UI's status strip.
            "eda_tool": True,
            "feature_builder": True,
        }

    @app.get("/roster")
    def roster() -> dict:
        """The real tool roster the planner can select from — what the plan rail renders."""
        return {"tools": _tool_roster()}

    @app.post("/investigate")
    def investigate_ep(q: Query) -> dict:
        eng = _require_ready()

        query = q.query.strip()
        if not query:
            raise ApiError(400, "EMPTY_QUERY", "Query must not be blank.")

        # B3: reject unknown accounts instead of inventing a case for them.
        from ..intent import parse as parse_intent
        for node in parse_intent(query).entities:
            if not eng.has_node(node):
                raise ApiError(404, "ACCOUNT_NOT_FOUND",
                               f"Account {node} does not exist in "
                               f"{eng.settings.variant}.", {"node": node})

        try:
            # B5: serialize access to the shared DuckDB connection.
            with eng.lock:
                res = run(query, eng.ds.con, eng.peers, eng.profiles, eng.settings)
        except ApiError:
            raise
        except ValueError as exc:
            # e.g. broad query with nothing to rank
            raise ApiError(400, "UNRESOLVABLE_QUERY", str(exc)) from exc
        except Exception as exc:  # B4: never surface a stack trace
            raise ApiError(500, "INVESTIGATION_FAILED",
                           "The investigation could not be completed.",
                           {"type": type(exc).__name__}) from exc

        return _serialize(res)

    return app


def _anomaly_present() -> bool:
    from .. import anomaly
    return anomaly.MODEL_PATH.is_file()


def _tool_roster() -> list[dict]:
    from ..planner import ROSTER
    return [
        {"tool": t.id, "label": t.label, "purpose": t.purpose, "scoring": t.scoring}
        for t in ROSTER
    ]


# For `uvicorn nexus.api.app:app` — warms up in the background on startup.
app = create_app()
