# NEXUS-AML

### *An AI agent that **investigates** money laundering, rather than merely detecting it*

> **Thesis:** NEXUS-AML runs a controlled investigation. For every suspicious signal it forms competing **suspicious and benign hypotheses**, gathers only the **minimal evidence** needed to distinguish them, compresses noisy alerts into a few **network-level cases**, and produces a **challengeable, proof-backed** recommendation for a human investigator.

---

## What problem does this solve?

Banks are legally required to run **Anti-Money Laundering (AML)** programs. Their existing rule-based systems fail in two ways:

- **Too many false positives** — 90–95% of alerts are innocent, drowning compliance analysts in manual review.
- **Easily evaded** — criminals simply structure transactions *just under* the rules.

NEXUS-AML behaves like a **junior compliance investigator**: it understands a natural-language request, builds a *per-query* plan (not a fixed pipeline), tests guilty **and** innocent theories, logs verifiable evidence, scores risk transparently, expands a single flag into a whole network case, and recommends **monitor / review / report** — with every claim traceable to a real transaction.

## Why it's different (not just another detector)

| Generic AML detector | NEXUS-AML |
|---|---|
| Outputs one anomaly score | Runs an **investigation** with competing hypotheses |
| Flags everything anomalous | **Rules out innocence** to kill false positives |
| Runs every tool, every time | **Selectively** invokes only the tools a query needs |
| Ranks isolated transactions | **Compresses** alerts into network cases |
| LLM may hallucinate reasons | **Proof-carrying** explanations (0% unsupported claims) |
| Black-box score | **Decomposable** score + counterfactual ("what changed the decision?") |

## The signature innovations

- **Hypothesis Duel** — weighs guilty *and* innocent theories.
- **Minimal-Evidence Investigation** — selective tool use as a measurable efficiency metric.
- **Alert-to-Case Compression** — dozens of alerts → a handful of rich cases.
- **Proof-Carrying Conclusions** — an evidence ledger + validator.
- **Counterfactual Panel** — proves corroboration, not one threshold.
- **Challenge Mode** — the human can contest a conclusion; NEXUS answers with tested evidence.

## Data

Primary dataset: **IBM AMLworld (HI-Small)** — synthetic, labeled transactions with account-to-account edges and labeled laundering patterns. Labels enable real precision / recall / false-positive metrics. **SAML-D** is the backup for richer structuring typologies.

## Tech stack (planned)

Python 3.11 · **LangGraph** (agent loop) · bounded LLM (parse / narrate / challenge — never scores) · **DuckDB + pandas** · **NetworkX** · **scikit-learn** · custom additive **Risk Engine** · **Pydantic** · **FastAPI** · **React + Vite + Cytoscape.js** · **SQLite**.

## The full design

The complete design — problem, banking background, architecture, every component in detail, the scoring arithmetic, a full worked example, the user interface, rubric compliance, evaluation plan, tech stack, build order, demo script, and glossary — is in:

### 👉 [NEXUS-AML-Design-Document.md](./NEXUS-AML-Design-Document.md)

## Project artifacts

| File | Purpose |
|---|---|
| [NEXUS-AML-Design-Document.md](./NEXUS-AML-Design-Document.md) | The full design (problem → architecture → components → UI → evaluation) |
| [DATA-SPEC.md](./DATA-SPEC.md) | **Canonical** dataset spec (IBM AMLworld) — schema, patterns format, integrity rules. Point the build agent here. |
| [DECISIONS.md](./DECISIONS.md) | Running log of durable decisions and their rationale |
| [prompts/phase1-data.md](./prompts/phase1-data.md) | Trimmed Phase 1 build prompt (data layer only) |
| [prompts/prompt-template.md](./prompts/prompt-template.md) | Reusable per-phase prompt template |

> Where `DATA-SPEC.md` and the design document differ on data details, **`DATA-SPEC.md` wins.**

---

> *NEXUS does not automate suspicion. It automates the collection and verification of evidence — so investigators make faster, more defensible decisions.*
