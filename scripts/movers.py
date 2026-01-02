from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts._shared import load_collection


def latest_snapshot_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(snapshot_date) FROM price_snapshots;").fetchone()
    if not row or not row[0]:
        raise RuntimeError("No snapshots found. Run scripts/snapshot_prices.py first.")
    return row[0]


def snapshot_on_or_before(conn: sqlite3.Connection, target_date: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM price_snapshots WHERE snapshot_date <= ?;",
        (target_date,),
    ).fetchone()
    return row[0] if row and row[0] else None


def main(days: int = 7, top_n: int = 5, sort_mode: str = "abs") -> None:
    """
    sort_mode:
      - "abs" => rank by absolute $ change (delta_usd)
      - "pct" => rank by percent change (pct_change)
    """
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 80)

    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    db_path = root / "data" / "mtg_prices.sqlite"

    df = load_collection(csv_path)

    conn = sqlite3.connect(db_path)
    try:
        latest = latest_snapshot_date(conn)
        target = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
        baseline = snapshot_on_or_before(conn, target)

        if baseline is None:
            raise RuntimeError(
                f"Not enough snapshot history. Need a snapshot on or before {target}. "
                f"Current latest is {latest}. (Tip: run scripts/dev_clone_snapshot.py for testing.)"
            )

        # Only rank cards you own (match by set_code + collector_number + finish)
        owned_keys = df[["set", "collector_number", "finish", "qty"]].copy()
        owned_keys.rename(columns={"set": "set_code"}, inplace=True)
        owned_keys["set_code"] = owned_keys["set_code"].astype(str).str.strip().str.lower()
        owned_keys["collector_number"] = owned_keys["collector_number"].astype(str).str.strip()
        owned_keys["finish"] = owned_keys["finish"].astype(str).str.strip().str.lower()

        latest_rows = pd.read_sql_query(
            """
            SELECT set_code, collector_number, finish, scryfall_id, name, usd
            FROM price_snapshots
            WHERE snapshot_date = ?;
            """,
            conn,
            params=(latest,),
        )

        baseline_rows = pd.read_sql_query(
            """
            SELECT scryfall_id, finish, usd AS usd_baseline
            FROM price_snapshots
            WHERE snapshot_date = ?;
            """,
            conn,
            params=(baseline,),
        )

        latest_owned = owned_keys.merge(
            latest_rows, on=["set_code", "collector_number", "finish"], how="inner"
        )

        # -------------------------
        # Top holdings (by value)
        # -------------------------
        holdings = latest_owned.dropna(subset=["usd"]).copy()
        holdings["position_value_usd"] = holdings["usd"] * holdings["qty"]

        print(f"\n=== Top {top_n} Cards by Value (Latest Snapshot) ===")
        print(f"Snapshot date: {latest}")
        if holdings.empty:
            print("(none)")
        else:
            top_holdings = holdings.sort_values("position_value_usd", ascending=False).head(top_n)
            hold_cols = ["name", "finish", "qty", "usd", "position_value_usd", "set_code", "collector_number"]
            print(top_holdings[hold_cols].to_string(index=False, col_space=14, justify="left"))

        total_value = holdings["position_value_usd"].sum()
        print(f"\nTotal collection value (latest snapshot): ${total_value:,.2f}")

        # -------------------------
        # Movers (gainers/losers)
        # -------------------------
        movers = latest_owned.merge(baseline_rows, on=["scryfall_id", "finish"], how="inner")

        movers = movers.dropna(subset=["usd", "usd_baseline"]).copy()
        movers["delta_usd"] = movers["usd"] - movers["usd_baseline"]
        movers["pct_change"] = (movers["delta_usd"] / movers["usd_baseline"]) * 100.0

        sort_col = "delta_usd" if sort_mode == "abs" else "pct_change"

        moved = movers[movers[sort_col] != 0].copy()

        gainers_pool = moved[moved[sort_col] > 0]
        losers_pool = moved[moved[sort_col] < 0]

        gainers = gainers_pool.sort_values(sort_col, ascending=False).head(top_n)
        losers = losers_pool.sort_values(sort_col, ascending=True).head(top_n)

        print(f"\n=== Movers over last {days} day(s) ===")
        print(f"Latest snapshot:   {latest}")
        print(f"Baseline snapshot: {baseline}")
        print(f"Ranking by: {sort_col}")

        cols = [
            "name",
            "finish",
            "usd_baseline",
            "usd",
            "delta_usd",
            "pct_change",
            "qty",
            "set_code",
            "collector_number",
        ]

        print("\n--- Top Gainers ---")
        if gainers.empty:
            print("(none)")
        else:
            print(gainers[cols].to_string(index=False, col_space=14, justify="left"))

        print("\n--- Top Losers ---")
        if losers.empty:
            print("(none)")
        else:
            print(losers[cols].to_string(index=False, col_space=14, justify="left"))

    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    sort_mode = sys.argv[3] if len(sys.argv) > 3 else "abs"  # "abs" or "pct"
    main(days=days, top_n=top_n, sort_mode=sort_mode)
