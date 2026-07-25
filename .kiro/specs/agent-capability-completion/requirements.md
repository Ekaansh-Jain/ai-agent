# Requirements Document

## Introduction

NEXUS-AML already investigates money laundering with a deterministic scoring core (hypothesis duel, additive risk engine, proof-carrying evidence ledger, validator-gated narration). An audit against the hackathon brief found that three of the five required agent capabilities are strong (anomaly detection, risk classification, explanation), while two are present in the codebase but unreachable by the agent (exploratory data analysis, feature engineering), and four of the six recommended agent outputs are incomplete (ranked multi-item findings, per-item risk, supporting charts/tables/metrics, query-aware execution summary with real telemetry). Parsed query filters are also discarded before any tool sees them.

This feature closes those gaps **additively**. The scoring core stays locked: `duel.py`, `risk.py`, and `hypotheses/library.yaml` are unchanged, and the locked scoring anchors (Phase 2 fixture = 86.65, real ring = 53.18) must still reproduce exactly. New capability is delivered as new agent-callable tools, new planner nodes, filter-aware slicing, a ranked two-stage screening funnel, per-tool execution telemetry, and derived presentation payloads. The feature also rewrites `FRONTEND_BACKEND_DATA_CONTRACT.md` so the document describes what the backend actually produces, since the frontend will be built against that document.

Every requirement below is written to be verifiable by a test in the existing harness: the hermetic unit suite (`cd backend && .venv/bin/python -m pytest -q`) or the real-data integration suite (`NEXUS_RUN_INTEGRATION=1 .venv/bin/python -m pytest -q`).

## Glossary

- **Agent**: The end-to-end run path `orchestrator.run()` — intent parse, plan, execute, score, narrate, validate.
- **Intent_Parser**: `intent.py` — natural-language query to `InvestigationSpec`.
- **Planner**: `planner.py` — selects which tools run for a spec and records declined tools with reasons.
- **Orchestrator**: `orchestrator.py` — executes the plan and assembles the run result.
- **Case_Builder**: `casebuilder.py` — investigates one seed account and returns a `Case`.
- **Risk_Engine**: `risk.py` — additive weighted sum to 0..100, tier, escalation, counterfactuals. Locked.
- **Duel_Engine**: `duel.py` plus `hypotheses/library.yaml` — hypothesis scoring. Locked.
- **Narrator**: `narrator.py` plus the LLM narration edge in `llm.py`.
- **Claim_Validator**: `validator.py` — rejects any narrative number that does not trace to the case evidence.
- **Evidence_Ledger**: `ledger.py` — ordered store of `EvidenceRecord` objects for one investigation.
- **EDA_Tool**: New agent-callable exploratory-data-analysis tool that profiles a transaction slice.
- **Feature_Builder**: New agent-visible wrapper over `profiles.build_profiles()` that reports engineered account features as a declinable plan step.
- **Candidate_Screener**: New cheap prefilter over the in-memory account profiles table that produces a ranked candidate pool for broad queries.
- **Finding**: One flagged account returned by a broad query, carrying risk score, tier, escalation, evidence, and explanation.
- **Findings_List**: The ordered list of `Finding` objects returned for one query.
- **Plan_Trace**: The per-tool execution record: tool id, status, reason, measured duration, rows in, rows out.
- **Chart_Builder**: New component that derives chart, table, and metric payloads from data the backend already holds.
- **Filter_Scope**: The SQL and DataFrame predicate derived from `InvestigationSpec.filters` (`payment_format`, `month`).
- **Neutral_Family**: An evidence family that is absent from every hypothesis fingerprint and absent from every risk weight profile, and therefore changes no score. The existing `anomaly` family is a Neutral_Family.
- **Anchor**: A pinned scoring output that must not change: Phase 2 fixture risk = 86.65, real ring risk = 53.18.
- **Ground_Truth**: `*_Patterns.txt` parsed instances and the `is_laundering` column — evaluation only.
- **API**: `nexus/api/app.py` FastAPI application, `POST /investigate` and `GET /health`.
- **Contract_Document**: `FRONTEND_BACKEND_DATA_CONTRACT.md` at the repository root.
- **Unit_Suite**: `cd backend && .venv/bin/python -m pytest -q` (hermetic, LLM forced off).
- **Integration_Suite**: `NEXUS_RUN_INTEGRATION=1 .venv/bin/python -m pytest -q` over `tests/cases/real_cases.json`.

## Requirements

### Requirement 1: Regression-pin the locked scoring anchors before any adjacent change

**User Story:** As the project owner, I want the locked scoring numbers pinned by tests before new capability lands, so that any accidental recalibration is caught by the suite instead of discovered in a demo.

#### Acceptance Criteria

1. THE Unit_Suite SHALL contain a test that scores the Phase 2 fixture evidence set through THE Risk_Engine and asserts the resulting risk score equals 86.65 within an absolute tolerance of 0.01.
2. THE Unit_Suite SHALL contain a test that scores the ring fixture in `backend/tests/fixtures/` for the ring hub account through THE Risk_Engine and asserts the resulting risk score equals 53.18 within an absolute tolerance of 0.01.
3. THE Unit_Suite SHALL contain a test that asserts THE Risk_Engine counterfactual output for the Phase 2 fixture matches a pinned sequence, comparing entry count, entry order, each entry label, and each entry score to an absolute tolerance of 0.01, where the pinned sequence is the sequence produced at the time the test is written.
4. WHERE a change modifies code that computes evidence `strength` or evidence `direction`, including a refactor intended to leave the mathematics unchanged, THE design or task notes for that change SHALL record, for each of the two Anchors, the value before the change and the value after the change to two decimal places, together with an explicit statement of whether that Anchor changed.
5. THE Unit_Suite SHALL contain a test that asserts THE Risk_Engine family weight tables expose the same family names and the same weight values as at the start of this feature, with each weight compared to an absolute tolerance of 0.001 and each typology profile's weights summing to 1.0 within 0.001.
6. THE Unit_Suite SHALL contain a test that asserts THE Duel_Engine hypothesis set for each supported typology exposes the same hypothesis identifiers, the same hypothesis kinds, and the same per-family importance values as at the start of this feature, with each importance value compared to an absolute tolerance of 0.001.
7. IF any Anchor assertion in THE Unit_Suite fails, THEN THE Unit_Suite SHALL exit with a non-zero status and SHALL report which Anchor deviated, its expected value, and its observed value.
8. WHEN THE Unit_Suite is executed twice with the LLM disabled and without the integration environment flag set, THE Anchor tests SHALL produce identical scores across both runs and SHALL require no network access or dataset outside `backend/tests/fixtures/`.

### Requirement 2: Agent-callable exploratory data analysis

**User Story:** As an analyst, I want the agent to profile the data slice my query targets, so that I can see distributions and data quality before trusting any flag.

#### Acceptance Criteria

