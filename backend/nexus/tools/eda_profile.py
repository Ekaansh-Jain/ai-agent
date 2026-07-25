"""eda_profile — exploratory data analysis as an agent-callable tool.

`profile.py` already did this work, but as a print()-only script that reads the held-out
ground truth, so the agent could never invoke it. This tool profiles the slice the analyst
actually asked about, returns everything through its return value, and reads ONLY the twelve
transaction columns listed in COLUMNS — no label column, no pattern file.

Its evidence lands under the NEUTRAL `data_profile` family: absent from every hypothesis
fingerprint and every risk weight profile, so profiling can never move a score.
"""

from __future__ import annotations

import duckdb

from . import clamp
from ..config import Settings
from ..ledger import EvidenceLedger
from ..schemas import (
    AmountSummary, CategoryCount, DataQuality, Distribution, EdaProfile, EvidenceRecord,
    FilterScope, TimeSpan,
)
from .. import scope as scope_mod

# The ONLY transaction columns this tool may read. The held-out label column is absent.
COLUMNS: tuple[str, ...] = (
    "timestamp", "from_bank", "sender_account", "to_bank", "receiver_account",
    "amount_paid", "amount_received", "payment_currency", "receiving_currency",
    "payment_format", "amount_base", "cross_currency",
)
# Held-out columns are absent from COLUMNS by construction, and a test scans this module
# to prove no label or pattern symbol appears anywhere in it.

_TOP_N = 20          # distribution entries kept before collapsing into a remainder
_MAX_PROOF_TX = 50   # upper bound on tx_ids cited by the evidence record


class MissingColumnError(RuntimeError):
    """A required transaction column is absent or unreadable."""

    def __init__(self, column: str):
        super().__init__(f"transactions.{column} is absent or unreadable")
        self.column = column


def _require_columns(con: duckdb.DuckDBPyConnection) -> None:
    present = {r[0] for r in con.execute("DESCRIBE transactions").fetchall()}
    for col in COLUMNS:
        if col not in present:
            raise MissingColumnError(col)


def _distribution(
    con: duckdb.DuckDBPyConnection, column: str, clause: str, params: list, total: int
) -> Distribution:
    rows = con.execute(
        f"SELECT {column} AS category, COUNT(*) AS n FROM transactions {clause} "
        f"GROUP BY 1 ORDER BY n DESC, category ASC",
        params,
    ).fetchall()
    kept = rows[:_TOP_N]
    rest = rows[_TOP_N:]
    return Distribution(
        column=column,
        entries=[CategoryCount(category=str(c), count=int(n)) for c, n in kept],
        remainder_categories=len(rest),
        remainder_count=sum(int(n) for _, n in rest),
    )


