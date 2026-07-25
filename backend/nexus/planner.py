"""Planner: choose which tools run for a spec, and record which are skipped and why.

This is the visible proof of a per-query plan (not a fixed pipeline). Selection is derived
from THREE spec inputs only — `intent`, `typology`, and whether the query named an entity —
so the same spec always yields the same plan, and two different query shapes yield visibly
different plans.

Locked-anchor guarantee: the SCORING tool set for each existing typology route is exactly
what it was before this module grew (smurfing -> peer/pass-through/motif/benign;
structuring -> peer/near-threshold). The nodes added here (`eda_profile`, `feature_builder`,
`candidate_screener`, `isolation_forest`) emit only NEUTRAL families, so they cannot move a
score no matter when they run.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .schemas import InvestigationSpec

MIN_TRACE_DEPTH = 1
MAX_TRACE_DEPTH = 3


class RosterTool(BaseModel):
    """One capability the agent can decide to invoke."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str = Field(..., min_length=1, max_length=60)
    purpose: str
    needs_features: bool = False    # consumes the engineered account feature table
    traverses_graph: bool = False   # builds a transaction subgraph
    scoring: bool = False           # its families carry hypothesis/risk weight


# Declaration order is the order skipped entries appear in the trace.
ROSTER: tuple[RosterTool, ...] = (
    RosterTool(id="eda_profile", label="Data Profile (EDA)",
               purpose="what does the requested slice actually look like?"),
    RosterTool(id="feature_builder", label="Feature Builder",
               purpose="engineered account features for models and rules"),
    RosterTool(id="candidate_screener", label="Candidate Screener",
               purpose="rank accounts worth a full investigation", needs_features=True),
    RosterTool(id="peer_comparison", label="Peer Comparison",
               purpose="is the account unusual vs behavioral peers?", scoring=True),
    RosterTool(id="rapid_pass_through", label="Rapid Pass-Through",
               purpose="did money arrive and leave quickly?", scoring=True),
    RosterTool(id="graph_motif", label="Graph Motif / Path Trace",
               purpose="fan-in / convergence shape", traverses_graph=True, scoring=True),
    RosterTool(id="benign_signals", label="Benign Signals",
               purpose="retention / recurrence / stability (benign discriminators)",
               scoring=True),
    RosterTool(id="near_threshold", label="Near-Threshold Deposits",
               purpose="count of near-threshold deposits (structuring)", scoring=True),
    RosterTool(id="isolation_forest", label="Isolation Forest (neutral)",
               purpose="unsupervised anomaly score, informational only",
               needs_features=True),
)

BY_ID: dict[str, RosterTool] = {t.id: t for t in ROSTER}

# Kept verbatim from the pre-feature implementation. Do not reorder or extend: the anchors
# depend on exactly these scoring tools running for these typologies.
_ROUTES: dict[str, list[str]] = {
    "smurfing": ["peer_comparison", "rapid_pass_through", "graph_motif", "benign_signals"],
    "structuring": ["peer_comparison", "near_threshold"],
}
DEFAULT_TYPOLOGY = "smurfing"

# Legacy one-line purposes, still used for the skip reason wording.
_ALL = {t.id: t.purpose for t in ROSTER}


class PlanDecision(BaseModel):
    tool: str
    label: str
    selected: bool
    reason: str = Field(..., min_length=1, max_length=200)
    selecting_intents: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    decisions: list[PlanDecision]
    typology: str
    typology_recognized: bool = True
    trace_depth: int = MIN_TRACE_DEPTH
    broad: bool = False             # no named entity -> screen candidates
    notes: list[str] = Field(default_factory=list)

    @property
    def run(self) -> list[str]:
        return [d.tool for d in self.decisions if d.selected]

    @property
    def skipped(self) -> list[tuple[str, str]]:
        return [(d.tool, d.reason) for d in self.decisions if not d.selected]

    def selected(self, tool: str) -> bool:
        return tool in set(self.run)


def _route(typology: str) -> tuple[list[str], bool]:
    if typology in _ROUTES:
        return list(_ROUTES[typology]), True
    return list(_ROUTES[DEFAULT_TYPOLOGY]), False