1. THE EDA_Tool SHALL expose a typed function that accepts a DuckDB connection, an optional Filter_Scope, and an Evidence_Ledger, and SHALL return a structured profile result carrying every metric named in criteria 5 through 12 of this requirement.
2. WHEN THE EDA_Tool is called with a Filter_Scope, THE EDA_Tool SHALL compute every reported metric over only the transactions that satisfy that Filter_Scope, and SHALL report the applied `payment_format` value, the applied `month` value, and the resulting profiled transaction count.
3. THE EDA_Tool SHALL derive every reported metric exclusively from the normalized `transactions` table columns `timestamp`, `from_bank`, `sender_account`, `to_bank`, `receiver_account`, `amount_paid`, `amount_received`, `payment_currency`, `receiving_currency`, `payment_format`, `amount_base`, and `cross_currency`.
4. WHEN THE EDA_Tool is called without a Filter_Scope, THE EDA_Tool SHALL compute every reported metric over every row of the normalized `transactions` table, and SHALL report the profiled scope as unfiltered.
5. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report the profiled transaction count and the distinct account count, where one account is the pair of bank identifier and account identifier, and the distinct count is taken over the union of the `from_bank` with `sender_account` pairs and the `to_bank` with `receiver_account` pairs of the profiled slice.
6. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report one distribution for `payment_format`, one for `payment_currency`, and one for `receiving_currency`, each as category-and-count entries ordered by count descending with a deterministic tie-break on ascending category name, each bounded to at most the 20 highest-count categories, with every remaining category collapsed into a single residual entry reporting the number of collapsed categories and their combined transaction count.
7. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report the count of profiled transactions whose `cross_currency` value is true, and the cross-currency rate expressed as that count divided by the profiled transaction count as a fraction in the range 0.0 to 1.0 inclusive.
8. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report, over the `amount_base` values of the profiled slice, the contributing value count, minimum, maximum, mean, median, 95th percentile, and sum.
9. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report time-span coverage as the earliest non-null `timestamp`, the latest non-null `timestamp`, the inclusive count of calendar days between them, and the count of distinct calendar days holding at least one profiled transaction.
10. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report the count of profiled transactions whose `timestamp` is null, and separately the count of profiled transactions whose `amount_paid` or `amount_received` is less than or equal to zero.
11. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report the count of profiled transactions whose `payment_currency` or `receiving_currency` has no configured conversion rate in `Settings.fx_per_usd`, together with the distinct names of those currencies bounded to at most 20 names ordered by descending transaction count.
12. WHEN THE EDA_Tool runs, THE EDA_Tool SHALL report every metric of criteria 5 through 11 as a value in the returned profile result, and SHALL report a metric as absent rather than as zero when the profiled slice provides no value for it.
13. WHEN the profiled transaction count is greater than zero, THE EDA_Tool SHALL append at least one `EvidenceRecord` to THE Evidence_Ledger whose `transactions` list holds between 1 and 50 transaction identifiers, every one of which belongs to the profiled slice.
14. THE EDA_Tool SHALL emit every `EvidenceRecord` under a Neutral_Family with `strength` in the range 0.0 to 1.0 inclusive and `direction` drawn from the values `high` or `low`, so that every Anchor value is unchanged.
15. THE EDA_Tool SHALL return every metric through its return value, and SHALL write nothing to standard output, so that THE Orchestrator can consume the results.
16. WHEN the profiled slice holds zero transactions, THE EDA_Tool SHALL return a profile result reporting a transaction count of zero, a distinct account count of zero, empty distributions, absent amount summary statistics, and absent time-span coverage, and SHALL append no `EvidenceRecord` to THE Evidence_Ledger.
17. IF any column listed in criterion 3 is absent from the `transactions` table or cannot be read, THEN THE EDA_Tool SHALL return no profile result, SHALL raise a typed error indicating which column is absent or unreadable, and SHALL leave THE Evidence_Ledger with the same records in the same order as before the call.
18. THE EDA_Tool SHALL appear exactly once in THE Planner tool roster.
19. WHEN THE Planner selects THE EDA_Tool for a query, THE Orchestrator SHALL invoke THE EDA_Tool and SHALL consume the returned profile result.

### Requirement 3: Ground truth stays held out at inference

**User Story:** As the project owner, I want the new tools to be provably label-free, so that the honest-evaluation claim survives scrutiny.

#### Acceptance Criteria

1. THE EDA_Tool SHALL restrict every column it reads from the `transactions` table to the twelve columns listed in Requirement 2 criterion 3, and SHALL read no `is_laundering` column even when that column is present in the table.
2. THE Feature_Builder SHALL restrict every column it reads from the `transactions` table to the twelve columns listed in Requirement 2 criterion 3, and SHALL read no `is_laundering` column even when that column is present in the table.
3. THE Candidate_Screener SHALL rank candidates using only the engineered account features listed in Requirement 4 criterion 2, and SHALL read no Ground_Truth artefact.
4. THE Unit_Suite SHALL contain a test that asserts the source module of THE EDA_Tool, the source module of THE Feature_Builder, and the source module of THE Candidate_Screener each contain no occurrence of the four held-out symbols `Patterns.txt`, `parse_patterns`, `GroundTruth`, and `is_laundering`.
5. THE offline reporting script `profile.py` SHALL remain byte-identical to its pre-feature version.
6. THE Unit_Suite SHALL contain a test that asserts every module in the import closure reachable from THE Agent entry point `orchestrator.run`, excluding test modules, `backend/scripts/`, and `nexus/eval/`, contains no occurrence of the four held-out symbols listed in criterion 4.
7. THE Unit_Suite SHALL contain a test that asserts no module in the import closure defined in criterion 6 imports `nexus.ground_truth` or `nexus.profile`.
8. IF the scan in criterion 4 or criterion 6 finds an occurrence of a held-out symbol, THEN THE Unit_Suite SHALL fail and SHALL report the offending module name and the matched symbol.

### Requirement 4: Feature engineering as a visible, declinable agent step

**User Story:** As a reviewer, I want to see feature engineering as a step the agent decided to run, so that the model-ready feature layer is credited and auditable rather than hidden in warmup.

#### Acceptance Criteria

