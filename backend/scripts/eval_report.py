"""Honest evaluation scorecard on real HI-Small (claims only what holds).

1. Explanation integrity: unsupported-claim rate over a sample of investigations (target 0%).
2. Consolidation vs rule (held-out): confusion + precision/recall (NEXUS ~parity, not superior).
3. Seeded demonstrations: ring -> escalate, benign merchant -> monitor, structuring -> report.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.config import Settings, paths_for  # noqa: E402
from nexus.eval.metrics import confusion  # noqa: E402
from nexus.ground_truth import parse_patterns  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.orchestrator import run  # noqa: E402
from nexus.peers import PeerModel  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402
from nexus.seeds import seed_demo_constructs, SEED_C1, SEED_M1  # noqa: E402

CONSOLIDATION = {"FAN-IN", "GATHER-SCATTER"}


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    con = ds.con
    profiles = build_profiles(con)
    peers = PeerModel(profiles)
    indeg = profiles["in_degree"].to_dict()
    instances = parse_patterns(paths_for("HI-Small").patterns)

    hubs = sorted({
        f"{Counter((r[3], r[4]) for r in i.transactions).most_common(1)[0][0][0]}|"
        f"{Counter((r[3], r[4]) for r in i.transactions).most_common(1)[0][0][1]}"
        for i in instances if i.typology in CONSOLIDATION
    })
    hubs = [h for h in hubs if h in profiles.index and indeg[h] <= 400]
    laund = {r[0] for r in con.execute(
        "SELECT DISTINCT to_bank||'|'||receiver_account FROM transactions WHERE is_laundering "
        "UNION SELECT DISTINCT from_bank||'|'||sender_account FROM transactions WHERE is_laundering"
    ).fetchall()}
    neg = profiles[(profiles["in_degree"] >= 6) & (profiles["in_degree"] <= 60)]
    neg = sorted(neg[~neg.index.isin(laund)].index)[:100]

    # 1. Unsupported-claim rate over a sample.
    sample = hubs[:25] + neg[:25]
    bad = 0
    for node in sample:
        res = run(f"explain {node}", con, peers, profiles)
        if not res.validated:
            bad += 1
    print("=" * 60)
    print("1) EXPLANATION INTEGRITY")
    print(f"   investigations sampled : {len(sample)}")
    print(f"   unsupported-claim rate : {100 * bad / len(sample):.1f}%  (target 0%)")

    # 2. Consolidation vs rule (held-out test half).
    pos_te, neg_te = hubs[1::2], neg[1::2]
    preds, labels = [], []
    for h in pos_te:
        preds.append(run(f"trace ring at {h}", con, peers, profiles).case.escalation
                     in {"review", "report"})
        labels.append(True)
    for n in neg_te:
        preds.append(run(f"trace ring at {n}", con, peers, profiles).case.escalation
                     in {"review", "report"})
        labels.append(False)
    c = confusion(preds, labels)
    print("\n2) CONSOLIDATION vs GROUND TRUTH (held-out)")
    print(f"   {c.as_dict()}")
    print("   (honest: NEXUS ~parity with a fan-in threshold; not superior — see plan.md)")

    # 3. Seeded demonstrations.
    seed_demo_constructs(con)
    peers2 = PeerModel(build_profiles(con))
    ring = run(f"trace ring at {SEED_C1}", con, peers2, profiles).case.escalation
    merch = run(f"explain {SEED_M1}", con, peers2, profiles).case.escalation
    struct = run(f"structuring at {SEED_C1}", con, peers2, profiles).case.escalation
    print("\n3) SEEDED DEMONSTRATIONS")
    print(f"   smurfing ring    -> {ring.upper()}")
    print(f"   benign merchant  -> {merch.upper()}")
    print(f"   structuring ring -> {struct.upper()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