def plan(spec: InvestigationSpec, anomaly_available: bool = True) -> Plan:
    """Build the full roster decision set for one spec."""
    intents = list(spec.intent) or ["detect"]
    entities = list(spec.entities)
    has_entity = bool(entities)
    broad = not has_entity

    route, recognized = _route(spec.typology)
    depth = max(MIN_TRACE_DEPTH, min(MAX_TRACE_DEPTH, int(spec.trace_depth or 1)))

    notes: list[str] = []
    if not recognized:
        notes.append(
            f"typology '{spec.typology}' is unrecognized; substituted the "
            f"'{DEFAULT_TYPOLOGY}' route"
        )

    # tool id -> intent values that voted to select it.
    votes: dict[str, list[str]] = {}

    def vote(tool: str, intent: str) -> None:
        votes.setdefault(tool, [])
        if intent not in votes[tool]:
            votes[tool].append(intent)

    for intent in intents:
        # Every intent runs the typology's scoring route; that is what keeps the
        # anchors stable regardless of how the query was phrased.
        for tool in route:
            vote(tool, intent)

        if intent == "trace":
            for t in ROSTER:
                if t.traverses_graph:
                    vote(t.id, intent)
        if intent == "detect" and broad:
            vote("candidate_screener", intent)
        # Profiling earns its place on a broad sweep; on an entity-scoped question the
        # analyst already knows the slice, so it is wasted work.
        if broad:
            vote("eda_profile", intent)

    # The neutral anomaly family runs wherever the consolidation route runs.
    if recognized and spec.typology != "structuring" or not recognized:
        if anomaly_available:
            votes.setdefault("isolation_forest", ["detect"])

    # Feature engineering is required iff something selected consumes the feature table.
    needs = [t for t in ROSTER if t.needs_features and t.id in votes]
    if needs:
        votes.setdefault("feature_builder", sorted({i for t in needs for i in votes[t.id]}))

    decisions: list[PlanDecision] = []
    for tool in ROSTER:
        selecting = votes.get(tool.id, [])
        if selecting:
            reason = _select_reason(tool, selecting, spec, broad, depth, needs)
        else:
            reason = _skip_reason(tool, spec, has_entity, entities, anomaly_available)
        decisions.append(PlanDecision(
            tool=tool.id, label=tool.label, selected=bool(selecting),
            reason=reason[:200], selecting_intents=selecting,
        ))

    # Feature builder must precede everything that consumes its table.
    decisions.sort(key=lambda d: (0 if d.tool == "feature_builder" else 1))
    order = {t.id: i for i, t in enumerate(ROSTER)}
    decisions.sort(key=lambda d: (0 if d.tool == "feature_builder" else 1, order[d.tool]))

    return Plan(
        decisions=decisions, typology=spec.typology, typology_recognized=recognized,
        trace_depth=depth, broad=broad, notes=notes,
    )


def _select_reason(
    tool: RosterTool, selecting: list[str], spec: InvestigationSpec,
    broad: bool, depth: int, needs: list[RosterTool],
) -> str:
    joined = "/".join(selecting)
    if tool.id == "eda_profile":
        return f"broad '{joined}' query: profile the slice before flagging anything"
    if tool.id == "feature_builder":
        consumers = ", ".join(t.id for t in needs)
        return f"account-level features required by {consumers}"
    if tool.id == "candidate_screener":
        return f"'{joined}' names no entity, so candidates must be ranked first"
    if tool.id == "isolation_forest":
        return "neutral ML second opinion (out of risk weights)"
    if tool.traverses_graph and "trace" in selecting:
        return f"'trace' intent: graph traversal bounded to depth {depth}"
    return f"informative for '{spec.typology}' under '{joined}' ({tool.purpose})"


def _skip_reason(
    tool: RosterTool, spec: InvestigationSpec, has_entity: bool,
    entities: list[str], anomaly_available: bool,
) -> str:
    if tool.id == "eda_profile" and has_entity:
        return (
            f"query is scoped to {len(entities)} named account(s); dataset-wide "
            "profiling would not change the answer"
        )
    if tool.id == "candidate_screener" and has_entity:
        return f"{len(entities)} account(s) named directly, so no ranking is needed"
    if tool.id == "feature_builder":
        return "no selected tool consumes account-level features"
    if tool.id == "isolation_forest" and not anomaly_available:
        return "no trained model artifact in models/ (run scripts/train_model.py)"
    return f"not informative for '{spec.typology}' ({tool.purpose})"


def plan_for(spec: InvestigationSpec) -> tuple[list[str], list[tuple[str, str]]]:
    """Backward-compatible view: (tools_to_run, [(skipped_tool, reason), ...])."""
    p = plan(spec)
    return p.run, p.skipped