1. THE Feature_Builder SHALL expose a typed function that accepts a DuckDB connection and an optional prebuilt account feature table, and returns both the engineered account feature table, indexed by the node string `bank|account` with exactly one row per account observed in the analysed slice, and a feature manifest.
2. THE Feature_Builder SHALL return a feature table whose columns are exactly `out_count`, `out_sum`, `out_degree`, `in_count`, `in_sum`, `in_degree`, `txn_count`, `span_days`, `velocity`, `io_ratio`, and no other columns.
3. WHEN a prebuilt feature table is supplied by THE API warmup, THE Feature_Builder SHALL return that supplied table with its index and column set unchanged and SHALL execute no profile-building query against THE DuckDB connection.
4. WHEN THE Feature_Builder runs, THE Feature_Builder SHALL appear exactly once in THE Plan_Trace with status `ran` and a measured duration in milliseconds that is greater than or equal to zero.
5. WHERE no tool selected for the plan declares the engineered account feature table as a required input, THE Planner SHALL record THE Feature_Builder with status `skipped` and a plain-language reason of 1 to 200 characters stating that no selected tool consumes account-level features.
6. WHERE at least one tool selected for the plan declares the engineered account feature table as a required input, THE Planner SHALL select THE Feature_Builder and SHALL order it ahead of every tool that declares that table as an input.
7. WHEN THE Feature_Builder builds the feature table because no prebuilt table was supplied, THE Feature_Builder SHALL produce the same index set as the current `build_profiles()` implementation for the same input slice, with the integer-valued columns `out_count`, `out_degree`, `in_count`, `in_degree`, `txn_count` equal exactly and the remaining columns equal within a relative tolerance of 1e-9.
8. THE feature manifest SHALL contain exactly one entry per returned feature column, each entry carrying the feature name and a definition string of 1 to 200 characters, and SHALL identify the nine-member clustering feature list `CLUSTER_FEATURES` in the order currently exported by `profiles.py`.
9. WHEN THE Feature_Builder returns a feature table, THE Feature_Builder SHALL record in THE Plan_Trace the returned account row count as rows-out and a reason stating whether the table was reused from THE API warmup or built during the run.
10. IF the analysed slice contains zero transactions, THEN THE Feature_Builder SHALL return a zero-row feature table that retains all ten columns and the complete feature manifest, SHALL record rows-out of zero in THE Plan_Trace, and SHALL return without raising an error.

### Requirement 5: Query filters scope the analysis

**User Story:** As an analyst, I want "analyse cash deposits in March" to actually restrict the analysis to cash transactions in March, so that the agent answers the question I asked.

#### Acceptance Criteria

1. WHEN `InvestigationSpec.filters` contains the key `payment_format`, THE Orchestrator SHALL apply a Filter_Scope that selects exactly those transactions whose normalized `payment_format` value equals the filter value under case-insensitive exact comparison, and that excludes every transaction with any other `payment_format` value.
2. WHEN `InvestigationSpec.filters` contains the key `month`, THE Orchestrator SHALL apply a Filter_Scope that selects exactly those transactions whose `timestamp` falls in the named calendar month in any year present in the dataset, and that excludes every transaction in any other calendar month.
3. WHEN `InvestigationSpec.filters` contains both `payment_format` and `month`, THE Orchestrator SHALL apply one Filter_Scope that selects only transactions satisfying both predicates simultaneously.
4. WHILE a Filter_Scope is active, THE Orchestrator SHALL report in THE Plan_Trace each applied filter key together with the value applied for that key.
5. WHILE a Filter_Scope is active, THE Orchestrator SHALL emit, from every tool the Filter_Scope was applied to, only transaction identifiers that satisfy every predicate of that Filter_Scope.
6. WHILE a Filter_Scope is active, THE Orchestrator SHALL report in THE Plan_Trace a filtered transaction count equal to the number of dataset transactions satisfying every predicate of that Filter_Scope.
7. IF a Filter_Scope selects zero transactions, THEN THE Agent SHALL return an empty Findings_List with no risk score and no escalation action.
8. IF a Filter_Scope selects zero transactions, THEN THE Agent SHALL return a stated reason naming each filter key and applied value that produced the empty slice, including any applied value that matches no value present in the dataset.
9. WHERE THE Filter_Scope cannot be applied to a tool without changing that tool's locked calibration, THE Planner SHALL record in THE Plan_Trace that the tool ran on the unfiltered slice, naming each filter key that was not applied to that tool.
10. THE Unit_Suite SHALL contain a test that runs one filtered query and one unfiltered query against the same fixture and asserts that the reported filtered transaction count is strictly lower than the unfiltered transaction count, that the reported filtered count equals the number of fixture transactions satisfying the filter predicates, and that every transaction identifier emitted by the filtered run's filter-applied tools satisfies those predicates.

### Requirement 6: Ranked multi-item findings for broad queries

**User Story:** As an analyst, I want "flag high-risk customers" to return a ranked list of suspicious accounts, so that I get a triage queue instead of a single account.

#### Acceptance Criteria

1. WHEN a query names no account entity, THE Candidate_Screener SHALL build a candidate pool by ranking the rows of the engineered account feature table with an in-memory prefilter, and SHALL order that pool by descending prefilter rank with equal ranks ordered by ascending lexicographic comparison of the `bank|account` node identifier string.
2. IF the number of ranked rows exceeds the configured maximum candidate count, THEN THE Candidate_Screener SHALL retain only the highest-ranked rows up to that configured count and SHALL discard the remainder.
3. WHEN the candidate pool is built, THE Orchestrator SHALL run THE Case_Builder on candidates in pool order until either the configured maximum investigation count is reached or the candidate pool is exhausted, whichever occurs first.
4. THE Orchestrator SHALL order THE Findings_List by risk score in descending order, and IF two findings have risk scores that are equal when compared at two-decimal precision, THEN THE Orchestrator SHALL order those two findings by ascending lexicographic comparison of their `bank|account` node identifier strings.
5. THE Orchestrator SHALL include, for every item in THE Findings_List, the `bank|account` node identifier, the risk score as a value from 0.00 to 100.00, the risk tier from the set `low`, `medium`, `high`, the escalation action from the set `monitor`, `review`, `report`, and non-empty explanation text.
6. WHEN a query names one or more account entities that are present in the engineered account feature table, THE Orchestrator SHALL return exactly one Finding for each present named entity, including entities whose investigation returns `winning_kind` `benign` or `indeterminate`.
7. WHEN the same query is run twice against the same dataset with the LLM disabled, THE Agent SHALL return THE Findings_List with the same number of items in the same order, with identical account identifiers, and with risk score, risk tier, and escalation action identical at two-decimal precision for every item.
8. THE Orchestrator SHALL report in THE Plan_Trace, each as a non-negative integer, the candidate pool size, the investigated candidate count, the count of investigated candidates excluded from THE Findings_List, and the returned finding count, where the returned finding count equals the number of items in THE Findings_List.
9. THE Case_Builder public function signature and returned `Case` field semantics SHALL stay backward compatible with the current implementation.
10. THE Orchestrator SHALL include, for every item in THE Findings_List, at least one evidence record whose transaction list contains at least one transaction identifier drawn from the investigated dataset.
11. IF a named account entity is absent from the engineered account feature table, THEN THE Orchestrator SHALL return no Finding for that entity, SHALL record in THE Plan_Trace a reason identifying the absent account identifier, and SHALL continue investigating any remaining named entities.
12. WHERE a query names no account entity, IF an investigated candidate returns `winning_kind` `benign` or `indeterminate`, THEN THE Orchestrator SHALL exclude that candidate from THE Findings_List.

