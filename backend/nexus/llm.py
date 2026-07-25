"""Gemini LLM edge — intent parsing (in) and narration (out) ONLY.

The LLM never scores or decides risk (steering non-negotiable). If no key is configured, or
the SDK/network is unavailable, every function returns None and callers fall back to the
deterministic path. Narration output is still checked by the claim validator, so the LLM
cannot inject an unsupported number.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional
    pass


def use_llm() -> bool:
    return (
        os.getenv("NEXUS_USE_LLM", "1") != "0"
        and bool(os.getenv("GEMINI_API_KEY"))
    )


@lru_cache(maxsize=1)
def _model():
    if not use_llm():
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        return genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
    except Exception:
        return None


def _generate(prompt: str) -> str | None:
    model = _model()
    if model is None:
        return None
    try:
        resp = model.generate_content(prompt)
        return (resp.text or "").strip()
    except Exception:
        return None


def intent_llm(query: str) -> dict | None:
    """Extract InvestigationSpec fields as JSON. Returns None on any failure -> fallback."""
    prompt = (
        "You are the intent parser for an AML investigation system. Extract fields from the "
        "analyst query and reply with ONLY minified JSON, no prose. Schema:\n"
        '{"intent":[subset of "detect","trace","explain","monitor"],'
        '"typology":"smurfing"|"structuring",'
        '"filters":{"payment_format"?:str,"month"?:str},'
        '"entities":[account ids like "0500|C1"],"trace_depth":1|2}\n'
        f"Query: {query!r}"
    )
    raw = _generate(prompt)
    if not raw:
        return None
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        data = json.loads(raw)
        data["query"] = query
        return data
    except Exception:
        return None


def narrate_llm(facts: str) -> str | None:
    """Write an analyst summary using ONLY the provided facts. None -> template fallback."""
    prompt = (
        "You are a compliance analyst writing a short, neutral case summary. Use ONLY the "
        "facts below. Do NOT invent numbers, accounts, or claims — every figure must appear "
        "in the facts. 4-7 sentences, plain professional English.\n\n"
        f"FACTS:\n{facts}"
    )
    return _generate(prompt)
