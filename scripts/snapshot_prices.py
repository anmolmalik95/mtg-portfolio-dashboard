from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import time

from scripts._shared import (
    PrintingKey,
    CACHE_TTL_HOURS,
    load_collection,
    fetch_scryfall_card_cached,
    choose_unit_price_usd,
)


def _get_engine() -> Engine:
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "mtg_prices.sqlite"

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return create_engine(db_url, pool_pre_ping=True)

    return create_engine(f"sqlite:///{db_path.as_posix()}", pool_pre_ping=True)


def ensure_schema(engine: Engine) -> None:
    ddl = """
    create table if not exists price_snapshots (
        snapshot_date text not null,
        scryfall_id text not null,
        set_code text not null,
        collector_number text not null,
        finish text not null,
        name text,
        rarity text,
        type_line text,
        usd double precision,
        fetched_at_epoch double precision,
        primary key (snapshot_date, scryfall_id, finish)
    );
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))

        # Indexes (SQLite supports them; Postgres too)
        conn.execute(text("create index if not exists idx_snapshots_scryfall_finish on price_snapshots(scryfall_id, finish);"))
        conn.execute(text("create index if not exists idx_snapshots_date on price_snapshots(snapshot_date);"))


def upsert_snapshot(
    engine: Engine,
    snapshot_date: str,
    card: Dict[str, Any],
    set_code: str,
    collector_number: str,
    finish: str,
    usd: Optional[float],
) -> None:
    payload = {
        "snapshot_date": snapshot_date,
        "scryfall_id": card.get("id"),
        "set_code": set_code,
        "collector_number": collector_number,
        "finish": finish,
        "name": card.get("name"),
        "rarity": (card.get("rarity") or "unknown"),
        "type_line": (card.get("type_line") or ""),
        "usd": usd,
        "fetched_at_epoch": card.get("_fetched_at_epoch"),
    }

    dialect = engine.dialect.name

    if dialect == "sqlite":
        sql = """
        insert or replace into price_snapshots (
            snapshot_date, scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line, usd, fetched_at_epoch
        ) values (
            :snapshot_date, :scryfall_id, :set_code, :collector_number, :finish,
            :name, :rarity, :type_line, :usd, :fetched_at_epoch
        );
        """
    else:
        # Postgres / Supabase
        sql = """
        insert into price_snapshots (
            snapshot_date, scryfall_id, set_code, collector_number, finish,
            name, rarity, type_line, usd, fetched_at_epoch
        ) values (
            :snapshot_date, :scryfall_id, :set_code, :collector_number, :finish,
            :name, :rarity, :type_line, :usd, :fetched_at_epoch
        )
        on conflict (snapshot_date, scryfall_id, finish)
        do update set
            set_code = excluded.set_code,
            collector_number = excluded.collector_number,
            name = excluded.name,
            rarity = excluded.rarity,
            type_line = excluded.type_line,
            usd = excluded.usd,
            fetched_at_epoch = excluded.fetched_at_epoch;
        """

    with engine.begin() as conn:
        conn.execute(text(sql), payload)


def run_snapshot(engine: Engine | None = None, snapshot_date: str | None = None) -> str:
    """
    Writes snapshot rows for the given date (default: today).
    Uses DATABASE_URL engine in production, SQLite locally.
    Returns the snapshot_date written.
    """
    engine = engine or _get_engine()
    ensure_schema(engine)

    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    cache_dir = root / "data" / "cache" / "scryfall"

    df = load_collection(csv_path)

    snap_date = snapshot_date or date.today().isoformat()

    # Snapshot per *finish* present in your collection rows
    for _, row in df[["set", "collector_number", "finish"]].drop_duplicates().iterrows():
        time.sleep(0.12)

        key = PrintingKey(row["set"], row["collector_number"])
        card, _note = fetch_scryfall_card_cached(cache_dir, key, ttl_hours=CACHE_TTL_HOURS)
        usd, _price_note = choose_unit_price_usd(card, row["finish"])

        upsert_snapshot(
            engine=engine,
            snapshot_date=snap_date,
            card=card,
            set_code=key.set_code,
            collector_number=key.collector_number,
            finish=row["finish"],
            usd=usd,
        )

    print(f"✅ Snapshot saved for {snap_date}")
    return snap_date


def ensure_today_snapshot(engine: Engine | None = None) -> bool:
    """
    Creates today's snapshot if it doesn't exist.
    Returns True if a new snapshot was created, False if already present.
    """
    engine = engine or _get_engine()
    ensure_schema(engine)

    today = date.today().isoformat()
    with engine.begin() as conn:
        exists = conn.execute(
            text("select 1 from price_snapshots where snapshot_date = :d limit 1;"),
            {"d": today},
        ).fetchone()

    if exists:
        return False

    run_snapshot(engine=engine, snapshot_date=today)
    return True


def main() -> None:
    created = ensure_today_snapshot()
    if created:
        print("✅ Created today's snapshot")
    else:
        print("ℹ️ Today's snapshot already exists")


if __name__ == "__main__":
    main()
