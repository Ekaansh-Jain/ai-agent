# NEXUS-AML — Frontend / Backend Data Contract

**Version:** 3.0 (supersedes 2.0)
**Direction:** derived from the implemented backend.
**Status:** implemented and test-enforced.

---

## 1. How to read this document

**The backend is the source of truth.** Every field below is emitted by code in
`backend/nexus/`. If a behaviour is described only by a frontend mockup, it is not in this
contract and the backend does not provide it.

Version 2.0 of this document was reverse-engineered from an unbuilt frontend and described a
large surface that never existed. Section 9 lists exactly what was removed and why, so you
don't build against it a second time.

A test (`backend/tests/test_phase6.py`, plus `test_contract_parity.py`) compares the field
names documented here against a live response. If they drift, the suite fails.

### 1.1 What exists

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | readiness, dataset stats, capability presence |
| `/roster` | GET | the tool roster the planner can select from |
| `/investigate` | POST | one natural-language query in, one complete investigation out |

That is the entire API. There is no streaming, no polling of an investigation, no
WebSocket, and no live transaction feed. One request, one complete response.

### 1.2 Conventions

| Rule | Detail |
|---|---|
| Casing | `snake_case`. This is what the backend emits; do not expect camelCase. |
| Numbers | **All numeric fields are raw.** `risk` is a float 0–100 (2 dp). `strength`, `value`, `rank` are raw floats. Format them in the frontend. |
| Display strings | **None.** The backend pre-formats nothing. Every currency, date and percentage is yours to format. The only human-readable prose fields are `narrative`, `explanation`, `claim`, `calculation` and `reason`. |
| Arrays | Never `null`. Empty is `[]`. |
| Optional | Absent values are `null`, never `0` or `""`. `null` means *absent*, and that distinction is load-bearing: a profiled slice with no amounts reports `amounts: null`, not a zero-filled object. |
| Order | Array order is render order for `findings`, `plan_trace`, `evidence`, and every chart's `entries`/`rows`. Pre-sorted; do not re-sort. |
| Timestamps | ISO-8601. |

---

## 2. `GET /health`

Answers immediately, including while the dataset loads in a background thread. Poll until
`status` is `"ready"` before calling `/investigate`.

| Field | Type | Description |
|---|---|---|
| `status` | `"warming" \| "ready" \| "error"` | Readiness state |
| `data_loaded` | boolean | `true` only when `status == "ready"` |
| `error` | string \| null | Load failure detail when `status == "error"` |
| `variant` | string | Loaded AMLworld dataset, e.g. `"HI-Small"` |
| `transactions` | number | Loaded transaction rows (`0` until ready) |
| `accounts` | number | Distinct profiled accounts (`0` until ready) |
| `llm_enabled` | boolean | Whether a Gemini key is configured |
| `llm_model` | string \| null | Model name when enabled |
| `anomaly_model` | boolean | Whether the IsolationForest artifact is on disk |
| `eda_tool` | boolean | EDA capability present |
| `feature_builder` | boolean | Feature-engineering capability present |

```json
{
  "status": "ready", "data_loaded": true, "error": null,
  "variant": "HI-Small", "transactions": 5078345, "accounts": 515088,
  "llm_enabled": true, "llm_model": "gemini-2.0-flash",
  "anomaly_model": true, "eda_tool": true, "feature_builder": true
}
```

---

## 3. `GET /roster`

The nine tools the planner chooses among. Use this to render an idle plan rail before any
query is submitted. Every id here appears in every `plan_trace`.

```json
{ "tools": [ { "tool": "eda_profile", "label": "Data Profile (EDA)",
              "purpose": "what does the requested slice actually look like?",
              "scoring": false } ] }
```

