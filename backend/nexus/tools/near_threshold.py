"""near_threshold — structuring detector (Typology A).

Counts inbound deposits sitting just below the reporting threshold, i.e. in
[threshold * (1 - band), threshold). Many such deposits are the fingerprint of
structuring. Entity-level: if `entity_nodes` is given, aggregates across an entity's
accounts; otherwise scores the single account. Emits `typology_rule`.
"""

from __future__ import annotations

import duckdb

from . import clamp
from ..config import Settings
from ..ledger import EvidenceLedger
from ..schemas import EvidenceRecord, FilterScope
from .. import scope as scope_mod

# A count at/above this saturates strength to 1.0.
_SATURATE_AT = 5.0


def run(
    con: duckdb.DuckDBPyConnection,
    node: str,
    ledger: EvidenceLedger,
    settings: Settings | None = None,
    entity_nodes: list[str] | None = None,
    scope: FilterScope | None = None,
) -> EvidenceRecord:
    settings = settings or Settings()
    low = settings.near_threshold * (1.0 - settings.near_threshold_band)
    high = settings.near_threshold
    nodes = entity_nodes or [node]

    # "cash deposits in March" is precisely this tool's question, so it honours the filter.
    count_where, count_params = scope_mod.where(scope, "to_bank = ?", "receiver_account = ?")
    band_where, band_params = scope_mod.where(
        scope, "to_bank = ?", "receiver_account = ?",
        "amount_base >= ?", "amount_base < ?",
    )

    tx_ids: list[int] = []
    n_near = 0
    n_in = 0
    for n in nodes:
        bank, acct = n.split("|", 1)
        n_in += con.execute(
            f"SELECT COUNT(*) FROM transactions {count_where}",
            [bank, acct] + count_params,
        ).fetchone()[0]
        rows = con.execute(
            f"SELECT tx_id FROM transactions {band_where}",
            [bank, acct, low, high] + band_params,
        ).fetchall()
        n_near += len(rows)
        tx_ids.extend(int(r[0]) for r in rows)

    # High when several deposits cluster near the threshold. Otherwise a *strong low*
    # signal (deposits exist but aren't threshold-shaped) so the benign theory can win;
    # neutral only when there are no deposits at all.
    if n_in == 0:
        direction, strength = "low", 0.0
    elif n_near >= 2:
        direction, strength = "high", clamp(n_near / _SATURATE_AT)
    else:
        direction, strength = "low", clamp(n_in / 8.0)

    scope = f"{len(nodes)} entity account(s)" if entity_nodes else "account"
    return ledger.add(EvidenceRecord(
        claim_id=ledger.mint_id(),
        family="typology_rule",
        claim=f"{n_near} of {n_in} inbound deposits in [{low:,.0f}, {high:,.0f}) across {scope}",
        calculation=f"count in near-threshold band; high if n_near>=2 (strength n/{_SATURATE_AT:.0f})",
        value=float(n_near),
        direction=direction,
        strength=round(strength, 3),
        transactions=tx_ids,
    ))
