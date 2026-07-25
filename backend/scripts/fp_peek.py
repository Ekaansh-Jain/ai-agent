"""Throwaway one-number peek: rules-baseline false positives vs. our engine.

Rules baseline (classic structuring rule): flag any account that RECEIVES >= 3 cash
deposits in the near-threshold band [9000, 10000) USD. Count how many flagged accounts are
actually benign (no laundering inflow) -> the false-positive pain.

Then run our 3-tool engine on those same flagged accounts and count how many WE escalate
(risk >= review). Not the full harness — one honest number each.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.config import Settings  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.ledger import EvidenceLedger  # noqa: E402
from nexus.peers import PeerModel  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402
from nexus.risk import risk_score  # noqa: E402
from nexus.tools import graph_motif, peer_comparison, rapid_pass_through  # noqa: E402

CAP = 100  # bound engine runtime for the peek


def main() -> None:
    ds = load_dataset(Settings(variant="HI-Small"))
    con = ds.con

    rule = con.execute(
        """
        SELECT to_bank || '|' || receiver_account AS node,
               COUNT(*) AS n_near,
               MAX(CASE WHEN is_laundering THEN 1 ELSE 0 END) AS has_laundering
        FROM transactions
        WHERE payment_format = 'Cash' AND amount_base >= 9000 AND amount_base < 10000
        GROUP BY 1
        HAVING COUNT(*) >= 3
        """
    ).df()

    flagged = len(rule)
    rule_fp = int((rule["has_laundering"] == 0).sum())
    rule_tp = flagged - rule_fp
    print("=" * 60)
    print("RULES BASELINE — 'account receives >=3 cash deposits in [9000,10000)'")
    print("=" * 60)
    print(f"  flagged accounts     : {flagged:,}")
    print(f"  true positives       : {rule_tp:,}")
    print(f"  FALSE POSITIVES      : {rule_fp:,}")
    if flagged:
        print(f"  precision            : {100 * rule_tp / flagged:.1f}%")

    print("\nbuilding profiles + peers for our-engine pass ...")
    peers = PeerModel(build_profiles(con))

    sample = rule.head(CAP)
    ours_escalate = 0
    ours_escalate_benign = 0
    for _, row in sample.iterrows():
        node = row["node"]
        lg = EvidenceLedger()
        peer_comparison.run(con, peers, node, lg)
        rapid_pass_through.run(con, node, lg)
        graph_motif.run(con, node, lg)
        if risk_score(lg.records).escalation in {"review", "report"}:
            ours_escalate += 1
            if row["has_laundering"] == 0:
                ours_escalate_benign += 1

    print("\n" + "=" * 60)
    print(f"OUR ENGINE — on the first {len(sample)} rule-flagged accounts")
    print("=" * 60)
    print(f"  we escalate          : {ours_escalate}/{len(sample)}")
    print(f"  of those, benign (FP): {ours_escalate_benign}")
    kept = len(sample) - ours_escalate
    print(f"  we DID NOT escalate  : {kept}/{len(sample)}  "
          f"(rule would have investigated all {len(sample)})")
    print("=" * 60)


if __name__ == "__main__":
    main()
