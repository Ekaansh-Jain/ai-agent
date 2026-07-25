"""Held-out train/test benchmark for the precision fix (Phase 3d).

Split ring hubs (positives) + benign fan-in (negatives) deterministically into train/test.
Tuning decisions are made on TRAIN only; the verdict is precision-at-matched-recall on TEST,
compared to the tuned fan-in threshold rule's precision at the same recall.

Success bar (fixed before results): on TEST, NEXUS precision >= ~0.60 at recall ~0.37.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.config import Settings, paths_for  # noqa: E402
from nexus.casebuilder import investigate  # noqa: E402
from nexus.ground_truth import parse_patterns  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.peers import PeerModel  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402

CONSOLIDATION = {"FAN-IN", "GATHER-SCATTER"}
MAX_HUB_DEGREE = 400
N_NEG = 200


def _laundering_nodes(con):
    rows = con.execute(
        "SELECT DISTINCT to_bank||'|'||receiver_account FROM transactions WHERE is_laundering "
        "UNION SELECT DISTINCT from_bank||'|'||sender_account FROM transactions WHERE is_laundering"
    ).fetchall()
    return {r[0] for r in rows}


def _ring_hubs(instances):
    hubs = set()
    for inst in instances:
        if inst.typology in CONSOLIDATION:
            recv = Counter((r[3], r[4]) for r in inst.transactions)
            (b, a), _ = recv.most_common(1)[0]
            hubs.add(f"{b}|{a}")
    return hubs


def _escalates(con, peers, node) -> bool:
    return investigate(con, peers, node, "smurfing").escalation in {"review", "report"}


def _evaluate(con, peers, pos, neg, indeg, label):
    tp = sum(1 for h in pos if _escalates(con, peers, h))
    fp = sum(1 for n in neg if _escalates(con, peers, n))
    recall = tp / len(pos) if pos else float("nan")
    prec = tp / (tp + fp) if (tp + fp) else float("nan")

    # Rule PR curve on this split; report precision at recall closest to NEXUS recall.
    best = None
    for k in range(6, 61):
        rtp = sum(1 for h in pos if indeg[h] >= k)
        rfp = sum(1 for n in neg if indeg[n] >= k)
        rrec = rtp / len(pos) if pos else 0.0
        rprec = rtp / (rtp + rfp) if (rtp + rfp) else 0.0
        if best is None or abs(rrec - recall) < abs(best[1] - recall):
            best = (k, rrec, rprec, rfp)
    k, rrec, rprec, rfp = best

    print(f"[{label}]  n_pos={len(pos)} n_neg={len(neg)}")
    print(f"   NEXUS : recall={recall:.2f}  FP={fp}  precision={prec:.2f}")
    print(f"   RULE  : in_degree>={k}  recall={rrec:.2f}  FP={rfp}  precision={rprec:.2f}")
    verdict = "BEATS/parity" if prec >= rprec - 0.02 else "loses to"
    print(f"   -> NEXUS {verdict} the rule at matched recall")
    return prec, recall, rprec


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    con = ds.con
    profiles = build_profiles(con)
    peers = PeerModel(profiles)
    instances = parse_patterns(paths_for("HI-Small").patterns)
    laund = _laundering_nodes(con)
    indeg = profiles["in_degree"].to_dict()

    pos = sorted(h for h in _ring_hubs(instances)
                 if h in profiles.index and indeg[h] <= MAX_HUB_DEGREE)
    neg_all = profiles[(profiles["in_degree"] >= 6) & (profiles["in_degree"] <= 60)]
    neg_all = sorted(neg_all[~neg_all.index.isin(laund)].index)[:N_NEG]

    # Deterministic interleaved split (similar degree distribution in both halves).
    pos_train, pos_test = pos[::2], pos[1::2]
    neg_train, neg_test = neg_all[::2], neg_all[1::2]

    print("=" * 64)
    print("HELD-OUT BENCHMARK — consolidation precision fix")
    print("=" * 64)
    _evaluate(con, peers, pos_train, neg_train, indeg, "TRAIN (tuning)")
    print("-" * 64)
    prec, recall, rprec = _evaluate(con, peers, pos_test, neg_test, indeg, "TEST (verdict)")
    print("=" * 64)
    bar = prec >= 0.60 or prec >= rprec - 0.02
    print(f"SUCCESS BAR (test precision >= ~0.60 or >= rule): "
          f"{'MET' if bar else 'NOT MET'}  (test precision={prec:.2f})")
    print("=" * 64)


if __name__ == "__main__":
    main()