### Requirement 7: Bounded cost for broad queries

**User Story:** As a demo operator, I want broad queries to finish in a predictable time on a 5-million-row dataset, so that the live demo does not stall.

#### Acceptance Criteria

1. WHILE THE Candidate_Screener is ranking candidates, THE Candidate_Screener SHALL read only the in-memory engineered account feature table and SHALL issue zero database queries, so that the observed query count attributable to ranking is zero regardless of candidate pool size.
2. WHERE the candidate pool holds more candidates than the configured maximum investigation count, THE Orchestrator SHALL issue full-investigation database work only for the highest-ranked candidates up to that count and SHALL issue no full-investigation database work for the remaining candidates.
3. THE Orchestrator SHALL issue no more database round-trips per investigated candidate than the configured maximum round-trips-per-candidate value.
4. IF the configured maximum candidate count or the configured maximum investigation count is zero, THEN THE Orchestrator SHALL run no Case_Builder investigation, SHALL return an empty Findings_List, and SHALL record the zero-valued cap as the stated reason in THE Plan_Trace.
5. IF the configured maximum candidate count or the configured maximum investigation count is greater than or equal to the number of available candidates, THEN THE Orchestrator SHALL process every available candidate and SHALL complete without an out-of-range error.
6. THE new tools SHALL query transaction data through DuckDB.
7. THE new tools SHALL materialise into pandas only the result rows returned for one Filter_Scope-bounded or seed-bounded query, and SHALL hold no pandas copy of the full `transactions` table.
8. THE new tools SHALL construct NetworkX graphs only over a slice bounded by a seed neighbourhood, a Filter_Scope, or both.
9. THE new tools SHALL construct no NetworkX graph over the full `transactions` table or the full account population.
10. THE Integration_Suite SHALL contain a test that runs one broad query end to end against HI-Small, records the measured wall-clock duration of that run, and asserts that the measured duration does not exceed the broad-query wall-clock budget value read from configuration.
11. IF the integration environment flag is absent or the HI-Small dataset is unavailable, THEN THE Integration_Suite SHALL skip the test in criterion 10 with a skip reason naming the missing flag or the missing dataset, and SHALL report no failure and no error.
12. THE configured maximum candidate count, the configured maximum investigation count, the configured maximum round-trips-per-candidate value, and the broad-query wall-clock budget SHALL be readable from configuration.
13. WHEN a broad query completes, THE Orchestrator SHALL report in THE Plan_Trace the configured maximum candidate count, the configured maximum investigation count, the observed database round-trip count per investigated candidate, and the measured wall-clock duration of the run.

### Requirement 8: Query-aware execution summary with real telemetry

**User Story:** As a reviewer, I want to see exactly which tools ran, which were declined and why, and how long each took, so that the claim of dynamic planning is checkable.

#### Acceptance Criteria

1. THE Planner SHALL return exactly one Plan_Trace entry for every tool in THE Planner roster, covering invoked, declined, and failed tools, with no roster tool absent and no roster tool repeated.
2. THE Plan_Trace entry SHALL carry the tool identifier as declared in THE Planner roster.
3. THE Plan_Trace entry SHALL carry a display label of 1 to 60 characters.
4. THE Plan_Trace entry SHALL carry exactly one status value drawn from the set `ran`, `skipped`, `failed`.
5. THE Plan_Trace entry SHALL carry a plain-language reason of 1 to 200 characters that contains no stack trace and no source file path.
6. THE Plan_Trace entry SHALL carry a measured duration expressed in milliseconds whose value is greater than or equal to zero.
7. THE Orchestrator SHALL order THE Plan_Trace so that entries with status `ran` or `failed` come first in the order their execution was attempted, followed by entries with status `skipped` in THE Planner roster declaration order.
8. WHERE a tool reads or produces row-countable data, THE Plan_Trace entry SHALL carry a rows-in count and a rows-out count, each an integer greater than or equal to zero.
9. WHERE a tool with status `ran` reads and produces no row-countable data, THE Plan_Trace entry SHALL report rows-in and rows-out as not applicable, distinguishable by a consumer from a measured count of zero.
10. WHEN a tool status is `skipped`, THE Plan_Trace entry SHALL report a duration of zero milliseconds.
11. WHEN a tool status is `ran`, THE Plan_Trace entry SHALL report the wall-clock time elapsed between the start and the end of that tool's execution, and SHALL report a value strictly greater than zero even when the elapsed time is below one millisecond, so that a fast tool is never indistinguishable from a `skipped` tool.
12. IF a tool raises an error during execution, THEN THE Orchestrator SHALL record that tool in THE Plan_Trace with status `failed` and a reason naming the tool and describing the resulting loss of analysis.
13. IF a tool raises an error during execution, THEN THE Orchestrator SHALL attempt the remaining tools in the plan and SHALL complete the run.
14. IF a tool has status `failed`, THEN THE Orchestrator SHALL exclude every evidence record originating from that tool from THE Evidence_Ledger used for scoring, so that the verdict carries no contribution from the failed tool.
15. IF a tool has status `failed`, THEN THE Orchestrator SHALL omit that tool from the `tools_run` list of THE `AuditReceipt`, so that no consumer can read the verdict as having used that tool's output.
16. IF every tool selected for execution has status `failed`, THEN THE Agent SHALL return no risk score and no escalation action, and SHALL return a reason naming the failed tools.
17. THE Orchestrator SHALL include the original query text as submitted in the execution summary.
18. THE Orchestrator SHALL include the detected `InvestigationSpec.intent` values in the execution summary.
19. THE Orchestrator SHALL include the detected `InvestigationSpec.typology` value in the execution summary.
20. THE Orchestrator SHALL include the detected `InvestigationSpec.entities` values in the execution summary, or a statement that no entity was detected when that list is empty.
21. THE Orchestrator SHALL include each applied Filter_Scope filter name with its applied value in the execution summary, or a statement that no filter was applied when no filter is active.
22. THE `AuditReceipt` SHALL retain its existing `tools_run` and `tools_skipped` fields with their current meaning.
23. THE `AuditReceipt` SHALL carry THE Plan_Trace as an additional field alongside its existing fields.
24. THE Unit_Suite SHALL contain a test asserting that every measured duration in THE Plan_Trace is greater than or equal to zero.
25. THE Unit_Suite SHALL contain a test asserting that every tool in THE Planner roster appears exactly once in THE Plan_Trace.

### Requirement 9: Plan varies with intent as well as typology

**User Story:** As a reviewer, I want different query shapes to produce visibly different plans, so that the "no fixed pipeline" claim is demonstrated rather than asserted.

#### Acceptance Criteria