| `tool` | `label` | `scoring` | Role |
|---|---|---|---|
| `eda_profile` | Data Profile (EDA) | false | profiles the requested slice |
| `feature_builder` | Feature Builder | false | engineered account features + manifest |
| `candidate_screener` | Candidate Screener | false | ranks accounts worth investigating |
| `peer_comparison` | Peer Comparison | **true** | robust z vs behavioural peer cluster |
| `rapid_pass_through` | Rapid Pass-Through | **true** | did money arrive and leave fast |
| `graph_motif` | Graph Motif / Path Trace | **true** | fan-in / convergence shape |
| `benign_signals` | Benign Signals | **true** | retention / recurrence / stability |
| `near_threshold` | Near-Threshold Deposits | **true** | structuring rule |
| `isolation_forest` | Isolation Forest (neutral) | false | unsupervised anomaly score |

`scoring: true` means the tool's evidence families carry hypothesis and risk weight.
`scoring: false` tools emit **neutral** families that are deliberately excluded from every
hypothesis fingerprint and every risk weight, so they inform the reviewer without moving the
score. Render them differently: they are context, not accusation.

---

## 4. `POST /investigate`

### 4.1 Request

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | yes | 1–500 characters. Outside that range returns 422. |

```json
{ "query": "Flag high-risk cash customers in March" }
```

No context object, no idempotency key, no date-range or jurisdiction parameters. Scope is
extracted from the query text itself (see `spec.filters`).

### 4.2 Latency

| Query shape | Observed | Notes |
|---|---|---|
| Named account (`Is 0500\|C1 suspicious?`) | ~0.2–1.5 s | one investigation |
| Broad sweep (`Flag high-risk customers`) | ~5–17 s | up to 25 investigations |

Measured on HI-Small (5,078,345 rows) on an Apple-silicon laptop with the dataset already
warm. The backend enforces a 30 s budget (`execution.cost.budget_ms`). Set a client timeout
above that; 60 s is comfortable.

### 4.3 Response: top-level keys

Fifteen keys. The first eight are the original contract and are unchanged.

| Key | Type | Section |
|---|---|---|
| `spec` | object | §5 — what the agent understood |
| `plan` | object | §6 — legacy run/skipped view |
| `case` | object \| **null** | §8 — the top finding's case |
| `narrative` | string | §8.3 — validated prose for the top case |
| `validated` | boolean | §8.3 |
| `unsupported` | string[] | §8.3 |
| `sources` | object | §5.2 — which path produced intent and narration |
| `audit` | object | §10 |
| `findings` | `Finding[]` | §7 — **the ranked triage queue** |
| `no_findings_reason` | string \| null | §7.3 |
| `plan_trace` | `PlanTraceEntry[]` | §6 — **per-tool telemetry** |
| `execution` | object | §11 — query-aware execution summary |
| `charts` | object | §12 — six derived payloads |
| `eda` | object \| null | §13 — data profile |
| `feature_manifest` | object \| null | §14 |

**`case` is `null` if and only if `findings` is empty.** That is a normal 200 response, not
an error (see §7.3).

---

## 5. `spec` — what the agent understood

Parsed from the query, either by the deterministic keyword parser or by the LLM (whose output
is validated into the same shape).

| Field | Type | Description |
|---|---|---|
| `query` | string | Echo of the submitted text |
| `intent` | string[] | Subset of `detect`, `trace`, `explain`, `monitor` |
| `typology` | `"smurfing" \| "structuring"` | Detected AML pattern family |
| `filters` | object | `payment_format` and/or `month`, both optional |
| `entities` | string[] | Named account ids, e.g. `["0500\|C1"]`. Empty = broad query |
| `trace_depth` | number | 1–3 graph hops |

Account ids are `"bank|account"`, e.g. `"024144|80D9B69A0"`. Both halves are strings —
leading zeros are significant, so never parse them as numbers.

### 5.2 `sources`

| Field | Type | Description |
|---|---|---|
| `intent` | `"llm" \| "deterministic"` | Which parser produced `spec` |
| `narrator` | `"llm" \| "template"` | Which writer produced `narrative` |

Both degrade to the deterministic path silently when the LLM is unavailable, malformed, rate
limited, or when its output fails claim validation. Worth surfacing as a small badge: it is
the honest signal that no score depended on the model.

