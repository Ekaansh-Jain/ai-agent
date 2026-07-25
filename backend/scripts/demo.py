"""End-to-end agentic demo: natural-language query -> validated, fact-checked case.

Seeds the demo constructs into the real store, then runs the orchestrator on a few queries,
printing the parsed intent, the per-query plan (tools run / skipped), the validated
narrative, and the escalation.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus.config import Settings  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.orchestrator import run  # noqa: E402
from nexus.peers import PeerModel  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402
from nexus.seeds import seed_demo_constructs  # noqa: E402

QUERIES = [
    "Find and trace the smurfing ring at 0500|C1",
    "Explain why 0900|M1 looks suspicious",
    "Look for structuring at 0500|C1",
]


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    seed_demo_constructs(ds.con)
    profiles = build_profiles(ds.con)
    peers = PeerModel(profiles)

    for q in QUERIES:
        res = run(q, ds.con, peers, profiles)
        print("=" * 68)
        print(f"QUERY: {q}")
        print("-" * 68)
        print(f"intent={res.spec.intent}  typology={res.spec.typology}  "
              f"entities={res.spec.entities}")
        print(f"plan run    : {res.tools_run}")
        print(f"plan skipped: {[t for t, _ in res.tools_skipped]}")
        print("-" * 68)
        print(res.narrative)
        print("-" * 68)
        print(f"sources: intent={res.intent_source} narrator={res.narrator_source}")
        print(f"validated={res.validated}  unsupported={res.unsupported}  "
              f"-> {res.case.escalation.upper()}")
    print("=" * 68)


if __name__ == "__main__":
    main()