1. THE Planner SHALL include THE EDA_Tool and THE Feature_Builder in its tool roster as independently selectable nodes, so that each appears in THE Plan_Trace with status `ran` or `skipped` on every run.
2. WHEN `InvestigationSpec.intent` contains `explain` and `InvestigationSpec.entities` contains at least one account node, THE Planner SHALL decline THE EDA_Tool.
3. WHEN THE Planner declines THE EDA_Tool, THE Planner SHALL record in THE Plan_Trace a plain-language reason stating that the query is entity-scoped explanation and reporting the count of named entities in the spec.
4. WHEN `InvestigationSpec.intent` contains `detect` and `InvestigationSpec.entities` is empty, THE Planner SHALL select THE Candidate_Screener.
5. WHEN `InvestigationSpec.intent` contains `detect` and `InvestigationSpec.entities` is empty, THE Planner SHALL select THE Feature_Builder.
6. WHEN `InvestigationSpec.intent` contains `trace`, THE Planner SHALL select every roster tool that traverses the transaction graph.
7. WHEN `InvestigationSpec.intent` contains `trace`, THE Planner SHALL bound the traversal of every selected graph-traversing tool to `InvestigationSpec.trace_depth` hops, where `trace_depth` is an integer in the inclusive range 1 to 3.
8. WHEN `InvestigationSpec.intent` contains two or more values, THE Planner SHALL select the union of the tool sets selected by each individual intent value, and SHALL list each selected tool exactly once in the invoked-tool set.
9. IF one intent value in a spec selects a tool that another intent value in the same spec declines, THEN THE Planner SHALL select that tool and SHALL record the selecting intent value in the Plan_Trace reason for that tool.
10. IF `InvestigationSpec.typology` is not one of the typologies with a defined route, THEN THE Planner SHALL return a plan using the documented default route without raising an error, and SHALL record in THE Plan_Trace that the typology value was unrecognized and which route was substituted.
11. THE Planner SHALL derive the invoked-tool set from `InvestigationSpec.intent`, `InvestigationSpec.typology`, and the count of `InvestigationSpec.entities` only, so that two specs with identical values for those three inputs produce identical invoked-tool sets.
12. THE Planner SHALL produce, for the `smurfing` typology and for the `structuring` typology, the same set of scoring tools that the current implementation produces, where a scoring tool is a roster tool whose evidence families appear in a Duel_Engine hypothesis fingerprint or carry a non-zero weight in a Risk_Engine weight profile.
13. THE Planner SHALL widen any existing typology route only with tools whose evidence records are emitted under a Neutral_Family, so that Anchor values are unchanged.
14. THE Unit_Suite SHALL contain a test asserting that the following four spec shapes produce four pairwise-distinct invoked-tool sets: intent `[detect]` with zero entities and typology `smurfing`; intent `[explain]` with exactly one entity and typology `smurfing`; intent containing `trace` with exactly one entity, typology `smurfing`, and `trace_depth` 2; intent `[detect]` with zero entities and typology `structuring`.
15. THE Unit_Suite SHALL contain a test asserting that the scoring tool set produced for a `smurfing` spec with one entity and the scoring tool set produced for a `structuring` spec with one entity each equal the sets produced by the current implementation.

### Requirement 10: Per-finding explanation tied to query intent

**User Story:** As an analyst, I want a short plain-language reason for every flagged item that references my query and the detected pattern, so that I can act without reading raw evidence.

#### Acceptance Criteria

1. THE Narrator SHALL produce exactly one explanation text for every Finding in THE Findings_List.
2. THE explanation text for a Finding SHALL be at most 400 characters long.
3. WHERE the winning kind of a Finding is `suspicious` or `benign`, THE explanation text for that Finding SHALL name the detected typology and the winning hypothesis label of that Finding.
4. THE explanation text for a Finding SHALL name the risk tier of that Finding as one of `low`, `medium`, `high` and the escalation action of that Finding as one of `monitor`, `review`, `report`.
5. THE explanation text for a Finding SHALL restate at least one intent term recorded in THE `InvestigationSpec` for the query that produced that Finding.
6. IF the winning kind of a Finding is `indeterminate`, THEN THE Narrator SHALL state in the explanation text for that Finding that the available evidence does not separate the competing explanations, and SHALL omit any suspicious or benign conclusion and any winning hypothesis label for that Finding.
7. IF the winning kind of a Finding is `benign`, THEN THE Narrator SHALL state in the explanation text for that Finding that a benign explanation prevailed, and SHALL name `monitor` as the escalation action for that Finding.
8. WHEN LLM-generated explanation text is produced for a Finding, THE Claim_Validator SHALL validate that text using only the evidence records attached to that Finding, before THE Orchestrator accepts the text.
9. IF LLM-generated explanation text for a Finding contains a number that does not trace to the evidence records of that Finding, including a number that traces only to the evidence records of another Finding in THE Findings_List, THEN THE Claim_Validator SHALL report that number as unsupported.
10. IF THE Claim_Validator reports one or more unsupported numbers for a Finding, THEN THE Orchestrator SHALL reject the LLM-generated explanation text for that Finding.
11. IF the LLM-generated explanation text for a Finding is rejected, THEN THE Orchestrator SHALL use the deterministic template explanation text produced by THE Narrator for that Finding.
12. IF the LLM narration edge is disabled or unreachable, returns empty text, or does not return within 15 seconds, THEN THE Orchestrator SHALL use the deterministic template explanation text produced by THE Narrator for every Finding in THE Findings_List, so that every Finding still carries explanation text satisfying criteria 1 through 7.
13. THE Integration_Suite SHALL assert that THE Claim_Validator reports zero unsupported numeric claims across the explanation text of every Finding returned for every case in `tests/cases/real_cases.json`.
14. THE Orchestrator SHALL derive the risk score, risk tier, and escalation action of every Finding from THE Risk_Engine, so that no LLM-generated text contributes to or alters those three values.
15. WHEN THE Findings_List is empty, THE Orchestrator SHALL report no risk score, no risk tier, and no escalation action, and SHALL NOT substitute any LLM-derived score, tier, or escalation action.

### Requirement 11: Supporting charts, tables, and metrics derived from held data

**User Story:** As a reviewer, I want charts and tables that back up the verdict, so that I can sanity-check the reasoning visually.

#### Acceptance Criteria

