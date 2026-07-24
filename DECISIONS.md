# NEXUS-AML — Decisions Log

A running record of durable decisions and *why*. Update when a decision changes (add a new dated row; don't silently rewrite history).

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-24 | **Dataset: IBM AMLworld only** (drop SAML-D) | Labeled, graph-structured, has entity mapping + typology ground truth; one clean schema |
| 2026-07-24 | **Dev + demo on HI-Small; false-positive eval on LI-Small** | HI = easy-to-find examples for the demo; LI = realistic rarity to prove FP reduction |
| 2026-07-24 | **Never build a global graph — subgraphs on filtered slices only** | HI-Small is millions of rows; a full NetworkX graph would exhaust memory and burn debugging time/credits |
| 2026-07-24 | **DuckDB is the canonical store; pandas for small slices only** | Avoids two parallel code paths; keeps memory low |
| 2026-07-24 | **Use real labeled patterns instead of seeding rings** | AMLworld ships labeled FAN-IN / GATHER-SCATTER = real, ground-truthed rings; leaner (no seeding code) and a stronger claim ("we caught a real labeled ring"). Only the **benign lookalike** is hand-crafted (it has no natural label) |
| 2026-07-24 | **Lead with FAN-IN (smurfing); structuring is the near-threshold facet** | No native "structuring below threshold" label exists; FAN-IN is labeled and graph-visual. Confirm demo-worthy FAN-IN instances via Phase-1 per-typology counts before committing |
| 2026-07-24 | **CYCLE available as the layering showcase** | Labeled and visually striking (money loops to origin); promote from roadmap to a live trace if time allows |
| 2026-07-24 | **Defer FX table; keep an `amount_base` seam + `cross_currency` flag** | AMLworld is heavily USD; profile first, build FX only if non-USD is material. Seam avoids a later retrofit |
| 2026-07-24 | **Defer account→entity mapping until a query needs it** | The two core typologies are account-level; don't build customer-level plumbing before something consumes it |
| 2026-07-24 | **Repo layout: Python under `backend/`, frontend gets its own dir later** | Clean separation so the UI has somewhere to sit; prevents drift |
| 2026-07-24 | **Held-out Patterns file is an integrity rule, not a note** | Feeding labels to the agent invalidates all metrics; kept in a separate ground-truth module |
| 2026-07-24 | **Node identity = `(Bank, Account)`** | Hex account codes repeat across banks |
| 2026-07-24 | **No accuracy metric; use precision@K / recall / F1 / PR-AUC** | ~0.1% positive rate makes accuracy meaningless |
| 2026-07-24 | **Structuring = seed ONE small case (disclosed), not derived from FAN-IN** | The full FAN-IN example is large-amount consolidation (legs from ~62k to 676 billion Ruble), not near-threshold — so FAN-IN does not double as structuring. Smurfing/consolidation still uses real labeled FAN-IN |
| 2026-07-24 | **Load all bank/account codes as strings; no int coercion, no re-padding** | Bank codes are variable-length (`026`, `029`, `000`, `00952`, `0072043`); different lengths are different banks. Coercing to int would drop leading zeros and cause collisions/failed joins |
| 2026-07-24 | **Log-scaled / robust amount features** | Amounts span ~1e3 to ~1e11+; mean/std would be dominated by outliers |
| 2026-07-24 | **`cross_currency` (in≠out) is optional, not core** | Every observed row has received==paid, same currency; currency handling matters for cross-transaction comparison, not within a row |
