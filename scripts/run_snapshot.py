from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text

# import your existing snapshot routine
from scripts.snapshot_prices import run_snapshot  # adjust if your function name differs


def _get_engine():
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "mtg_prices.sqlite"
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return create_engine(db_url, pool_pre_ping=True)
    return create_engine(f"sqlite:///{db_path.as_posix()}", pool_pre_ping=True)


def ensure_today_snapshot():
    """
    If a snapshot row exists for today, do nothing.
    Otherwise run snapshot job once.
    """
    engine = _get_engine()

    today = date.today().isoformat()
    with engine.begin() as conn:
        # ensure table exists (same schema)
        conn.execute(
            text(
                """
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
            )
        )

        exists = conn.execute(
            text("select 1 from price_snapshots where snapshot_date = :d limit 1;"),
            {"d": today},
        ).fetchone()

    if exists:
        return False  # already have today

    # Run your existing snapshot job, but it must write using DATABASE_URL / engine
    # If run_snapshot currently writes to sqlite via path, we will adjust next step.
    run_snapshot(engine=engine, snapshot_date=today)
    return True