1. THE Chart_Builder SHALL produce exactly six payloads for one run result: a risk-contribution breakdown, a counterfactual comparison, a hypothesis scoreboard, a findings table, an evidence table, and a data-profile distribution set.
2. THE Chart_Builder SHALL produce the risk-contribution breakdown with one entry per family present in THE Risk_Engine `contributions` mapping for the item, no entry for any family absent from that mapping, each entry value equal to the mapped value compared at two-decimal precision with no rescaling, normalisation, or re-rounding, and entries ordered by contribution value descending with ties broken by ascending family name.
3. THE Chart_Builder SHALL produce the counterfactual comparison as a sequence of the same length and the same order as THE Risk_Engine `counterfactuals` output for the item, reproducing each returned label verbatim and each returned score compared equal at two-decimal precision.
4. THE Chart_Builder SHALL produce the findings table with one row per Finding in THE Findings_List, in THE Findings_List order, bounded above by the configured maximum investigation count, each row reproducing that Finding's account identifier, risk score, risk tier, and escalation action unchanged, with numeric values compared equal at two-decimal precision.
5. WHERE THE EDA_Tool ran, THE Chart_Builder SHALL produce the data-profile distribution set from THE EDA_Tool result only, carrying the payment-format distribution bounded to the 20 highest-count categories with all remaining categories aggregated into a single remainder entry, and the amount distribution summary statistics as reported, every count and statistic reproduced unchanged.
6. THE Chart_Builder SHALL populate every numeric value in every payload by copying a numeric value already present in the run result, applying no transformation other than rounding to two decimal places, and SHALL compute no new statistic, ratio, or aggregate of its own.
7. THE Unit_Suite SHALL contain a test that collects every numeric value in every Chart_Builder payload and every numeric value reachable from the run result, asserts that each collected payload value matches at least one run-result value when both are compared at two-decimal precision, and fails while naming the payload and the offending value on the first unmatched value.
8. THE Chart_Builder SHALL produce every payload without loading a supervised model artifact and without importing a feature-attribution library, using only dependencies already declared by the project.
9. IF an input source for a payload is unavailable, because THE EDA_Tool did not run, or THE Findings_List is empty, or fewer than two families contributed to the item score, THEN THE Chart_Builder SHALL emit that payload with zero entries together with a stated reason naming the unavailable source, SHALL raise no error, and SHALL emit the remaining payloads unchanged.
10. THE Chart_Builder SHALL produce the hypothesis scoreboard with one entry per hypothesis score returned by THE Duel_Engine for the item, each entry reproducing that score's identifier, label, kind, raw score, normalised score, band, matched family list, and contradicted family list unchanged with numeric values compared equal at two-decimal precision, and entries ordered by normalised score descending with ties broken by ascending hypothesis identifier.
11. THE Chart_Builder SHALL produce the evidence table with one row per `EvidenceRecord` of the item in Evidence_Ledger order, bounded to a maximum of 50 rows, each row reproducing that record's family, claim, calculation, value, direction, and strength unchanged, listing at most 25 transaction identifiers per row alongside that row's total transaction count, and reporting the total record count whenever rows are omitted by the row bound.

### Requirement 12: API exposes the new capability additively

**User Story:** As a frontend developer, I want the new data on the existing endpoint without breaking changes, so that I can build one integration.

#### Acceptance Criteria

1. THE API SHALL return THE Plan_Trace in every successful `POST /investigate` response as an additional top-level entry alongside the existing response keys.
2. THE API SHALL return THE Findings_List in every successful `POST /investigate` response as an additional top-level entry, preserving the order produced by THE Orchestrator.
3. THE API SHALL return THE Chart_Builder payloads in every successful `POST /investigate` response as an additional top-level entry.
4. THE API SHALL return the existing `POST /investigate` top-level keys `spec`, `plan`, `case`, `narrative`, `validated`, `unsupported`, `sources`, and `audit` in every successful response, each retaining its current meaning and value type.
5. THE API SHALL retain the current nested entries of `plan` (`run`, `skipped`) and of `sources` (`intent`, `narrator`) with their current meanings.
6. WHEN THE Findings_List contains at least one Finding, THE API SHALL return `case` as the case of the highest-ranked Finding, reporting the same account identifier, risk score, risk tier, and escalation action as that Finding.
7. IF THE Findings_List is empty, THEN THE API SHALL return the empty-findings error response and SHALL return no success body, so that `case` is never absent or null in a successful response.
8. IF a query yields an empty Findings_List, THEN THE API SHALL return a 404 response in the existing error envelope carrying error code `NO_FINDINGS` and a message naming the submitted query text and the applied Filter_Scope that produced no findings.
9. THE API SHALL retain the current 404 response with error code `ACCOUNT_NOT_FOUND` for a named account absent from the loaded variant, and SHALL start no investigation for that request.
10. IF `POST /investigate` is received while the dataset is still loading, THEN THE API SHALL return the current 503 response with error code `WARMING_UP` instructing the caller to poll `GET /health`, and SHALL start no investigation.
11. IF `POST /investigate` is received after dataset loading has failed, THEN THE API SHALL return the current 503 response with error code `ENGINE_ERROR`.
12. IF the submitted query is empty after surrounding whitespace is removed, THEN THE API SHALL return the current 400 response with error code `EMPTY_QUERY`.
13. IF the submitted query cannot be resolved to any rankable target, THEN THE API SHALL return the current 400 response with error code `UNRESOLVABLE_QUERY` and a message stating why the query could not be resolved.
14. THE API SHALL retain the current `POST /investigate` request body validation that accepts a query of 1 to 500 characters inclusive and rejects an absent or longer query with the existing validation error response.
15. IF an unhandled error occurs while producing THE Plan_Trace, THE Findings_List, or THE Chart_Builder payloads, THEN THE API SHALL return the current 500 response with error code `INVESTIGATION_FAILED`, and that response SHALL contain no stack trace, no raw exception message text, and no source file or line reference.
16. WHEN the same query is submitted twice against the same loaded dataset with the LLM disabled, THE API SHALL return identical content for every response key, excluding only the measured durations carried in THE Plan_Trace.
17. THE API SHALL report a presence indicator for THE EDA_Tool and a presence indicator for THE Feature_Builder from `GET /health`.
18. THE API SHALL retain the existing `GET /health` keys `status`, `data_loaded`, `error`, `variant`, `transactions`, `accounts`, `llm_enabled`, `llm_model`, and `anomaly_model` with their current meanings.
19. WHILE the dataset is loading, THE API SHALL answer `GET /health` within 1000 milliseconds and SHALL report data-loaded as not ready.
20. THE Unit_Suite SHALL contain a test asserting that a successful `POST /investigate` response contains all eight retained top-level keys.
21. THE Unit_Suite SHALL contain a test asserting that a successful `POST /investigate` response contains THE Plan_Trace, THE Findings_List, and THE Chart_Builder payloads.
22. THE Unit_Suite SHALL contain a test asserting that a query yielding an empty Findings_List produces a 404 response carrying error code `NO_FINDINGS`.

### Requirement 13: LLM boundary is preserved

**User Story:** As the project owner, I want the deterministic core untouched by the LLM, so that the trust claim holds for every new capability.

#### Acceptance Criteria

