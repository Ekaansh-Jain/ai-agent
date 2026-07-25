"""rapid_pass_through — did money arrive and leave quickly?

ratio = (outbound value leaving within `window_hours` of the inflow window) / (total inflow).
High ratio => funds pass straight through (a mule/collector), which supports the suspicious
theory and contradicts "legitimate business that keeps its money". Emits `flow_through`.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from . import clamp
from ..ledger import EvidenceLedger
from ..schemas import EvidenceRecord, FilterScope
from .. import scope as scope_mod


def run(
    con: duckdb.DuckDBPyConnection,
    node: str,
    ledger: EvidenceLedger,
    window_hours: float = 6.0,
    scope: FilterScope | None = None,
) -> EvidenceRecord:
    bank, acct = node.split("|", 1)
    # The ratio is computed entirely from the queried slice, so it honours the filter.
    in_where, in_params = scope_mod.where(scope, "to_bank = ?", "receiver_account = ?")
    out_where, out_params = scope_mod.where(scope, "from_bank = ?", "sender_account = ?")
    inbound = con.execute(
        f"SELECT tx_id, timestamp, amount_base FROM transactions {in_where}",
        [bank, acct] + in_params,
    ).df()
    outbound = con.execute(
        f"SELECT tx_id, timestamp, amount_base FROM transactions {out_where}",
        [bank, acct] + out_params,
    ).df()

    total_in = float(inbound["amount_base"].sum())
    tx_ids = [int(t) for t in inbound["tx_id"].tolist()]
    window_txs: list[int] = []

    if total_in <= 0 or inbound.empty:
        # No inflow -> no evidence either way.
        ratio, direction, strength = 0.0, "low", 0.0
    else:
        in_min = inbound["timestamp"].min()
        in_max = inbound["timestamp"].max()
        cutoff = in_max + pd.Timedelta(hours=window_hours)
        mask = (outbound["timestamp"] >= in_min) & (outbound["timestamp"] <= cutoff)
        out_within = float(outbound.loc[mask, "amount_base"].sum())
        ratio = clamp(min(out_within, total_in) / total_in)
        window_txs = [int(t) for t in outbound.loc[mask, "tx_id"].tolist()]
        # Direction + strength vs a neutral midpoint (0.5): funds passing straight
        # through (high) AND funds strongly retained (low) are both meaningful signals.
        midpoint = 0.5
        direction = "high" if ratio >= midpoint else "low"
        strength = clamp(abs(ratio - midpoint) / midpoint)

    return ledger.add(EvidenceRecord(
        claim_id=ledger.mint_id(),
        family="flow_through",
        claim=f"{ratio * 100:.0f}% of inflow left within {window_hours:.0f}h of arrival",
        calculation="out_within_window / total_in; strength = |ratio-0.5|/0.5",
        value=round(ratio, 3),
        direction=direction,
        strength=round(strength, 3),
        transactions=tx_ids + window_txs,
    ))