---

## 6. `plan` and `plan_trace`

### 6.1 `plan` (legacy view, retained)

```json
{ "run": ["feature_builder", "eda_profile", "..."],
  "skipped": [["near_threshold", "not informative for 'smurfing' (...)"]] }
```

`skipped` is an array of **two-element arrays** `[tool_id, reason]`. Prefer `plan_trace`
for anything new; this key exists so older integrations keep working.

### 6.2 `plan_trace` — one entry per roster tool

Exactly nine entries, every run, covering invoked and declined tools alike. Order is render
order: `ran`/`failed` first in the order execution was attempted, then `skipped` in roster
declaration order.

| Field | Type | Description |
|---|---|---|
| `tool` | string | Roster id, matches `/roster` |
| `label` | string | Display name, ≤60 chars |
| `status` | `"ran" \| "skipped" \| "failed"` | See below |
| `reason` | string | ≤200 chars. **Always populated**, for every status |
| `duration_ms` | number | Measured wall clock. Exactly `0.0` when skipped; strictly `> 0` when ran |
| `rows_in` | number \| null | `null` = not row-countable, which is different from `0` |
| `rows_out` | number \| null | Same |
| `invocations` | number | How many times it ran. A broad query runs the scoring tools once per candidate |
| `filters_applied` | object | Filters this tool honoured |
| `filters_not_applied` | string[] | Filters this tool could **not** honour |

Status semantics:

- `ran` — executed successfully at least once.
- `skipped` — the planner declined it, and `reason` says why in analyst language. This is the
  interesting one: declined-with-a-reason is the visible proof the plan was built for this
  query rather than being a fixed pipeline.
- `failed` — it raised. Its evidence was rolled back and excluded from scoring, and it is
  absent from `plan.run`. The rest of the plan still ran. Render as degraded, not fatal.

`filters_not_applied` is deliberate, not a bug. `peer_comparison` compares an account against
peer clusters fitted over full history; narrowing its slice would change the clusters and
invalidate the calibration. So it runs unfiltered and says so. Same for `isolation_forest`,
`feature_builder`, `candidate_screener`, and the feeder out-degree gate.

---

## 7. `findings` — the ranked triage queue

The headline output. One entry per flagged account, ordered by `risk` descending with ties
broken by ascending `node`.

| Field | Type | Description |
|---|---|---|
| `rank` | number | 1-based, assigned after sorting |
| `node` | string | Account id `"bank\|account"` |
| `risk` | number | 0–100, two decimals |
| `tier` | `"low" \| "medium" \| "high"` | See §7.1 |
| `escalation` | `"monitor" \| "review" \| "report"` | Suggested action |
| `winning_kind` | `"suspicious" \| "benign" \| "indeterminate"` | Verdict class — **handle all three** |
| `winning_hypothesis` | string | Hypothesis id, e.g. `"H1"` |
| `hypothesis_label` | string | Human label, e.g. `"Structuring + rapid consolidation"` |
| `confidence` | `"weak" \| "moderate" \| "strong" \| "high"` | A **label**, not a probability (§7.2) |
| `explanation` | string | ≤400 chars, references the query intent |
| `explanation_source` | `"llm" \| "template"` | Which writer produced it |
| `validated` | boolean | Every number in `explanation` traces to this finding's evidence |
| `unsupported` | string[] | Numbers that failed validation. Target: always empty |
| `evidence` | `EvidenceRecord[]` | §7.4 |
| `case` | object | §8 — the full case for this finding |

### 7.1 Risk tiers

| `risk` | `tier` | `escalation` |
|---|---|---|
| ≥ 70 | `high` | `report` |
| 40 – 69.99 | `medium` | `review` |
| < 40 | `low` | `monitor` |

Derive your own colour from `tier`, not from the raw score.

### 7.2 `confidence` is a label, not a number

It is computed from the margin between the top two hypotheses and how many evidence families
corroborate the winner. It is deliberately **not** a probability. Do not do arithmetic on it,
do not render it as a percentage, and do not build a gauge from it.

