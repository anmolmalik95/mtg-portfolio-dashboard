from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "mtg_prices.sqlite"

    conn = sqlite3.connect(db_path)
    try:
        latest = conn.execute("SELECT MAX(snapshot_date) FROM price_snapshots;").fetchone()[0]
        if not latest:
            raise RuntimeError("No snapshots found.")

        df = pd.read_sql_query(
            """
            SELECT snapshot_date, name, finish, usd, scryfall_id, set_code, collector_number
            FROM price_snapshots
            WHERE snapshot_date = ?
            ORDER BY usd DESC;
            """,
            conn,
            params=(latest,),
        )

        print(f"\nLatest snapshot: {latest}\n")
        print(df.to_string(index=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