def run(
    con: duckdb.DuckDBPyConnection,
    scope: FilterScope | None = None,
    ledger: EvidenceLedger | None = None,
    settings: Settings | None = None,
) -> EdaProfile:
    """Profile the scoped slice. Raises MissingColumnError BEFORE touching the ledger."""
    settings = settings or Settings()
    _require_columns(con)

    clause, params = scope_mod.where(scope)
    active = scope_mod.is_active(scope)

    # One combined scalar pass: counts, quality flags, time bounds.
    (
        n_tx, n_cross, n_null_ts, n_bad_amt, ts_min, ts_max, active_days,
    ) = con.execute(
        f"""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE cross_currency),
               COUNT(*) FILTER (WHERE timestamp IS NULL),
               COUNT(*) FILTER (WHERE amount_paid <= 0 OR amount_received <= 0),
               MIN(timestamp), MAX(timestamp),
               COUNT(DISTINCT CAST(timestamp AS DATE))
        FROM transactions {clause}
        """,
        params,
    ).fetchone()
    n_tx = int(n_tx or 0)

    if n_tx == 0:
        return EdaProfile(
            scope_active=active, scope=scope_mod.applied(scope),
            transactions=0, accounts=0, distributions={},
            cross_currency_count=0, cross_currency_rate=None,
            amounts=None, time_span=None, quality=DataQuality(),
        )

    # Distinct accounts = bank+account pairs over the union of both sides of the slice.
    n_accounts = int(con.execute(
        f"""
        SELECT COUNT(*) FROM (
          SELECT DISTINCT from_bank AS b, sender_account AS a FROM transactions {clause}
          UNION
          SELECT DISTINCT to_bank AS b, receiver_account AS a FROM transactions {clause}
        )
        """,
        params + params,
    ).fetchone()[0])

    amt = con.execute(
        f"""
        SELECT COUNT(amount_base), MIN(amount_base), MAX(amount_base), AVG(amount_base),
               MEDIAN(amount_base), QUANTILE_CONT(amount_base, 0.95), SUM(amount_base)
        FROM transactions {clause}
        """,
        params,
    ).fetchone()
    amounts = (
        AmountSummary(
            count=int(amt[0]), min=float(amt[1]), max=float(amt[2]), mean=float(amt[3]),
            median=float(amt[4]), p95=float(amt[5]), sum=float(amt[6]),
        )
        if amt and amt[0] and int(amt[0]) > 0 else None
    )

    distributions = {
        col: _distribution(con, col, clause, params, n_tx)
        for col in ("payment_format", "payment_currency", "receiving_currency")
    }

    # Currencies with no configured FX rate — a real data-quality risk for amount_base.
    known = list(settings.fx_per_usd)
    placeholders = ", ".join(["?"] * len(known)) or "NULL"
    unpriced_clause = (
        f"{clause} AND " if clause else "WHERE "
    ) + f"(payment_currency NOT IN ({placeholders}) OR receiving_currency NOT IN ({placeholders}))"
    unpriced_rows = con.execute(
        f"""
        SELECT payment_currency AS ccy, COUNT(*) AS n
        FROM transactions {unpriced_clause}
        GROUP BY 1 ORDER BY n DESC, ccy ASC LIMIT {_TOP_N}
        """,
        params + known + known,
    ).fetchall()
    n_unpriced = int(con.execute(
        f"SELECT COUNT(*) FROM transactions {unpriced_clause}", params + known + known
    ).fetchone()[0])

    time_span = None
    if ts_min is not None and ts_max is not None:
        time_span = TimeSpan(
            first=ts_min, last=ts_max,
            span_days=max((ts_max.date() - ts_min.date()).days + 1, 1),
            active_days=int(active_days or 0),
        )

    rate = clamp(int(n_cross or 0) / n_tx)
    profile = EdaProfile(
        scope_active=active,
        scope=scope_mod.applied(scope),
        transactions=n_tx,
        accounts=n_accounts,
        distributions=distributions,
        cross_currency_count=int(n_cross or 0),
        cross_currency_rate=round(rate, 4),
        amounts=amounts,
        time_span=time_span,
        quality=DataQuality(
            null_timestamps=int(n_null_ts or 0),
            non_positive_amounts=int(n_bad_amt or 0),
            unpriced_currency_transactions=n_unpriced,
            unpriced_currencies=[str(c) for c, _ in unpriced_rows],
        ),
    )

    if ledger is not None:
        tx_ids = [
            int(r[0]) for r in con.execute(
                f"SELECT tx_id FROM transactions {clause} LIMIT {_MAX_PROOF_TX}", params
            ).fetchall()
        ]
        ledger.add(EvidenceRecord(
            claim_id=ledger.mint_id(),
            family="data_profile",   # NEUTRAL — in no fingerprint, in no risk weight
            claim=(
                f"profiled {n_tx:,} transactions across {n_accounts:,} accounts "
                f"({scope_mod.describe(scope)}); cross-currency {rate * 100:.2f}%"
            ),
            calculation="aggregate scan over the scoped slice; strength = cross-currency rate",
            value=round(rate, 3),
            direction="high" if rate >= 0.5 else "low",
            strength=round(rate, 3),
            transactions=tx_ids,
        ))

    return profile
