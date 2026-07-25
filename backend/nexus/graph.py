"""Subgraph builder — NetworkX graphs on FILTERED SLICES only, never the full population.

Given a center node we pull its inbound feeders (predecessors) and outbound receivers
(successors) up to a bounded depth. Edges carry amount_base, timestamp, tx_id so evidence
can point back to exact transactions.

Cost note: expansion used to issue one query per frontier node and one out-degree query per
feeder. Real HI-Small hubs have 70-545 feeders, so a single investigation cost a median of
89 DuckDB round-trips (max 554). Both are now batched — one query per DEPTH LEVEL and one
grouped out-degree query — which is output-identical and bounds a candidate at ~11 round
trips. `ORDER BY tx_id` makes the surviving edge attributes deterministic when a
(sender, receiver) pair repeats.
"""

from __future__ import annotations

import duckdb
import networkx as nx

from . import scope as scope_mod
from .schemas import FilterScope


def _split(node: str) -> tuple[str, str]:
    bank, acct = node.split("|", 1)
    return bank, acct


def _pair_predicate(bank_col: str, acct_col: str, nodes: list[str]) -> tuple[str, list]:
    """Build `(bank_col, acct_col) IN ((?,?), ...)` for a batch of nodes."""
    tuples = ", ".join(["(?, ?)"] * len(nodes))
    params: list = []
    for n in nodes:
        bank, acct = _split(n)
        params.extend([bank, acct])
    return f"({bank_col}, {acct_col}) IN ({tuples})", params


def _inbound_batch(
    con: duckdb.DuckDBPyConnection, nodes: list[str], scope: FilterScope | None = None
):
    """One query for the inbound edges of every node in `nodes`."""
    if not nodes:
        return []
    pair_sql, pair_params = _pair_predicate("to_bank", "receiver_account", nodes)
    clause, scope_params = scope_mod.where(scope, pair_sql)
    return con.execute(
        "SELECT from_bank || '|' || sender_account AS src, "
        "to_bank || '|' || receiver_account AS dst, tx_id, amount_base, timestamp "
        f"FROM transactions {clause} ORDER BY tx_id",
        pair_params + scope_params,
    ).fetchall()


def _outbound(
    con: duckdb.DuckDBPyConnection, node: str, scope: FilterScope | None = None
):
    bank, acct = _split(node)
    clause, scope_params = scope_mod.where(
        scope, "from_bank = ?", "sender_account = ?"
    )
    return con.execute(
        "SELECT to_bank || '|' || receiver_account AS dst, tx_id, amount_base, timestamp "
        f"FROM transactions {clause} ORDER BY tx_id",
        [bank, acct] + scope_params,
    ).fetchall()


def out_degrees(
    con: duckdb.DuckDBPyConnection, nodes: list[str]
) -> dict[str, int]:
    """Distinct-receiver count for each node, in ONE grouped query.

    Deliberately UNFILTERED: the earn-your-flag gate asks about a counterparty's whole
    behaviour. A filtered out-degree would make a salary payer look mule-thin and break the
    benign-exclusion demo.
    """
    if not nodes:
        return {}
    unique = sorted(set(nodes))
    pair_sql, params = _pair_predicate("from_bank", "sender_account", unique)
    rows = con.execute(
        "SELECT from_bank || '|' || sender_account AS node, "
        "COUNT(DISTINCT to_bank || '|' || receiver_account) AS deg "
        f"FROM transactions WHERE {pair_sql} GROUP BY 1",
        params,
    ).fetchall()
    found = {str(n): int(d) for n, d in rows}
    return {n: found.get(n, 0) for n in unique}


def ego_subgraph(
    con: duckdb.DuckDBPyConnection,
    center: str,
    depth: int = 1,
    scope: FilterScope | None = None,
) -> nx.DiGraph:
    """Build a directed subgraph around `center`: feeders expanded up to `depth` levels
    backward, plus one level of successors. Bounded — for ring inspection, not the world.
    """
    g = nx.DiGraph()
    g.add_node(center)

    # Backward expansion, one batched query per depth level.
    frontier = [center]
    seen = {center}
    for _ in range(depth):
        nxt: list[str] = []
        for src, dst, tx_id, amt, ts in _inbound_batch(con, frontier, scope):
            g.add_edge(src, dst, tx_id=tx_id, amount_base=amt, timestamp=ts)
            if src not in seen:
                seen.add(src)
                nxt.append(src)
        frontier = nxt
        if not frontier:
            break

    # One level forward from the center (where the money goes).
    for dst, tx_id, amt, ts in _outbound(con, center, scope):
        g.add_edge(center, dst, tx_id=tx_id, amount_base=amt, timestamp=ts)

    return g