### 7.3 Empty findings is a success, not an error

A broad query drops candidates whose duel came back `benign` or `indeterminate` — a triage
queue should not be padded with accounts the system already cleared. So a legitimate run can
return nothing. When that happens:

```json
{ "findings": [], "case": null,
  "no_findings_reason": "investigated 25 candidate(s); the evidence supported a benign or indeterminate explanation for every one, so nothing was flagged",
  "plan_trace": [ /* full, so the analyst sees what was searched */ ],
  "execution": { /* full */ },
  "charts": { /* each payload available:false with a reason */ } }
```

HTTP status is **200**. `no_findings_reason` is populated only in this case. Render it as an
informative empty state, and keep the plan trace visible — that is precisely when a reviewer
wants to know what was looked at.

A query that names an account behaves differently: it returns one finding per named account
**regardless of verdict**, including `benign` and `indeterminate`, because "is 4521
suspicious?" deserves an answer even when the answer is no.

### 7.4 `EvidenceRecord`

The unit of proof. Every number the system asserts traces back to one of these.

| Field | Type | Description |
|---|---|---|
| `claim_id` | string | `"CL-01"`, dense per case |
| `family` | string | Evidence family, e.g. `peer_deviation` (§7.5) |
| `claim` | string | Human-readable finding |
| `calculation` | string | The formula used — show this on demand; it is the audit story |
| `value` | number | Raw measured value |
| `direction` | `"high" \| "low"` | Versus a neutral midpoint |
| `strength` | number | 0–1 |
| `transactions` | number[] | **Real transaction ids backing the claim** |
| `feature_version` | string | `"v1"` |
| `data_snapshot` | string | Usually `""` |

### 7.5 Evidence families

| Family | Carries risk weight | Emitted by |
|---|---|---|
| `peer_deviation` | 0.20 | peer_comparison |
| `flow_through` | 0.25 | rapid_pass_through |
| `network_convergence` | 0.25 | graph_motif |
| `temporal_coordination` | 0.20 | (reserved) |
| `typology_rule` | 0.10 | near_threshold |
| `retention`, `recurrence`, `stability` | 0 (duel only) | benign_signals |
| `anomaly` | **0 — neutral** | isolation_forest |
| `data_profile` | **0 — neutral** | eda_profile |

Neutral families appear in no hypothesis fingerprint and no risk weight profile. They are
informational by construction: the IsolationForest can say `0.82` and the verdict can still
be benign, and showing both is the point.

---

## 8. `case` — the network case for the top finding

Identical to `findings[0].case`. Retained at the top level for backward compatibility.

| Field | Type | Description |
|---|---|---|
| `seed` | string | The investigated account |
| `typology` | string | `smurfing` or `structuring` |
| `winning_hypothesis` | string | Hypothesis id |
| `winning_kind` | `"suspicious" \| "benign" \| "indeterminate"` | |
| `confidence` | string | Label, see §7.2 |
| `risk` | number | 0–100 |
| `tier` | string | low / medium / high |
| `escalation` | string | monitor / review / report |
| `members` | string[] | Every account in the compressed network case |
| `feeders_included` | string[] | Feeders that earned their flag (mule-thin) |
| `beneficiaries` | string[] | Where the money went |
| `excluded` | `[string, string][]` | `[node, reason]` — connected but deliberately NOT flagged |
| `evidence` | `EvidenceRecord[]` | The ledger for this case |

`excluded` is worth a dedicated panel. It is the false-positive control made visible: a
salary payer connected to a ring is reported as *considered and cleared*, with the reason.

### 8.1 Graph rendering

There is no separate graph endpoint. Build the money-flow graph from the case:
nodes = `members`, plus `excluded` nodes rendered as cleared; edges from `feeders_included` →
`seed` → `beneficiaries`. Edge transaction ids come from `evidence[].transactions`.

### 8.2 The indeterminate verdict

