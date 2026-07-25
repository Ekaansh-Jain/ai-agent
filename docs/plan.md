# NEXUS-AML — Build Plan (single source of truth)

Full concept/reference lives in `read.md`. This file is the roadmap. Steering files
(`.kiro/steering/product.md`, `tech.md`, `structure.md`) hold the always-on rules.

## The idea (one paragraph)
NEXUS-AML reads bank transaction data and, for anything suspicious, runs a mini-investigation:
it lines up a "guilty" theory against one or more "innocent" theories, runs only the tools
needed to tell them apart, records every finding as evidence pointing back to real transaction
IDs, scores risk with a transparent additive formula, bundles related alerts into one network
case, and writes a plain-English monitor/review/report recommendation a human can challenge.
The LLM only reads the request and writes the summary — all scoring is deterministic.

## Data (confirmed)
- 6 variants × 3 files: `{HI|LI}-{Small|Medium|Large}_{Trans.csv | Patterns.txt | accounts.csv}`.
- **HI-Small** = development. **LI-Small** = false-positive evaluation. Medium/Large unused.
- **Trans.csv**: the transactions — the core input. Columns: Timestamp, From Bank, Account
  (sender), To Bank, Account (receiver → `Account.1`), Amount Received, Receiving Currency,
  Amount Paid, Payment Currency, Payment Format, and optional `Is Laundering` (auto-detect).
- **Patterns.txt**: the answer key. `BEGIN/END LAUNDERING ATTEMPT - <TYPOLOGY>[: desc]` blocks;
  each block = one PatternInstance. Typologies: STACK, CYCLE, FAN-IN, FAN-OUT, GATHER-SCATTER,
  BIPARTITE, RANDOM (+ SCATTER-GATHER). **Held out — grading only, never seen by the agent.**
- **accounts.csv**: account→entity phonebook. Optional; used only for customer-level views.
- Bank codes + accounts load as **strings** (leading zeros matter).

### Resolved by HI-Small profiling (5,078,345 txns)
- **FX normalization: KEEP + needed.** Only ~37% USD; 15 currencies, all with rates.
- **`cross_currency`: KEEP.** Fires on 72,170 rows (1.42%) — a real signal, not negligible.
- **Timestamps:** 100% parse; pattern→txn join = **100% match**.
- **Labels:** `Is Laundering` present, 5,177 positives (0.102%). NOTE the label set (5,177) is
  LARGER than named pattern rows (3,209) — ~2k illicit txns belong to no titled scheme.
  Eval: binary truth = `Is Laundering` (recall denominator); Patterns = typology validation.
- **Typology counts:** CYCLE 54, GATHER-SCATTER 51, BIPARTITE 49, FAN-OUT 48, SCATTER-GATHER 44,
  STACK 43, RANDOM 41, FAN-IN 40. Plenty of FAN-IN/GATHER-SCATTER for the smurfing demo.

## Typology coverage
- **Smurfing + consolidation (Typology B):** covered by real labeled FAN-IN / GATHER-SCATTER.
- **Structuring (Typology A):** no native label — decision pending (seed a small case, my rec).

---

## Phases (5 backend + 1 optional frontend)
Each phase ends in something runnable and checkable.

### Phase 1 — Data Foundation
Load Trans.csv → DuckDB (normalized, `tx_id`, parsed timestamp, string keys, auto-detect
`Is Laundering`); parse Patterns.txt into held-out PatternInstances; load accounts (schema +
optional map); profiling summary. FX table stubbed until profiling justifies it.
**Done when:** `python -m nexus.ingest --variant HI-Small` prints the profile + per-typology
pattern counts; laundering counts from Patterns ≈ `is_laundering==1`.

### Phase 2 — Evidence & Scoring Core (trust moat)
EvidenceRecord + ledger, hypothesis fingerprint model + library, the duel scoring
(`±importance×strength`), additive risk engine + confidence, counterfactual. Built/tested
against hand-made fixture evidence — no real tools yet.
**Done when:** worked-example fixtures → guilty theory wins, risk ≈ 87, counterfactual
87→64→41; benign fixtures → benign theory wins, low risk. Pure math, no LLM.

