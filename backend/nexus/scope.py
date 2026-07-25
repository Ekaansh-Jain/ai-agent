"""Filter_Scope — the one place a parsed query filter becomes SQL.

`intent.parse` extracts `payment_format` and `month` into `InvestigationSpec.filters`.
Before this module nothing downstream read them, so "analyse cash deposits in March" ran
identically to "analyse everything". Now a scope is derived once and threaded into the
tools that can honour it without shifting their calibration.

An INACTIVE scope (or `None`) contributes no SQL text at all — that is what keeps the
locked anchors byte-identical, since no anchor query carries a filter.
"""

from __future__ import annotations

import duckdb

from .schemas import FilterScope, InvestigationSpec

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def is_active(scope: FilterScope | None) -> bool:
    return scope is not None and (scope.payment_format is not None or scope.month is not None)


def from_spec(spec: InvestigationSpec) -> FilterScope | None:
    """Build a scope from the spec's filters, or None when there is nothing to apply."""
    return from_filters(spec.filters)


def from_filters(filters: dict[str, str] | None) -> FilterScope | None:
    if not filters:
        return None
    fmt = filters.get("payment_format")
    month = filters.get("month")
    if fmt is None and month is None:
        return None
    scope = FilterScope(
        payment_format=fmt,
        month=month,
        month_number=_MONTHS.get(month.lower()) if month else None,
    )
    return scope if is_active(scope) else None


def sql(scope: FilterScope | None) -> tuple[str, list]:
    """Return (predicate_sql, params). ('', []) when the scope is inactive."""
    if not is_active(scope):
        return "", []
    parts: list[str] = []
    params: list = []
    if scope.payment_format is not None:
        parts.append("lower(payment_format) = lower(?)")
        params.append(scope.payment_format)
    if scope.month_number is not None:
        # Named calendar month in ANY year present — the parser extracts no year.
        parts.append("month(timestamp) = ?")
        params.append(scope.month_number)
    elif scope.month is not None:
        # Month name we could not map: match nothing rather than silently ignoring it.
        parts.append("1 = 0")
    return " AND ".join(parts), params


def where(scope: FilterScope | None, *clauses: str) -> tuple[str, list]:
    """Compose caller clauses with the scope predicate into one WHERE fragment.

    Returns ("WHERE a AND b", params_for_scope_only). Callers keep their own params and
    append the returned ones in order.
    """
    scope_sql, params = sql(scope)
    all_clauses = [c for c in clauses if c] + ([scope_sql] if scope_sql else [])
    if not all_clauses:
        return "", params
    return "WHERE " + " AND ".join(all_clauses), params


def applied(scope: FilterScope | None) -> dict[str, str]:
    """The filters actually in force, for the plan trace and the execution summary."""
    if not is_active(scope):
        return {}
    out: dict[str, str] = {}
    if scope.payment_format is not None:
        out["payment_format"] = scope.payment_format
    if scope.month is not None:
        out["month"] = scope.month
    return out


def describe(scope: FilterScope | None) -> str:
    """Human-readable scope, e.g. "payment_format=Cash, month=March"."""
    items = applied(scope)
    return ", ".join(f"{k}={v}" for k, v in items.items()) if items else "unfiltered"


def count(con: duckdb.DuckDBPyConnection, scope: FilterScope | None) -> int:
    """Transactions satisfying every predicate of the scope (all rows when inactive)."""
    clause, params = where(scope)
    row = con.execute(f"SELECT COUNT(*) FROM transactions {clause}", params).fetchone()
    return int(row[0]) if row else 0
