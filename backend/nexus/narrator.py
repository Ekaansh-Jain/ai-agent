"""Narrator: turn a finished Case into analyst-readable prose.

Two paths: a deterministic template (always valid) and an LLM narrator (via llm.py). The
orchestrator tries the LLM, runs the claim validator, and falls back to the template if the
LLM produces any unsupported number. Either way the output is fact-checked.
"""

from __future__ import annotations

from .hypotheses import load_hypotheses
from .schemas import Case, InvestigationSpec


def _label(typology: str, hyp_id: str) -> str:
    for h in load_hypotheses(typology):
        if h.id == hyp_id:
            return h.label
    return hyp_id


def facts(case: Case, spec: InvestigationSpec) -> str:
    """Compact, number-bearing fact sheet the LLM must stay within."""
    lines = [
        f"query_intent: {', '.join(spec.intent)}",
        f"typology: {spec.typology}",
        f"seed_account: {case.seed}",
        f"winning_explanation: {_label(spec.typology, case.winning_hypothesis)} "
        f"({case.winning_kind})",
        f"confidence: {case.confidence}",
        f"risk: {case.risk}/100 -> {case.tier} -> {case.escalation}"
        + (" (INDETERMINATE: do not assert a conclusion)"
           if case.winning_kind == "indeterminate" else ""),
        f"network_members: {len(case.members)} "
        f"(mule_feeders={len(case.feeders_included)}, beneficiaries={len(case.beneficiaries)})",
    ]
    for r in case.evidence:
        lines.append(f"evidence[{r.family}]: {r.claim} | strength {r.strength}")
    for node, reason in case.excluded:
        lines.append(f"excluded[{node}]: {reason}")
    return "\n".join(lines)


def narrate_template(case: Case, spec: InvestigationSpec) -> str:
    lines = [f"Query intent: {', '.join(spec.intent)} under the {spec.typology} typology."]
    if case.winning_kind == "indeterminate":
        lines.append(f"Seed account {case.seed}: no explanation is supported by the evidence.")
    else:
        label = _label(spec.typology, case.winning_hypothesis)
        lines.append(
            f"Seed account {case.seed}: winning explanation '{label}' "
            f"({case.winning_kind}), confidence {case.confidence}."
        )
    if case.winning_kind == "suspicious":
        lines.append(f"Risk {case.risk}/100 -> {case.tier} -> recommend {case.escalation}.")
    elif case.winning_kind == "benign":
        lines.append(
            f"A benign explanation prevailed -> recommend {case.escalation} "
            f"(risk {case.risk})."
        )
    else:
        lines.append(
            "The available evidence does not separate the competing explanations; "
            f"no conclusion is asserted -> recommend {case.escalation}."
        )
    lines.append("Evidence:")
    for r in case.evidence:
        lines.append(f"  - [{r.family}] {r.claim} (strength {r.strength}).")
    if len(case.members) > 1:
        lines.append(
            f"Network case: {len(case.members)} accounts — "
            f"{len(case.feeders_included)} mule feeders, "
            f"{len(case.beneficiaries)} beneficiary(ies)."
        )
    for node, reason in case.excluded:
        lines.append(f"Excluded {node} (benign, not scooped up): {reason}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-finding explanation (Phase 6). A ranked findings list needs one short reason
# per row, tied to the query intent — not one narrative for the whole run.
# ---------------------------------------------------------------------------

_MAX_EXPLANATION = 400


def finding_facts(case: Case, spec: InvestigationSpec) -> str:
    """Fact sheet for ONE finding. Same contract as `facts`, scoped to one case."""
    return facts(case, spec)


def explain_finding(case: Case, spec: InvestigationSpec) -> str:
    """A <=400 character reason for one flagged account.

    Always names the risk tier, the escalation, and at least one intent term from the
    query. Names the typology and winning hypothesis for suspicious/benign verdicts, and
    deliberately asserts NO conclusion when the duel was indeterminate.
    """
    intent = ", ".join(spec.intent) or "detect"
    if case.winning_kind == "indeterminate":
        text = (
            f"[{intent}] {case.seed}: the available evidence does not separate the "
            f"competing explanations, so no conclusion is asserted. "
            f"Risk tier {case.tier} -> recommend {case.escalation}."
        )
    elif case.winning_kind == "benign":
        label = _label(spec.typology, case.winning_hypothesis)
        text = (
            f"[{intent}] {case.seed}: a benign explanation prevailed under the "
            f"{spec.typology} typology ('{label}', confidence {case.confidence}). "
            f"Risk {case.risk}/100, tier {case.tier} -> recommend monitor."
        )
    else:
        label = _label(spec.typology, case.winning_hypothesis)
        drivers = ", ".join(
            r.family for r in case.evidence if r.direction == "high"
        )[:120]
        text = (
            f"[{intent}] {case.seed}: {spec.typology} — '{label}' wins "
            f"(confidence {case.confidence}). Risk {case.risk}/100, tier {case.tier} "
            f"-> recommend {case.escalation}."
        )
        if drivers:
            text += f" Driven by: {drivers}."
    return text[:_MAX_EXPLANATION]