### Phase 3 — Analysis Tools + Case Building (senses) — SPLIT INTO 3a / 3b

**Phase 3a — the three scoring-critical tools, against a real ring.**
Build only `peer_comparison`, `rapid_pass_through`, `graph_motif/path_trace`. Each emits
EvidenceRecords into the existing ledger; reuse the locked Phase 2 duel/risk unchanged.
Plus: account profiles, behavioral peer clustering, subgraph builder (filtered slices only).
peer_comparison design: behavioral MiniBatchKMeans clusters (no demographics), robust
median/MAD z, MIN_CLUSTER_SIZE=30, global fallback for tiny/zero-spread clusters, clamp z/5.
Note: with 3 of 5 weighted families the risk ceiling is 70; GATHER-SCATTER exercises
pass-through better than pure FAN-IN.
**Done when:** a real ring from HI-Small → tools emit records → duel picks H1 → risk in the
escalation range; show ring account IDs + evidence + score. Plus a throwaway `scripts/fp_peek.py`
printing rules-baseline false-positive count on a labeled slice (one real number).

**Phase 3b — network expansion + seeds + benign lookalike (the demo money shot).**
Network expansion (one flag → the ring), earn-your-flag rule, seeds, benign lookalike.
**Done when:** benign lookalike downgraded (risk low → monitor) AND expansion excludes a
benign connected node (e.g. salary payer). ✅

**Phase 3c — minimal structuring + typology routing.** near_threshold tool, structuring
hypotheses, `investigate()` routes on typology, structuring risk profile (typology_rule only).
**Done when:** the missed structuring TP escalates. ✅ (node 024144|80D9B69A0 → report)

**Phase 3d — consolidation precision (RESOLVED via honest reframe).**
Added benign-discriminating features (retention/recurrence/stability + pooler hypothesis):
FP on the test negatives dropped ~5x, precision 0.23→0.34 (real, kept). But held-out
precision never reached the fixed 0.60 bar or beat a tuned fan-in threshold (0.42).
Attempts: benign features (0.34), +temporal (0.28), +subset/burst detection (0.20).
**Root finding:** AMLworld labels rings by GRAPH TOPOLOGY (fan-in shape), so an `in_degree`
threshold is near-optimal *by construction*; behavioral/temporal/amount features can't beat
it because the ground truth doesn't encode them. This is a benchmark property, not an
architecture flaw.
**Honest positioning (adopted):** NEXUS does NOT claim precision superiority over a threshold
on AMLworld. Its differentiators are explainability (proof-carrying evidence), the benign
duel (benign-lookalike + salary-payer exclusion, demonstrated), case compression, and
challenge. Claims are limited to what holds on held-out data.

### Steering rule (locked)
Success bars are fixed before results and not moved after. Sensor recalibration is flagged
with before/after on affected anchors. Pinned anchors and their provenance are in the
Phase 6 anchor report below — note that 53.18 is a **real HI-Small** number (node
`0048309|811C599A0`), not a fixture number, which Phase 6 measurement established, and it
only became reproducible per rebuild once the profile row order was pinned (see the
determinism fix report below).