1. THE Intent_Parser SHALL be the only component that invokes the LLM intent-parsing edge.
2. THE Narrator SHALL be the only component that invokes the LLM narration edge.
3. WHEN THE Candidate_Screener is invoked twice in succession with the same engineered account feature table and the same `InvestigationSpec`, THE Candidate_Screener SHALL return candidate pools with identical membership, identical ordering, and identical ranking values.
4. WHEN THE Planner is invoked twice in succession with the same `InvestigationSpec`, THE Planner SHALL return an identical invoked-tool set, an identical declined-tool set, and identical declined reasons.
5. THE Planner SHALL derive tool selection exclusively from the `InvestigationSpec` fields `intent`, `typology`, `entities`, `filters`, and `trace_depth`.
6. THE EDA_Tool SHALL complete every invocation without an LLM call.
7. THE Feature_Builder SHALL complete every invocation without an LLM call.
8. THE Chart_Builder SHALL complete every invocation without an LLM call.
9. THE Unit_Suite SHALL contain a test asserting that no source module under `backend/nexus/` other than THE Intent_Parser module and THE Narrator module imports or calls the LLM module (`llm.py`).
10. IF the LLM intent edge returns content that is unparseable or that fails `InvestigationSpec` validation, THEN THE Intent_Parser SHALL discard that content, SHALL produce the `InvestigationSpec` by deterministic parsing, and SHALL report the intent path as deterministic.
11. IF an LLM call exceeds a configured timeout of at most 10 seconds or the LLM provider is unreachable during a run, THEN THE Agent SHALL complete that run on the deterministic path, SHALL surface no error to the caller, and SHALL report the affected path as deterministic for intent or template for narration.
12. WHERE the LLM is enabled, IF the LLM provider rejects a call with a rate-limit or quota response, THEN THE Agent SHALL issue no further LLM call for the remainder of that run and SHALL complete the run on the deterministic path.
13. WHILE any LLM edge is degraded by unavailability, malformed output, a rate-limit response, or Claim_Validator rejection, THE Agent SHALL return the same Findings_List content, the same ordering, and the same Risk_Engine score, tier, and escalation action per finding that it returns for the same query and dataset with the LLM disabled.
14. THE Agent SHALL report in the run result `sources` field the path that produced the intent, drawn from the set `llm`, `deterministic`, and the path that produced the narration, drawn from the set `llm`, `template`.
15. WHERE the LLM is disabled, THE Agent SHALL complete every run end to end, returning a populated `InvestigationSpec`, a Plan_Trace covering every roster tool, a Findings_List, and narration text.
16. THE Unit_Suite SHALL execute with the LLM forced off and SHALL pass with zero LLM invocations.

### Requirement 14: Contract document describes the real backend

**User Story:** As a frontend developer, I want the data contract to describe what the backend actually returns, so that I build against reality instead of an aspiration.

#### Acceptance Criteria

1. THE Contract_Document SHALL describe exactly two endpoints, `POST /investigate` and `GET /health`, and SHALL state that THE API implements no other endpoint.
2. THE Contract_Document SHALL state that THE API is the source of truth for this contract and that any behaviour described only by a frontend artefact is outside the contract.
3. THE Contract_Document SHALL document the request contract of `POST /investigate` as a body carrying a single `query` string field with a minimum length of 1 character and a maximum length of 500 characters, and SHALL state that a body outside those bounds is rejected with a validation error response carrying the error envelope.
4. THE Contract_Document SHALL document every field returned by `POST /investigate` using the field names THE API emits, covering the retained keys `spec`, `plan`, `case`, `narrative`, `validated`, `unsupported`, `sources`, `audit` and the additional keys required by Requirement 12.
5. THE Contract_Document SHALL document every field returned by `GET /health` using the field names THE API emits: `status`, `data_loaded`, `error`, `variant`, `transactions`, `accounts`, `llm_enabled`, `llm_model`, `anomaly_model`, and SHALL state that `variant` identifies the loaded AMLworld dataset and that `transactions` and `accounts` report the loaded row and account counts of that dataset.
6. THE Unit_Suite SHALL contain a test that extracts the field names THE Contract_Document documents for `POST /investigate` and for `GET /health` and compares each extracted set against the field names present in an actual response from THE API for the same endpoint.
7. IF the field names documented for an endpoint differ from the field names present in the actual response for that endpoint, THEN THE Unit_Suite test SHALL fail and SHALL report the field names that are documented but absent and the field names that are present but undocumented.
8. THE Contract_Document SHALL exclude the aspirational fourteen-node tool roster and its node identifiers, and SHALL contain no reference to `intent_classifier`, `tool_selector`, `entity_resolver`, `transaction_loader`, `eda_profiler`, `graph_builder`, `detection_engine`, `direct_aggregation`, `explainability`, `recommendation_engine`, or `report_generator` as tools THE Planner can select.
9. THE Contract_Document SHALL exclude the `xgboost_v4` model version and any drift-index reporting, and SHALL document the anomaly model presence indicator that THE API actually returns instead.
10. THE Contract_Document SHALL exclude per-feature attribution output, and SHALL state that THE Risk_Engine explains a score through its additive contributions and counterfactuals rather than through feature-attribution values.
11. THE Contract_Document SHALL exclude live transaction screening, and SHALL state that THE API answers one query per request with no streaming or continuously updated transaction feed.
12. THE Contract_Document SHALL exclude the nine unimplemented read endpoints for bootstrap, watchtower, case list, case detail, entity graph, transaction ledger, model registry, case report, and audit log.
13. THE Contract_Document SHALL exclude every response object THE API does not emit, covering the SAR draft, download list, timeline, case record, spine item, and user-permission objects, and SHALL exclude the `POST /api/investigations` path in favour of `POST /investigate`.
14. THE Contract_Document SHALL document the tool roster THE Planner actually reports, listing for each tool the tool identifier and display label THE Planner emits, and including THE EDA_Tool and THE Feature_Builder.
15. THE Contract_Document SHALL document every field of a Plan_Trace entry: tool identifier, display label, status drawn from `ran`, `skipped`, `failed`, reason, measured duration in milliseconds, rows-in, and rows-out.
16. THE Contract_Document SHALL document every field of a Findings_List item: account identifier, risk score, risk tier, escalation action, evidence records, and explanation text, together with the ordering rule that items are sorted by descending risk score with a tie-break on account identifier.
17. THE Contract_Document SHALL document each payload shape THE Chart_Builder returns, covering the risk-contribution, counterfactual, hypothesis-scoreboard, findings-table, evidence-table, and data-profile distribution payloads, and SHALL state that every numeric value in those payloads also appears elsewhere in the same response.
18. THE Contract_Document SHALL document the escalation vocabulary as exactly the values `monitor`, `review`, `report`, and SHALL state that THE API emits no other escalation value.
19. THE Contract_Document SHALL document the risk tier vocabulary as exactly the values `low`, `medium`, `high` over a 0 to 100 risk score, with `high` at a score of 70 or above, `medium` at a score of 40 or above and below 70, and `low` at a score below 40.
20. THE Contract_Document SHALL document the `winning_kind` vocabulary as exactly the values `suspicious`, `benign`, `indeterminate`, and SHALL state that an `indeterminate` verdict is returned with risk score 0, tier `low`, escalation `monitor`, and no asserted conclusion.
21. THE Contract_Document SHALL document the confidence vocabulary as exactly the values `weak`, `moderate`, `strong`, `high`, and SHALL state that confidence is a label derived from evidence margin and corroboration rather than a probability, so that no arithmetic is performed on it.
22. THE Contract_Document SHALL document the observed wall-clock response latency of `POST /investigate` as a measured minimum-to-maximum range for a named-entity query and for a broad query, each range stated with the dataset and hardware under which it was measured.
23. THE Contract_Document SHALL document the warmup behaviour of `GET /health`: the endpoint answers while the dataset loads in a background thread, `data_loaded` is false and `status` is not `ready` until loading completes, and a client polls the endpoint until `status` is `ready` before calling `POST /investigate`.
24. THE Contract_Document SHALL document the error envelope as a single top-level error object carrying a machine-readable code, a human-readable message, and an optional detail value.
25. THE Contract_Document SHALL document every error code THE API returns with the response status that accompanies it: `ENGINE_ERROR` and `WARMING_UP` at 503, `EMPTY_QUERY` and `UNRESOLVABLE_QUERY` at 400, `ACCOUNT_NOT_FOUND` and `NO_FINDINGS` at 404, `INVESTIGATION_FAILED` at 500, and `VALIDATION_ERROR` at 422.
26. THE Contract_Document SHALL document both 404 cases separately: the response returned for a named account that is absent from the loaded dataset, and the response returned when a query yields an empty Findings_List.
27. THE Contract_Document SHALL state, for every documented field, whether the value is a raw value for the frontend to format or a pre-formatted display string emitted by the backend.

