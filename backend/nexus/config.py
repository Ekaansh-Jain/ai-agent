"""Phase 1 configuration: paths, dataset variant, currency, thresholds.

Everything downstream reads config from here so there are no scattered constants.
FX rates are a stub for now (Phase 1 refinement 1): we only build out a real table if
profiling shows meaningful non-USD volume. Rates are "units of currency per 1 USD".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Repo root = two levels up from this file: backend/nexus/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"

# The six dataset variants shipped by AMLworld.
VARIANTS = ("HI-Small", "HI-Medium", "HI-Large", "LI-Small", "LI-Medium", "LI-Large")
DEFAULT_VARIANT = "HI-Small"

BASE_CURRENCY = "US Dollar"

# Stub FX table: units of the named currency per 1 US Dollar.
# Only used when a transaction's currency != BASE_CURRENCY. Approximate, static,
# clearly a placeholder — replaced/removed once profiling quantifies currency mix.
FX_PER_USD: dict[str, float] = {
    "US Dollar": 1.0,
    "Euro": 0.92,
    "UK Pound": 0.79,
    "Yuan": 7.2,
    "Yen": 150.0,
    "Ruble": 90.0,
    "Canadian Dollar": 1.35,
    "Brazil Real": 5.0,
    "Australian Dollar": 1.5,
    "Swiss Franc": 0.88,
    "Rupee": 83.0,
    "Mexican Peso": 17.0,
    "Saudi Riyal": 3.75,
    "Shekel": 3.7,
    "Bitcoin": 1 / 60000.0,
}

# Structuring "near-threshold" band is configurable because AMLworld has no baked-in
# statutory threshold. Not used in Phase 1; declared here so it lives in one place.
NEAR_THRESHOLD = 10_000.0
NEAR_THRESHOLD_BAND = 0.10  # within 10% below the threshold counts as "near"

# --- Broad-query funnel cost caps (Phase 6) -------------------------------------
# Measured on HI-Small (5,078,345 rows / 515,088 accounts) on a dev laptop:
#   ranking the full feature table in pandas .... 137 ms, 0 DB queries
#   investigate() per candidate, batched, d=2 ... median 211 ms, <=11 round-trips
# So 25 investigations ~= 5.3 s typical, ~15.7 s on the widest hubs. The screener cap is
# 20x the investigation cap so benign/indeterminate candidates can be skipped without
# exhausting the pool.
MAX_CANDIDATES = 500
MAX_INVESTIGATIONS = 25
MAX_ROUNDTRIPS_PER_CANDIDATE = 16
BROAD_QUERY_BUDGET_S = 30.0


@dataclass(frozen=True)
class Paths:
    """Resolved file paths for a given dataset variant."""

    variant: str
    trans: Path
    patterns: Path
    accounts: Path

    def exist(self) -> dict[str, bool]:
        return {
            "trans": self.trans.is_file(),
            "patterns": self.patterns.is_file(),
            "accounts": self.accounts.is_file(),
        }


def paths_for(variant: str = DEFAULT_VARIANT, root: Path = DATA_RAW) -> Paths:
    """Build the three file paths for a variant.

    Note the AMLworld naming: `Trans.csv` and `Patterns.txt` are capitalized, while
    `accounts.csv` is lowercase.
    """
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant {variant!r}; expected one of {VARIANTS}")
    return Paths(
        variant=variant,
        trans=root / f"{variant}_Trans.csv",
        patterns=root / f"{variant}_Patterns.txt",
        accounts=root / f"{variant}_accounts.csv",
    )


@dataclass(frozen=True)
class Settings:
    variant: str = DEFAULT_VARIANT
    base_currency: str = BASE_CURRENCY
    fx_per_usd: dict[str, float] = field(default_factory=lambda: dict(FX_PER_USD))
    near_threshold: float = NEAR_THRESHOLD
    near_threshold_band: float = NEAR_THRESHOLD_BAND
    # Broad-query funnel caps. Lower these to speed up a demo; raise for coverage.
    max_candidates: int = MAX_CANDIDATES
    max_investigations: int = MAX_INVESTIGATIONS
    max_roundtrips_per_candidate: int = MAX_ROUNDTRIPS_PER_CANDIDATE
    broad_query_budget_s: float = BROAD_QUERY_BUDGET_S

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        """Build settings with the funnel caps overridable from the environment."""
        import os

        def _int(name: str, default: int) -> int:
            raw = os.getenv(name)
            return int(raw) if raw not in (None, "") else default

        def _float(name: str, default: float) -> float:
            raw = os.getenv(name)
            return float(raw) if raw not in (None, "") else default

        base = {
            "max_candidates": _int("NEXUS_MAX_CANDIDATES", MAX_CANDIDATES),
            "max_investigations": _int("NEXUS_MAX_INVESTIGATIONS", MAX_INVESTIGATIONS),
            "max_roundtrips_per_candidate": _int(
                "NEXUS_MAX_ROUNDTRIPS", MAX_ROUNDTRIPS_PER_CANDIDATE
            ),
            "broad_query_budget_s": _float("NEXUS_BUDGET_S", BROAD_QUERY_BUDGET_S),
        }
        base.update(overrides)
        return cls(**base)
