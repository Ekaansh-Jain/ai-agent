"""Test session setup.

Force the deterministic path for all tests (no live Gemini calls) so the suite is hermetic,
fast, and reproducible regardless of a local .env key. The LLM edge is validated separately
via scripts/demo.py and the live API.

Two session-level hermeticity sentinels back that claim up instead of merely stating it:

1. An LLM invocation counter wrapped around the three `nexus/llm.py` edge functions,
   asserted zero at session teardown (Requirement 13.16). Unconditional: the gated
   integration suite forces `NEXUS_USE_LLM=0` in its own fixture too, so zero holds there.
2. A guard that fails any file access under `data/raw/` (Requirement 15.13). Disabled when
   `NEXUS_RUN_INTEGRATION` is set, because the gated suite legitimately loads
   `data/raw/HI-Small_Trans.csv`.
"""

import builtins
import os

os.environ["NEXUS_USE_LLM"] = "0"

from pathlib import Path  # noqa: E402 - after the LLM-off forcing, on purpose

import pytest  # noqa: E402

# backend/tests/conftest.py -> repo root -> data/raw
_DATA_RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
_INTEGRATION = bool(os.getenv("NEXUS_RUN_INTEGRATION"))

# The LLM edge functions. `_generate` is the single outbound call site; `intent_llm` and
# `narrate_llm` are the two public edges. All three are counted, so a call through a public
# edge registers twice — irrelevant, since the assertion is that the total is zero.
_LLM_EDGES = ("_generate", "intent_llm", "narrate_llm")


@pytest.fixture(scope="session", autouse=True)
def _llm_invocation_counter():
    """Requirement 13.16: the suite runs with zero LLM invocations."""
    from nexus import llm

    calls: list[str] = []
    originals = {name: getattr(llm, name) for name in _LLM_EDGES}

    def _counted(name, original):
        def wrapper(*args, **kwargs):
            calls.append(name)
            return original(*args, **kwargs)

        return wrapper

    for name, original in originals.items():
        setattr(llm, name, _counted(name, original))
    try:
        yield calls
    finally:
        for name, original in originals.items():
            setattr(llm, name, original)

    assert not calls, (
        f"hermeticity sentinel: expected zero LLM invocations, saw {len(calls)} "
        f"({', '.join(sorted(set(calls)))}) — the suite must run with NEXUS_USE_LLM=0"
    )


def _under_data_raw(path) -> bool:
    """True when `path` names a file inside the repo's data/raw directory."""
    try:
        raw = os.fspath(path)
    except TypeError:
        return False  # file descriptor or unsupported object
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if "raw" not in raw:  # cheap pre-filter: skip resolve() for the common case
        return False
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return False
    return resolved == _DATA_RAW or _DATA_RAW in resolved.parents


@pytest.fixture(scope="session", autouse=True)
def _no_data_raw_access():
    """Requirement 15.13: the hermetic suite reads no file under data/raw/.

    Scoped off when NEXUS_RUN_INTEGRATION is set: the gated real-data suite loads
    data/raw/HI-Small_Trans.csv by design.
    """
    if _INTEGRATION:
        yield
        return

    import duckdb

    real_open, real_os_open = builtins.open, os.open
    real_execute, real_sql = duckdb.DuckDBPyConnection.execute, duckdb.DuckDBPyConnection.sql

    def _fail(path):
        raise AssertionError(
            f"hermeticity sentinel: the unit suite must not read {path} — it lives under "
            f"{_DATA_RAW}. Use a fixture in backend/tests/fixtures/, or gate the test on "
            "NEXUS_RUN_INTEGRATION."
        )

    def _guarded_open(file, *args, **kwargs):
        if _under_data_raw(file):
            _fail(file)
        return real_open(file, *args, **kwargs)

    def _guarded_os_open(path, *args, **kwargs):
        if _under_data_raw(path):
            _fail(path)
        return real_os_open(path, *args, **kwargs)

    def _guard_sql(original):
        # Catches a DuckDB-side read (read_csv_auto and friends), which never touches
        # Python file IO and so slips past the two open() wrappers above.
        def wrapper(self, query="", *args, **kwargs):
            if isinstance(query, str) and ("data/raw" in query or str(_DATA_RAW) in query):
                _fail(query)
            return original(self, query, *args, **kwargs)

        return wrapper

    builtins.open = _guarded_open
    os.open = _guarded_os_open
    duckdb.DuckDBPyConnection.execute = _guard_sql(real_execute)
    duckdb.DuckDBPyConnection.sql = _guard_sql(real_sql)
    try:
        yield
    finally:
        builtins.open, os.open = real_open, real_os_open
        duckdb.DuckDBPyConnection.execute = real_execute
        duckdb.DuckDBPyConnection.sql = real_sql
