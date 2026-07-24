# NEXUS-AML — Complete Design Document

### *An AI agent that **investigates** money laundering, rather than merely detecting it*

> **Product thesis:** NEXUS-AML runs a controlled investigation. For every suspicious signal it forms competing **suspicious and benign hypotheses**, gathers only the **minimal evidence** needed to distinguish them, compresses noisy alerts into a few **network-level cases**, and produces a **challengeable, proof-backed** recommendation for a human investigator.

This document is written so that **someone encountering the project for the first time can understand it end to end** — from the real-world problem, through the banking concepts, to the solution, the architecture, and a line-by-line explanation of every component, the data, the scoring math, the user interface, and how we prove it works.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Part I — The Problem (in plain language)](#2-part-i--the-problem-in-plain-language)
3. [Part II — The Solution: NEXUS-AML](#3-part-ii--the-solution-nexus-aml)
4. [Part III — The Data](#4-part-iii--the-data)
5. [Part IV — System Architecture](#5-part-iv--system-architecture)
6. [Part V — The End-to-End Data Flow](#6-part-v--the-end-to-end-data-flow)
7. [Part VI — Every Component in Detail](#7-part-vi--every-component-in-detail)
8. [Part VII — How Hypotheses Score (the full arithmetic)](#8-part-vii--how-hypotheses-score-the-full-arithmetic)
9. [Part VIII — A Complete Worked Example](#9-part-viii--a-complete-worked-example)
10. [Part IX — The User Interface (Investigation Arena)](#10-part-ix--the-user-interface-investigation-arena)
11. [Part X — Rubric Compliance](#11-part-x--rubric-compliance)
12. [Part XI — Evaluation & Proof of Superiority](#12-part-xi--evaluation--proof-of-superiority)
13. [Part XII — Technology Stack](#13-part-xii--technology-stack)
14. [Part XIII — Build Order & Roadmap](#14-part-xiii--build-order--roadmap)
15. [Part XIV — Five-Minute Demo Script](#15-part-xiv--five-minute-demo-script)
16. [Part XV — Anticipated Judge Questions](#16-part-xv--anticipated-judge-questions)
17. [Glossary of Banking & Technical Terms](#17-glossary-of-banking--technical-terms)
18. [Appendix — Repository Structure](#18-appendix--repository-structure)

---

## 1. Executive Summary

Financial institutions are legally required to run **Anti-Money Laundering (AML)** programs. Their existing systems are mostly **rule-based** ("flag any deposit over $10,000"), which produces two chronic failures:

- **Too many false positives** — 90–95% of alerts are innocent, drowning compliance analysts in manual review.
- **Easily evaded** — criminals simply structure transactions *just under* the rules.

**NEXUS-AML** is an **agentic AI system** that behaves like a junior compliance investigator. Instead of emitting a single anomaly score, it:

1. **Understands** a natural-language request and builds a per-query execution plan (it is *not* a fixed pipeline).
2. **Forms competing theories** — both guilty ("this is structuring") and innocent ("this is a legitimate cash business").
3. **Selectively runs only the tools** needed to tell those theories apart.
4. **Logs every finding as verifiable evidence** pointing back to exact transactions.
5. **Scores risk** with a transparent, decomposable formula.
6. **Expands a single flag into the whole network case** (e.g., a smurfing ring) — safely.
7. **Writes a fact-checked, human-readable explanation** and recommends **monitor / review / report**.

The result is **fewer, richer, defensible cases** — a direct attack on the false-positive problem that costs banks the most.

**What makes it novel:** the **Hypothesis Duel** (weighing innocence, not just guilt), **proof-carrying evidence** (0% unsupported claims), **minimal-evidence investigation** (selective tool use as a measurable efficiency metric), **alert-to-case compression**, and an interactive **Challenge Mode** and **counterfactual** panel.

---

## 2. Part I — The Problem (in plain language)

### 2.1 What is money laundering?

**Money laundering** is taking "dirty" money earned from crime (drugs, fraud, corruption, trafficking) and passing it through the banking system so it emerges looking "clean" and legal — so the criminal can spend it without anyone asking where it came from.

It classically happens in **three stages**:

1. **Placement** — getting the dirty cash *into* the financial system (e.g., depositing it into bank accounts).
2. **Layering** — moving the money through many transactions and accounts to *hide the trail* and obscure its origin.
3. **Integration** — the money re-emerges looking legitimate (e.g., as "business profit" or a property sale).

> **Analogy:** like laundering physical dirty clothes — put them in (placement), spin them through many cycles (layering), pull out clean clothes (integration).

### 2.2 Why the bank must care (the regulatory mandate)

Banks are **legally required** to detect and report laundering. Regulators include **FinCEN** (US), **FATF** (global standards body), and national authorities. A bank that fails to catch laundering faces **fines in the hundreds of millions to billions**, plus reputational damage. Every large bank (including Société Générale) runs a large, expensive **compliance team** whose job is to monitor transactions and file reports on suspicious activity.

### 2.3 The specific techniques criminals use (the "typologies")

A **typology** is a known method/pattern of laundering. This project focuses on:

- **Structuring** — deliberately splitting a large sum into several smaller deposits that each stay *just below* a reporting threshold (e.g., $9,500 + $9,800 + $9,300 instead of one $28,600 deposit), to avoid triggering an automatic report.
- **Smurfing** — the same idea using **many people ("smurfs")** making many small deposits into one account, so no single person or transaction looks large.
- **Layering** — the "move it around to hide the trail" stage: money bounces through accounts A → B → C → D, often in loops, to obscure its origin.

Each typology leaves a **fingerprint in the transaction graph**: structuring/smurfing shows up as **fan-in** (many senders → one account); layering shows up as **long paths and cycles**.

### 2.4 Why today's systems fail

Current systems are largely **static rules**: `IF amount > $10,000 THEN flag`. This fails in two ways:

1. **False-positive fatigue** — simple rules flag huge numbers of innocent customers (a business owner, someone who sold a car, a wedding gift). Analysts must manually investigate each; the vast majority are nothing. This is the **#1 cost and pain** in AML.
2. **Trivially evaded** — if the rule is $10k, criminals deposit $9,500. Static rules are blind to *adaptive* behavior.

### 2.5 The problem statement (what we were asked to build)

Build an **AI-powered agent** that autonomously:

- Accepts a natural-language instruction (e.g., *"Analyse this dataset for suspicious activity"* or *"Flag high-risk customers"*).
- **Parses intent, filters, entities, and pattern types**, and **dynamically constructs an execution plan** — invoking only the tools needed (it must **not** be a fixed sequential pipeline).
- Performs EDA, feature engineering, anomaly detection, risk classification, and explanation.
- Returns: an execution summary, top suspicious transactions/customers, risk levels, explanations tied to the query and AML pattern, escalation recommendations, and supporting charts/tables.

NEXUS-AML meets every one of these requirements and layers genuine innovation on top (see [Part X](#11-part-x--rubric-compliance)).

---

## 3. Part II — The Solution: NEXUS-AML

### 3.1 The core reframe: investigate, not detect

The single idea behind everything:

> **An alert is not a number to compute; it is a question to be settled by evidence.**

So NEXUS is built like a **courtroom**, not a calculator:

| Courtroom role | NEXUS component |
|---|---|
| Prosecution vs. defense | Competing **hypotheses** (suspicious + benign) |
| Investigators who fetch facts | The **tools** |
| The case file | The **Evidence Ledger** |
| The sentencing formula | The **Risk Engine** (transparent, additive) |
| The clerk who writes the report | The **LLM narrator** (can describe the file, never invent facts) |

- A **detector** says: *"Isolation Forest score 0.87 — high risk."* Dead end.
- An **investigator** says: *"This looks like structuring. I tested whether it's a legitimate cash business and ruled it out because 91% of the money left within 6 hours. Here is my evidence. Recommend: report."*

### 3.2 Design principles (the non-negotiables)

1. **The LLM never decides risk.** It parses the request and turns a typed evidence ledger into prose. Scoring is deterministic. *(Governance moat.)*
2. **Every claim is proof-carrying.** No number appears unless it exists in the ledger and passes validation. Target: **0% unsupported claims**.
3. **Selective, not exhaustive.** Run the smallest sufficient set of tools; access the fewest data fields. *(Measurable efficiency + privacy.)*
4. **Additive scoring by design.** Risk = a weighted sum of independent evidence families, so **counterfactual ablation** ("what changed the decision?") is trivial and honest.
5. **Curated hypotheses, not free-form.** Hypotheses come from a typology→hypothesis library (encoded investigator playbooks) so the system generalizes and cannot be accused of per-demo hardcoding.
6. **One golden path, rehearsed.** A single flawless end-to-end story beats broad, half-working coverage.

### 3.3 What makes it novel (the differentiators)

- **Hypothesis Duel** — weighs guilty *and* innocent theories, so false positives are actively ruled out (not just "not selected").
- **Minimal-Evidence Investigation** — the agent seeks the smallest sufficient evidence; tool-use efficiency becomes a metric almost nobody else measures.
- **Alert-to-Case Compression** — consolidates dozens of scattered alerts into a handful of network cases.
- **Proof-Carrying Conclusions** — an evidence ledger + validator guarantees every statement is traceable to a transaction.
- **Counterfactual Panel** — shows that a flag rests on corroboration, not one threshold.
- **Challenge Mode** — the human can contest a conclusion; NEXUS answers with tested evidence, not free-form speculation.
- **Benign-Lookalike Downgrade** — the clearest possible demonstration of false-positive reduction.

### 3.4 Scope (deliberately narrow, built deep)

Two typologies, built thoroughly:

- **Typology A — Structuring** (temporal / entity-level intelligence).
- **Typology B — Smurfing with rapid consolidation** (graph / network intelligence).

Together they demonstrate *both* temporal and graph reasoning. Layering is shown as a bounded path-trace stretch feature, not a separately engineered pipeline.

**Explicitly out of scope:** multi-agent swarms, ten typologies, Kafka streaming, blockchain, federated learning, a production GNN, autonomous report filing, always-on generic dashboards, production authentication.

---

## 4. Part III — The Data

### 4.1 Primary dataset — IBM AMLworld

We use **IBM's AMLworld (HI-Small)** dataset ([GitHub: IBM/AML-Data](https://github.com/IBM/AML-Data), also on [Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)). It is synthetic (no real individuals), generated by a multi-agent virtual world, and — crucially — **labeled**.

**Transaction file columns:**

| Column | Meaning | How we use it |
|---|---|---|
| `Timestamp` | when the transaction occurred | temporal features, coordination windows, pass-through timing |
| `From Bank` + `Account` | sender | graph source node |
| `To Bank` + `Account.1` | receiver | graph sink node |
| `Amount Paid` / `Amount Received` (+ currencies) | value | amount features, near-threshold detection |
| `Payment Format` | Cash, Wire, ACH, Cheque, Credit Card, Bitcoin, Reinvestment | the "cash deposit" filter |
| `Is Laundering` | 0/1 ground-truth label | **enables real precision/recall/false-positive metrics** |
| *(separate patterns file)* | labeled laundering subgraphs (fan-in, fan-out, gather-scatter, cycle) | validates the graph tool against ground truth |

> **Why a labeled dataset matters:** it lets us compute genuine numbers ("we reduced false positives by X% at Y% recall"). Teams using unlabeled data cannot make that claim. Synthetic ground truth is also *complete* — unlike real data where most laundering is never discovered.

### 4.2 Two honest data constraints (and how we handle them)

1. **No customer demographics / segments / balances / KYC.** The data is account IDs and transactions only.
   → **Peer groups are derived behaviorally** by clustering accounts (see §7.6), not read from a "segment" field. This is actually more robust — behavior beats declared attributes.
2. **No baked-in statutory reporting threshold.** AMLworld amounts are not designed around a fixed $10k line.
   → Structuring's "near-threshold" detection uses a **configurable threshold plus our seeded structuring cases**. **SAML-D** ([Bournemouth, 12 features / 28 typologies](https://eprints.bournemouth.ac.uk/40982/)) is the backup for richer structuring variety.

Also: a query filter like *"retail accounts"* has no matching column, so demo queries use filters the data supports (`Payment Format = Cash`, date range, amount band).

### 4.3 The seeded "haystack"

We embed three constructs inside the larger real population so the agent is visibly *finding* them (not reading a script):

- A **structuring case** (one account, near-threshold deposits).
- A **smurfing ring** (many originators → collector → beneficiary).
- A **benign-lookalike merchant** — a legitimate account that *superficially* looks anomalous (high volume, many payers, occasional large withdrawals) but differs in the ways that matter (recurring counterparties, funds retained for operations, stable profile, no rapid convergence). This account is our most valuable demo asset: it proves false-positive reduction.

### 4.4 Entity note

AMLworld is **account-level** (no customer-to-account mapping), so the investigated **entity is an account**. This is stated plainly rather than pretending we have a customer graph.

---

## 5. Part IV — System Architecture

### 5.1 Layered architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — INTERFACE: Investigation Arena                                   │
│  NL query · Hypothesis panel · Network graph · Evidence ledger ·           │
│  Counterfactual · Challenge · Escalation · Charts (from EDA)               │
└───────────────────────────────┬──────────────────────────────────────────┘
                                 │  (query down, case up)
┌───────────────────────────────▼──────────────────────────────────────────┐
│  LAYER 4 — REASONING / ORCHESTRATION (the "mind")                          │
│  Intent Parser → Hypothesis Generator →                                    │
│      ┌── Investigation Loop:  Planner ⇄ Tools ⇄ Ledger ⇄ Duel ──┐          │
│      └───────────────────────────────────────────────────────────┘        │
│  → Risk Engine → Counterfactual → Network Expansion / Case Compressor      │
│  → Narrator → Claim Validator                                              │
└───────────────────────────────┬──────────────────────────────────────────┘
                                 │  (asks for facts / features)
┌───────────────────────────────▼──────────────────────────────────────────┐
│  LAYER 3 — ANALYTICS TOOLKIT (the "senses")  [rubric-labeled]              │
│  EDA · Feature Engineering · Anomaly Detection                             │
│    (IsolationForest + statistical + rules + graph) ·                       │
│  behaviour_baseline · peer_comparison · temporal_coordination ·            │
│  rapid_pass_through · graph_motif / path_trace                             │
└───────────────────────────────┬──────────────────────────────────────────┘
                                 │  (queries ONLY the needed fields)
┌───────────────────────────────▼──────────────────────────────────────────┐
│  LAYER 2 — DATA & FEATURE LAYER                                            │
│  Transaction store (DuckDB) · derived account profiles ·                   │
│  peer clusters · transaction graph (NetworkX)                              │
└───────────────────────────────┬──────────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────────┐
│  LAYER 1 — GOVERNANCE (cross-cutting, always on)                           │
│  Evidence Ledger (proof) · Audit log (every tool call + reasoning) ·       │
│  Disposition store (false-positive feedback hook)                          │
└────────────────────────────────────────────────────────────────────────────┘
```

**Why layered this way:** each layer is independently validatable — exactly what regulators demand. The **LLM lives only at the edges of Layer 4** (parse in, narrate out); it never reaches into Layers 1–3 where facts and scores live. That separation is the governance story.

### 5.2 The two "brains"

- **The Planner** (adaptive control): decides *what to do next*.
- **The Risk Engine** (deterministic judgment): decides *how severe* a confirmed suspicious finding is.

Keeping these separate is what lets the system be both *flexible* (agentic) and *trustworthy* (auditable).

---

## 6. Part V — The End-to-End Data Flow

The clearest way to understand NEXUS is to follow the **single object that keeps transforming** as it moves through the system:

```text
 "Find structuring in June cash deposits, trace the funds"     ← natural language
        │  INTENT PARSER (LLM)
        ▼
 InvestigationSpec { typology, filters, intent, entities, trace_depth }   ← structured intent
        │  HYPOTHESIS GENERATOR (library lookup)
        ▼
 HypothesisSet [ H1 guilty, H2/H3 innocent ]  (all score = 0)  ← open questions
        │  INVESTIGATION LOOP  ◄────────────────┐
        ▼                                        │  repeats until
 EvidenceLedger [ CL-01, CL-02, ... ] ───────────┘  theories separate
 + HypothesisSet with updated scores
        │  STOP (one theory dominates OR budget spent OR no useful tool left)
        ▼
 WinningHypothesis + full Ledger                               ← settled question
        │  RISK ENGINE + CONFIDENCE + NETWORK EXPANSION + CASE COMPRESSION
        ▼
 Case { ring members, paths, risk, confidence, counterfactuals, evidence[] }  ← verdict object
        │  NARRATOR (LLM) → CLAIM VALIDATOR
        ▼
 Validated Narrative + Escalation (monitor / review / report)  ← human-ready output
        │  AUDIT RECEIPT + DISPOSITION
        ▼
 Immutable case file  +  feedback signal
```

**Read it as:** language → structure → questions → evidence → verdict → *fact-checked* language. The LLM bookends the pipeline; deterministic logic owns the middle.

---

## 7. Part VI — Every Component in Detail

For each component: **what it is, how it works, why it exists, and when it runs.**

### 7.1 Intent Parser

- **What:** turns a natural-language query into a validated, structured `InvestigationSpec`.
- **How:** the LLM extracts `intent` (detect / explain / trace / monitor), `filters` (Cash, June, amount band — only columns the data supports), `entities` (specific account IDs if named), and `pattern type / typology` (structuring / smurfing / layering). The output is schema-validated (Pydantic) before anything proceeds.
- **Why:** the rest of the system is deterministic and cannot act on ambiguity; the spec is the contract. This is the *only* place natural-language flexibility is allowed.
- **When:** once, at the very start.

### 7.2 Hypothesis Generator + the fingerprint model

- **What:** loads the competing explanations for the requested typology.
- **How:** pulls from a **curated library** (a YAML of investigator playbooks) one or more **suspicious** hypotheses and several **benign** ones. **A hypothesis is a fingerprint** — for each evidence family it cares about, it declares an **expected direction** (high/low) and an **importance weight** (how decisive). Families it does not list are **neutral**. Each hypothesis starts at score 0, support = "untested".
- **Why:** the benign theories are the false-positive filter. If nothing forces a suspicious theory to *beat* an innocent one, you get the alert flood banks already suffer. Using a library (not free LLM generation) makes it generalizable and defends against "you hardcoded the demo."
- **When:** once, immediately after parsing, before any data is touched.

Example fingerprints:

**H1 — Structuring + consolidation (suspicious)** — max score = 3.5
| family | expects | importance |
|---|---|---|
| peer_deviation | high | 0.7 |
| flow_through | high | 1.0 |
| network_convergence | high | 1.0 |
| temporal_coordination | high | 0.8 |

**H2 — Legitimate cash-intensive business (benign)** — tested max = 1.6
| family | expects | importance |
|---|---|---|
| flow_through | **low** (a real business keeps money) | 1.0 |
| peer_deviation | low | 0.6 |

**H3 — Recurring legitimate collections (benign)** — tested max = 1.3
| family | expects | importance |
|---|---|---|
| flow_through | low | 0.6 |
| temporal_coordination | **low** (regular cadence, not a burst) | 0.7 |

Note that H1 and H2 expect the **opposite** direction for `flow_through`. That opposition is the entire engine of the duel.

### 7.3 The Investigation Loop

- **What:** the iterative heartbeat — *choose a tool → run it → record evidence → re-score theories → decide whether to continue.*
- **How:** a state machine (orchestrated with LangGraph). State = {hypothesis scores, evidence ledger so far, budget remaining, tools already used}. Each pass mutates that state.
- **Why:** real investigation is adaptive — what you look at next depends on what you just found. A fixed pipeline cannot do that; a loop can.
- **When:** runs repeatedly until a stop condition fires (§7.5).

### 7.4 Budget-Aware Planner (the agentic core)

- **What:** picks the single most valuable next tool — or stops.
- **How:**
  1. Identify hypothesis **pairs still tied** (not yet clearly separated).
  2. For each unused tool, compute **utility = discrimination value − data cost − latency cost**, where discrimination comes from a precomputed **matrix** (`tool × hypothesis-pair → separating power`, expert-assigned).
  3. Run the **highest-utility** tool. If the best utility is below a threshold ε, stop (nothing left worth doing).
- **Why (three reasons):**
  - *Accuracy* — it runs the test that best separates the live theories.
  - *Efficiency & privacy* — subtracting cost means it won't access data it doesn't need ("minimal sufficient evidence").
  - *Honesty* — discrimination values are expert domain knowledge in a lookup table, **not** runtime information theory. We say so plainly.
- **When:** at the top of every loop iteration.
- **Visible artifact:** the UI's *"why this tool / tools skipped"* trace comes straight from these utility comparisons — this is how we *prove* selectivity.

**Proof that it is not a fixed pipeline — two queries, two plans:**
- *"Analyse this dataset for suspicious activity"* (broad) → **EDA → Feature Engineering → Anomaly Detection → Risk Classification → rank suspects.**
- *"Explain why account C1 is flagged"* (targeted) → **skip EDA entirely**, load stored evidence, run only peer + pass-through, narrate.

### 7.5 The Stop Decision

- **What:** decide the investigation is complete.
- **How:** stop when **any** of: (a) one hypothesis clearly dominates *and* confidence is high; (b) the budget (max tools / fields / latency) is spent; (c) no remaining tool has utility above threshold.
- **Why:** mirrors a good investigator who stops when the question is settled, not when tests run out. This also produces the efficiency metric ("4 tools, not 8").
- **When:** evaluated at the end of every loop iteration.

### 7.6 The Tool Registry (rubric-labeled)

Every tool has the same shape: it takes data, computes a signal, and emits an **evidence record**. Tools are labeled with the problem-statement vocabulary so grading is effortless.

**EDA Tool** *(rubric capability #1)*
- **Data in:** the whole filtered slice (amounts, formats, timestamps, counts).
- **Method:** missing-value profile, distributions, correlations, top counterparties, class balance; produces charts.
- **When:** broad queries only (skipped for targeted ones) — this *is* the agentic showcase.
- **Output:** a profile summary + charts (feeds the "supporting charts" output).

**Feature Engineering Tool** *(rubric capability #2)*
- **Data in:** `Account`, `Timestamp`, `Amount`, `Payment Format`, graph edges.
- **Method:** rolling sums, transaction frequency / velocity, amount deviation (z vs own history), near-threshold count, rapid cash-out ratio, in/out degree.
- **Output:** model-ready / rule-ready AML features consumed by the anomaly and evidence tools.

**Anomaly Detection Tool (hybrid)** *(rubric capability #3)* — an umbrella over five evidence producers:

| Evidence tool | Question it settles | Data columns consumed | Method → strength (0–1) | Family emitted |
|---|---|---|---|---|
| behaviour_baseline | unusual *for this account*? | Account, Timestamp, Amount | z vs the account's own history → `clamp(z/5)` | behavioural_deviation |
| peer_comparison | unusual *for similar accounts*? | derived account features | cluster peers, z vs cluster → `clamp(z/5)` | peer_deviation |
| temporal_coordination | are accounts acting in sync? | From/To, Timestamp | timestamp dispersion vs a random baseline | temporal_coordination |
| rapid_pass_through | did money leave fast? | per-account in/out, Timestamp, Amount | (out within 6h) ÷ (total in) | flow_through |
| graph_motif / path_trace | flow shape / convergence? | From/To edges, Amount | fan-in breadth + convergence fraction | network_convergence |
| isolation_forest | statistically anomalous? | engineered feature vector | unsupervised score, normalized | anomaly |

**Risk Classification Tool** *(rubric capability #4)* → see §7.9.
**Explanation / Rule Layer** *(rubric capability #5)* → see §7.11.

### 7.7 The Evidence Ledger

- **What:** the proof store — the case file. It is a growing list of **evidence records**.
- **Anatomy of an evidence record:**

  | Field | Meaning |
  |---|---|
  | `claim_id` | unique id, e.g. "CL-02" |
  | `family` | the kind of signal (e.g., `flow_through`) |
  | `claim` | the finding in words ("91% moved onward within 6 hours") |
  | `calculation` | how it was computed ("out_within_6h / total_in") |
  | `value` | the raw number (0.91) |
  | `direction` | high / low, vs a neutral midpoint |
  | `strength` | how strongly present, squashed to 0–1 |
  | `supports` / `contradicts` | which hypotheses it helps or hurts |
  | `transactions` | the **exact transaction IDs** behind it — the proof |
  | `feature_version`, `data_snapshot` | reproducibility stamps |

- **Where it physically lives:** born as objects returned by a tool → appended to the run's in-memory `ledger` list → persisted to **SQLite** and attached to the `Case` → rendered in the UI's Evidence Ledger panel.
- **Why:** the `transactions` pointer is the "proof of work." A signal without its source is an opinion; a signal *with* its source is evidence. This is what lets the validator guarantee zero unsupported claims.
- **When:** written once per tool run, throughout the loop.

### 7.8 The Duel — how evidence updates hypotheses

- **What:** every new evidence record re-scores **all** hypotheses at once.
- **How (the one rule):**
  - family in fingerprint & **direction matches** → `score += importance × strength`
  - family in fingerprint & **direction clashes** → `score −= importance × strength`
  - family absent from fingerprint → no change (neutral)
- **Why multiply importance × strength:** two things matter — how decisive the family is for this theory (importance) and how strong the actual signal is (strength).
- **Why subtract on mismatch:** this is the false-positive killer. Evidence doesn't merely fail to help the innocent theory — it *demolishes* it. Without subtraction, benign theories would never get ruled out.
- **When:** immediately after each tool runs, before the next planning step (so the next tool choice reflects the new state).

(Full arithmetic in [Part VII](#8-part-vii--how-hypotheses-score-the-full-arithmetic).)

### 7.9 Risk Engine + Confidence (Risk Classification)

- **What:** converts the settled evidence into a 0–100 risk score and a confidence level.
- **Crucial distinction:** the *hypothesis score* decides **which story is true**; the *risk score* measures **how severe** the suspicious story is — computed **only** when a suspicious theory wins.
- **How:** `risk = Σ (family_weight × family_strength)` across the independent families, normalized to 0–100. Tiers: **0–39 low → monitor**, **40–69 medium → review**, **70–100 high → report**. Confidence is a **level** (weak/moderate/strong/high) from margin + number of corroborating families + coverage — deliberately *not* a fake calibrated probability.
- **Why additive:** transparency and decomposability. A black-box score cannot be explained or ablated; a sum can.
- **Why two numbers matter for false positives:** if a **benign** theory wins, the Risk Engine has little suspicious evidence to sum → risk stays low → no escalation. The duel *gates* the risk.
- **When:** once, after the loop stops and the case is assembled.

### 7.10 Counterfactual Engine

- **What:** shows which evidence families are load-bearing.
- **How:** because risk is a sum, zero out one family and recompute; repeat for combinations. Present the deltas (e.g., 87 → 64 without pass-through → 41 without pass-through *and* network).
- **Why:** proves the flag rests on *corroboration*, not one threshold — the answer to "aren't you just a rules engine?" and a core false-positive argument (a single-signal fluke collapses to a low score here).
- **When:** on demand (analyst clicks) and precomputed for the case summary.

### 7.11 Network Expansion + Case Compression

- **What:** turn a single seed flag into the full network case, then collapse scattered alerts into one.
- **How:**
  1. **Seed:** one account trips first — usually a **collector hub** (high fan-in + rapid pass-through).
  2. **Expand:** the path-tracer walks the transaction graph within `trace_depth` — backward to the feeders (smurfs), forward to the beneficiary.
  3. **Earn-your-flag rule:** each pulled-in account joins **only** if it has its *own* supporting evidence (coordinated timing, mule-thin activity). Mere connection is not enough.
  4. **Benign gate:** a legitimate hub (popular merchant) passes its benign hypothesis, so its customers are **not** scooped up (e.g., a salary payer is excluded).
  5. **Compress:** the connected subgraph becomes **one Case** — listing originators, collector hubs, final beneficiary, suspicious paths, shared time window, and evidence for and against.
- **Why:** investigators want fewer, richer cases, not more alerts. The "evidence-per-node + benign gate" safeguard is what stops network expansion from becoming a guilt-by-association false-positive machine.
- **When:** after the loop settles, triggered by network-convergence evidence.

```text
 A1 A2 A3 A4 A5 A6   (smurfs, coordinated, near-threshold deposits)
   \  \  |  /  /  /
          C1  ◄── seed flag (high fan-in + rapid pass-through)
           │
           ▼
          B1  (beneficiary, 91% within 6h)      E1 salary → EXCLUDED (benign)
```

### 7.12 Explanation Layer — Narrator + Claim Validator

- **Narrator (LLM):** reads *only the evidence ledger* and writes analyst-style prose, tying each flag to the query intent and the detected typology.
- **Claim Validator (deterministic):** checks **every sentence** against the ledger — does each number exist in a record? Is each cited transaction real? Any claim that cannot be traced is **rejected** and replaced with a deterministic template.
- **Why:** the LLM makes the output *readable*; the validator makes it *true*. **Target: 0% unsupported claims** — in financial services, this beats using the biggest model.
- **When:** after the case (with score, counterfactuals, ring) is fully assembled — the LLM narrates a finished, factual object, never a guess.

### 7.13 Escalation + Audit + Disposition (false-positive feedback)

- **Escalation:** maps risk + confidence to **monitor / review / report** ("report" = worthy of a regulator filing / SAR).
- **Audit receipt:** immutable record — evidence chronology, the alternative theories considered and *why they lost*, missing information, recommended action, human sign-off. This *is* the "query-aware execution summary."
- **Disposition store:** records the analyst's verdict, including "false positive." Lightweight now (audit + a weight-nudging hook); full online learning is **roadmap** — the honest answer to "does it improve over time?"
- **When:** at the end, on human sign-off.

---

## 8. Part VII — How Hypotheses Score (the full arithmetic)

This section makes the scoring concrete. It uses the seeded smurfing ring from [Part VIII](#9-part-viii--a-complete-worked-example).

### 8.1 From raw metric to "strength" and "direction"

Each tool converts a raw metric into a **strength ∈ [0,1]** via a fixed calibration curve, and a **direction** (high/low) by comparing to a neutral midpoint. Examples:

- `peer_comparison`: `strength = clamp(z / 5, 0, 1)`. z = 4.1 → strength ≈ **0.82**, direction **high**.
- `rapid_pass_through`: `strength = ratio`. 0.91 → **0.91**, direction **high**.
- `graph_motif`: `strength = 0.5·min(1, fan_in/6) + 0.5·convergence`. 6 senders + 0.91 convergence → ≈ **0.90**, direction **high**.
- `temporal_coordination`: synchronization index → **0.80**, direction **high**.

### 8.2 The four evidence records for the ring

```
CL-01  peer_deviation        value 4.1σ   strength 0.82  high   supports H1, contradicts H2      TX-01..06
CL-02  flow_through          value 0.91   strength 0.91  high   supports H1, contradicts H2,H3   TX-01..07
CL-03  network_convergence   value ~0.95  strength 0.90  high   supports H1, contradicts H3      TX-01..07
CL-04  temporal_coordination value 0.82   strength 0.80  high   supports H1                      TX-01..06
```

### 8.3 The scoreboard, round by round

Start: **H1 = 0, H2 = 0, H3 = 0.**

| After | H1 | H2 | H3 |
|---|---|---|---|
| start | 0 | 0 | 0 |
| CL-01 peer (high, 0.82) | +0.7·0.82 = **0.574** | −0.6·0.82 = **−0.492** | neutral **0** |
| CL-02 flow (high, 0.91) | +0.91 → **1.484** | −0.91 → **−1.402** | −0.6·0.91 → **−0.546** |
| CL-03 network (high, 0.90) | +0.90 → **2.384** | neutral **−1.402** | neutral **−0.546** |
| CL-04 temporal (high, 0.80) | +0.8·0.80 → **3.024** | neutral **−1.402** | −0.7·0.80 → **−1.106** |

### 8.4 Normalize → labels → winner + confidence

Normalize each score by that theory's own maximum (→ roughly −1…+1):

- H1: 3.024 / 3.5 = **+0.86 → strong**
- H2: −1.402 / 1.6 = **−0.88 → contradicted**
- H3: −1.106 / 1.3 = **−0.85 → contradicted**

Label thresholds: `≥ 0.66 strong · 0.33–0.66 moderate · 0.10–0.33 weak · ±0.10 neutral · ≤ −0.50 contradicted`.

**Winner = H1.** **Confidence = High**, from a huge **margin** (+0.86 vs −0.85), **4 independent corroborating families**, and **100% coverage** of H1's fingerprint.

### 8.5 Risk score (severity of the winning suspicious theory)

| family | global weight | strength | contribution |
|---|---|---|---|
| peer_deviation | 0.20 | 0.82 | 16.4 |
| flow_through | 0.25 | 0.91 | 22.75 |
| network_convergence | 0.25 | 0.90 | 22.5 |
| temporal_coordination | 0.20 | 0.80 | 16.0 |
| typology_rule (near-threshold count) | 0.10 | 0.90 | 9.0 |
| **Risk** | | | **≈ 87 / 100 → HIGH** |

**Counterfactual:** `Full 87 → without flow_through 64 → without network 64 → without both 41`. No single family carries it to "high."

### 8.6 The benign path (proof it isn't rigged)

Run the identical machine on the **legit merchant**: `flow_through` observed **low** (funds retained), `peer_deviation` **low**, recurring counterparties **high**. Now:
- H1 (expects flow_through high) → mismatch → H1 drops.
- H2 (expects flow_through low) → match → H2 climbs → **H2 wins**.
- The Risk Engine has little suspicious evidence to sum → **risk stays low → no escalation.**

Same tools, same rule, opposite conclusion — driven entirely by evidence. This is the demo's false-positive-reduction moment.

---

## 9. Part VIII — A Complete Worked Example

### 9.1 The sample data (a seeded ring)

*(Illustrative numbers showing the exact mechanics on real-shaped rows.)*

| TX ID | Timestamp | From | To | Amount | Format |
|---|---|---|---|---|---|
| TX-01 | Jun 3 09:15 | A1 | C1 | $9,200 | Cash |
| TX-02 | Jun 3 10:40 | A2 | C1 | $9,500 | Cash |
| TX-03 | Jun 3 11:05 | A3 | C1 | $9,100 | Cash |
| TX-04 | Jun 4 09:30 | A4 | C1 | $9,800 | Cash |
| TX-05 | Jun 4 10:10 | A5 | C1 | $9,300 | Cash |
| TX-06 | Jun 4 12:00 | A6 | C1 | $9,600 | Cash |
| TX-07 | Jun 4 15:00 | C1 | B1 | $51,400 | Wire |
| TX-08 | Jun 20 | E1 (employer) | C1 | $3,200 | ACH |

Total into C1 = $56,500. Out to B1 within ~3–6h of the last deposits = $51,400 (91%). TX-08 is a legitimate salary.

### 9.2 The full trace

1. **Query:** *"Find possible structuring in cash deposits in June and trace outgoing funds."*
2. **Intent Parser →** `{ typology: structuring, filters: {Cash, June}, intent: [detect, trace], trace_depth: 2 }`.
3. **Hypotheses:** H1 (structuring+consolidation), H2 (cash business), H3 (recurring collections).
4. **Loop 1 — planner picks `peer_comparison`** (best separates H1 vs H2). Result: 6 deposits clustered at $9.1–9.8k, 4.1σ above peer group → **CL-01**. H1 → moderate, H2 → weakened.
5. **Loop 2 — planner picks `rapid_pass_through`** (strongest H1-vs-H2 separator). Result: 91% left within 6h → **CL-02**. H1 → strong, **H2 → contradicted**.
6. **Loop 3 — planner picks `graph_motif / path_trace`** (spec asked to trace; reveals shape). Result: fan-in of 6 → C1 → B1 → **CL-03**. Triggers network expansion.
7. **Loop 4 — planner picks `temporal_coordination`**. Result: 6 originators inside a 51-hour window → **CL-04**.
8. **Stop:** H1 dominates, confidence high; remaining tools (EDA, geo, currency, IsolationForest) below utility threshold. UI shows *"tools run: peer, pass-through, graph, temporal; skipped: EDA, geography, currency."*
9. **Risk Engine →** ≈ **87 / 100 (HIGH)**. **Counterfactual:** 87 → 64 → 41.
10. **Network expansion + compression →** pulls A1–A6 (each with its own evidence) and B1; **excludes E1** (salary passes the benign test). Produces **CASE-001: 6 originators → C1 → B1.**
11. **Narrator →** prose summary. **Validator →** every claim traced to TX-01…07; a hypothetical "offshore shell" claim would be rejected. **Result: 0% unsupported claims.**
12. **Escalation →** `report`. **Audit receipt** written. **Disposition** captured on sign-off.

---

## 10. Part IX — The User Interface (Investigation Arena)

The UI is deliberately **one polished screen**, not ten dashboards. It is called the **Investigation Arena**. Everything is synchronized so that clicking one element highlights the related elements everywhere else — that synchronization is the demo's "wow" factor.

### 10.1 Overall layout

```text
┌───────────────────────────────────────────────────────────────────────────┐
│  TOP BAR                                                                    │
│  [ Natural-language query input .................................. ] [Run]  │
│  Detected intent: structuring · Filters: Cash, June · Entity: —            │
│  Plan: peer ▸ pass-through ▸ graph ▸ temporal   |   Skipped: EDA, geo, curr │
├──────────────────────┬───────────────────────────┬─────────────────────────┤
│  LEFT PANEL          │  CENTER PANEL              │  RIGHT PANEL            │
│  HYPOTHESIS ARENA    │  TRANSACTION NETWORK       │  EVIDENCE LEDGER        │
│                      │                            │                         │
│  H1 Structuring      │      A1 ┐                  │  CL-01 peer 4.1σ        │
│     ██████ strong    │      A2 ┼──► C1 ──► B1      │  CL-02 91% in 6h        │
│                      │      A3 ┘        (91%)      │  CL-03 fan-in 6         │
│  H2 Cash business    │      ... (animated flow)   │  CL-04 sync 51h window  │
│     ✗ contradicted   │                            │                         │
│                      │  Excluded: E1 (payroll)    │  [click any row to      │
│  H3 Recurring        │                            │   verify → highlights   │
│     ✗ contradicted   │                            │   graph + timeline]     │
├──────────────────────┴───────────────────────────┴─────────────────────────┤
│  BOTTOM TOOLBAR                                                             │
│  [ Timeline ]  [ Counterfactual 87→41 ]  [ Challenge ]  [ Escalate: REPORT ]│
└───────────────────────────────────────────────────────────────────────────┘
```

### 10.2 The Top Bar — query & execution summary

- **Query input:** free-text box where the analyst types the request.
- **Detected intent / filters / entity:** shown immediately after parsing, so the analyst confirms NEXUS understood the request. *(This is rubric output #1 — the query-aware execution summary.)*
- **Plan strip:** shows the ordered tools the agent chose **and** the tools it deliberately skipped, each with a hover explaining *why*. This is the visible proof of selective, non-fixed-pipeline agency.

### 10.3 Left Panel — Hypothesis Arena

- Lists every hypothesis (suspicious and benign) with a **live support bar** (untested → weak → moderate → strong → contradicted).
- As tools run during the investigation, the bars move in real time — the audience literally watches theories rise and fall.
- Starting state deliberately shows **uncertainty** (structuring "moderate," merchant "plausible"), which makes the final conclusion credible.

### 10.4 Center Panel — Transaction Network

- An **animated money-flow graph** (Cytoscape.js): nodes are accounts, edges are transactions (thickness ∝ amount).
- Highlights the **fan-in** star, the **collector hub**, and the **beneficiary**; animates funds flowing along the suspicious paths.
- **Excluded** accounts (like the salary payer) are visibly greyed out, demonstrating the anti-false-positive safeguard.
- Clicking a node filters the timeline and highlights that account's evidence.

### 10.5 Right Panel — Evidence Ledger

- A scrollable list of evidence records (CL-01, CL-02, …), each showing the claim, the value, and the transactions behind it.
- **Clicking a record verifies it:** the exact transactions light up in the network graph and on the timeline. This is the interactive form of "proof-carrying conclusions."
- *(This panel + the network + the plan strip together satisfy rubric output #6 — supporting charts/tables/metrics for reviewer confidence.)*

### 10.6 Bottom Toolbar — the four power tools

- **Timeline** — a horizontal time view of the transactions; changing the window recomputes the case.
- **Counterfactual** — a small bar showing how the risk score drops as evidence families are removed (87 → 64 → 41), proving corroboration.
- **Challenge** — a menu of benign challenges ("Could this be payroll? a merchant? recurring collections?"). Selecting one runs a targeted test and shows the *expected* benign evidence vs. the *observed* contradicting evidence. *(Menu-constrained so a live demo can't be broken by an unhandled free-text question.)*
- **Escalate** — the recommended action button (monitor / review / report); on click it produces the case + **audit receipt** and captures the analyst **disposition**.

### 10.7 Secondary screens

- **Ranked Suspects view** (for broad queries): a sortable table of the top suspicious entities with risk tier and one-line reason — the entry point that expands into a full case in the Arena. *(Rubric output #2 & #3.)*
- **EDA view** (when a broad query invokes the EDA tool): distribution charts, missing-value profile, correlations, top counterparties — the "supporting charts."
- **Audit Receipt view:** the immutable case file (evidence chronology, alternatives considered, sign-off) — the governance artifact.

### 10.8 UI design principles

- **One screen, synchronized** — every click cross-highlights.
- **Show reasoning, not just results** — the plan strip and hypothesis bars make the agent's thinking visible.
- **Start uncertain, end decisive** — credibility comes from watching the evidence resolve the question.
- **Neutral, professional language** — no sensational wording; this is a compliance tool.

---

## 11. Part X — Rubric Compliance

### 11.1 Required capabilities → our components

| Required capability | Our component | Status |
|---|---|---|
| EDA Tool (profiling + visualization) | `eda_tool`, invoked selectively on broad queries | ✅ |
| Feature Engineering Tool | `feature_engineering_tool` — AML features on demand | ✅ |
| Anomaly Detection Tool (ML / statistical / rules / hybrid) | Hybrid: IsolationForest + statistical + rules + graph | ✅ Exceeds |
| Risk Classification Tool | Additive Risk Engine → low/med/high + confidence | ✅ Exceeds |
| Explanation Component / Rule Layer | Narrator + Claim Validator over the Evidence Ledger | ✅ Exceeds |
| **Agentic, NOT a fixed pipeline** | Budget-aware planner builds a plan per query | ✅ Core strength |

### 11.2 Required outputs → our sources

| Required output | Source |
|---|---|
| Query-aware execution summary | Intent spec + planner "tools run / skipped" trace |
| Top suspicious transactions/customers | Ranked suspects view (case is the drill-down) |
| Risk level per item | Risk Engine tier |
| Explanation per flag (tied to intent + pattern) | Narrator |
| Escalation action | monitor / review / report |
| Supporting charts / tables / metrics | EDA charts + network graph + evidence table + counterfactual |

### 11.3 The agentic requirement

- Accepts NL query & autonomously orchestrates tools — **Intent Parser + Planner**.
- Not a fixed pipeline — **utility-driven planner that skips irrelevant tools**.
- Parses intent, filters, entities, **and pattern types** — all four in the `InvestigationSpec`.
- Dynamically constructs an execution plan — the loop builds the plan per query. *(See the two demo queries in §7.4.)*

---

## 12. Part XI — Evaluation & Proof of Superiority

We compare **NEXUS** against two baselines, using the `Is Laundering` labels:

- **Baseline 1 — Static rules** (e.g., "more than N transactions under threshold").
- **Baseline 2 — Generic AI** (IsolationForest + run-everything + independent per-transaction scores).

| Dimension | Metric | Honest target |
|---|---|---|
| Detection | Precision@10, recall, F1 by typology | beat rules |
| False-positive burden | false positives per 1,000 customers | lower than both |
| Investigator workload | alerts → cases (compression ratio) | ≥ 3× |
| Agent efficiency | tool calls + data fields accessed | ≥ 30% fewer |
| Explanation integrity | unsupported-claim rate | **0%** |
| Operational value | time to disposition | lower |
| Robustness | performance on the benign lookalike | merchant downgraded |
| Graph value | with vs. without network signals | measurable lift |

> Numbers are **targets until the evaluation produces real values** — never fabricated. The single most persuasive result: *"Both accounts looked anomalous. The generic model flagged both. NEXUS escalated the ring and downgraded the legitimate merchant after testing the benign hypothesis."*

---

## 13. Part XII — Technology Stack

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.11 | one language across the engine |
| Agent orchestration | **LangGraph** | stateful plan→act→update→stop loop; reasoning trace + checkpoints (audit) for free |
| LLM (bounded) | provider-agnostic | only 3 jobs: parse intent, narrate ledger, map challenge → hypothesis. **Never scores.** |
| Analytical data | **DuckDB + pandas** | SQL over CSVs; reads only needed columns = the data-minimization story |
| Graph | **NetworkX** | auditable motif detection & bounded traversal; no training, fully explainable |
| Features / anomaly | **scikit-learn + numpy** | IsolationForest (one family), KMeans (peer groups), z-scores |
| Risk engine | custom **additive** model (pure Python) | decomposable → counterfactuals are trivial and honest |
| Schemas / validation | **Pydantic v2** | typed `InvestigationSpec`, `Hypothesis`, `EvidenceRecord`, `Case` |
| Backend API | **FastAPI** | async; streams the reasoning trace to the UI |
| Frontend | **React + Vite + TypeScript**, **Cytoscape.js** | the synchronized Investigation Arena |
| Persistence | **SQLite** | cases, audit receipts, dispositions — file-based, zero-ops |
| Reproducibility | seeded data snapshot + `feature_version` in every record | defensible, reproducible metrics |

---

## 14. Part XIII — Build Order & Roadmap

> The #1 execution risk is building horizontally and having nothing work end-to-end until the last day. Build a thin vertical slice first, then deepen.

- **Phase 0 — Walking skeleton:** one hardcoded query → one seeded case → the Arena screen rendering hypotheses + a stub graph + a stub ledger, end to end (even faked). **Lock the golden path.**
- **Phase 1 — Data & seeds:** load AMLworld into DuckDB; craft & embed the structuring case, smurfing ring, and benign-lookalike merchant.
- **Phase 2 — Evidence + risk core:** evidence ledger, additive risk engine, confidence, counterfactual. *(Highest ROI, lowest risk — the trust moat.)*
- **Phase 3 — Feature engineering + the evidence tools** (structuring + smurfing/graph tools).
- **Phase 4 — Network expansion + case compression.**
- **Phase 5 — Hypothesis library + planner** (+ EDA tool) with the "why / skipped" trace.
- **Phase 6 — Narrator + validator.**
- **Phase 7 — Investigation Arena UI** (synchronized panels; menu-based Challenge Mode).
- **Phase 8 — Evaluation vs. baselines** (real numbers).
- **Phase 9 — Rehearse + record a backup demo** (cache the golden path against LLM latency).

**Roadmap (post-hackathon):** full online learning (dispositions re-weight evidence families over time), a temporal GNN as a model challenger, layering as a first-class typology, streaming ingestion.

---

## 15. Part XIV — Five-Minute Demo Script

| Time | Beat | On screen |
|---|---|---|
| 0:00–0:25 | **Problem** | 47 disconnected alerts. "AML teams don't lack alerts — they lack defensible context." |
| 0:25–0:55 | **Request** | Type the query; show extracted intent, filters, and chosen tools. |
| 0:55–1:30 | **Honest uncertainty** | Structuring moderate, merchant also plausible — "can't responsibly escalate yet." |
| 1:30–2:20 | **Adaptive investigation** | Tools animate; irrelevant ones visibly skipped, with the "why." |
| 2:20–3:00 | **Network reveal** | 47 alerts → 1 case: 6 contributors → C1 → B1, 91% pass-through. Graph ↔ timeline sync. |
| 3:00–3:35 | **Challenge Mode** | "Could this be a legit merchant?" → expected benign evidence vs. contradicting observations. |
| 3:35–4:05 | **Counterfactual** | Remove evidence → 87 → 41. "No single threshold caused this." |
| 4:05–4:30 | **Human escalation** | Create case with evidence chronology, alternatives, sign-off, audit receipt. |
| 4:30–5:00 | **Measured value** | Final slide: Rules vs. Generic vs. NEXUS. Close on the one-liner. |

---

## 16. Part XV — Anticipated Judge Questions

- **"Isn't this just a rules engine?"** Rules are one of six evidence families (customer-relative, peer-relative, temporal, graph, IsolationForest, typology rules). The innovation is the adaptive investigation workflow and case-level reasoning, not one classifier.
- **"Why is an agent required?"** Different questions need different evidence. The agent chooses the smallest valid analysis path and stops when sufficient. (Show the two demo queries.)
- **"How do you stop hallucination?"** The LLM cannot compute or change risk. Every sentence is generated from a typed evidence ledger and rejected if its numbers, entities, or transactions can't be verified; deterministic template fallback.
- **"Can you deploy this on real data?"** Data access, feature computation, scoring, evidence, and narrative are separated so each is independently governable and validatable.
- **"Why not a GNN?"** Bounded graph motifs are auditable and need no labels. A temporal GNN is a model challenger in the roadmap, not an unexplained centerpiece.
- **"Does it improve over time?"** Analyst dispositions feed back to re-weight evidence families — our continuous-improvement path.

---

## 17. Glossary of Banking & Technical Terms

| Term | Plain meaning |
|---|---|
| **AML** | Anti-Money Laundering — the bank's program to detect and report laundering. |
| **Money laundering** | Making illegally-obtained money look legitimate. |
| **Placement / Layering / Integration** | The three stages: get cash in → hide the trail → bring it back clean. |
| **Structuring** | Splitting a big sum into smaller deposits just under a reporting threshold. |
| **Smurfing** | Structuring using many people making many small deposits. |
| **Layering** | Moving money through many accounts/loops to obscure origin. |
| **Typology** | A known method/pattern of laundering. |
| **Fan-in / fan-out** | Many senders → one account / one account → many receivers (graph shapes). |
| **False positive** | An innocent transaction wrongly flagged as suspicious. |
| **SAR** | Suspicious Activity Report — the official filing to the regulator ("report"). |
| **KYC** | Know Your Customer — background info a bank holds on a customer. |
| **FinCEN / FATF** | US financial-crime regulator / global AML standards body. |
| **Escalation** | The action on a flag: monitor / review / report. |
| **Evidence record** | A single verifiable finding, pointing to exact transactions. |
| **Hypothesis (fingerprint)** | A theory expressed as expected evidence directions + importances. |
| **Evidence family** | A category of signal (e.g., flow_through, peer_deviation). |
| **Counterfactual** | Recomputing the score with a piece of evidence removed. |
| **Peer group** | A behaviorally-similar cluster of accounts, used as the "normal" baseline. |

---

## 18. Appendix — Repository Structure

```text
nexus-aml/
├── data/
│   ├── raw/                      # AMLworld HI-Small CSVs
│   ├── seeds/                    # crafted structuring case, smurfing ring, benign merchant
│   └── snapshot/                 # frozen investigation dataset
├── engine/
│   ├── spec.py                   # Pydantic models (InvestigationSpec, Hypothesis, EvidenceRecord, Case)
│   ├── intent_parser.py          # LLM → InvestigationSpec (validated)
│   ├── hypotheses/               # library.yaml + generator
│   ├── planner.py                # budget-aware discrimination planner
│   ├── tools/                    # each tool = typed fn emitting EvidenceRecord(s)
│   │   ├── eda.py
│   │   ├── feature_engineering.py
│   │   ├── behaviour_baseline.py
│   │   ├── peer_comparison.py
│   │   ├── temporal_coordination.py
│   │   ├── rapid_pass_through.py
│   │   ├── graph_motif.py        # NetworkX fan-in/out, path trace
│   │   └── isolation_forest.py
│   ├── ledger.py                 # evidence ledger store
│   ├── risk_engine.py            # additive risk + confidence + counterfactuals
│   ├── validator.py              # claim validator (0% unsupported target)
│   ├── narrator.py               # ledger → prose (LLM, validated)
│   ├── case_compressor.py        # alert → network case
│   └── graph_state.py            # LangGraph investigation loop
├── api/                          # FastAPI app, streams reasoning trace
├── ui/                           # React + Vite + Cytoscape "Investigation Arena"
├── eval/                         # baselines + metrics harness
│   ├── baseline_rules.py
│   ├── baseline_generic.py       # IsolationForest, run-everything
│   └── metrics.py
└── scripts/  seed_data.py  run_demo.py
```

---

### Final word

NEXUS-AML meets every requirement of the problem statement and adds a genuine, defensible innovation on top: it **investigates rather than detects**, it **weighs innocence to kill false positives**, it **proves every claim**, and it **turns noise into a handful of network cases** a human can act on with confidence.

> *NEXUS does not automate suspicion. It automates the collection and verification of evidence — so investigators make faster, more defensible decisions.*
