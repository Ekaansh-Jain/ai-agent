"""isolation_forest — the generic-AI anomaly detector as a proof-carrying evidence family.

Emits a NEUTRAL `anomaly` record: it is deliberately NOT in any hypothesis fingerprint or
the risk weights, so it never changes the verdict or the locked anchors. It documents what a
black-box model thought — strengthening the explainability story ("the ML said 0.82, and
here is why NEXUS still concluded benign").
"""

from __future__ import annotations

import duckdb
import pandas as pd

from ..anomaly import AnomalyModel
from ..ledger import EvidenceLedger
from ..schemas import EvidenceRecord


def run(
    con: duckdb.DuckDBPyConnection,
    model: AnomalyModel,
    node: str,
    profiles: pd.DataFrame,
    ledger: EvidenceLedger,
) -> EvidenceRecord | None:
    if model is None or node not in profiles.index:
        return None
    score = model.score_row(profiles.loc[node])
    bank, acct = node.split("|", 1)
    tx = con.execute(
        "SELECT tx_id FROM transactions WHERE to_bank = ? AND receiver_account = ? LIMIT 200",
        [bank, acct],
    ).fetchall()
    return ledger.add(EvidenceRecord(
        claim_id=ledger.mint_id(),
        family="anomaly",
        claim=f"IsolationForest anomaly score {score:.2f} (unsupervised, informational)",
        calculation="normalized -decision_function of IsolationForest on account features",
        value=round(score, 3),
        direction="high" if score >= 0.5 else "low",
        strength=round(score, 3),
        transactions=[int(r[0]) for r in tx],
    ))
