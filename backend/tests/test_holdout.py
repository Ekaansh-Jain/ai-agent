"""Held-out boundary: the agent path may not reach the answer key.

Property 4 of the agent-capability-completion spec. Two scans, one digest:

1. The import closure of `orchestrator.run` (the agent entry point) is walked from
   `nexus/orchestrator.py` through the package's own imports, and every source line of
   every module in it is checked for the four held-out symbols. Comments and docstrings
   are stripped first, so prose about the answer key is not mistaken for a read of it.
   Exactly two occurrences are allowlisted, keyed on module + exact line content
   (design divergence D3). A third occurrence fails, naming module and symbol.
2. The three modules this feature adds are scanned with **no allowlist**, and every
   transaction column they name must be inside the twelve-column allowlist. The scan is
   parameterised over the module list so it passes while they are absent and tightens
   the moment they land.
3. `profile.py` is pinned by byte digest (Requirement 3.5).

Hermetic: reads only source files under `backend/nexus/`. No network, no `data/raw/`.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

import nexus
from nexus.schemas import Transaction

PKG_DIR = Path(nexus.__file__).resolve().parent
CLOSURE_ROOT = "nexus.orchestrator"

# The four artefacts of the held-out answer key (Requirement 3 criteria 4 and 6).
HELD_OUT_SYMBOLS = ("Patterns.txt", "parse_patterns", "GroundTruth", "is_laundering")

# Modules the closure walk must never descend into (Requirement 3 criterion 6).
# Test modules and `backend/scripts/` are outside the `nexus` package, so they are
# excluded by construction; `nexus.eval` is the evaluation harness and is named here.
EXCLUDED_PREFIXES = ("nexus.eval",)

# Requirement 3 criterion 7: the agent path must not reach these two modules at all.
FORBIDDEN_IMPORTS = ("nexus.ground_truth", "nexus.profile")

# The twelve normalized transaction columns the new tools may read
# (Requirement 2 criterion 3). `tx_id` is the row identity used to carry proof, not a
# data column, so it is listed separately.
TWELVE_COLUMNS = frozenset({
    "timestamp", "from_bank", "sender_account", "to_bank", "receiver_account",
    "amount_paid", "amount_received", "payment_currency", "receiving_currency",
    "payment_format", "amount_base", "cross_currency",
})
IDENTITY_COLUMN = "tx_id"

# Derived from the normalized model, so a new column tightens this test automatically.
TRANSACTION_COLUMNS = frozenset(Transaction.model_fields)
FORBIDDEN_COLUMNS = TRANSACTION_COLUMNS - TWELVE_COLUMNS - {IDENTITY_COLUMN}

# Documented allowlist, exactly two entries (design D3). Keyed on module + the exact
# stripped line content, so an edit to either line re-opens the failure.
ALLOWLIST: tuple[tuple[str, str], ...] = (
    # Declarative model field only. No module in the closure reads the attribute or the
    # column; `ingest.py` (which writes it) is not in the closure.
    ("nexus.schemas", "is_laundering: bool = Field("),
    # Path construction. `Paths.patterns` is consumed only by `profile.py` and the eval
    # harness, both outside the closure.
    ("nexus.config", 'patterns=root / f"{variant}_Patterns.txt",'),
)

NEW_MODULES = (
    "nexus/tools/eda_profile.py",
    "nexus/tools/feature_builder.py",
    "nexus/screener.py",
)


# --------------------------------------------------------------------------- helpers


def _module_file(module: str) -> Path | None:
    """Resolve a dotted `nexus.*` module name to its source file, or None."""
    parts = module.split(".")
    if parts[0] != "nexus":
        return None
    base = PKG_DIR.joinpath(*parts[1:])
    flat = base.with_suffix(".py")
    if flat.is_file():
        return flat
    pkg = base / "__init__.py"
    if pkg.is_file():
        return pkg
    return None


def _package_of(module: str, path: Path) -> str:
    """The package a relative import inside `module` resolves against."""
    if path.name == "__init__.py":
        return module
    return module.rsplit(".", 1)[0] if "." in module else module


def _local_imports(module: str, path: Path, source: str) -> set[str]:
    """Every `nexus.*` module imported by this source, including function-local imports."""
    package = _package_of(module, path)
    found: set[str] = set()

    def consider(candidate: str) -> None:
        if _module_file(candidate) is not None:
            found.add(candidate)

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "nexus":
                    consider(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                trimmed = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(trimmed)
            elif node.module and node.module.split(".")[0] == "nexus":
                base = ""
            else:
                continue
            target = f"{base}.{node.module}" if node.module and base else (node.module or base)
            if not target:
                continue
            consider(target)
            # `from . import anomaly, intent` — the names may themselves be modules.
            for alias in node.names:
                consider(f"{target}.{alias.name}")
    return found


def _walk_closure(root: str = CLOSURE_ROOT) -> dict[str, Path]:
    """Real import-graph walk over the package, transitively, from the agent entry point."""
    closure: dict[str, Path] = {}
    pending = [root]
    while pending:
        module = pending.pop()
        if module in closure or module.startswith(EXCLUDED_PREFIXES):
            continue
        path = _module_file(module)
        if path is None:
            continue
        closure[module] = path
        for imported in _local_imports(module, path, path.read_text(encoding="utf-8")):
            if imported not in closure and not imported.startswith(EXCLUDED_PREFIXES):
                pending.append(imported)
    return closure


def _strip_comments_and_docstrings(source: str) -> list[tuple[int, str]]:
    """Source lines with comments and docstrings removed, as (1-based lineno, text)."""
    lines = source.splitlines()

    # Comments first: blank out each COMMENT token in place.
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            lines[row - 1] = lines[row - 1][:col]

    # Docstrings second: blank every line the docstring expression spans.
    tree = ast.parse(source)
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        first = node.body[0]
        for lineno in range(first.lineno, (first.end_lineno or first.lineno) + 1):
            lines[lineno - 1] = ""

    return [(i + 1, text) for i, text in enumerate(lines) if text.strip()]


def _occurrences(module: str, path: Path, symbol: str) -> list[tuple[int, str]]:
    """Every stripped source line of `module` that names `symbol`."""
    return [
        (lineno, text.strip())
        for lineno, text in _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
        if symbol in text
    ]


def _allowlisted(module: str, line: str) -> bool:
    return (module, line) in ALLOWLIST


# Computed once at collection: the scan is pure source reading and never varies.
CLOSURE = _walk_closure()
CLOSURE_TARGETS = sorted(
    (module, symbol) for module in CLOSURE for symbol in HELD_OUT_SYMBOLS
)
CLOSURE_MODULES = sorted(CLOSURE)


# ------------------------------------------------------------------- Property 4 (a)
# Feature: agent-capability-completion, Property 4: Held-out artefacts are unreachable
# from the agent path


@settings(max_examples=100, deadline=None)
@given(target=st.sampled_from(CLOSURE_TARGETS))
def test_closure_holds_no_unallowlisted_holdout_reference(target: tuple[str, str]) -> None:
    """No module reachable from `orchestrator.run` reads a held-out artefact.

    **Validates: Requirements 3.1, 3.2, 3.6, 3.8**
    """
    module, symbol = target
    offending = [
        (lineno, line)
        for lineno, line in _occurrences(module, CLOSURE[module], symbol)
        if not _allowlisted(module, line)
    ]
    assert not offending, (
        f"held-out symbol {symbol!r} reachable from {CLOSURE_ROOT} in module {module} at "
        + ", ".join(f"line {lineno}: {line!r}" for lineno, line in offending)
    )


@settings(max_examples=100, deadline=None)
@given(module=st.sampled_from(CLOSURE_MODULES))
def test_closure_imports_no_ground_truth_module(module: str) -> None:
    """The agent path imports neither `nexus.ground_truth` nor `nexus.profile`.

    **Validates: Requirements 3.7, 3.8**
    """
    path = CLOSURE[module]
    imported = _local_imports(module, path, path.read_text(encoding="utf-8"))
    leaked = sorted(imported & set(FORBIDDEN_IMPORTS))
    assert not leaked, f"module {module} imports held-out module(s) {leaked}"


def test_closure_allowlist_has_exactly_the_two_documented_entries() -> None:
    """The allowlist is exhaustive: exactly two occurrences exist, and both are the
    documented ones (design D3). A third occurrence anywhere fails here too.

    **Validates: Requirements 3.6, 3.8**
    """
    assert CLOSURE_ROOT in CLOSURE
    found = sorted(
        (module, symbol, lineno, line)
        for module in CLOSURE
        for symbol in HELD_OUT_SYMBOLS
        for lineno, line in _occurrences(module, CLOSURE[module], symbol)
    )
    assert [(m, line) for m, _, _, line in found] == sorted(ALLOWLIST), (
        "closure held-out occurrences differ from the two documented allowlist entries: "
        + ", ".join(f"{m}:{lineno} {symbol!r} -> {line!r}" for m, symbol, lineno, line in found)
    )


# ------------------------------------------------------------------- Property 4 (b)


@pytest.mark.parametrize("relative", NEW_MODULES)
def test_new_module_holds_no_holdout_reference_and_only_allowed_columns(relative: str) -> None:
    """The three new modules are scanned with no allowlist, and every transaction column
    they name is inside the twelve-column allowlist.

    Skipped while a module is absent; tightens automatically once it lands.

    **Validates: Requirements 2.3, 3.1, 3.2, 3.3, 3.4, 3.8**
    """
    path = PKG_DIR.parent / relative
    if not path.is_file():
        pytest.skip(f"{relative} not implemented yet")

    stripped = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))

    holdout_hits = [
        (symbol, lineno, text.strip())
        for lineno, text in stripped
        for symbol in HELD_OUT_SYMBOLS
        if symbol in text
    ]
    assert not holdout_hits, (
        f"held-out symbol in {relative} (no allowlist applies): "
        + ", ".join(f"line {lineno}: {symbol!r} in {line!r}" for symbol, lineno, line in holdout_hits)
    )

    column_hits = [
        (column, lineno, text.strip())
        for lineno, text in stripped
        for column in sorted(FORBIDDEN_COLUMNS)
        if column in text
    ]
    assert not column_hits, (
        f"{relative} references a transaction column outside the twelve-column allowlist: "
        + ", ".join(f"line {lineno}: {column!r} in {line!r}" for column, lineno, line in column_hits)
    )


# ----------------------------------------------------------------- Requirement 3.5


PROFILE_SHA256 = "faf103474c782e1d054ae043bc9b87f4545354fff70fbadfde65e724f72212fe"
PROFILE_BYTES = 3793


def test_profile_module_is_byte_identical() -> None:
    """`profile.py` is the offline reporting script and stays byte-identical.

    **Validates: Requirements 3.5**
    """
    raw = (PKG_DIR / "profile.py").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert (len(raw), digest) == (PROFILE_BYTES, PROFILE_SHA256), (
        "nexus/profile.py changed: expected "
        f"{PROFILE_BYTES} bytes / sha256 {PROFILE_SHA256}, observed {len(raw)} bytes / "
        f"sha256 {digest}"
    )
