"""benign_signals — features that separate a benign pass-through pooler / legit business
from a laundering collector, which look identical under peer/flow/network alone.

Three families (design's benign traits), each a direction+strength vs a 0.5 midpoint:
  retention  : share of inflow RETAINED in-window (1 - flow_through). High => keeps funds.
  recurrence : share of inbound that is with REPEAT counterparties. High => stable clientele.
  stability  : spread of activity over time. High => steady operation, not a burst.

Rings show all three LOW (fresh mules, funds forwarded, bursty); poolers/businesses show
them HIGH. The suspicious hypothesis expects them low; benign hypotheses expect them high,
so the duel now SUBTRACTS on real poolers.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from . import clamp
from ..ledger import EvidenceLedger
from ..schemas import EvidenceRecord, FilterScope
from .. import scope as scope_mod

_STABILITY_DAYS = 15.0  # active-day count at/above which stability saturates high


def _dir_strength(value: float, midpoint: float = 0.5):
    return ("high" if value >= midpoint else "low"), clamp(abs(value - midpoint) / midpoint)


def run(
    con: duckdb.DuckDBPyConnection,
    node: str,
    ledger: EvidenceLedger,
    window_hours: float = 6.0,
    scope: FilterScope | None = None,
) -> list[EvidenceRecord]:
    bank, acct = node.split("|", 1)
    # Filtered alongside flow_through: a duel between a full-history benign family and a
    # filtered suspicious family would compare incommensurable evidence.
    in_where, in_params = scope_mod.where(scope, "to_bank = ?", "receiver_account = ?")
    out_where, out_params = scope_mod.where(scope, "from_bank = ?", "sender_account = ?")
    inbound = con.execute(
        "SELECT from_bank || '|' || sender_account AS src, tx_id, timestamp, amount_base "
        f"FROM transactions {in_where}", [bank, acct] + in_params,
    ).df()
    outbound = con.execute(
        f"SELECT timestamp, amount_base FROM transactions {out_where}",
        [bank, acct] + out_params,
    ).df()

    tx_ids = [int(t) for t in inbound["tx_id"].tolist()]
    in_count = len(inbound)
    total_in = float(inbound["amount_base"].sum())

    # recurrence: 1 - distinct_senders / inbound_count
    if in_count > 0:
        distinct_src = inbound["src"].nunique()
        recurrence = 1.0 - distinct_src / in_count
    else:
        recurrence = 0.0

    # retention: 1 - (out within window / total in)
    if total_in > 0 and in_count > 0:
        in_min, in_max = inbound["timestamp"].min(), inbound["timestamp"].max()
        cutoff = in_max + pd.Timedelta(hours=window_hours)
        out_within = float(outbound.loc[
            (outbound["timestamp"] >= in_min) & (outbound["timestamp"] <= cutoff),
            "amount_base"].sum())
        retention = clamp(1.0 - min(out_within, total_in) / total_in)
    else:
        retention = 0.0

    # stability: distinct active days of inbound activity, saturating at _STABILITY_DAYS
    active_days = inbound["timestamp"].dt.date.nunique() if in_count > 0 else 0
    stability = clamp(active_days / _STABILITY_DAYS)

    out: list[EvidenceRecord] = []
    for family, value, calc in [
        ("retention", retention, "1 - out_within_window/total_in"),
        ("recurrence", recurrence, "1 - distinct_senders/inbound_count"),
        ("stability", stability, f"active_days/{_STABILITY_DAYS:.0f}"),
    ]:
        direction, strength = _dir_strength(value)
        out.append(ledger.add(EvidenceRecord(
            claim_id=ledger.mint_id(), family=family,
            claim=f"{family}={value:.2f}",
            calculation=f"{calc}; strength=|v-0.5|/0.5",
            value=round(value, 3), direction=direction, strength=round(strength, 3),
            transactions=tx_ids,
        )))
    return out
