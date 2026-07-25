"""Suite integrity: the pre-feature suite is preserved and new modules stay in place.

Property 25 (Requirements 15.2, 15.15). Two halves:

1. **Inventory preservation.** The full pre-feature test-function-name inventory across the
   ten existing test modules is pinned here as data. Every pinned function must still exist
   by name and must carry no `skip`/`skipif`/`xfail` marker beyond the one gate that was
   already there before this feature (see `TOLERATED_MODULE_GATES`). Requirement 15.3 makes
   deletion, renaming, skipping or xfailing a pre-feature test a regression, so this file is
   the mechanical enforcement of that.

2. **Module placement.** Every module this feature adds must live under `backend/nexus/`
   (Requirement 15.15), and no new module may appear at the repository root.

The inventory is discovered by **parsing** the test files with `ast`, never by importing
them: importing a test module from inside a test drags in its fixtures, its module-level
side effects and its collection-time skips, and would make this guard fragile.

Hermetic: reads only files under `backend/`, never `data/raw/`, never the network.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# backend/tests/test_suite_integrity.py -> tests -> backend -> repo root
TESTS_DIR = Path(__file__).resolve().parent
BACKEND = TESTS_DIR.parent
REPO_ROOT = BACKEND.parent
PACKAGE = BACKEND / "nexus"

# Directories excluded from every filesystem walk below. `data` and `models` are excluded so
# the walk cannot even stat a held-out artefact; the rest are vendored or generated trees.
EXCLUDED_DIRS = frozenset(
    {".venv", "__pycache__", ".pytest_cache", ".git", ".kiro", "node_modules", "data", "models"}
)

# The two package-relative trees a Python file in this repo is allowed to live in, per the
# structure rules: the importable package and the throwaway script directory. Tests are the
# third, handled separately.
ALLOWED_PY_ROOTS = ("backend/nexus", "backend/scripts", "backend/tests")

# --------------------------------------------------------------------------------------- #
# Pinned pre-feature inventory: 47 test functions across ten modules, captured before any
# module under backend/nexus/ was touched by this feature. Counts per module are pinned
# alongside the names so an addition to a pre-feature module is visible, not silent.
# --------------------------------------------------------------------------------------- #

PRE_FEATURE_INVENTORY: dict[str, tuple[str, ...]] = {
    "test_phase1.py": (
        "test_transactions_load_and_normalize",
        "test_fx_normalization_non_usd",
        "test_string_bank_codes_not_collapsed",
        "test_patterns_parse_and_link",
        "test_accounts_entity_map",
    ),
    "test_phase2.py": (
        "test_hypothesis_library_loads",
        "test_duel_suspicious_ring_wins",
        "test_risk_score_matches_worked_example",
        "test_counterfactual_shows_corroboration",
        "test_benign_lookalike_downgraded",
    ),
    "test_phase3a.py": (
        "test_tools_emit_expected_evidence",
        "test_duel_and_risk_on_ring",
    ),
    "test_phase3b.py": (
        "test_benign_lookalike_downgraded_to_monitor",
        "test_ring_expands_and_excludes_salary_payer",
    ),
    "test_phase3c.py": (
        "test_structuring_routing_escalates",
        "test_structuring_benign_when_not_threshold_shaped",
        "test_consolidation_anchor_unchanged",
    ),
    "test_phase4.py": (
        "test_intent_parser",
        "test_plan_is_per_query_not_fixed",
        "test_orchestration_suspicious_ring_validated",
        "test_narrative_zero_unsupported_on_benign",
    ),
    "test_phase5.py": (
        "test_metrics_confusion",
        "test_llm_fallback_without_key",
        "test_api_investigate_endpoint",
        "test_api_structuring_plan_differs",
    ),
    "test_anomaly.py": (
        "test_anomaly_scores_in_unit_interval",
        "test_anomaly_is_neutral_to_the_verdict",
        "test_baselines_return_metrics",
    ),
    "test_api_hardening.py": (
        "test_cors_preflight_allowed",
        "test_cors_header_on_real_request",
        "test_health_reports_ready_state",
        "test_health_reports_warming_and_investigate_returns_503",
        "test_background_warmup_reaches_ready",
        "test_no_evidence_is_indeterminate_not_suspicious",
        "test_unknown_account_returns_404",
        "test_known_account_still_works",
        "test_validation_error_uses_envelope",
        "test_blank_query_rejected",
        "test_error_envelope_shape_is_consistent",
        "test_concurrent_requests_are_serialized_safely",
        "test_state_exposes_lock",
    ),
    "test_integration_realdata.py": (
        "test_zero_unsupported_claims_everywhere",
        "test_every_case_is_well_formed",
        "test_intent_and_plan_route_by_typology",
        "test_determinism",
        "test_structuring_true_positive_escalates",
        "test_metrics_report",
    ),
}

PRE_FEATURE_TOTAL = 47
assert sum(len(v) for v in PRE_FEATURE_INVENTORY.values()) == PRE_FEATURE_TOTAL

INVENTORY_PAIRS = tuple(
    (module, function)
    for module, functions in PRE_FEATURE_INVENTORY.items()
    for function in functions
)

# The one pre-existing gate. `test_integration_realdata.py` carries a module-level
# `pytestmark = pytest.mark.skipif(...)` keyed on NEXUS_RUN_INTEGRATION and the presence of
# the real dataset. That gate predates this feature and is exactly what Requirement 15.14
# asks for, so it is tolerated here rather than flagged. Every other module must be ungated,
# and no *function* in any module may carry a skip or xfail.
TOLERATED_MODULE_GATES: dict[str, str] = {
    "test_integration_realdata.py": "NEXUS_RUN_INTEGRATION",
}

# Modules this feature adds, as package-relative paths (design § Module layout).
NEW_FEATURE_MODULES: tuple[str, ...] = (
    "nexus/scope.py",
    "nexus/tools/eda_profile.py",
    "nexus/tools/feature_builder.py",
    "nexus/screener.py",
    "nexus/trace.py",
    "nexus/findings.py",
    "nexus/charts.py",
)

# Pinned pre-feature top-level module inventory. The repository root holds no Python file and
# neither does `backend/` itself; the package lives at `backend/nexus/` (structure rules).
PRE_FEATURE_ROOT_MODULES: frozenset[str] = frozenset()
PRE_FEATURE_BACKEND_MODULES: frozenset[str] = frozenset()

_SKIP_TOKENS = ("skip", "skipif", "xfail")


@lru_cache(maxsize=None)
def _parse(path: Path) -> ast.Module:
    """Parse a test module without importing it."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@lru_cache(maxsize=None)
