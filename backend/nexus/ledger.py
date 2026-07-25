"""The Evidence Ledger — the case file / proof store.

A growing, ordered list of EvidenceRecords. Every claim downstream (duel, risk, narrator)
reads from here, and every record carries the transaction IDs that prove it.
"""

from __future__ import annotations

from .schemas import EvidenceRecord


class EvidenceLedger:
    def __init__(self) -> None:
        self._records: list[EvidenceRecord] = []

    def mint_id(self) -> str:
        """Next claim id, e.g. 'CL-01'."""
        return f"CL-{len(self._records) + 1:02d}"

    def add(self, record: EvidenceRecord) -> EvidenceRecord:
        self._records.append(record)
        return record

    def mark(self) -> int:
        """Current record count — a savepoint for `rollback`."""
        return len(self._records)

    def rollback(self, mark: int) -> int:
        """Drop every record appended after `mark`. Returns how many were dropped.

        Used when a tool raises partway through: its partial output must not reach the
        duel or the risk engine. Truncating keeps claim ids dense, so a fully successful
        run mints exactly the ids it did before this method existed.
        """
        dropped = len(self._records) - mark
        if dropped > 0:
            del self._records[mark:]
        return max(dropped, 0)

    @property
    def records(self) -> list[EvidenceRecord]:
        return list(self._records)

    def by_family(self) -> dict[str, list[EvidenceRecord]]:
        out: dict[str, list[EvidenceRecord]] = {}
        for r in self._records:
            out.setdefault(r.family, []).append(r)
        return out

    def transactions(self) -> set[int]:
        """Union of all transaction IDs cited across the ledger."""
        tx: set[int] = set()
        for r in self._records:
            tx.update(r.transactions)
        return tx

    def __len__(self) -> int:
        return len(self._records)
