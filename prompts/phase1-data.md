# PHASE 1 PROMPT — Data Layer (trimmed, backend-first)

Context: assume the steering files, `DATA-SPEC.md`, and `DECISIONS.md`. Do NOT re-explain the project or re-derive the schema — read those.

Goal: a clean, verified DATA LAYER only. No detectors, no scoring, no graph builder, no UI.

Tech: Python 3.11 · DuckDB (canonical store) · pandas (small slices only) · Pydantic v2. Package lives under `backend/nexus/`.

## Preconditions
- Raw files in `data/raw/`: the `*_Trans.csv` files, the `*_Patterns.txt` files, and the Accounts file.

## Tasks
1. `backend/nexus/config.py` — dataset paths, variant (default `HI-Small`), base currency (`USD`), near-threshold value (configurable), a place-holder FX map (empty for now).
2. `backend/nexus/schemas.py` (Pydantic):
   - `Transaction` (all 10 columns + `tx_id`, parsed `timestamp`, `amount_base`, `cross_currency`, `from_node=(bank,account)`, `to_node=(bank,account)`, optional `is_laundering`).
   - `PatternInstance` (`typology: str`, `transactions: list`, `accounts: set`, header params if any).
3. `backend/nexus/ingest.py`:
   - `load_transactions(variant)` → load into **DuckDB**; add `tx_id` (stable row index), parse timestamp, add `amount_base` (= Amount Paid when USD; else leave = amount for now — DO NOT build an FX table yet), add `cross_currency` (Receiving ≠ Payment currency). Detect and handle the optional `Is Laundering` column (present → use it; absent → leave null, to be derived from patterns).
4. `backend/nexus/ground_truth.py` (HELD OUT — never imported by detectors):
   - `parse_patterns(variant)` → scan `BEGIN…END` blocks, capture typology from the header, collect rows, resolve involved `(Bank,Account)` accounts → return `list[PatternInstance]`.
5. `backend/nexus/profile.py` — a one-shot profiling script printing: #transactions, #distinct accounts, currency distribution, payment-format distribution, laundering rate (if `Is Laundering` present), and **pattern-instance count per typology** (from ground_truth).

## Out of scope (do NOT build)
- Graph builder (belongs to the graph-tool phase; subgraphs only, later).
- Account→entity mapping (build only when a customer-level query needs it).
- FX conversion table (deferred until profiling proves non-USD is material).
- Any detector, scoring, evidence, or UI code.

## Constraints
- DuckDB canonical; pandas only for tiny slices.
- Patterns file is HELD OUT — only `ground_truth.py` touches it; nothing at inference does.
- Node key = `(Bank, Account)`.

## Acceptance
- `python -m backend.nexus.profile --variant HI-Small` loads into DuckDB and prints the profiling summary + per-typology pattern counts.
- `parse_patterns` returns `PatternInstance` objects; counts print per typology.
- Sanity: if `Is Laundering` exists, its positive count is in the same ballpark as the transactions enumerated across all pattern blocks.
- No graph/detector/scoring/UI code committed.

## When green
Stop and report: the profiling summary + per-typology pattern counts + whether `Is Laundering` was present and the currency mix. These numbers decide Phase 2. Do not proceed to Phase 2.