def _top_level_test_functions(module: str) -> dict[str, tuple[str, ...]]:
    """Map top-level `test*` function name -> its decorator source strings."""
    tree = _parse(TESTS_DIR / module)
    return {
        node.name: tuple(ast.unparse(d) for d in node.decorator_list)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    }


@lru_cache(maxsize=None)
def _module_level_marks(module: str) -> tuple[str, ...]:
    """Source of every `pytestmark = ...` assignment at module level."""
    tree = _parse(TESTS_DIR / module)
    marks: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            marks.append(ast.unparse(node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "pytestmark"
            and node.value is not None
        ):
            marks.append(ast.unparse(node.value))
    return tuple(marks)


def _skip_markers(sources: tuple[str, ...]) -> list[str]:
    """Return the sources that name a skip or xfail mark."""
    hits = []
    for src in sources:
        lowered = src.lower()
        if any(f"mark.{token}" in lowered for token in _SKIP_TOKENS):
            hits.append(src)
    return hits


def _repo_python_files() -> tuple[Path, ...]:
    """Every Python file in the repository, excluding vendored and generated trees."""
    found: list[Path] = []
    stack = [REPO_ROOT]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir()):
            if child.name in EXCLUDED_DIRS:
                continue
            if child.is_dir() and not child.is_symlink():
                stack.append(child)
            elif child.suffix == ".py":
                found.append(child)
    return tuple(found)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# --------------------------------------------------------------------------------------- #
