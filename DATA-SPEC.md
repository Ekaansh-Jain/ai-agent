# NEXUS-AML — Dataset Specification (canonical)

> This is the **authoritative** description of the data. Where it differs from the Data section of `NEXUS-AML-Design-Document.md`, **this file wins.** Point the build agent here.

Dataset: **IBM AMLworld** (synthetic, multi-agent virtual world; labeled).

---

## 1. Files

| Group | Transactions | Ground-truth patterns |
|---|---|---|
| HI-Small | `HI-Small_Trans.csv` | `HI-Small_Patterns.txt` |
| HI-Medium | `HI-Medium_Trans.csv` | `HI-Medium_Patterns.txt` |
| HI-Large | `HI-Large_Trans.csv` | `HI-Large_Patterns.txt` |
| LI-Small | `LI-Small_Trans.csv` | `LI-Small_Patterns.txt` |
| LI-Medium | `LI-Medium_Trans.csv` | `LI-Medium_Patterns.txt` |
| LI-Large | `LI-Large_Trans.csv` | `LI-Large_Patterns.txt` |
| — | **Accounts file** (`Bank Name, Bank ID, Account Number, Entity ID, Entity Name`) | — |

- **HI vs LI** = Higher vs Lower Illicit ratio (HI = more laundering; LI = realistically rare).
- **Small / Medium / Large** = size (Small ≈ millions of rows; larger = much bigger).
- **Recommended usage:** develop + demo on **HI-Small**; run false-positive evaluation on **LI-Small** (realistic rarity). Mention a Large variant only for a scalability line.

---

## 2. Transactions CSV — columns (in order)

