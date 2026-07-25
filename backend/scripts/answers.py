"""Answers to the three pre-Phase-4 questions, with real numbers.

Q1: confusion matrix (rules vs NEXUS) on the rule-flagged slice, incl. recall / FN.
Q2: did the rapid_pass_through change move the Phase 2 (86.65) and real-ring (53.18) anchors?
Q3: is the benign downgrade only on the seed, or do real high-fan-in accounts downgrade too?
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
from nexus.ledger import EvidenceLedger  # noqa: E402
from nexus.peers import PeerModel  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402
from nexus.risk import risk_score  # noqa: E402
from nexus.tools import graph_motif, peer_comparison, rapid_pass_through  # noqa: E402


def q2_phase2_anchor():
    lg = EvidenceLedger()
    from nexus.schemas import EvidenceRecord
    data = [
        ("peer_deviation", 0.82, "high"), ("flow_through", 0.91, "high"),
        ("network_convergence", 0.90, "high"), ("temporal_coordination", 0.80, "high"),
        ("typology_rule", 0.90, "high"),
    ]
    for fam, s, d in data:
        lg.add(EvidenceRecord(claim_id=lg.mint_id(), family=fam, claim="", calculation="",
                              value=s, direction=d, strength=s, transactions=[1]))
    print(f"Q2 Phase 2 worked example risk = {risk_score(lg.records).score}  (expected 86.65)")


def q2_real_ring(con, peers):
    instances = parse_patterns(paths_for("HI-Small").patterns)
    for typ in ("GATHER-SCATTER", "FAN-IN"):
        for inst in instances:
            if inst.typology != typ:
                continue
            recv = Counter((r[3], r[4]) for r in inst.transactions)
            (b, a), _ = recv.most_common(1)[0]
            node = f"{b}|{a}"
            if con.execute("SELECT COUNT(*) FROM transactions WHERE from_bank=? AND sender_account=?",
                           [b, a]).fetchone()[0] > 0:
                lg = EvidenceLedger()
                peer_comparison.run(con, peers, node, lg)
                ft = rapid_pass_through.run(con, node, lg)
                graph_motif.run(con, node, lg)
                ratio = ft.value
                old_strength = round(ratio, 3)                      # old formula: strength=ratio
                new_strength = ft.strength                          # current: |ratio-0.5|/0.5
                print(f"Q2 real ring hub={node} typ={typ}")
                print(f"   flow_through ratio={ratio}  old_strength={old_strength}  "
                      f"new_strength={new_strength}")
                print(f"   risk now = {risk_score(lg.records).score}  (3a reported 53.18)")
                return


def _positive_nodes(con):
    rows = con.execute(
        "SELECT DISTINCT to_bank || '|' || receiver_account FROM transactions "
        "WHERE is_laundering"
    ).fetchall()
    return {r[0] for r in rows}


def q1_confusion(con, peers):
    rule = con.execute(
        """
        SELECT to_bank || '|' || receiver_account AS node,
               MAX(CASE WHEN is_laundering THEN 1 ELSE 0 END) AS positive
        FROM transactions
        WHERE payment_format = 'Cash' AND amount_base >= 9000 AND amount_base < 10000
        GROUP BY 1 HAVING COUNT(*) >= 3
        """
    ).df()
    flagged = len(rule)
    positives = int(rule["positive"].sum())

    # Rules baseline predicts POSITIVE for every flagged account.
    print(f"\nQ1 slice = {flagged} rule-flagged accounts; ground-truth positives = {positives}")
    print(f"   RULES  : TP={positives}  FP={flagged - positives}  FN=0  "
          f"recall={positives}/{positives}=1.00")

    tp = fp = fn = tn = 0
    for _, row in rule.iterrows():
        esc = investigate(con, peers, row["node"]).escalation
        predicted_pos = esc in {"review", "report"}
        actual_pos = row["positive"] == 1
        if predicted_pos and actual_pos:
            tp += 1
        elif predicted_pos and not actual_pos:
            fp += 1
        elif not predicted_pos and actual_pos:
            fn += 1
        else:
            tn += 1
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    print(f"   NEXUS  : TP={tp}  FP={fp}  FN={fn}  TN={tn}  "
          f"recall={tp}/{tp + fn}={recall:.2f}")
    print(f"   -> FP suppressed: {flagged - positives} -> {fp}")


def q3_real_high_fan_in(con, peers, profiles):
    positives = _positive_nodes(con)
    cand = profiles[(profiles["in_degree"] >= 8) & (profiles["in_degree"] <= 60)]
    cand = cand[~cand.index.isin(positives)].sort_values("in_degree", ascending=False)
    sample = list(cand.index[:20])
    monitored = escalated = 0
    example = None
    for node in sample:
        c = investigate(con, peers, node)
        if c.escalation == "monitor":
            monitored += 1
        else:
            escalated += 1
        if example is None:
            example = (node, int(profiles.at[node, "in_degree"]), c.winning_kind, c.escalation)
    print(f"\nQ3 real non-seeded benign high-fan-in accounts (in_degree 8-60): n={len(sample)}")
    print(f"   downgraded to monitor: {monitored}/{len(sample)}   escalated: {escalated}")
    if example:
        print(f"   example {example[0]} in_degree={example[1]} -> "
              f"{example[2]} / {example[3]}")


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    profiles = build_profiles(ds.con)
    peers = PeerModel(profiles)

    print("=" * 64)
    q2_phase2_anchor()
    q2_real_ring(ds.con, peers)
    q1_confusion(ds.con, peers)
    q3_real_high_fan_in(ds.con, peers, profiles)
    print("=" * 64)


if __name__ == "__main__":
    main()
