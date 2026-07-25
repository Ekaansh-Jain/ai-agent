"""Held-out ground truth: parse Patterns.txt and expose labels for EVALUATION ONLY.

HARD RULE (steering non-negotiable): nothing in the agent/detector path may import this
module. It exists solely so the evaluation harness can grade NEXUS against the answer key.

Patterns.txt structure:
    BEGIN LAUNDERING ATTEMPT - <TYPOLOGY>[:  <description>]
    <full transaction row ending in ",1">
    ...
    END LAUNDERING ATTEMPT - <TYPOLOGY>

Each row has the Trans columns plus a trailing Is-Laundering flag (always 1 here).
"""

from __future__ import annotations

from pathlib import Path

from .schemas import PatternInstance

_BEGIN = "BEGIN LAUNDERING ATTEMPT - "
_END = "END LAUNDERING ATTEMPT"

# Field order of a pattern/trans row (before any synthetic tx_id).
# 0:Timestamp 1:FromBank 2:FromAcct 3:ToBank 4:ToAcct 5:AmtRecv 6:RecvCcy
# 7:AmtPaid 8:PayCcy 9:PayFormat 10:IsLaundering
COL = {
    "timestamp": 0,
    "from_bank": 1,
    "from_account": 2,
    "to_bank": 3,
    "to_account": 4,
    "amount_received": 5,
    "receiving_currency": 6,
    "amount_paid": 7,
    "payment_currency": 8,
    "payment_format": 9,
}


def match_key(row: tuple | list) -> tuple:
    """Stable key to join a pattern row to a transaction row.

    Uses (timestamp, from_bank, from_account, to_bank, to_account, amount_paid) with the
    amount rounded to 2dp to avoid float-formatting mismatches. Tuple collisions are
    possible but rare; the join reports its match rate so we catch problems early.
    """
    return (
        str(row[COL["timestamp"]]).strip(),
        str(row[COL["from_bank"]]).strip(),
        str(row[COL["from_account"]]).strip(),
        str(row[COL["to_bank"]]).strip(),
        str(row[COL["to_account"]]).strip(),
        round(float(row[COL["amount_paid"]]), 2),
    )


def _parse_typology_header(line: str) -> tuple[str, str]:
    """Split 'FAN-IN:  Max 9-degree Fan-In' -> ('FAN-IN', 'Max 9-degree Fan-In')."""
    rest = line[len(_BEGIN):].strip()
    if ":" in rest:
        typ, desc = rest.split(":", 1)
        return typ.strip(), desc.strip()
    return rest, ""


def parse_patterns(path: str | Path) -> list[PatternInstance]:
    """Scan BEGIN/END blocks into PatternInstance objects (raw rows, no tx_ids yet)."""
    path = Path(path)
    instances: list[PatternInstance] = []
    current: PatternInstance | None = None

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(_BEGIN):
                typ, desc = _parse_typology_header(line)
                current = PatternInstance(typology=typ, description=desc)
            elif line.startswith(_END):
                if current is not None:
                    instances.append(current)
                    current = None
            elif current is not None:
                fields = [f.strip() for f in line.split(",")]
                if len(fields) < 10:
                    continue  # malformed row, skip defensively
                current.transactions.append(tuple(fields))
                current.accounts.add(
                    (fields[COL["from_bank"]], fields[COL["from_account"]])
                )
                current.accounts.add(
                    (fields[COL["to_bank"]], fields[COL["to_account"]])
                )

    return instances


class GroundTruth:
    """Evaluation-only accessor over parsed patterns.

    After construction, call `link(key_to_tx_id)` with a map built from the transaction
    store so per-transaction lookups work. `link` returns the join match rate.
    """

    def __init__(self, instances: list[PatternInstance]):
        self.instances = instances
        self._tx_typology: dict[int, str] = {}
        self._laundering_tx: set[int] = set()

    def link(self, key_to_tx_id: dict[tuple, int]) -> float:
        """Resolve raw pattern rows to tx_ids via match_key. Returns fraction matched."""
        matched = 0
        total = 0
        for inst in self.instances:
            inst.tx_ids = []
            for row in inst.transactions:
                total += 1
                tx_id = key_to_tx_id.get(match_key(row))
                if tx_id is not None:
                    matched += 1
                    inst.tx_ids.append(tx_id)
                    self._laundering_tx.add(tx_id)
                    self._tx_typology[tx_id] = inst.typology
        return matched / total if total else 0.0

    def is_laundering_tx(self, tx_id: int) -> bool:
        return tx_id in self._laundering_tx

    def typology_of_tx(self, tx_id: int) -> str | None:
        return self._tx_typology.get(tx_id)

    def counts_by_typology(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for inst in self.instances:
            counts[inst.typology] = counts.get(inst.typology, 0) + 1
        return counts

    def laundering_row_count(self) -> int:
        """Total pattern rows across all instances (pre-join)."""
        return sum(inst.size for inst in self.instances)
