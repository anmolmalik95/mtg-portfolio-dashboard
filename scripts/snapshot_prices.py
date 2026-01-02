from __future__ import annotations

from _shared import (
    PrintingKey,
    CACHE_TTL_HOURS,
    load_collection,
    fetch_scryfall_card_cached,
    choose_unit_price_usd,
)

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS price_snapshots (
        snapshot_date TEXT NOT NULL,
        scryfall_id TEXT NOT NULL,
        set_code TEXT NOT NULL,
        collector_number TEXT NOT NULL,
        finish TEXT NOT NULL,
        name TEXT,
        rarity TEXT,
        type_line TEXT,
        usd REAL,
        fetched_at_epoch REAL,
        PRIMARY KEY (snapshot_date, scryfall_id, finish)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_scryfall_finish ON price_snapshots(scryfall_id, finish);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_date ON price_snapshots(snapshot_date);")
    conn.commit()

def upsert_snapshot(
    conn: sqlite3.Connection,
    snapshot_date: str,
    card: Dict[str, Any],
    set_code: str,
    collector_number: str,
    finish: str,
    usd: Optional[float],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO price_snapshots (
            snapshot_date, scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line, usd, fetched_at_epoch
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            snapshot_date,
            card.get("id"),
            set_code,
            collector_number,
            finish,
            card.get("name"),
            (card.get("rarity") or "unknown"),
            (card.get("type_line") or ""),
            usd,
            card.get("_fetched_at_epoch"),  # from our disk cache payload
        ),
    )

def main() -> None:
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    cache_dir = root / "data" / "cache" / "scryfall"
    db_path = root / "data" / "mtg_prices.sqlite"

    df = load_collection(csv_path)

    # Unique printings to fetch once
    unique = df[["set", "collector_number"]].drop_duplicates()

    # Open DB
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)

        snap_date = date.today().isoformat()

        # Fetch cards + snapshot per *finish present in your collection rows*
        # (If you have both foil and nonfoil versions of same printing in collection.csv, both get saved.)
        for _, row in df[["set", "collector_number", "finish"]].drop_duplicates().iterrows():
            key = PrintingKey(row["set"], row["collector_number"])
            card, _note = fetch_scryfall_card_cached(cache_dir, key, ttl_hours=CACHE_TTL_HOURS)
            usd, _price_note = choose_unit_price_usd(card, row["finish"])

            upsert_snapshot(
                conn=conn,
                snapshot_date=snap_date,
                card=card,
                set_code=key.set_code,
                collector_number=key.collector_number,
                finish=row["finish"],
                usd=usd,
            )

        conn.commit()
        print(f"✅ Snapshot saved for {snap_date}")
        print(f"DB: {db_path}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