### Requirement 15: Existing behaviour and test harness stay green

**User Story:** As the project owner, I want the current suites to keep passing, so that additive capability does not regress the demo.

#### Acceptance Criteria

1. WHEN THE Unit_Suite runs after this feature is complete, THE Unit_Suite SHALL report at least twenty-five passed tests, zero failed tests, and zero errored tests, where twenty-five is the pre-feature passing count.
2. THE Unit_Suite SHALL retain every pre-feature test function by name across `test_phase1.py`, `test_phase2.py`, `test_phase3a.py`, `test_phase3b.py`, `test_phase3c.py`, `test_phase4.py`, `test_phase5.py`, `test_anomaly.py`, `test_api_hardening.py`, and `test_integration_realdata.py`.
3. IF a pre-feature test function is deleted, renamed, marked skipped or expected-to-fail, or has an existing assertion removed or replaced by a weaker bound, THEN THE change SHALL be treated as a regression and SHALL NOT be part of this feature.
4. WHEN THE Integration_Suite runs, THE Integration_Suite SHALL assert that the count of unsupported claims equals zero across all forty-one cases.
5. WHEN THE Integration_Suite runs, THE Integration_Suite SHALL assert that every returned escalation value is one of `monitor`, `review`, or `report`.
6. WHEN THE Integration_Suite runs, THE Integration_Suite SHALL assert that every reported number traces to at least one transaction identifier present in the case evidence.
7. WHEN THE Integration_Suite runs, THE Integration_Suite SHALL assert that each case is routed to the expected typology recorded for that case in `backend/tests/cases/real_cases.json`.
8. WHEN THE Integration_Suite runs the same case twice with unchanged input, THE Integration_Suite SHALL assert that the risk score, tier, escalation, and evidence ordering are identical across both runs.
9. WHEN THE Integration_Suite runs, THE Integration_Suite SHALL report precision and recall computed over exactly forty-one cases from `backend/tests/cases/real_cases.json` and SHALL report no claim of superiority over the threshold baseline.
10. WHEN THE Integration_Suite runs after this feature, THE reported precision and recall SHALL be recorded, rounded to two decimal places, alongside the pre-feature values of precision 0.58 and recall 0.33.
11. IF the post-feature precision, rounded to two decimal places, is below 0.58, or the post-feature recall, rounded to two decimal places, is below 0.33, THEN THE Integration_Suite result SHALL be recorded as a reported regression stating the pre-feature value, the post-feature value, and the difference for each affected metric, and THE pre-feature values SHALL remain the comparison bars.
12. THE Unit_Suite SHALL contain at least one hermetic test for THE EDA_Tool, at least one for THE Feature_Builder, at least one for THE Candidate_Screener, and at least one for THE Chart_Builder, each using only the fixtures in `backend/tests/fixtures/`.
13. WHILE THE Unit_Suite is running, THE Unit_Suite SHALL make zero outbound network calls, SHALL run with the LLM forced off, and SHALL read no file under `data/raw/`.
14. WHEN THE Unit_Suite runs with the `NEXUS_RUN_INTEGRATION` environment variable unset, THE Integration_Suite tests SHALL be reported as skipped, with zero failed tests and zero errored tests.
15. THE new modules added by this feature SHALL live under `backend/nexus/`, and no new module SHALL be added at the repository root.

## Assumptions and Open Design Decisions

These are deliberately left to the design phase and are called out so the requirements above stay solution-free:

1. **Screening and cost cutoffs.** The configured maximum candidate count, the configured maximum investigation count, the configured maximum database round-trips per investigated candidate, and the broad-query wall-clock budget referenced in Requirements 6, 7, and 11 are latency-versus-coverage tradeoffs. The design phase sets the default values and states the measured latency behind each choice.
2. **Prefilter ranking signal.** The exact feature combination THE Candidate_Screener uses to rank the pool is a design decision, constrained by Requirement 3 (features only, no labels) and Requirement 13 (deterministic).
3. **Filter propagation depth.** Which existing tools accept a Filter_Scope directly, versus which run on the unfiltered slice with that scope stated in THE Plan_Trace (Requirement 5 criterion 9), is decided in design so that no tool's locked calibration shifts.
4. **Neutral family names.** The specific family strings used by THE EDA_Tool and THE Feature_Builder are design choices; the binding constraint is that the families appear in no hypothesis fingerprint and no risk weight profile.
5. **Chart payload schema.** The concrete payload shape for THE Chart_Builder is designed alongside the Contract_Document rewrite so that both describe one schema. The evidence-table bounds in Requirement 11 criterion 11 (50 rows, 25 transaction identifiers per row) are provisional and confirmed in design.
6. **Empty-findings status code.** Requirement 12 criteria 7 and 8 map an empty Findings_List to a 404 with code `NO_FINDINGS`. This is an open decision flagged for confirmation: a 404 discards THE Plan_Trace and THE Chart_Builder payloads on exactly the path where an analyst most wants to see what was searched, and it overloads 404 with both "the account does not exist" and "nothing crossed the bar". The alternative is a 200 carrying an empty Findings_List, no case, the full Plan_Trace, and a stated reason, reserving 404 for the unknown named account. Design records whichever is confirmed, and Requirement 14 criterion 26 follows it.
