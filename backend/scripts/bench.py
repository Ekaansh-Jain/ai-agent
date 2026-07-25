"""Capability-matched benchmark (replaces the retired '91% cut' claim).

Consolidation (the typology NEXUS is built for): recall on the 91 FAN-IN/GATHER-SCATTER
ring hubs + false positives on a benign high-fan-in background, vs a naive fan-in rule.
Precision/recall/FP reported together.

Also: confirm the previously-missed structuring true-positive now escalates under the
structuring lens (n=1, demonstrated not measured).
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
MAX_HUB_DEGREE = 400   # skip mega-hubs to bound subgraph runtime
N_NEG = 200


def _laundering_nodes(con) -> set[str]:
    rows = con.execute(
        "SELECT DISTINCT to_bank || '|' || receiver_account FROM transactions WHERE is_laundering "
        "UNION SELECT DISTINCT from_bank || '|' || sender_account FROM transactions WHERE is_laundering"
    ).fetchall()
    return {r[0] for r in rows}


def _ring_hubs(instances) -> set[str]:
    hubs = set()
    for inst in instances:
        if inst.typology in CONSOLIDATION:
            recv = Counter((r[3], r[4]) for r in inst.transactions)
            (b, a), _ = recv.most_common(1)[0]
            hubs.add(f"{b}|{a}")
    return hubs


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    con = ds.con
    profiles = build_profiles(con)
    peers = PeerModel(profiles)
    instances = parse_patterns(paths_for("HI-Small").patterns)

    laund = _laundering_nodes(con)

    # POSITIVES: ring hubs (bounded degree).
    hubs = [h for h in _ring_hubs(instances)
            if h in profiles.index and profiles.at[h, "in_degree"] <= MAX_HUB_DEGREE]
    tp = sum(1 for h in hubs if investigate(con, peers, h, "smurfing").escalation
             in {"review", "report"})
    recall = tp / len(hubs) if hubs else float("nan")

    # NEGATIVES: benign high-fan-in background (in_degree 6..60, not laundering-linked).
    neg = profiles[(profiles["in_degree"] >= 6) & (profiles["in_degree"] <= 60)]
    neg = neg[~neg.index.isin(laund)]
    neg_sample = list(neg.index[:N_NEG])
    nexus_fp = sum(1 for n in neg_sample if investigate(con, peers, n, "smurfing").escalation
                   in {"review", "report"})

    # Rules baseline for consolidation: "flag any account with in_degree >= 6".
    rules_tp = len(hubs)          # all ring hubs have high fan-in
    rules_fp = len(neg_sample)    # all negatives selected with in_degree >= 6

    nexus_prec = tp / (tp + nexus_fp) if (tp + nexus_fp) else float("nan")
    rules_prec = rules_tp / (rules_tp + rules_fp) if (rules_tp + rules_fp) else float("nan")

    print("=" * 64)
    print("CONSOLIDATION BENCHMARK  (FAN-IN + GATHER-SCATTER)")
    print("=" * 64)
    print(f"positives (ring hubs)      : {len(hubs)}")
    print(f"negatives (benign fan-in)  : {len(neg_sample)}")
    print("-" * 64)
    print(f"  RULES (in_degree>=6): recall={rules_tp}/{len(hubs)}=1.00  "
          f"FP={rules_fp}  precision={rules_prec:.2f}")
    print(f"  NEXUS               : recall={tp}/{len(hubs)}={recall:.2f}  "
          f"FP={nexus_fp}  precision={nexus_prec:.2f}")
    print(f"  FP reduction        : {rules_fp} -> {nexus_fp}")

    # Structuring TP (previously missed), n=1, demonstrated.
    tp_row = con.execute(
        """
        SELECT to_bank || '|' || receiver_account AS node
        FROM transactions
        WHERE payment_format = 'Cash' AND amount_base >= 9000 AND amount_base < 10000
        GROUP BY 1
        HAVING COUNT(*) >= 3 AND MAX(CASE WHEN is_laundering THEN 1 ELSE 0 END) = 1
        LIMIT 1
        """
    ).fetchone()
    print("\n" + "=" * 64)
    print("STRUCTURING TP  (previously missed; n=1, demonstrated not measured)")
    print("=" * 64)
    if tp_row:
        node = tp_row[0]
        before = investigate(con, peers, node, "smurfing")
        after = investigate(con, peers, node, "structuring")
        print(f"  node {node}")
        print(f"  smurfing lens    : {before.winning_kind} -> {before.escalation}")
        print(f"  structuring lens : {after.winning_kind} -> {after.escalation}  "
              f"(risk {after.risk})")
    else:
        print("  (no such account found)")
    print("=" * 64)


if __name__ == "__main__":
    main()