When `winning_kind` is `"indeterminate"` the evidence did not separate the competing
explanations. The backend returns `risk: 0.0`, `tier: "low"`, `escalation: "monitor"` and
asserts **no conclusion**. Do not render it as "clean" — render it as "not enough evidence".
A UI that only handles suspicious/benign will misreport this case.

### 8.3 `narrative`, `validated`, `unsupported`

`narrative` is analyst prose for the top case. `validated` is `true` when every number in it
traces to that case's evidence; `unsupported` lists any that did not.

The LLM narrator is attempted first and its output is **rejected** if it contains an
unsupported number, falling back to the deterministic template. So `validated: true` with
`unsupported: []` is the normal state, and `sources.narrator` tells you which writer won. If
you surface one trust indicator in the whole UI, surface this one.

---

## 9. What was removed from version 2.0

None of the following exists in the backend. Do not build against it.

**Endpoints removed:** `/api/bootstrap`, `/api/watchtower`, `/api/cases`,
`/api/cases/{id}`, `/api/entities/{id}/graph`, `/api/transactions`, `/api/models`,
`/api/cases/{id}/report`, `/api/audit`. Also `POST /api/investigations` — the real path is
`POST /investigate`.

**Objects removed:** `SarDraft`, `Download[]`, `Timeline`/`TimelineEvent`, `CaseRecord`,
`SpineItem`, `User`/permissions, `SavedView`, `NavBadges`, `SystemStatus`, `ExamplePrompt`.

**Model claims removed:** `xgboost_v4` (there is no XGBoost model — the ML is an
unsupervised IsolationForest plus MiniBatchKMeans peer clustering), PSI drift reporting, and
all SHAP / per-feature attribution strings. The risk engine is an **additive weighted sum**,
so its own contributions and counterfactuals *are* the explanation; that is what `charts`
delivers instead of attribution values.

**Behaviours removed:** live transaction screening / tickers, and editable risk thresholds.
Tier boundaries are fixed in code (§7.1) and are not client-configurable.

**Tool roster:** version 2.0 listed fourteen aspirational nodes. Nine exist (§3). These never
existed as selectable tools: `intent_classifier`, `tool_selector`, `entity_resolver`,
`transaction_loader`, `graph_builder`, `detection_engine`, `direct_aggregation`,
`explainability`, `recommendation_engine`, `report_generator`. Intent parsing, planning and
narration are pipeline stages reported through `spec`, `plan_trace` and `sources` — not
roster entries.

**Also note:** there is no per-tool animation data beyond `duration_ms`. If you want a replay
animation, drive it from `plan_trace[].duration_ms` and `invocations`.

---

## 10. `audit`

The run receipt. Every field also appears elsewhere; this is the single object to persist or
export.

| Field | Type | Description |
|---|---|---|
| `query` | string | |
| `typology` | string | |
| `intent` | string[] | |
| `tools_run` | string[] | Tools with status `ran` |
| `tools_skipped` | `[string, string][]` | `[tool, reason]` |
| `winning_hypothesis` | string | Top case's winner, `""` when nothing flagged |
| `alternatives` | `[string, string, string][]` | `[id, label, band]` for **every** hypothesis considered |
| `risk` | number | Top case risk, `0.0` when nothing flagged |
| `escalation` | string | Top case escalation, `"monitor"` when nothing flagged |
| `evidence_ids` | string[] | Claim ids |
| `narrative` | string | |
| `plan_trace` | `PlanTraceEntry[]` | Same as top-level |

`alternatives` includes the losers with their bands (`strong`, `moderate`, `weak`, `neutral`,
`weakened`, `contradicted`). Showing the rejected theories, and how badly they lost, is the
strongest single element you can put on screen.

---

## 11. `execution` — the query-aware execution summary

