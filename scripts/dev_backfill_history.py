from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
import random


def latest_snapshot_date(conn: sqlite3.Connection) -> str:
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


def read_snapshot_rows(conn: sqlite3.Connection, snap_date: str) -> list[tuple]:
    """
    Returns rows from a snapshot that we can re-insert with a new date.
    """
    cur = conn.execute(
        """
        SELECT
            scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line,
            usd, fetched_at_epoch
        FROM price_snapshots
        WHERE snapshot_date = ?;
        """,
        (snap_date,),
    )
    return cur.fetchall()


def insert_snapshot_rows(conn: sqlite3.Connection, snap_date: str, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO price_snapshots (
            snapshot_date,
            scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line,
            usd, fetched_at_epoch
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        [(snap_date, *r) for r in rows],
    )


def apply_random_walk(rows: list[tuple], rng: random.Random, daily_vol: float) -> list[tuple]:
    """
    rows tuple structure:
      (scryfall_id, set_code, collector_number, finish, name, rarity, type_line, usd, fetched_at_epoch)

    daily_vol ~ 0.02 means roughly +/-2% typical day move (not strict).
    """
    new_rows = []
    for r in rows:
        usd = r[7]
        if usd is None:
            new_rows.append(r)
            continue

        # multiplicative move: price *= (1 + noise)
        noise = rng.normalvariate(0.0, daily_vol)
        new_usd = max(0.01, float(usd) * (1.0 + noise))

        # keep to 2 decimals like market prices
        new_usd = round(new_usd, 2)

        new_rows.append((*r[:7], new_usd, r[8]))
    return new_rows


def main(days_back: int = 90, daily_vol: float = 0.02, seed: int = 42) -> None:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "mtg_prices.sqlite"

    rng = random.Random(seed)

    conn = sqlite3.connect(db_path)
    try:
        latest = latest_snapshot_date(conn)
        latest_dt = date.fromisoformat(latest)

        base_rows = read_snapshot_rows(conn, latest)
        if not base_rows:
            raise RuntimeError("Latest snapshot has no rows.")

        created = 0

        # create snapshots for latest-1 ... latest-days_back
        current_rows = base_rows
        for i in range(1, days_back + 1):
            d = (latest_dt - timedelta(days=i)).isoformat()

            if snapshot_exists(conn, d):
                continue

            # evolve prices one day at a time
            current_rows = apply_random_walk(current_rows, rng, daily_vol=daily_vol)
            insert_snapshot_rows(conn, d, current_rows)
            created += 1

        conn.commit()
        print(f"✅ Backfilled {created} snapshot day(s) (up to {days_back} days back).")
        print(f"DB: {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    # tweak here if you want
    main(days_back=90, daily_vol=0.02, seed=42)
