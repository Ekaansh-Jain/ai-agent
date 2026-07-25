# Tech

## Stack
- Python 3.11. Backend first; frontend (React+Vite+TS, Cytoscape) comes later.
- DuckDB + pandas (SQL over CSVs, read only needed columns).
- NetworkX (graph motifs + bounded traversal, no training).
- scikit-learn + numpy (IsolationForest, KMeans, z-scores).
- Pydantic v2 (typed InvestigationSpec, Hypothesis, EvidenceRecord, Case).
- FastAPI (async, streams reasoning trace). SQLite for cases/audit.

## Rules
- Keep code lean. Prefer small pure functions over frameworks and abstraction.
- Deterministic core; LLM only at the edges (parse in, narrate out). LLM = Gemini (free tier)
  via `llm.py`, optional: no key -> deterministic parser + template narrator. LLM narration
  MUST pass the claim validator or it's rejected for the template (LLM can't inject a number).
- Each tool = typed function: transactions in → EvidenceRecord(s) out. Independently testable, no LLM.
- **ML models:** MiniBatchKMeans (peer clustering, live) + IsolationForest (`anomaly.py`,
  unsupervised, trained on account profiles — no label leakage). The anomaly score is a
  NEUTRAL evidence family (out of risk weights + fingerprints) and the generic-AI eval
  baseline. Train once: `python scripts/train_model.py` -> `models/` (git-ignored).
- Build against the normalized internal model, not raw CSV.
- **DuckDB is the canonical store.** Use pandas only for small slices pulled out for compute — no parallel DataFrame-as-store path.
- **Never build a global graph.** HI-Small is ~5M rows; build NetworkX subgraphs on filtered slices (seed neighborhood, date/format filter) on demand, never the full population.
- **Flag sensor recalibration.** Any change to how a tool computes strength/direction, or to risk weights, must be reported at the time with before/after on every affected anchor number (e.g. Phase 2 fixture = 86.65; real ring = HI-Small node `0048309|811C599A0` = 53.18, stable only since the profile row order was pinned — pre-fix it drew from [52.08, 52.86, 53.18] per rebuild). Anchors and their provenance live in `backend/tests/cases/anchors.json`. The scoring engine (duel/risk math) stays locked; extensions must default to preserving existing numbers.
- **Deterministic row order into ML.** `profiles.build_profiles` sorts by node id. MiniBatchKMeans is row-order sensitive even with a fixed `random_state`, and DuckDB's unordered joins are not order-stable, so any new frame that feeds a model must impose its own total order.

## Data
AMLworld HI-Small CSV headers (map on load):
`Timestamp, From Bank, Account, To Bank, Account(.1), Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Form`
→ normalize to: `timestamp, from_bank, sender_account, to_bank, receiver_account, amount_received, receiving_currency, amount_paid, payment_currency, payment_format`.
Note: two source columns are both `Account`; the second loads as `Account.1`. Label column `Is Laundering` may be absent — fall back to seeded ground-truth if so.