| Field | Type | Description |
|---|---|---|
| `query` | string | Verbatim |
| `intent` | string[] | Detected |
| `typology` | string | Detected |
| `typology_recognized` | boolean | `false` when an unknown typology fell back to the default route |
| `entities` | string[] | Detected account ids |
| `entities_note` | string | `"no account entity detected in the query"` when empty, else `""` |
| `filters` | object | Applied filters |
| `filters_note` | string | `"no filter applied"` when none, else `""` |
| `scoped_transactions` | number \| null | Rows in scope. `null` when unfiltered |
| `total_transactions` | number \| null | Rows in the dataset |
| `cost` | object | §11.1 |
| `notes` | string[] | Failed tools, absent entities, route substitutions, filter effects |

When a filter is active, `scoped_transactions` / `total_transactions` is the honest "we looked
at N of M" line. Both are `null`/absent when nothing narrowed the slice.

### 11.1 `execution.cost`

| Field | Type | Description |
|---|---|---|
| `max_candidates` | number | Screening cap (default 500) |
| `max_investigations` | number | Full-investigation cap (default 25) |
| `max_roundtrips_per_candidate` | number | Cost guard (default 16) |
| `candidate_pool_size` | number | Candidates screened in |
| `candidates_eligible` | number | Accounts that met the screening floor |
| `candidates_dropped` | number | Accounts below the floor |
| `investigated` | number | Candidates fully investigated |
| `excluded` | number | Investigated but benign/indeterminate, so not flagged |
| `returned` | number | `== findings.length` |
| `roundtrips_max_per_candidate` | number | Measured worst candidate |
| `roundtrips_total` | number | Measured total |
| `wall_clock_ms` | number | Measured run duration |
| `budget_ms` | number | Configured budget (default 30000) |
| `within_budget` | boolean | |

`investigated == returned + excluded`. Surfacing "screened 500, investigated 25, flagged 3,
cleared 22" is a strong reviewer-confidence line and costs you one row.

---

## 12. `charts` — six derived payloads

Every payload shares an envelope:

| Field | Type | Description |
|---|---|---|
| `id` | string | Stable payload id |
| `title` | string | Suggested heading |
| `available` | boolean | `false` when its source was missing |
| `reason` | string \| null | Why unavailable. Populated only when `available` is `false` |

**Every number in every payload also appears elsewhere in the same response.** The builder
copies values and rounds to two decimals; it computes no new statistic. A test enforces this.
So charts are as proof-carrying as the evidence.

Payloads degrade independently: one unavailable payload does not affect the other five.

| Key | Body | Ordering | Unavailable when |
|---|---|---|---|
| `risk_contribution` | `entries[{family, contribution}]` | contribution desc, ties by family | no family contributed |
| `counterfactual` | `entries[{label, score}]` | source order (`full` first, then `-family`) | no contributing family |
| `hypothesis_scoreboard` | `entries[{id, label, kind, raw, normalized, band, matched[], contradicted[]}]` | normalized desc, ties by id | no findings |
| `findings_table` | `rows[{node, risk, tier, escalation}]` | findings order, ≤ `max_investigations` | no findings |
| `evidence_table` | `rows[{family, claim, calculation, value, direction, strength, tx_ids[≤25], tx_count}]` plus `total_records`, `rows_omitted` | ledger order, ≤50 rows | no findings |
| `data_profile` | `payment_format` (a `Distribution`), `amounts` (an `AmountSummary`) | see §13 | EDA declined or slice empty |

Notes for rendering:

- `counterfactual` labels read `"full"`, `"-peer_deviation"`, `"-flow_through-network_convergence"`. A bar chart against `full` shows which evidence is load-bearing — the most persuasive single visual in the product.
- `evidence_table.tx_ids` is capped at 25 per row while `tx_count` reports the true total, so "25 of 412" is honest rather than a silent truncation.
- `hypothesis_scoreboard` includes losing hypotheses with negative `normalized`. Do not clamp them to zero; a contradicted theory is information.

---

## 13. `eda` — the data profile

`null` when the planner declined the EDA tool, which it does for entity-scoped questions
(profiling the whole dataset would not change the answer for one named account).

