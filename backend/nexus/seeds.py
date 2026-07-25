"""Seeded demo constructs — injected into a transaction store so the money shot can be
shown on the real dataset, not just fixtures.

Three constructs (all clearly labeled as seeded — honest):
  - a smurfing ring: 6 mule feeders -> collector C1 -> beneficiary B1 (rapid pass-through)
  - a salary payer E1 connected to C1 but benign (high out-degree) -> excluded on expansion
  - a benign-lookalike merchant M1: many payers, but retains funds -> downgraded to monitor

Seeded rows use synthetic tx_ids continuing after the store's current max.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from .config import Settings

SEED_C1 = "0500|C1"
SEED_M1 = "0900|M1"
SEED_E1 = "0801|E1"

# (timestamp, from_bank, sender, to_bank, receiver, amount, currency, format, laundering)
_ROWS = [
    ("2022/08/03 09:15", "0501", "A1", "0500", "C1", 9200, "Cash", 1),
    ("2022/08/03 10:40", "0502", "A2", "0500", "C1", 9500, "Cash", 1),
    ("2022/08/03 11:05", "0503", "A3", "0500", "C1", 9100, "Cash", 1),
    ("2022/08/04 09:30", "0504", "A4", "0500", "C1", 9800, "Cash", 1),
    ("2022/08/04 10:10", "0505", "A5", "0500", "C1", 9300, "Cash", 1),
    ("2022/08/04 12:00", "0506", "A6", "0500", "C1", 9600, "Cash", 1),
    ("2022/08/04 15:00", "0500", "C1", "0600", "B1", 51400, "Wire", 1),
    ("2022/08/20 09:00", "0801", "E1", "0500", "C1", 3200, "ACH", 0),
    ("2022/08/20 09:01", "0801", "E1", "0802", "EMP1", 2500, "ACH", 0),
    ("2022/08/20 09:02", "0801", "E1", "0803", "EMP2", 2600, "ACH", 0),
    ("2022/08/20 09:03", "0801", "E1", "0804", "EMP3", 2400, "ACH", 0),
    ("2022/08/20 09:04", "0801", "E1", "0805", "EMP4", 2700, "ACH", 0),
    ("2022/07/04 10:00", "0901", "P1", "0900", "M1", 450, "Credit Card", 0),
    ("2022/07/08 11:00", "0902", "P2", "0900", "M1", 1200, "Credit Card", 0),
    ("2022/07/12 12:30", "0903", "P3", "0900", "M1", 780, "ACH", 0),
    ("2022/07/19 09:45", "0904", "P4", "0900", "M1", 1500, "Credit Card", 0),
    ("2022/07/26 14:20", "0905", "P5", "0900", "M1", 320, "Credit Card", 0),
    ("2022/08/02 16:10", "0906", "P6", "0900", "M1", 910, "ACH", 0),
    ("2022/08/10 10:05", "0907", "P7", "0900", "M1", 640, "Credit Card", 0),
    ("2022/08/18 13:40", "0908", "P8", "0900", "M1", 1100, "Credit Card", 0),
    ("2022/09/15 09:00", "0900", "M1", "0910", "SUP1", 5000, "Wire", 0),
]


def seed_demo_constructs(
    con: duckdb.DuckDBPyConnection, settings: Settings | None = None
) -> dict[str, str]:
    """Append the demo constructs to the `transactions` table. Returns seed node ids."""
    settings = settings or Settings()
    start = con.execute("SELECT COALESCE(MAX(tx_id), -1) + 1 FROM transactions").fetchone()[0]

    df = pd.DataFrame([
        {
            "tx_id": start + i,
            "timestamp": pd.to_datetime(ts, format="%Y/%m/%d %H:%M"),
            "from_bank": fb, "sender_account": sa,
            "to_bank": tb, "receiver_account": ra,
            "amount_received": float(amt), "receiving_currency": "US Dollar",
            "amount_paid": float(amt), "payment_currency": "US Dollar",
            "payment_format": fmt, "cross_currency": False,
            "amount_base": float(amt), "is_laundering": bool(lnd),
        }
        for i, (ts, fb, sa, tb, ra, amt, fmt, lnd) in enumerate(_ROWS)
    ])

    con.register("df_seed", df)
    con.execute("INSERT INTO transactions SELECT * FROM df_seed")
    con.unregister("df_seed")
    return {"ring": SEED_C1, "merchant": SEED_M1, "salary_payer": SEED_E1}
