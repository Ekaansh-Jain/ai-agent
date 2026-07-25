"""Phase 1 profiling: a plain-language summary of a loaded dataset.

Uses the held-out ground truth ONLY to print counts for our eyes (pattern instances per
typology, join match rate). This is a profiling/reporting script, not the agent path.
"""

from __future__ import annotations

from .config import Settings, paths_for
from .ingest import Dataset
from .ground_truth import GroundTruth, parse_patterns


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def print_profile(ds: Dataset, settings: Settings | None = None, root=None) -> None:
    settings = settings or Settings(variant=ds.variant)
    con = ds.con

    n_accounts = con.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT from_bank AS b, sender_account AS a FROM transactions"
        "  UNION SELECT to_bank, receiver_account FROM transactions"
        ")"
    ).fetchone()[0]

    ccy = con.execute(
        "SELECT payment_currency, COUNT(*) c FROM transactions "
        "GROUP BY 1 ORDER BY c DESC"
    ).fetchall()
    fmt = con.execute(
        "SELECT payment_format, COUNT(*) c FROM transactions "
        "GROUP BY 1 ORDER BY c DESC"
    ).fetchall()
    cross = con.execute(
        "SELECT COUNT(*) FROM transactions WHERE cross_currency"
    ).fetchone()[0]
    bad_ts = con.execute(
        "SELECT COUNT(*) FROM transactions WHERE timestamp IS NULL"
    ).fetchone()[0]
    label_pos = con.execute(
        "SELECT COUNT(*) FROM transactions WHERE is_laundering"
    ).fetchone()[0]

    print("=" * 64)
    print(f"NEXUS-AML — Phase 1 profile — variant {ds.variant}")
    print("=" * 64)
    print(f"transactions      : {_fmt_int(ds.n_transactions)}")
    print(f"distinct accounts : {_fmt_int(n_accounts)}  (node = bank+account)")
    print(f"accounts.csv rows : {_fmt_int(len(ds.accounts))}")
    print(f"entities          : {_fmt_int(len(ds.entity_to_accounts))}")
    print(f"unparsed timestamps: {_fmt_int(bad_ts)}")
    print(f"cross-currency txns: {_fmt_int(cross)}  "
          f"({100 * cross / max(ds.n_transactions, 1):.3f}%)")

    print("\ncurrencies (by txn count):")
    for c, n in ccy:
        flag = "" if c in settings.fx_per_usd else "  <-- no FX rate!"
        print(f"  {c:<18} {_fmt_int(n)}{flag}")

    print("\npayment formats:")
    for f, n in fmt:
        print(f"  {f:<18} {_fmt_int(n)}")

    print("\nlabels:")
    if ds.has_label_column:
        rate = 100 * label_pos / max(ds.n_transactions, 1)
        print(f"  Is Laundering column present. positives = "
              f"{_fmt_int(label_pos)} ({rate:.4f}%)")
    else:
        print("  No Is Laundering column — binary label derived from Patterns membership.")

    # Held-out patterns (our eyes only).
    p = paths_for(ds.variant, root=root) if root else paths_for(ds.variant)
    if p.patterns.is_file():
        instances = parse_patterns(p.patterns)
        gt = GroundTruth(instances)
        match_rate = gt.link(ds.key_to_tx_id)
        counts = gt.counts_by_typology()
        print("\nground truth (HELD OUT — grading only):")
        print(f"  pattern instances : {_fmt_int(len(instances))}")
        print(f"  pattern rows      : {_fmt_int(gt.laundering_row_count())}")
        print(f"  join match rate   : {100 * match_rate:.2f}% of pattern rows -> a txn")
        print("  instances per typology:")
        for typ, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {typ:<16} {_fmt_int(n)}")
        if ds.has_label_column:
            print(f"  sanity: label positives={_fmt_int(label_pos)} vs "
                  f"pattern rows={_fmt_int(gt.laundering_row_count())} "
                  "(close, not exact — a txn can join multiple patterns)")
    else:
        print(f"\nno patterns file at {p.patterns} — skipping ground-truth summary.")
    print("=" * 64)
