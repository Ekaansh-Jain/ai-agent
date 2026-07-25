# Product

NEXUS-AML: an agentic AI that **investigates** money laundering instead of merely scoring it. For each suspicious signal it weighs competing suspicious vs. benign hypotheses, gathers only the evidence needed to separate them, and produces a proof-backed monitor/review/report recommendation.

## Non-negotiables
- **LLM never decides risk.** It only parses intent (in) and narrates the ledger (out). Scoring is deterministic.
- **Every claim is proof-carrying.** No number appears unless it traces to real transaction IDs. Target: 0% unsupported claims.
- **Additive scoring.** Risk = weighted sum of independent evidence families, so counterfactuals are trivial and honest.
- **Selective, not exhaustive.** Run the smallest sufficient set of tools.
- **Ground truth is held out.** `*_Patterns.txt` and labels are for evaluation ONLY — never passed to the agent/detectors at inference time.

## Scope (built deep, not wide)
- Typology A: Structuring (temporal / entity-level).
- Typology B: Smurfing with rapid consolidation (graph / network).
- Layering shown only as a bounded path-trace feature.

Full reference design lives in `read.md` — consult on demand, do not force-load.