| Field | Type | Description |
|---|---|---|
| `scope_active` | boolean | Whether a filter was applied |
| `scope` | object | The applied filters |
| `transactions` | number | Rows profiled |
| `accounts` | number | Distinct accounts in the slice |
| `distributions` | object | Keys `payment_format`, `payment_currency`, `receiving_currency` |
| `cross_currency_count` | number | |
| `cross_currency_rate` | number \| null | 0–1 fraction |
| `amounts` | `AmountSummary` \| null | |
| `time_span` | `TimeSpan` \| null | |
| `quality` | `DataQuality` | |

**`Distribution`** — `{ column, entries: [{category, count}], remainder_categories, remainder_count }`.
At most 20 entries, ordered by count desc then category asc; anything beyond is collapsed
into the remainder fields so no row is silently dropped.

**`AmountSummary`** — `{ count, min, max, mean, median, p95, sum }` over `amount_base` (USD-normalised). Raw numbers.

**`TimeSpan`** — `{ first, last, span_days, active_days }`. ISO-8601 timestamps.
`active_days` is distinct calendar days with activity, so `active_days << span_days` signals
bursty behaviour.

**`DataQuality`** — `{ null_timestamps, non_positive_amounts, unpriced_currency_transactions, unpriced_currencies[] }`.
`unpriced_currencies` are currencies with no configured FX rate, so their `amount_base` is
unreliable. A non-zero count deserves a visible caveat rather than a hidden footnote.

---

## 14. `feature_manifest`

`null` when no selected tool consumed account-level features.

| Field | Type | Description |
|---|---|---|
| `features` | `[{name, definition}]` | Exactly 10, each definition ≤200 chars |
| `cluster_features` | string[] | The 9 features used for peer clustering |
| `accounts` | number | Rows in the feature table |
| `source` | `"warmup" \| "built"` | Reused from startup, or built during this run |

The ten features: `out_count`, `out_sum`, `out_degree`, `in_count`, `in_sum`, `in_degree`,
`txn_count`, `span_days`, `velocity`, `io_ratio`. All derived from transactions only — no
demographics, no labels.

---

## 15. Errors

Every error uses one envelope:

```json
{ "error": { "code": "ACCOUNT_NOT_FOUND",
             "message": "Account 9999|XX does not exist in HI-Small.",
             "detail": { "node": "9999|XX" } } }
```

`detail` is optional. Responses never contain a stack trace, a raw exception message, or a
source path.

| Status | `code` | Meaning | Client action |
|---|---|---|---|
| 400 | `EMPTY_QUERY` | Query blank after trimming | Validate before sending |
| 400 | `UNRESOLVABLE_QUERY` | Broad query with nothing rankable | Show the message |
| 404 | `ACCOUNT_NOT_FOUND` | Named account absent from the dataset | Show `detail.node` |
| 422 | `VALIDATION_ERROR` | Body invalid, e.g. query > 500 chars | Fix the request |
| 500 | `INVESTIGATION_FAILED` | Unexpected failure | Offer retry |
| 503 | `WARMING_UP` | Dataset still loading | Poll `/health` until ready |
| 503 | `ENGINE_ERROR` | Dataset failed to load | Show `/health.error` |

**404 has exactly one meaning: the named account does not exist.** An empty result set is a
200 (§7.3). Do not treat "nothing flagged" as an error.

---

## 16. Integration checklist

1. `GET /health`; poll until `status == "ready"`. Show `variant` and `transactions` in the status strip.
2. `GET /roster` once; render the idle plan rail from it.
3. `POST /investigate`. Client timeout 60 s.
4. Drive the execution view from `plan_trace` (statuses, reasons, `duration_ms`) and `execution`.
5. Render `findings` as the triage queue. Handle all three `winning_kind` values.
6. On `findings: []`, render `no_findings_reason` as an empty state and keep `plan_trace` visible.
7. Build the graph from `case.members` / `feeders_included` / `beneficiaries` / `excluded`.
8. Render `charts` payloads, skipping any with `available: false` (show its `reason`).
9. Surface `sources` and `validated` as trust indicators.
10. Format all numbers yourself. The backend sends nothing pre-formatted.
