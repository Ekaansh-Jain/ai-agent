"""Pure classification metrics for the eval harness (no data dependencies).

Given class imbalance in AML, we use precision/recall/F1 and never accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Confusion:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
        }


def confusion(preds: list[bool], labels: list[bool]) -> Confusion:
    if len(preds) != len(labels):
        raise ValueError("preds and labels must be the same length")
    tp = fp = fn = tn = 0
    for p, y in zip(preds, labels):
        if p and y:
            tp += 1
        elif p and not y:
            fp += 1
        elif not p and y:
            fn += 1
        else:
            tn += 1
    return Confusion(tp, fp, fn, tn)
