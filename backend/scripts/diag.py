"""Diagnosis of the consolidation benchmark (no tuning, no new families).

1. Per-typology breakdown of ring hubs: FAN-IN vs GATHER-SCATTER count + NEXUS escalation
   rate within each, and WHY misses happen (benign-gated? flow_through low?).
2. Composition of the false positives: why did benign fan-in accounts escalate?
3. Right-axis comparison: rule precision/FP at NEXUS's recall (matched-recall), since a
   fan-in rule catching fan-in hubs is near-tautological on recall.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter, defaultdict

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


def _hub_typologies(instances):
    hub_typ = defaultdict(set)
    for inst in instances:
        if inst.typology in CONSOLIDATION:
            recv = Counter((r[3], r[4]) for r in inst.transactions)
            (b, a), _ = recv.most_common(1)[0]
            hub_typ[f"{b}|{a}"].add(inst.typology)
    return hub_typ


def _detail(case):
    ev = {r.family: r for r in case.evidence}
    ft = ev.get("flow_through")
    net = ev.get("network_convergence")
    peer = ev.get("peer_deviation")
    return {
        "escalated": case.escalation in {"review", "report"},
        "winner_kind": case.winning_kind,
        "risk": case.risk,
        "ft_dir": ft.direction if ft else None,
        "ft_str": ft.strength if ft else None,
        "net_str": net.strength if net else None,
        "peer_dir": peer.direction if peer else None,
        "peer_str": peer.strength if peer else None,
    }


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    con = ds.con
    profiles = build_profiles(con)
    peers = PeerModel(profiles)
    instances = parse_patterns(paths_for("HI-Small").patterns)
    laund = _laundering_nodes(con)

    hub_typ = _hub_typologies(instances)
    hubs = [h for h in hub_typ if h in profiles.index
            and profiles.at[h, "in_degree"] <= MAX_HUB_DEGREE]

    # Evaluate positives.
    pos_detail = {h: _detail(investigate(con, peers, h, "smurfing")) for h in hubs}

    # (1) Per-typology breakdown.
    print("=" * 64)
    print("(1) PER-TYPOLOGY BREAKDOWN of ring hubs")
    print("=" * 64)
    for typ in ("FAN-IN", "GATHER-SCATTER"):
        group = [h for h in hubs if typ in hub_typ[h]]
        esc = [h for h in group if pos_detail[h]["escalated"]]
        benign_gated = [h for h in group
                        if not pos_detail[h]["escalated"]
                        and pos_detail[h]["winner_kind"] == "benign"]
        flow_low = [h for h in group
                    if pos_detail[h]["ft_dir"] == "low"]
        rate = len(esc) / len(group) if group else float("nan")
        print(f"  {typ:15} n={len(group):3}  escalated={len(esc):3} ({rate:.2f})  "
              f"misses benign-gated={len(benign_gated):3}  flow_through_low={len(flow_low):3}")

    # (2) FP composition.
    neg = profiles[(profiles["in_degree"] >= 6) & (profiles["in_degree"] <= 60)]
    neg = neg[~neg.index.isin(laund)]
    neg_sample = list(neg.index[:N_NEG])
    neg_detail = {n: _detail(investigate(con, peers, n, "smurfing")) for n in neg_sample}
    fps = [n for n in neg_sample if neg_detail[n]["escalated"]]
    ft_high = [n for n in fps if neg_detail[n]["ft_dir"] == "high"]
    net_high = [n for n in fps if (neg_detail[n]["net_str"] or 0) >= 0.5]
    peer_high = [n for n in fps if neg_detail[n]["peer_dir"] == "high"]
    print("\n" + "=" * 64)
    print(f"(2) FALSE-POSITIVE COMPOSITION (of {len(fps)} escalated benign fan-in accounts)")
    print("=" * 64)
    print(f"  flow_through HIGH (incidental pass-through): {len(ft_high)}/{len(fps)}")
    print(f"  network_convergence strength>=0.5         : {len(net_high)}/{len(fps)}")
    print(f"  peer_deviation HIGH                        : {len(peer_high)}/{len(fps)}")

    # (3) Matched-recall comparison via the rule's PR curve.
    indeg = profiles["in_degree"].to_dict()
    nexus_recall = sum(1 for h in hubs if pos_detail[h]["escalated"]) / len(hubs)
    nexus_fp = len(fps)
    nexus_prec = (len(hubs) * nexus_recall) / (len(hubs) * nexus_recall + nexus_fp)
    print("\n" + "=" * 64)
    print("(3) PRECISION AT MATCHED RECALL  (rule PR curve on the same 76+200 set)")
    print("=" * 64)
    print(f"  NEXUS operating point: recall={nexus_recall:.2f}  FP={nexus_fp}  "
          f"precision={nexus_prec:.2f}")
    best = None
    for k in range(6, 61):
        tp_r = sum(1 for h in hubs if indeg[h] >= k)
        fp_r = sum(1 for n in neg_sample if indeg[n] >= k)
        rec_r = tp_r / len(hubs)
        prec_r = tp_r / (tp_r + fp_r) if (tp_r + fp_r) else 0.0
        if best is None or abs(rec_r - nexus_recall) < abs(best[1] - nexus_recall):
            best = (k, rec_r, prec_r, fp_r)
    k, rec_r, prec_r, fp_r = best
    print(f"  rule at in_degree>={k}: recall={rec_r:.2f}  FP={fp_r}  precision={prec_r:.2f}")
    verdict = "ABOVE" if nexus_prec > prec_r else "BELOW"
    print(f"  -> at matched recall (~{nexus_recall:.2f}), NEXUS precision is {verdict} the rule")
    print("=" * 64)


if __name__ == "__main__":
    main()