### Phase 4 — Agent Orchestration (mind) ✅
Intent parser (deterministic keyword parser; LLM pluggable) → validated InvestigationSpec;
planner (per-typology tool routing with "tools run / skipped" trace); orchestrator loop;
template narrator (LLM-swappable); claim validator (numbers must trace to ledger, 0%
unsupported); audit receipt. All deterministic — demoable with no LLM/API key.
**Done:** NL query → per-query plan → investigated case → validated narrative → escalation,
verified on real data via `scripts/demo.py`. Three queries produce three different plans
(proof it's not a fixed pipeline); structuring query on the ring → REPORT, all narratives
validated with zero unsupported claims.

### Phase 5 — API + Evaluation + LLM edge ✅
FastAPI (`POST /investigate`, `/health`); Gemini wired into the two LLM edges (intent parse +
narration) with automatic deterministic fallback when no key; LLM narration is still gated by
the claim validator (it cannot inject an unsupported number). Eval: pure metrics module
(precision/recall/F1/confusion) + `scripts/eval_report.py` honest scorecard (explanation
integrity, consolidation-vs-rule held-out, seeded demonstrations).
**Done:** `POST /investigate` returns spec + per-query plan + validated case; runs with or
without a Gemini key; 25/25 tests green.
Config: copy `backend/.env.example` -> `.env`, set `GEMINI_API_KEY` (free tier). Run:
`uvicorn nexus.api.app:app` (from `backend/`, venv active).

### Phase 6 — Agent Capability Completion ✅ (hermetic + integration pass both green)
Closes the gaps between the engine and the hackathon brief's five required agent
capabilities. Additive only: `duel.py`, `risk.py`, `hypotheses/library.yaml`, `profiles.py`,
`validator.py` and `intent.py` are unchanged.

New modules: `scope.py` (Filter_Scope), `tools/eda_profile.py` (agent-callable EDA),
`tools/feature_builder.py` (features as a visible plan step), `screener.py` (candidate
prefilter), `trace.py` (per-tool telemetry + failed-tool rollback), `findings.py` (ranked
Findings_List), `charts.py` (derived payloads).

What it fixed:
- **EDA and feature engineering are now agent-callable plan nodes**, both emitting under a
  neutral family (`NEUTRAL_FAMILIES`, disjoint from every fingerprint and risk weight) so
  they cannot move a score. `profile.py` stays byte-identical and outside the agent path.
- **Query filters actually scope the analysis.** They were parsed into
  `InvestigationSpec.filters` and then dropped — nothing downstream read them.
  `peer_comparison` deliberately stays unfiltered (its peer model is full-history; filtering
  it would move the anchors) and the trace says so.
- **Broad queries return a ranked findings list** with per-item risk/tier/escalation/
  evidence/explanation, instead of one account picked by `in_degree.idxmax()`.
- **Per-tool telemetry**: status, reason, measured duration, rows in/out for every roster
  tool, invoked and declined alike.
- **Cost**: per-candidate DuckDB round-trips went 89 median → ≤11 by batching the feeder
  out-degree gate into one grouped query and `ego_subgraph` into one query per depth level.
  Output-identical, proven by a loop-vs-batched equivalence test. Defaults: 500 candidates
  screened, 25 investigated, 30 s budget.

**Done:** hermetic suite green (now 310 passed / 8 skipped). The gated integration pass has
since been run: 318 passed, precision 0.583 / recall 0.333 / F1 0.424 on n=41 (unchanged
against the 0.58 / 0.33 bars), real-ring anchor confirmed at 53.18 on node
`0048309|811C599A0` and now stable per rebuild (see the determinism fix below).

#### Phase 6 anchor report

| Anchor | Provenance | Before | After | Changed? |
|---|---|---|---|---|
| Phase 2 fixture evidence set → `risk_score` | fixture, hermetic | 86.65 | 86.65 | no |
| `ring_Trans.csv` hub `0500\|C1` → `investigate()` | fixture, hermetic | 56.00 | 56.00 | no |
| `case_Trans.csv` hub `0500\|C1` → `investigate()` | fixture, hermetic | 45.54 | 45.54 | no |
| Phase 2 counterfactual sequence (7 entries) | fixture, hermetic | pinned | pinned | no |
| Real HI-Small ring `0048309\|811C599A0` | Phase 3a record | 53.18 | **53.18** | no (unstable until the determinism fix below pinned it) |

All four fixture anchors are pinned by `tests/test_anchors.py` with ±0.01 tolerance and
failure messages naming the anchor, expected and observed value.

Correction this phase produced: **53.18 was never a fixture number.** The ring fixture hub
scores 56.00. 53.18 was recorded in Phase 3a against a real HI-Small ring, and that node is
now identified: **`0048309|811C599A0`** (GATHER-SCATTER, the first entry in
`tests/cases/real_cases.json`). Measured over all 41 real cases, it is the only node
scoring 53.18, and it reproduces to the cent under both the current full `investigate()`
path and the reconstructed Phase 3a three-tool subset (peer_deviation 8.38 + flow_through
25.00 + network_convergence 19.80). The two paths agree because no post-Phase-3a smurfing
tool emits a weighted family (`benign_signals` → retention/recurrence/stability,
`isolation_forest` → anomaly; none is in `RISK_WEIGHTS`). What did change is the verdict,
not the score: the case now loses the duel to a benign hypothesis (monitor) — the documented
benign-gate FN in open decision 2.

All five anchors are recorded in `tests/cases/anchors.json` (the single anchor record). The
real-data anchor is re-measured and asserted at ±0.01 by
`test_real_data_anchors_match_recorded_values` in `tests/test_integration_realdata.py`;
divergence against the *historical* 53.18 is printed with before/after rather than failing,
since that value's provenance predates the repo's record-keeping.

#### Peer-clustering nondeterminism fix (`profiles.build_profiles`)

**Defect and root cause.** `build_profiles` ran an unordered `FULL OUTER JOIN`, so DuckDB
returned the same 515,088 HI-Small accounts in a different **row order** on every call
(`same index set: True`, `same row order: False`). That order feeds `PeerModel.__init__` →
`MiniBatchKMeans`, which is row-order sensitive even with a fixed `random_state`, so cluster
membership moved on every rebuild, moving the per-cluster median/MAD, the `peer_deviation`
z-score (weight 0.20) and the risk. Over 8 rebuilds in one process, node
`0048309|811C599A0` scored **53.18 (6/8), 52.86 (1/8), 52.08 (1/8)** with `peer_count`
23,653..40,436 and z 1.8206..2.0952 — i.e. the "locked" 53.18 was the modal draw from an
unstable distribution, and a boundary account could flip tier (cuts at 70/40) between server
restarts. Every existing determinism test reused one `PeerModel` in-process, so none could
see it. **Fix:** sort the profile frame by its unique `node` index in `build_profiles`
(one line, total and stable order). `PeerModel`, the clustering algorithm, `k`,
`random_state`, `n_init` and every threshold are untouched.

| Anchor | Source | Before | After | Changed? |
|---|---|---|---|---|
| Phase 2 fixture evidence set → `risk_score` | `tests/test_phase2.py::_ring_ledger` | 86.65 | 86.65 | no |
| `ring_Trans.csv` hub `0500\|C1` → `investigate()` | fixture | 56.00 | 56.00 | no |
| `case_Trans.csv` hub `0500\|C1` → `investigate()` | fixture | 45.54 | 45.54 | no |
| HI-Small `0048309\|811C599A0` → `investigate()` | real data | 53.18 (modal of [52.08, 52.86, 53.18]) | **53.18 on 8/8 rebuilds** | value no, stability yes |
| HI-Small `0048309\|811C599A0` → Phase 3a three-tool subset | real data | 53.18 (same spread) | **53.18 on 8/8 rebuilds** | value no, stability yes |

The real-data anchor settled on the historical number, so nothing needs re-baselining; what
changed is that 53.18 is now *reproducible* instead of *probable* (z pinned at 2.0952,
`peer_count` at 32,455). Guards added: `tests/test_profiles_determinism.py` (12 hermetic
tests — row order identical across calls, sorted, invariant to a reordered store, and two
independently rebuilt `PeerModel`s agreeing on z/direction/peer_set and on `investigate()`
risk, plus a synthetic 400-account population where clusters exceed `MIN_CLUSTER_SIZE` so
the drift is actually reproducible; 9 of the 12 fail against the pre-fix code) and
`test_real_data_anchors_are_stable_across_peer_model_rebuilds` in
`tests/test_integration_realdata.py` (rebuilds profiles + `PeerModel` 3× and asserts one
value). `nexus/risk.py`'s anchor comment and the steering rule cited a bare "real ring =
53.18"; both now name the node and the pinned value.

One deliberate behaviour change, recorded rather than buried: `ego_subgraph` now orders by
`tx_id`, so for a repeated `(sender, receiver)` pair the surviving edge attributes are
deterministic instead of DuckDB-order dependent. This can change *which* `tx_id`
`graph_motif` cites for a duplicated edge — never how many, never the strength.

### Phase 7 — Investigation Arena (frontend, optional/later)
React + Vite + Cytoscape: hypothesis bars, animated money-flow graph, evidence ledger,
counterfactual, challenge mode. Last, because backend is demoable without it.
Renamed from Phase 6 so the numbering matches `tests/test_phase6.py`, which covers the
capability work above.

## Guiding order
Cheap deterministic core (P2) before expensive/flaky LLM + planner (P4). Every phase has a
pass/fail test so we never burn credits guessing.

## Testing
- **Unit (hermetic, fast):** `cd backend && .venv/bin/python -m pytest -q` — **310 passed,
  8 skipped** (318 collected), LLM forced off via `tests/conftest.py` (deterministic, no
  network, nothing read from `data/raw/`). The 8 skips are the gated integration tests.
  This is the floor: no existing test may be deleted, renamed, skipped, xfailed, or have an
  assertion weakened.
- **Real-data integration:** `NEXUS_RUN_INTEGRATION=1 .venv/bin/python -m pytest -q` — runs the
  full engine over 41 cases pulled from ground truth (`tests/cases/real_cases.json`,
  regenerate with `scripts/gen_test_cases.py`). Asserts invariants: 0% unsupported claims,
  valid escalation, proof-carrying evidence, typology routing, determinism; reports honest
  precision/recall (no superiority claim). Pre-Phase-6: precision 0.58, recall 0.33 on n=41.
  Re-run after Phase 6: **318 passed** (8 gated tests included), `tp 7 / fp 5 / fn 14 /
  tn 15`, precision 0.583 / recall 0.333 / F1 0.424 — the bars hold, and the numbers repeat
  run-to-run. Also pins the real-data anchor recorded in `tests/cases/anchors.json` and
  asserts it is identical across three rebuilds of profiles + `PeerModel`.
- **Live LLM:** `scripts/demo.py` (seeded constructs) and `POST /investigate` exercise the
  Gemini edges with validator-gated fallback.

## Open decisions (non-blocking)
1. Frontend: build Phase 7 vs. CLI/API-only demo.
2. Recall vs precision: the benign gate trades recall for precision (real rings with
   recurring/stable full-history activity can be downgraded — a known, documented FN).
3. `intent._filters` matches month names by substring, so a query containing "maybe" sets
   `month: "May"`. No anchor or test is affected. Fix is a word-boundary regex; deliberately
   left out of Phase 6 scope.

## Remaining Phase 6 work
1. ~~Identify the HI-Small ring node behind 53.18 and write `tests/cases/anchors.json`.~~ ✅
   `0048309|811C599A0` = 53.18 under both scoring paths; recorded, guarded, and now stable
   per rebuild after the `build_profiles` row-order fix.
2. ~~Run the gated integration pass and record post-feature precision/recall.~~ ✅ Ran on
   n=41: `tp 7 / fp 5 / fn 14 / tn 15, precision 0.583, recall 0.333, F1 0.424` — unchanged
   against the 0.58 / 0.33 bars, so Phase 6 cost no detection quality. Suite: 318 passed,
   repeated across two consecutive runs with no anchor flap.
3. `.kiro/specs/agent-capability-completion/tasks.md` was deleted mid-execution, so the
   remaining property-test sub-tasks (the ones marked `*`) are no longer tracked anywhere.
   Regenerate it from `design.md` before resuming, or the coverage gap is invisible.
