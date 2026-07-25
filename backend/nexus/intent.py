"""Intent parser: natural-language query -> validated InvestigationSpec.

Deterministic keyword-based parser by default (no LLM, fully testable). An LLM can be
plugged in later via `parse(query, llm=...)`; whatever it returns is still validated into
the same Pydantic spec, so the rest of the pipeline is unaffected.
"""

from __future__ import annotations

import re

from .schemas import InvestigationSpec

_MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
# account node token like '0500|C1' or '024144|80D9B69A0'
_NODE_RE = re.compile(r"\b(\d{2,7}\|[0-9A-Za-z]+)\b")


def _typology(q: str) -> str:
    if "structur" in q:
        return "structuring"
    if any(w in q for w in ("smurf", "fan-in", "fan in", "consolidat", "ring", "mule")):
        return "smurfing"
    return "smurfing"


def _intent(q: str) -> list[str]:
    out = []
    if any(w in q for w in ("trace", "follow", "network", "downstream")):
        out.append("trace")
    if any(w in q for w in ("explain", "why", "justify")):
        out.append("explain")
    if any(w in q for w in ("monitor", "watch")):
        out.append("monitor")
    if any(w in q for w in ("find", "detect", "flag", "suspicious", "analyse", "analyze", "scan")):
        out.append("detect")
    return out or ["detect"]


def _filters(q: str) -> dict[str, str]:
    f: dict[str, str] = {}
    for fmt in ("cash", "wire", "ach", "cheque", "credit card", "bitcoin"):
        if fmt in q:
            f["payment_format"] = "Credit Card" if fmt == "credit card" else fmt.title()
            break
    for m in _MONTHS:
        if m in q:
            f["month"] = m.title()
            break
    return f


def parse(query: str, llm=None) -> InvestigationSpec:
    """Parse a query into an InvestigationSpec. `llm` is an optional callable that returns
    a dict of spec fields; its output is validated the same way."""
    if llm is not None:
        data = llm(query)
        data.setdefault("query", query)
        return InvestigationSpec(**data)

    q = query.lower()
    intent = _intent(q)
    entities = _NODE_RE.findall(query)
    trace_depth = 2 if "trace" in intent or "network" in q else 1
    return InvestigationSpec(
        query=query,
        intent=intent,
        typology=_typology(q),
        filters=_filters(q),
        entities=entities,
        trace_depth=trace_depth,
    )