| # | Column | Meaning |
|---|---|---|
| 1 | `Timestamp` | YYYY/MM/DD HH:MM |
| 2 | `From Bank` | numeric bank code (sender's bank) |
| 3 | `Account` | hex code — **sender** account |
| 4 | `To Bank` | numeric bank code (receiver's bank) |
| 5 | `Account` | hex code — **receiver** account (2nd "Account" column → read as `Account.1`) |
| 6 | `Amount Received` | amount received, in the receiving currency |
| 7 | `Receiving Currency` | e.g. US Dollar, Euro, Yuan, Yen |
| 8 | `Amount Paid` | amount paid, in the payment currency |
| 9 | `Payment Currency` | currency of the paid amount |
| 10 | `Payment Format` | Cash, Wire, ACH, Cheque, Credit Card, … |

> **Check for an 11th column `Is Laundering` (0/1)** at the end of the header. Standard AMLworld includes it. The build must handle **both** cases (see Labels below).

---

## 3. Accounts file — columns

`Bank Name`, `Bank ID`, `Account Number`, `Entity ID`, `Entity Name`

- Maps accounts → **entities** (customers). One **Entity** can own **many accounts across banks**.
- Enables optional **entity/customer-level** analysis (e.g., "flag high-risk customers").
- **No demographics** (no age/income/account-type/balance). `Entity Name` only loosely hints person vs. company.

---

## 4. Patterns file — format (the ANSWER KEY)

Blocks delimited by `BEGIN LAUNDERING ATTEMPT - <TYPE>` … `END LAUNDERING ATTEMPT - <TYPE>`. Each inner line is a full transaction row (same schema as Trans) ending in a trailing `1` (= laundering). The **typology** is in the BEGIN header.

Observed typologies (graph-shaped): **STACK, CYCLE, FAN-IN, FAN-OUT, GATHER-SCATTER, SCATTER-GATHER, BIPARTITE, RANDOM**. (Headers may carry parameters, e.g. "CYCLE: Max 12 hops", "FAN-IN: Max 9-degree Fan-In".)

Example blocks:

```
BEGIN LAUNDERING ATTEMPT - STACK
2022/08/09 05:14,00952,8139F54E0,0111632,8062C56E0,5331.44,US Dollar,5331.44,US Dollar,ACH,1
... (6 rows total) ...
END LAUNDERING ATTEMPT - STACK

BEGIN LAUNDERING ATTEMPT - CYCLE:  Max 12 hops
2022/08/01 00:19,0134266,814167590,0036925,810E343A0,132713.46,Yuan,132713.46,Yuan,ACH,1
... (12 rows; note the last row returns to the origin account 0134266/814167590) ...
END LAUNDERING ATTEMPT - CYCLE
```

**There is NO native "structuring below threshold" typology** — all labels are graph-shaped.

---

## 5. Confirmed characteristics

- **Bank codes are variable-length strings** — e.g. `026`, `029`, `000`, `00952`, `0072043`. They are NOT fixed width and leading zeros are significant. **Load ALL bank + account codes as strings; never coerce to int; never re-pad.** (`026` and `0072043` are different banks, not the same bank padded differently.)
- **Multi-currency (5+ currencies observed):** US Dollar, Euro, Yuan, Yen, **Ruble**. A single scheme can mix currencies across hops (the CYCLE spans four).
- **Per row, received == paid and same currency** in every observed example, so a `cross_currency` (in≠out) flag rarely fires. Currency handling matters for **cross-transaction / cross-account** comparison, not within a row.
- **Extreme amount magnitudes:** amounts span from ~1,000 up to hundreds of billions (a FAN-IN leg shows `675,961,603,689.89` Ruble). **Use log-scaled / robust features** (median, IQR) — naive mean/std will be dominated by outliers.
- **FAN-IN is large-amount consolidation, NOT near-threshold.** The labeled FAN-IN legs are large and wildly varying, so FAN-IN does **not** double as "structuring." (See §4 note and DECISIONS.)
- **Node identity = `(Bank, Account)` pair** — hex account codes repeat across banks; keying on `Account` alone would wrongly merge accounts.
- **Entity mapping available** via the Accounts file (`<variant>_accounts.csv`, e.g. `HI-Large_accounts.csv`) → account → Entity ID.
- **No unique transaction ID** in the data.
- **Severe class imbalance** — laundering is rare (especially LI).

---

## 6. Integrity & handling rules (non-negotiable)

1. **HELD-OUT PATTERNS:** `*_Patterns.txt` is ground truth for **evaluation only**. It must **NEVER** be fed to the agent/detectors at inference. Keep it in a separate `ground_truth` module.
2. **Node key = `(Bank, Account)`.** Provide an option to collapse to `Entity ID` for a customer-level view (build only when a query needs it).
3. **String typing:** read all bank codes and account codes as **strings** (`dtype=str`); never coerce to int, never re-pad. Verify the Accounts↔Trans join by match rate rather than assuming a fixed width.
4. **Currency seam:** add an `amount_base` column now (= amount when USD). **Profile the currency mix first**; only build a full FX-conversion table if non-USD volume proves material. `cross_currency` (in≠out) is nearly always false here, so treat it as optional, not a core signal.
5. **Amount magnitudes:** use log-scaled / robust statistics for amount features; guard against the extreme outliers (hundreds of billions in some currencies).
6. **Synthetic `tx_id`:** assign a stable row index on ingest.
7. **Labels:**
   - **Binary** — use `Is Laundering` if present; otherwise derive it (a transaction is laundering iff it appears in a Patterns block).
   - **Typology** — always from the Patterns file; parse blocks into typology **groupings (sets of accounts/entities)**. Prefer evaluating at **account / entity / case** level to avoid fragile per-row tuple matching (float amounts + duplicate timestamps collide). If you must join rows, match on the full tuple with rounded amounts and dedupe.
8. **Metrics:** never report accuracy (imbalance makes it meaningless). Use precision@K, recall, F1, PR-AUC.
9. **Graph:** never build a global graph over the whole dataset — build **subgraphs on filtered slices / around a seed** only.
10. **Store:** DuckDB is canonical; pandas only for small slices pulled out to compute on.
