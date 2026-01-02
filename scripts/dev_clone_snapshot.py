from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path


def get_latest_snapshot_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(snapshot_date) FROM price_snapshots;").fetchone()
    if not row or not row[0]:
        raise RuntimeError("No snapshots found. Run scripts/snapshot_prices.py first.")
    return row[0]


def snapshot_exists(conn: sqlite3.Connection, snap_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM price_snapshots WHERE snapshot_date = ? LIMIT 1;",
        (snap_date,),
    ).fetchone()
    return row is not None


def clone_snapshot(conn: sqlite3.Connection, src_date: str, dst_date: str) -> int:
    """
    Copies all rows from src_date to dst_date using INSERT OR REPLACE.
    Returns number of rows copied.
    """
    cur = conn.execute(
        """
        INSERT OR REPLACE INTO price_snapshots (
            snapshot_date, scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line, usd, fetched_at_epoch
        )
        SELECT
            ?, scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line, usd, fetched_at_epoch
        FROM price_snapshots
        WHERE snapshot_date = ?;
        """,
        (dst_date, src_date),
    )
    return cur.rowcount


def apply_override(conn: sqlite3.Connection, snap_date: str, scryfall_id: str, finish: str, new_usd: float) -> None:
    conn.execute(
        """
        UPDATE price_snapshots
        SET usd = ?
        WHERE snapshot_date = ? AND scryfall_id = ? AND finish = ?;
        """,
        (new_usd, snap_date, scryfall_id, finish),
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "mtg_prices.sqlite"

    conn = sqlite3.connect(db_path)
    try:
        latest = get_latest_snapshot_date(conn)
        yesterday = (date.fromisoformat(latest) - timedelta(days=1)).isoformat()

        dst = yesterday  # default target
        if snapshot_exists(conn, dst):
            print(f"ℹ️ Snapshot for {dst} already exists. Skipping clone and proceeding to optional override.")
        else:
            copied = clone_snapshot(conn, latest, dst)
            conn.commit()
            print(f"✅ Cloned snapshot {latest} -> {dst} ({copied} rows)")


        copied = clone_snapshot(conn, latest, dst)
        conn.commit()

        print(f"✅ Cloned snapshot {latest} -> {dst} ({copied} rows)")

        # OPTIONAL: quick interactive override so you can force movers to show something today
        print("\nOptional: simulate a price move.")
        ans = input("Do you want to override 1 price in the baseline snapshot? [y/N]: ").strip().lower()
        if ans == "y":
            print("\nTip: get scryfall_id+finish from latest snapshot via validate script output "
                  "or by querying DB later. We'll keep it simple:")
            scryfall_id = input("scryfall_id: ").strip()
            finish = input("finish (foil/nonfoil): ").strip().lower()
            new_usd = float(input("new baseline usd price (e.g. 1.23): ").strip())

            apply_override(conn, dst, scryfall_id, finish, new_usd)
            conn.commit()
            print("✅ Override applied.")
        else:
            print("Skipped overrides.")

        print(f"\nDB: {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
