"""TraceRecorder — per-tool execution telemetry and the failed-tool degradation rule.

The planner says what it intends to run; this records what actually happened: status,
measured duration, rows in/out, and a plain-language reason for every roster node including
the declined ones. That is the query-aware execution summary a reviewer can check.

The degradation rule is the important part: **a failing tool costs its own evidence and
nothing else.** Its partial ledger writes are rolled back so the duel and the risk engine
never see them, it is omitted from `tools_run`, and the rest of the plan still runs.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from .ledger import EvidenceLedger
from .planner import BY_ID, Plan
from .schemas import CostTelemetry, PlanTraceEntry

# A tool that really ran must never look like one that was skipped, so sub-millisecond
# runs are reported at this floor instead of 0.0.
_MIN_RAN_MS = 0.001


@dataclass
class StepHandle:
    """Handed to the caller inside `step()` so a tool can report what it moved."""

    tool: str
    rows_in: int | None = None
    rows_out: int | None = None
    detail: str = ""

    def rows(self, rows_in: int | None = None, rows_out: int | None = None) -> None:
        if rows_in is not None:
            self.rows_in = rows_in
        if rows_out is not None:
            self.rows_out = rows_out

    def say(self, text: str) -> None:
        self.detail = text


@dataclass
class _Acc:
    """Accumulated observations for one roster tool across all its invocations."""

    status: str = "skipped"
    duration_ms: float = 0.0
    rows_in: int | None = None
    rows_out: int | None = None
    invocations: int = 0
    failures: int = 0
    detail: str = ""
    first_seen: int = 0


@dataclass
class TraceRecorder:
    plan: Plan
    budget_ms: float = 30_000.0
    filters_applied: dict[str, str] = field(default_factory=dict)
    unfiltered_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._acc: dict[str, _Acc] = {}
        self._seq = 0
        self._notes: list[str] = list(self.plan.notes)
        self._roundtrips: dict[str, int] = {}
        self._started = time.perf_counter()
        self._cost_extra: dict[str, int] = {}

    # ---- observation ----------------------------------------------------------

    @contextmanager
    def step(
        self,
        tool: str,
        ledger: EvidenceLedger | None = None,
        rows_in: int | None = None,
    ) -> Iterator[StepHandle]:
        """Time a tool call, and roll its evidence back if it raises."""
        acc = self._acc.setdefault(tool, _Acc())
        if acc.invocations == 0:
            self._seq += 1
            acc.first_seen = self._seq

        handle = StepHandle(tool=tool, rows_in=rows_in)
        mark = ledger.mark() if ledger is not None else None
        start = time.perf_counter_ns()
        try:
            yield handle
        except Exception as exc:
            if ledger is not None and mark is not None:
                ledger.rollback(mark)
            acc.status = "failed" if acc.status != "ran" else acc.status
            acc.failures += 1
            acc.invocations += 1
            acc.detail = (
                f"{BY_ID[tool].label} raised {type(exc).__name__}; its evidence was "
                f"discarded and did not contribute to the verdict"
            )
            self._notes.append(f"{tool} failed: {type(exc).__name__}")
        else:
            acc.status = "ran"
            acc.invocations += 1
            if handle.detail:
                acc.detail = handle.detail
            for name in ("rows_in", "rows_out"):
                value = getattr(handle, name)
                if value is not None:
                    current = getattr(acc, name)
                    setattr(acc, name, value if current is None else current + value)
        finally:
            acc.duration_ms += (time.perf_counter_ns() - start) / 1e6

    def note(self, text: str) -> None:
        if text and text not in self._notes:
            self._notes.append(text)

    def roundtrips(self, node: str, n: int) -> None:
        self._roundtrips[node] = n

    def counts(self, **kwargs: int) -> None:
        self._cost_extra.update({k: int(v) for k, v in kwargs.items()})

    # ---- output --------------------------------------------------------------

    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    def entries(self) -> list[PlanTraceEntry]:
        """One entry per roster tool: ran/failed in attempted order, then skipped."""
        out: list[PlanTraceEntry] = []
        for decision in self.plan.decisions:
            acc = self._acc.get(decision.tool)
            tool = BY_ID[decision.tool]

            if acc is None or acc.invocations == 0:
                status, duration, rows_in, rows_out, invocations = "skipped", 0.0, None, None, 0
                reason = decision.reason
                if decision.selected:
                    reason = f"selected but not reached this run ({decision.reason})"
            else:
                status = acc.status
                duration = max(acc.duration_ms, _MIN_RAN_MS) if status == "ran" else acc.duration_ms
                rows_in, rows_out, invocations = acc.rows_in, acc.rows_out, acc.invocations
                reason = acc.detail or decision.reason
                if acc.invocations > 1:
                    reason = f"{reason}; ran on {acc.invocations} candidates"
                if status == "ran" and acc.failures:
                    reason = f"{reason}; {acc.failures} invocation(s) failed"

            not_applied = (
                [k for k in self.filters_applied]
                if decision.tool in self.unfiltered_tools and self.filters_applied
                else []
            )
            applied = {} if not_applied else dict(self.filters_applied)
            if not decision.selected and status == "skipped":
                applied = {}

            out.append(PlanTraceEntry(
                tool=decision.tool, label=tool.label, status=status,
                reason=(reason or decision.reason)[:200],
                duration_ms=round(duration, 4),
                rows_in=rows_in, rows_out=rows_out, invocations=invocations,
                filters_applied=applied, filters_not_applied=not_applied,
            ))

        rank = {"ran": 0, "failed": 0, "skipped": 1}
        order = {d.tool: i for i, d in enumerate(self.plan.decisions)}
        seen = {t: (self._acc[t].first_seen if t in self._acc else 10_000) for t in order}
        out.sort(key=lambda e: (rank[e.status], seen[e.tool], order[e.tool]))
        return out

    def ran(self) -> list[str]:
        return [e.tool for e in self.entries() if e.status == "ran"]

    def skipped(self) -> list[tuple[str, str]]:
        return [(e.tool, e.reason) for e in self.entries() if e.status != "ran"]

    def cost(self, settings) -> CostTelemetry:
        elapsed = (time.perf_counter() - self._started) * 1000.0
        rt = list(self._roundtrips.values())
        return CostTelemetry(
            max_candidates=settings.max_candidates,
            max_investigations=settings.max_investigations,
            max_roundtrips_per_candidate=settings.max_roundtrips_per_candidate,
            candidate_pool_size=self._cost_extra.get("candidate_pool_size", 0),
            candidates_eligible=self._cost_extra.get("candidates_eligible", 0),
            candidates_dropped=self._cost_extra.get("candidates_dropped", 0),
            investigated=self._cost_extra.get("investigated", 0),
            excluded=self._cost_extra.get("excluded", 0),
            returned=self._cost_extra.get("returned", 0),
            roundtrips_max_per_candidate=max(rt) if rt else 0,
            roundtrips_total=sum(rt),
            wall_clock_ms=round(elapsed, 2),
            budget_ms=settings.broad_query_budget_s * 1000.0,
            within_budget=elapsed <= settings.broad_query_budget_s * 1000.0,
        )


class CountingConnection:
    """Thin proxy that counts `execute` calls so per-candidate cost is measured, not guessed.

    Everything else delegates to the real DuckDB connection, so tools are unaware of it.
    """

    def __init__(self, con):
        self._con = con
        self.count = 0

    def execute(self, *args, **kwargs):
        self.count += 1
        return self._con.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._con, name)
