"""Phase 1 ingestion: Trans.csv + accounts.csv -> normalized DuckDB store.

DuckDB is the canonical store (steering rule). We read with pandas to deterministically
handle the duplicate `Account` header (pandas names the 2nd `Account.1`) and to force
string typing on bank/account codes so leading zeros survive, then load the clean frame
into DuckDB.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from .config import Settings, paths_for
from .schemas import Account

# Raw CSV column names as pandas sees them (2nd "Account" becomes "Account.1").
RAW_COLS = [
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account.1",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
]
LABEL_COL = "Is Laundering"

# Columns that must stay strings (leading zeros).
_STR_COLS = ["From Bank", "Account", "To Bank", "Account.1"]


@dataclass
class Dataset:
    """Everything Phase 1 produces, in one handle."""

    con: duckdb.DuckDBPyConnection
    variant: str
    n_transactions: int
    has_label_column: bool
    key_to_tx_id: dict[tuple, int]
    accounts: list[Account]
    account_to_entity: dict[tuple[str, str], str]
    entity_to_accounts: dict[str, set[tuple[str, str]]]


def _fx_to_base(amount: pd.Series, currency: pd.Series, settings: Settings) -> pd.Series:
    """Convert amounts to base currency. FX_PER_USD = units per 1 USD, so USD = amt / rate.

    Unknown currencies fall back to rate 1.0 (treated as already-base) and are surfaced by
    profiling rather than silently dropped.
    """
    rates = currency.map(settings.fx_per_usd).fillna(1.0)
    return (amount / rates).round(2)


def load_transactions(
    path: str | Path,
    settings: Settings,
    con: duckdb.DuckDBPyConnection | None = None,
) -> tuple[duckdb.DuckDBPyConnection, bool, dict[tuple, int]]:
    """Load and normalize transactions into a DuckDB table `transactions`.

    Returns (connection, has_label_column, key_to_tx_id).
    """
    path = Path(path)
    con = con or duckdb.connect()  # in-memory by default

    # Read everything as string first; convert amounts explicitly afterwards.
    df = pd.read_csv(path, dtype=str, keep_default_na=False)

    has_label = LABEL_COL in df.columns

    # Preserve raw timestamp string for exact pattern matching before we parse it.
    raw_ts = df["Timestamp"].str.strip()

    out = pd.DataFrame()
    out["tx_id"] = range(len(df))
    out["timestamp"] = pd.to_datetime(raw_ts, format="%Y/%m/%d %H:%M", errors="coerce")
    out["from_bank"] = df["From Bank"].str.strip()
    out["sender_account"] = df["Account"].str.strip()
    out["to_bank"] = df["To Bank"].str.strip()
    out["receiver_account"] = df["Account.1"].str.strip()
    out["amount_received"] = pd.to_numeric(df["Amount Received"], errors="coerce")
    out["receiving_currency"] = df["Receiving Currency"].str.strip()
    out["amount_paid"] = pd.to_numeric(df["Amount Paid"], errors="coerce")
    out["payment_currency"] = df["Payment Currency"].str.strip()
    out["payment_format"] = df["Payment Format"].str.strip()
    out["cross_currency"] = out["receiving_currency"] != out["payment_currency"]
    out["amount_base"] = _fx_to_base(out["amount_paid"], out["payment_currency"], settings)
    if has_label:
        out["is_laundering"] = df[LABEL_COL].str.strip().isin(["1", "1.0", "True", "true"])
    else:
        out["is_laundering"] = False

    con.register("df_norm", out)
    con.execute("DROP TABLE IF EXISTS transactions")
    con.execute("CREATE TABLE transactions AS SELECT * FROM df_norm")
    con.unregister("df_norm")

    # Build the join-key map from RAW string values so it matches Patterns rows exactly.
    key_to_tx_id: dict[tuple, int] = {}
    amt_round = out["amount_paid"].round(2)
    for tx_id, ts, fb, sa, tb, ra, amt in zip(
        out["tx_id"], raw_ts, out["from_bank"], out["sender_account"],
        out["to_bank"], out["receiver_account"], amt_round,
    ):
        key_to_tx_id[(ts, fb, sa, tb, ra, amt)] = tx_id

    return con, has_label, key_to_tx_id


def load_accounts(path: str | Path) -> tuple[
    list[Account], dict[tuple[str, str], str], dict[str, set[tuple[str, str]]]
]:
    """Load accounts.csv and build account<->entity maps. Missing file -> empty."""
    path = Path(path)
    if not path.is_file():
        return [], {}, {}

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    accounts: list[Account] = []
    a2e: dict[tuple[str, str], str] = {}
    e2a: dict[str, set[tuple[str, str]]] = {}

    for _, r in df.iterrows():
        acct = Account(
            bank_name=r.get("Bank Name", "").strip(),
            bank_id=r.get("Bank ID", "").strip(),
            account_number=r.get("Account Number", "").strip(),
            entity_id=r.get("Entity ID", "").strip(),
            entity_name=r.get("Entity Name", "").strip(),
        )
        accounts.append(acct)
        a2e[acct.node] = acct.entity_id
        e2a.setdefault(acct.entity_id, set()).add(acct.node)

    return accounts, a2e, e2a


def load_dataset(settings: Settings | None = None, root: Path | None = None) -> Dataset:
    """Load transactions + accounts for the configured variant into one Dataset handle.

    `root` overrides the data directory (used by tests to point at fixtures).
    """
    settings = settings or Settings()
    p = paths_for(settings.variant, root=root) if root else paths_for(settings.variant)
    missing = [k for k, ok in {"trans": p.trans.is_file()}.items() if not ok]
    if missing:
        raise FileNotFoundError(
            f"Missing {settings.variant} transactions at {p.trans}. "
            f"Drop the AMLworld files into {p.trans.parent}."
        )

    con, has_label, key_map = load_transactions(p.trans, settings)
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    accounts, a2e, e2a = load_accounts(p.accounts)

    return Dataset(
        con=con,
        variant=settings.variant,
        n_transactions=n,
        has_label_column=has_label,
        key_to_tx_id=key_map,
        accounts=accounts,
        account_to_entity=a2e,
        entity_to_accounts=e2a,
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description="NEXUS-AML Phase 1 ingestion.")
    parser.add_argument("--variant", default=Settings().variant, help="dataset variant")
    args = parser.parse_args()

    from .profile import print_profile  # local import to avoid cycle

    settings = Settings(variant=args.variant)
    ds = load_dataset(settings)
    print_profile(ds, settings)


if __name__ == "__main__":
    _main()
