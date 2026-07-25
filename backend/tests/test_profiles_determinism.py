"""Regression guard: rebuilding profiles + the peer model must reproduce the same numbers.

The defect this pins: `_PROFILE_SQL` is a `FULL OUTER JOIN` with no `ORDER BY`, so DuckDB
returned the same 515,088 HI-Small accounts in a different ROW ORDER on every call. That
order reaches `PeerModel.__init__` -> `MiniBatchKMeans`, which is order-sensitive even with a
fixed `random_state`. Shifted clusters shift the per-cluster median/MAD, which shifts the
`peer_deviation` z-score (weight 0.20), which moved the real-data anchor between rebuilds
(observed spread on `0048309|811C599A0`: 52.08 / 52.86 / 53.18).

Every existing determinism test reused ONE `PeerModel` inside one process, so none of them
could see it. These tests REBUILD, which is what a server restart does.

Hermetic: fixture CSVs plus a synthetic population written to `tmp_path`; nothing from
`data/raw/`. The gated counterpart on real HI-Small lives in `test_integration_realdata.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nexus.casebuilder import investigate
from nexus.config import Settings
from nexus.ingest import load_transactions
from nexus.peers import DEFAULT_K, PeerModel
from nexus.profiles import _PROFILE_SQL, build_profiles

FIXTURES = Path(__file__).parent / "fixtures"
HUB = "0500|C1"
FIXTURE_K = 3


class _Result:
    """Minimal stand-in for a DuckDB result object: only `.df()` is used here."""

    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def df(self) -> pd.DataFrame:
        return self._frame


class _ReorderedStore:
    """A connection whose profile query hands the same rows back in a different order.

    This is exactly what DuckDB's unordered `FULL OUTER JOIN` did between calls; simulating
    it makes the defect reproducible without loading a 5M-row dataset.
    """

    def __init__(self, con, seed: int):
        self._con = con
        self._rng = np.random.default_rng(seed)

    def execute(self, sql: str, *args, **kwargs) -> _Result:
        frame = self._con.execute(sql, *args, **kwargs).df()
        order = self._rng.permutation(len(frame))
        return _Result(frame.iloc[order].reset_index(drop=True))


def _fixture_con(csv_name: str):
    con, _, _ = load_transactions(FIXTURES / csv_name, Settings())
    return con


def _store_order(con) -> list[str]:
    """Row order the store itself returns, before `build_profiles` imposes one."""
    raw = con.execute(_PROFILE_SQL).df()
    return list(raw["bank"] + "|" + raw["acct"])


def _dev_tuple(peers: PeerModel, node: str) -> tuple:
    d = peers.deviation(node)
    return (round(d.z, 12), d.direction, d.feature, d.peer_set, d.peer_count)


def _synthetic_population(tmp_path: Path) -> Path:
    """400 accounts with spread-out fan-out/volume, so clusters exceed MIN_CLUSTER_SIZE.

    Needed because the tiny ring/case fixtures cluster below `MIN_CLUSTER_SIZE`, fall back to
    global stats and therefore cannot expose cluster drift at all.
    """
    rows = []
    n = 400
    for i in range(n):
        for j in range(1 + i % 9):
            k = (i * 11 + j * 7) % n
            amount = 500 + (i * 37 + j * 91) % 9000
            rows.append(
                {
                    "Timestamp": f"2022/09/{1 + (i + j) % 28:02d} {(i + j) % 24:02d}:15",
                    "From Bank": f"{100 + i % 50:04d}", "Account": f"A{i:04d}",
                    "To Bank": f"{100 + k % 50:04d}", "Account.1": f"A{k:04d}",
                    "Amount Received": amount, "Receiving Currency": "US Dollar",
                    "Amount Paid": amount, "Payment Currency": "US Dollar",
                    "Payment Format": "Cheque",
                }
            )
    path = tmp_path / "synthetic_Trans.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# 1. build_profiles imposes a deterministic row order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("csv_name", ["ring_Trans.csv", "case_Trans.csv"])
def test_build_profiles_row_order_is_identical_across_calls(csv_name: str):
    """Two calls on one connection agree on ORDER, not merely on the set of accounts."""
    con = _fixture_con(csv_name)
    first, second = build_profiles(con), build_profiles(con)

    assert set(first.index) == set(second.index)
    assert list(first.index) == list(second.index), (
        "build_profiles returned the same accounts in a different row order; that order "
        "feeds MiniBatchKMeans and moves peer_deviation"
    )
    pd.testing.assert_frame_equal(first, second)


@pytest.mark.parametrize("csv_name", ["ring_Trans.csv", "case_Trans.csv"])
def test_build_profiles_row_order_is_sorted_by_node(csv_name: str):
    """The imposed order is the total order on the unique node id."""
    con = _fixture_con(csv_name)
    index = list(build_profiles(con).index)

    assert index == sorted(index)
    assert len(set(index)) == len(index), "node ids must be unique for the sort to be total"
    # Non-vacuous: the store's own order is NOT sorted, so the sort is doing real work.
    assert _store_order(con) != sorted(index)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_build_profiles_row_order_survives_a_reordered_store(seed: int):
    """A store that permutes its rows cannot change the profile table."""
    con = _fixture_con("case_Trans.csv")
    baseline = build_profiles(con)
    reordered = build_profiles(_ReorderedStore(con, seed))

    assert list(reordered.index) == list(baseline.index)
    pd.testing.assert_frame_equal(reordered, baseline)


# ---------------------------------------------------------------------------
# 2. Rebuilding profiles + PeerModel reproduces the same evidence and risk
# ---------------------------------------------------------------------------


def test_rebuilt_peer_model_gives_identical_deviation_on_fixture():
    """Two INDEPENDENTLY constructed PeerModels agree on z, direction and peer_set."""
    con = _fixture_con("ring_Trans.csv")
    a = PeerModel(build_profiles(con), k=FIXTURE_K)
    b = PeerModel(build_profiles(con), k=FIXTURE_K)

    assert _dev_tuple(a, HUB) == _dev_tuple(b, HUB)
    assert list(a.profiles.index) == list(b.profiles.index)


@pytest.mark.parametrize("csv_name", ["ring_Trans.csv", "case_Trans.csv"])
def test_rebuilt_profiles_and_peer_model_give_identical_investigate_risk(csv_name: str):
    """The whole path — rebuild profiles, rebuild the peer model, investigate — is stable."""
    con = _fixture_con(csv_name)
    cases = []
    for _ in range(3):
        peers = PeerModel(build_profiles(con), k=FIXTURE_K)
        cases.append(investigate(con, peers, HUB))

    first = cases[0]
    for other in cases[1:]:
        assert other.risk == first.risk
        assert (other.tier, other.escalation) == (first.tier, first.escalation)
        assert other.winning_hypothesis == first.winning_hypothesis
        assert other.winning_kind == first.winning_kind


def test_reordered_store_cannot_move_the_deviation_on_a_clustered_population(tmp_path):
    """The real failure mode, hermetically: a population whose clusters exceed the floor.

    Pre-fix, the permuted store order reached MiniBatchKMeans and produced a different
    cluster (and so a different median/MAD and z) for the same account.
    """
    con, _, _ = load_transactions(_synthetic_population(tmp_path), Settings())
    baseline = PeerModel(build_profiles(con), k=DEFAULT_K)
    node = "0103|A0053"
    assert baseline.deviation(node).peer_set.startswith("cluster"), (
        "premise: this account must be scored against its cluster, not the global fallback"
    )

    for seed in range(4):
        rebuilt = PeerModel(build_profiles(_ReorderedStore(con, seed)), k=DEFAULT_K)
        assert _dev_tuple(rebuilt, node) == _dev_tuple(baseline, node), (
            f"store row order (seed {seed}) moved the peer deviation for {node}"
        )


def test_minibatchkmeans_is_order_sensitive_which_is_why_the_order_is_pinned(tmp_path):
    """Documents the root cause: same rows, different order, different clusters.

    If this ever stops holding, the sort in `build_profiles` becomes belt-and-braces rather
    than load-bearing — but with `scikit-learn` pinned it holds today.
    """
    con, _, _ = load_transactions(_synthetic_population(tmp_path), Settings())
    profiles = build_profiles(con)
    node = "0103|A0053"

    seen = set()
    for seed in range(4):
        order = np.random.default_rng(seed).permutation(len(profiles))
        permuted = PeerModel(profiles.iloc[order], k=DEFAULT_K)
        seen.add(_dev_tuple(permuted, node))

    assert len(seen) > 1, (
        "expected MiniBatchKMeans to be row-order sensitive; the defect being guarded "
        "against depends on it"
    )
