"""Claim validator: every number in the narrative must trace to the evidence ledger / case.

Extracts numeric tokens from the narrative and checks each against the numbers that appear
in the evidence claims, record values/strengths, the risk score, and structural counts.
Any number that can't be traced is reported as an unsupported claim (target: zero).
"""

from __future__ import annotations

import re

from .schemas import Case

_NUM = re.compile(r"\d+(?:\.\d+)?")
_NODE = re.compile(r"\d+\|[0-9A-Za-z]+")  # account identifiers, not quantitative claims


def _numbers(text: str) -> list[str]:
    # Normalize by stripping trailing zeros so '0.90' == '0.9'.
    out = []
    for tok in _NUM.findall(text):
        f = float(tok)
        out.append(f"{f:.4f}".rstrip("0").rstrip("."))
    return out


def _allowed(case: Case) -> set[str]:
    allowed: set[str] = set()
    for r in case.evidence:
        allowed.update(_numbers(r.claim))
        allowed.update(_numbers(str(r.value)))
        allowed.update(_numbers(str(r.strength)))
    allowed.update(_numbers(str(case.risk)))
    # System-generated exclusion reasons are trusted case facts (e.g. "out-degree 5").
    for _, reason in case.excluded:
        allowed.update(_numbers(reason))
    # Structural counts the narrator may cite.
    for count in (len(case.members), len(case.feeders_included),
                  len(case.beneficiaries), len(case.excluded)):
        allowed.add(str(count))
    allowed.add("100")  # risk is out of 100
    return allowed


def validate(narrative: str, case: Case) -> tuple[bool, list[str]]:
    """Return (ok, unsupported_numbers). Account identifiers are stripped first — they are
    references, not quantitative claims."""
    allowed = _allowed(case)
    scrubbed = _NODE.sub("", narrative)
    unsupported = [n for n in _numbers(scrubbed) if n not in allowed]
    return (len(unsupported) == 0), unsupported
