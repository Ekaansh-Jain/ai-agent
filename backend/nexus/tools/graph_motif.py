"""graph_motif / path_trace — what shape is the money flow, and how convergent?

Builds a bounded subgraph around the node and measures fan-in breadth and convergence:
  fan_in      = number of distinct feeders (in-degree)
  convergence = in_degree / (in_degree + out_degree)  -> ~1 when many feeders, few exits
  strength    = 0.5 * min(1, fan_in / 6) + 0.5 * convergence
Emits `network_convergence`.
"""

from __future__ import annotations

import duckdb

from . import clamp
from ..graph import ego_subgraph
from ..ledger import EvidenceLedger
from ..schemas import EvidenceRecord, FilterScope


def run(
    con: duckdb.DuckDBPyConnection,
    node: str,
    ledger: EvidenceLedger,
    depth: int = 1,
    scope: FilterScope | None = None,
) -> EvidenceRecord:
    # Fan-in shape is slice-local: if the analyst asked about cash in March, the motif
    # should describe cash in March.
    g = ego_subgraph(con, node, depth=depth, scope=scope)
    fan_in = g.in_degree(node)
    fan_out = g.out_degree(node)
    convergence = fan_in / (fan_in + fan_out) if (fan_in + fan_out) else 0.0
    strength = clamp(0.5 * min(1.0, fan_in / 6.0) + 0.5 * convergence)

    ring_tx = [
        d["tx_id"] for _, _, d in g.in_edges(node, data=True) if "tx_id" in d
    ]

    return ledger.add(EvidenceRecord(
        claim_id=ledger.mint_id(),
        family="network_convergence",
        claim=f"fan-in {fan_in} feeders, convergence {convergence:.2f}",
        calculation="0.5*min(1, fan_in/6) + 0.5*convergence",
        value=round(convergence, 3),
        direction="high" if strength >= 0.5 else "low",
        strength=round(strength, 3),
        transactions=ring_tx,
    ))
