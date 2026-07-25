"""Evidence tools: each reads transactions and emits EvidenceRecord(s) into the ledger.

Every tool is a plain typed function — no LLM, independently testable. Tools never see
labels/ground truth.
"""


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
