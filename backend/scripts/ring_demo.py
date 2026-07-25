"""Phase 3a demo: run the three tools on a REAL ring from HI-Small and score it.

The ring is located via held-out ground truth (test/eval scaffolding only) — the tools
themselves never see labels. Prefers a GATHER-SCATTER hub (has an outflow/scatter phase so
rapid_pass_through fires) and falls back to FAN-IN.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.config import Settings, paths_for  # noqa: E402
from nexus.duel import confidence, score_all, winner  # noqa: E402
from nexus.ground_truth import parse_patterns  # noqa: E402
from nexus.hypotheses import load_hypotheses  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.ledger import EvidenceLedger  # noqa: E402
from nexus.peers import PeerModel  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402
from nexus.risk import counterfactuals, risk_score  # noqa: E402
from nexus.tools import graph_motif, peer_comparison, rapid_pass_through  # noqa: E402

PREFERRED = ["GATHER-SCATTER", "FAN-IN"]


def _hub_of(inst) -> str:
    receivers = Counter((row[3], row[4]) for row in inst.transactions)
    (bank, acct), _ = receivers.most_common(1)[0]
    return f"{bank}|{acct}"


def _outbound_count(con, node: str) -> int:
    bank, acct = node.split("|", 1)
    return con.execute(
        "SELECT COUNT(*) FROM transactions WHERE from_bank = ? AND sender_account = ?",
        [bank, acct],
    ).fetchone()[0]


def _pick_ring(con, instances):
    for typ in PREFERRED:
        for inst in instances:
            if inst.typology != typ:
                continue
            hub = _hub_of(inst)
            if _outbound_count(con, hub) > 0:
                return inst, hub
    # last resort: any instance
    inst = instances[0]
    return inst, _hub_of(inst)


def main() -> None:
    settings = Settings(variant="HI-Small")
    print("loading dataset ...")
    ds = load_dataset(settings)

    print("building account profiles + peer clusters ...")
    profiles = build_profiles(ds.con)
    peers = PeerModel(profiles)

    instances = parse_patterns(paths_for(settings.variant).patterns)
    inst, hub = _pick_ring(ds.con, instances)

    feeders = sorted({(r[1], r[2]) for r in inst.transactions})
    print("\n" + "=" * 64)
    print(f"REAL RING  —  typology {inst.typology}  ({inst.description})")
    print("=" * 64)
    print(f"hub (collector) : {hub}")
    print(f"ring rows       : {inst.size}")
    print(f"feeder accounts : {len(feeders)}")
    for b, a in feeders[:10]:
        print(f"    {b}|{a}")
    if len(feeders) > 10:
        print(f"    ... +{len(feeders) - 10} more")

    ledger = EvidenceLedger()
    peer_comparison.run(ds.con, peers, hub, ledger)
    rapid_pass_through.run(ds.con, hub, ledger)
    graph_motif.run(ds.con, hub, ledger)

    print("\nEVIDENCE LEDGER")
    print("-" * 64)
    for r in ledger.records:
        print(f"  {r.claim_id} [{r.family}] {r.claim}")
        print(f"        value={r.value}  strength={r.strength}  dir={r.direction}  "
              f"proof={len(r.transactions)} txns")

    hyps = load_hypotheses("smurfing")
    scores = score_all(hyps, ledger.records)
    print("\nHYPOTHESIS DUEL")
    print("-" * 64)
    for s in scores:
        print(f"  {s.id:3} {s.label:38} {s.band:12} norm={s.normalized:+.3f} ({s.kind})")
    top = winner(scores)

    risk = risk_score(ledger.records)
    print("\nVERDICT")
    print("-" * 64)
    print(f"  winner     : {top.id} ({top.kind})  confidence={confidence(scores)}")
    print(f"  risk score : {risk.score}/100  ->  {risk.tier.upper()}  ->  {risk.escalation}")
    print(f"  contributions: {risk.contributions}")
    print(f"  counterfactual: {counterfactuals(ledger.records)}")
    print("=" * 64)


if __name__ == "__main__":
    main()
