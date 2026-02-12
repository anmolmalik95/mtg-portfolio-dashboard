import os
import sqlite3
import pandas as pd
from sqlalchemy import create_engine, text

SQLITE_PATH = "data/mtg_prices.sqlite"

CREATE_SQL = """
create table if not exists public.price_snapshots (
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

def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set. Set it in your terminal before running.")

    # Read from SQLite
    con = sqlite3.connect(SQLITE_PATH)
    df = pd.read_sql_query("select * from price_snapshots", con)
    con.close()

    print(f"SQLite rows: {len(df)}")
    if df.empty:
        print("No rows to migrate.")
        return

    # Connect to Supabase Postgres
    engine = create_engine(db_url, pool_pre_ping=True)

    # Ensure table exists (safe)
    with engine.begin() as conn:
        conn.execute(text(CREATE_SQL))

    # Load into a temporary staging table, then upsert into the real table
    stage = "price_snapshots_stage"

    with engine.begin() as conn:
        conn.execute(text(f"drop table if exists {stage};"))
        conn.execute(text(f"create temporary table {stage} (like public.price_snapshots including all) on commit drop;"))

    df.to_sql(stage, engine, if_exists="append", index=False, method="multi", chunksize=2000)

    upsert = f"""
    insert into public.price_snapshots as t
    select * from {stage}
    on conflict (snapshot_date, scryfall_id, finish)
    do update set
      set_code = excluded.set_code,
      collector_number = excluded.collector_number,
      finish = excluded.finish,
      name = excluded.name,
      rarity = excluded.rarity,
      type_line = excluded.type_line,
      usd = excluded.usd,
      fetched_at_epoch = excluded.fetched_at_epoch;
    """

    with engine.begin() as conn:
        conn.execute(text(upsert))

    print("Migration complete.")

if __name__ == "__main__":
    main()