"""Hypothesis library loader.

Loads curated fingerprints from library.yaml. Using a library (not free LLM generation)
keeps the system generalizable and defensible against "you hardcoded the demo".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ..schemas import FamilyExpectation, Hypothesis

_LIBRARY_PATH = Path(__file__).with_name("library.yaml")


@lru_cache(maxsize=1)
def _raw_library() -> dict:
    with _LIBRARY_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def available_typologies() -> list[str]:
    return list(_raw_library().keys())


def load_hypotheses(typology: str) -> list[Hypothesis]:
    """Return the competing hypotheses (suspicious + benign) for a typology."""
    lib = _raw_library()
    if typology not in lib:
        raise KeyError(
            f"No hypotheses for typology {typology!r}; have {list(lib)}"
        )
    out: list[Hypothesis] = []
    for h in lib[typology]:
        fingerprint = {
            fam: FamilyExpectation(**spec) for fam, spec in h["fingerprint"].items()
        }
        out.append(
            Hypothesis(
                id=h["id"], label=h["label"], kind=h["kind"], fingerprint=fingerprint
            )
        )
    return out