# Half 1 - the pre-feature inventory is preserved.
# A fixed inventory is honest as a parameterised example set, not as a synthetic generator:
# there is nothing to sample, the 47 names either are all still there or the suite regressed.
# --------------------------------------------------------------------------------------- #


# Feature: agent-capability-completion, Property 25: The pre-feature suite is preserved and the new modules stay in place
@pytest.mark.parametrize(("module", "function"), INVENTORY_PAIRS, ids=lambda v: v)
def test_property_25_pre_feature_test_function_survives(module: str, function: str) -> None:
    """Each pinned pre-feature test still exists and is neither skipped nor xfailed."""
    path = TESTS_DIR / module
    assert path.is_file(), f"pre-feature test module {module} was deleted"

    functions = _top_level_test_functions(module)
    assert function in functions, (
        f"pre-feature test {module}::{function} is missing — Requirement 15.2 keeps every "
        f"pre-feature test function by name. Present in {module}: "
        f"{', '.join(sorted(functions)) or '(none)'}"
    )

    offending = _skip_markers(functions[function])
    assert not offending, (
        f"pre-feature test {module}::{function} gained a skip/xfail marker "
        f"({'; '.join(offending)}) — Requirement 15.3 treats that as a regression"
    )


# Feature: agent-capability-completion, Property 25: The pre-feature suite is preserved and the new modules stay in place
@pytest.mark.parametrize("module", sorted(PRE_FEATURE_INVENTORY), ids=lambda v: v)
def test_property_25_module_gate_is_unchanged(module: str) -> None:
    """No pre-feature module gained a module-level skip beyond the one gate already there."""
    marks = _module_level_marks(module)
    offending = _skip_markers(marks)
    tolerated = TOLERATED_MODULE_GATES.get(module)

    if tolerated is None:
        assert not offending, (
            f"{module} gained a module-level skip/xfail ({'; '.join(offending)}); only "
            f"{sorted(TOLERATED_MODULE_GATES)} carried a gate before this feature"
        )
        return

    # The gate is pre-existing, so it must still be exactly the env-var gate it was: a
    # skipif keyed on NEXUS_RUN_INTEGRATION, not a blanket skip.
    assert offending, f"{module} lost its pre-existing {tolerated} gate"
    for src in offending:
        assert "skipif" in src.lower(), (
            f"{module}'s gate became an unconditional skip ({src}); it must stay a "
            f"skipif keyed on {tolerated}"
        )
        assert tolerated in src, (
            f"{module}'s module-level gate no longer references {tolerated}: {src}"
        )


# Feature: agent-capability-completion, Property 25: The pre-feature suite is preserved and the new modules stay in place
@pytest.mark.parametrize("module", sorted(PRE_FEATURE_INVENTORY), ids=lambda v: v)
def test_property_25_pre_feature_module_count_is_pinned(module: str) -> None:
    """A pre-feature module keeps at least its pinned function count, pinned names included."""
    present = set(_top_level_test_functions(module))
    pinned = set(PRE_FEATURE_INVENTORY[module])
    missing = sorted(pinned - present)
    assert not missing, f"{module} lost {len(missing)} pinned test(s): {', '.join(missing)}"
    assert len(present) >= len(pinned), (
        f"{module} reports {len(present)} test functions, fewer than the pinned {len(pinned)}"
    )


def test_pre_feature_inventory_total_is_the_measured_floor() -> None:
    """The ten pinned modules together still hold at least the measured 47 test functions."""
    total = sum(len(_top_level_test_functions(m)) for m in PRE_FEATURE_INVENTORY)
    assert total >= PRE_FEATURE_TOTAL, (
        f"the ten pre-feature modules now hold {total} test functions, below the pinned "
        f"pre-feature floor of {PRE_FEATURE_TOTAL}"
    )


