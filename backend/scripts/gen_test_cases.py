"""Materialize real test cases from HI-Small ground truth into tests/cases/real_cases.json.

Cases are just (node, label, group) — the integration test loads the data and runs the
engine on them. Labels come from the held-out Patterns file (eval-only use).
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.config import Settings, paths_for  # noqa: E402
from nexus.ground_truth import parse_patterns  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "tests" / "cases" / "real_cases.json"
CONSOLIDATION = {"FAN-IN", "GATHER-SCATTER"}
N_POS = 20
N_NEG = 20
N_STRUCT = 5


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    con = ds.con
    profiles = build_profiles(con)
    indeg = profiles["in_degree"].to_dict()
    instances = parse_patterns(paths_for("HI-Small").patterns)

    laund = {r[0] for r in con.execute(
        "SELECT DISTINCT to_bank||'|'||receiver_account FROM transactions WHERE is_laundering "
        "UNION SELECT DISTINCT from_bank||'|'||sender_account FROM transactions WHERE is_laundering"
    ).fetchall()}

    cases = []

    # Consolidation positives: unique ring hubs.
    hubs = []
    seen = set()
    for inst in instances:
        if inst.typology in CONSOLIDATION:
            recv = Counter((r[3], r[4]) for r in inst.transactions)
            (b, a), _ = recv.most_common(1)[0]
            node = f"{b}|{a}"
            if node in profiles.index and indeg[node] <= 400 and node not in seen:
                seen.add(node)
                hubs.append((node, inst.typology))
    for node, typ in hubs[:N_POS]:
        cases.append({"node": node, "label": "laundering", "group": typ, "typology": "smurfing"})

    # Benign negatives: high-fan-in accounts not linked to laundering.
    neg = profiles[(profiles["in_degree"] >= 6) & (profiles["in_degree"] <= 60)]
    neg = [n for n in sorted(neg.index) if n not in laund][:N_NEG]
    for node in neg:
        cases.append({"node": node, "label": "benign", "group": "high_fan_in",
                      "typology": "smurfing"})

    # Structuring positives: near-threshold cash accounts with laundering.
    struct = con.execute(
        """
        SELECT to_bank || '|' || receiver_account AS node
        FROM transactions
        WHERE payment_format = 'Cash' AND amount_base >= 9000 AND amount_base < 10000
        GROUP BY 1
        HAVING COUNT(*) >= 3 AND MAX(CASE WHEN is_laundering THEN 1 ELSE 0 END) = 1
        LIMIT ?
        """, [N_STRUCT],
    ).fetchall()
    for (node,) in struct:
        cases.append({"node": node, "label": "laundering", "group": "structuring",
                      "typology": "structuring"})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2))
    print(f"wrote {len(cases)} cases -> {OUT}")
    counts = Counter((c["label"], c["group"]) for c in cases)
    for (label, group), n in sorted(counts.items()):
        print(f"  {label:11} {group:14} {n}")


if __name__ == "__main__":
    main()
