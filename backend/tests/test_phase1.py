"""Phase 1 verification against the synthetic AMLworld-format fixtures."""

from __future__ import annotations

from pathlib import Path

from nexus.config import Settings, paths_for
from nexus.ground_truth import GroundTruth, parse_patterns
from nexus.ingest import load_dataset, load_transactions

FIXTURES = Path(__file__).parent / "fixtures"


def _settings() -> Settings:
    return Settings(variant="HI-Small")


def test_transactions_load_and_normalize():
    ds = load_dataset(_settings(), root=FIXTURES)
    assert ds.n_transactions == 10
    assert ds.has_label_column is True

    row = ds.con.execute(
        "SELECT from_bank, sender_account, amount_paid, amount_base, "
        "payment_currency, cross_currency, is_laundering "
        "FROM transactions WHERE tx_id = 0"
    ).fetchone()
    from_bank, sender, amt_paid, amt_base, ccy, cross, laundering = row
    # Leading zeros preserved as strings.
    assert from_bank == "00952"
    assert sender == "8139F54E0"
    assert ccy == "US Dollar" and amt_base == amt_paid  # USD -> base unchanged
    assert cross is False
    assert laundering is True


def test_fx_normalization_non_usd():
    ds = load_dataset(_settings(), root=FIXTURES)
    # Euro 3864 paid at 0.92/USD -> 4200 USD base.
    base = ds.con.execute(
        "SELECT amount_base FROM transactions WHERE payment_currency = 'US Dollar' "
        "AND amount_paid = 3864.0"
    ).fetchone()
    assert base is not None
    assert abs(base[0] - 3864.0) < 1e-6  # this row is already USD

    # The Euro-received / USD-paid row exercises cross_currency.
    cross = ds.con.execute(
        "SELECT COUNT(*) FROM transactions WHERE cross_currency"
    ).fetchone()[0]
    assert cross == 1  # only the Euro->USD cheque row differs in/out currency


def test_string_bank_codes_not_collapsed():
    ds = load_dataset(_settings(), root=FIXTURES)
    # '001' and '01' would collide if parsed as ints; ensure short codes survive.
    banks = {r[0] for r in ds.con.execute(
        "SELECT DISTINCT from_bank FROM transactions"
    ).fetchall()}
    assert "026" in banks and "029" in banks and "001" in banks


def test_patterns_parse_and_link():
    ds = load_dataset(_settings(), root=FIXTURES)
    instances = parse_patterns(paths_for("HI-Small", root=FIXTURES).patterns)
    assert {i.typology for i in instances} == {"STACK", "FAN-IN"}

    fan_in = next(i for i in instances if i.typology == "FAN-IN")
    assert fan_in.description == "Max 9-degree Fan-In"
    assert fan_in.size == 2

    gt = GroundTruth(instances)
    rate = gt.link(ds.key_to_tx_id)
    assert rate == 1.0  # every pattern row matches a transaction
    assert gt.counts_by_typology() == {"STACK": 1, "FAN-IN": 1}

    # Sanity: pattern rows vs label positives.
    label_pos = ds.con.execute(
        "SELECT COUNT(*) FROM transactions WHERE is_laundering"
    ).fetchone()[0]
    assert label_pos == gt.laundering_row_count() == 4


def test_accounts_entity_map():
    ds = load_dataset(_settings(), root=FIXTURES)
    # E001 owns two accounts across two banks.
    assert ds.account_to_entity[("00952", "8139F54E0")] == "E001"
    assert len(ds.entity_to_accounts["E001"]) == 2