# --------------------------------------------------------------------------------------- #
# Half 2 - the new modules stay in place.
# Written to pass today, when none of the seven exist, and to tighten as each lands.
# --------------------------------------------------------------------------------------- #


# Feature: agent-capability-completion, Property 25: The pre-feature suite is preserved and the new modules stay in place
@pytest.mark.parametrize("relative", NEW_FEATURE_MODULES, ids=lambda v: v)
def test_property_25_new_feature_module_lives_under_backend_nexus(relative: str) -> None:
    """If a module with this feature's module name exists, it is under `backend/nexus/`.

    Vacuously true before the module lands, an exact placement assertion afterwards.
    """
    expected = BACKEND / relative
    basename = Path(relative).name

    matches = [p for p in _repo_python_files() if p.name == basename and TESTS_DIR not in p.parents]
    for match in matches:
        assert PACKAGE in match.parents, (
            f"{_rel(match)} is a module this feature adds but it does not live under "
            f"backend/nexus/ (Requirement 15.15); expected {_rel(expected)}"
        )

    if expected.is_file():
        assert PACKAGE in expected.parents
        assert any(p == expected for p in matches)


def test_no_new_top_level_module_appeared() -> None:
    """The repository root and `backend/` itself hold exactly the Python files they did."""
    root_modules = {p.name for p in REPO_ROOT.glob("*.py")}
    backend_modules = {p.name for p in BACKEND.glob("*.py")}
    assert root_modules == PRE_FEATURE_ROOT_MODULES, (
        f"new top-level module(s) at the repository root: "
        f"{', '.join(sorted(root_modules - PRE_FEATURE_ROOT_MODULES))} — the Python package "
        f"lives under backend/nexus/"
    )
    assert backend_modules == PRE_FEATURE_BACKEND_MODULES, (
        f"new module(s) directly under backend/: "
        f"{', '.join(sorted(backend_modules - PRE_FEATURE_BACKEND_MODULES))}"
    )


# Feature: agent-capability-completion, Property 25: The pre-feature suite is preserved and the new modules stay in place
@settings(max_examples=100, deadline=None)
@given(index=st.integers(min_value=0))
def test_property_25_every_repo_module_lives_in_an_allowed_tree(index: int) -> None:
    """For all Python files in the repository, the file sits in an allowed tree.

    Real input variation: the file set is discovered at run time and grows as this feature
    lands, so the quantifier ranges over whatever is actually on disk rather than over a
    pinned list. Indices are drawn freely and folded onto the discovered set so the
    generator stays valid as the set changes size.
    """
    files = _repo_python_files()
    assert files, "found no Python files in the repository — the walk is broken"

    path = files[index % len(files)]
    relative = _rel(path)
    assert any(
        relative.startswith(f"{root}/") for root in ALLOWED_PY_ROOTS
    ), f"{relative} lives outside {', '.join(ALLOWED_PY_ROOTS)}"


# Feature: agent-capability-completion, Property 25: The pre-feature suite is preserved and the new modules stay in place
@settings(max_examples=100, deadline=None)
@given(
    name=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=24).filter(
        lambda s: s.isidentifier()
    )
)
def test_property_25_no_module_name_resolves_outside_the_package(name: str) -> None:
    """For all module names, a product module with that name is under `backend/nexus/`.

    Real input variation: the name space is unbounded, so this covers modules this feature
    has not been designed to add as well as the seven it has. Test modules and the throwaway
    `backend/scripts/` tree are excluded — neither is a product module.
    """
    filename = f"{name}.py"
    for path in _repo_python_files():
        if path.name != filename:
            continue
        relative = _rel(path)
        if relative.startswith("backend/tests/") or relative.startswith("backend/scripts/"):
            continue
        assert PACKAGE in path.parents, (
            f"product module {relative} does not live under backend/nexus/ "
            f"(Requirement 15.15)"
        )
