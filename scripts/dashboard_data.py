from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from scripts._shared import (
    CACHE_TTL_HOURS,
    PrintingKey,
    choose_unit_price_usd,
    fetch_scryfall_card_cached,
    load_collection,
)

# -------------------------
# Snapshot helpers (SQLite)
# -------------------------
def _latest_snapshot_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(snapshot_date) FROM price_snapshots;").fetchone()
    if not row or not row[0]:
        raise RuntimeError("No snapshots found. Run scripts/snapshot_prices.py first.")
    return row[0]


def _snapshot_on_or_before(conn: sqlite3.Connection, target_date: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM price_snapshots WHERE snapshot_date <= ?;",
        (target_date,),
    ).fetchone()
    return row[0] if row and row[0] else None


# -------------------------
# Live pricing (no persistence)
# -------------------------
def _compute_live_owned(
    owned: pd.DataFrame,
    cache_dir: Path,
    ttl_hours: int = CACHE_TTL_HOURS,
) -> pd.DataFrame:
    """
    owned: columns = set_code, collector_number, finish, qty
    Returns DataFrame with live-enriched columns:
      scryfall_id, name, rarity, type_line, usd, position_value_usd
    Uses disk cache for Scryfall payloads; does NOT touch SQLite.
    """
    # fetch once per unique printing
    unique = owned[["set_code", "collector_number"]].drop_duplicates()
    card_cache: dict[PrintingKey, dict[str, Any]] = {}

    for _, r in unique.iterrows():
        key = PrintingKey(str(r["set_code"]), str(r["collector_number"]))
        card, _note = fetch_scryfall_card_cached(cache_dir, key, ttl_hours=ttl_hours)
        card_cache[key] = card

    names: list[str] = []
    scry_ids: list[str] = []
    rarities: list[str] = []
    type_lines: list[str] = []
    usd_prices: list[float | None] = []

    for _, r in owned.iterrows():
        key = PrintingKey(str(r["set_code"]), str(r["collector_number"]))
        card = card_cache[key]

        unit, _price_note = choose_unit_price_usd(card, str(r["finish"]))
        usd_prices.append(unit)

        names.append(str(card.get("name") or ""))
        scry_ids.append(str(card.get("id") or ""))
        rarities.append(str((card.get("rarity") or "unknown")).lower())
        type_lines.append(str(card.get("type_line") or ""))

    out = owned.copy()
    out["name"] = names
    out["scryfall_id"] = scry_ids
    out["rarity"] = rarities
    out["type_line"] = type_lines
    out["usd"] = usd_prices
    out["position_value_usd"] = out["usd"] * out["qty"]
    return out


# -------------------------
# Public API for Streamlit
# -------------------------
def get_dashboard_data(days: int = 7, top_n: int = 5, live_prices: bool = True) -> dict[str, object]:
    """
    Returns a dict of DataFrames and metadata used by the Streamlit dashboard.

    days: lookback window for movers (uses latest snapshot date as 'today' for baseline selection)
    live_prices:
      - True  => totals/holdings/breakdowns computed using live Scryfall prices (cached), no persistence
      - False => totals/holdings/breakdowns computed from latest SQLite snapshot
    """
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    db_path = root / "data" / "mtg_prices.sqlite"
    cache_dir = root / "data" / "cache" / "scryfall"

    df = load_collection(csv_path)

    # Normalize keys
    owned = df[["set", "collector_number", "finish", "qty"]].copy()
    owned.rename(columns={"set": "set_code"}, inplace=True)
    owned["set_code"] = owned["set_code"].astype(str).str.strip().str.lower()
    owned["collector_number"] = owned["collector_number"].astype(str).str.strip()
    owned["finish"] = owned["finish"].astype(str).str.strip().str.lower()

    # Defaults if DB not available / empty
    latest: str | None = None
    baseline: str | None = None
    baseline_rows = pd.DataFrame()

    # Try to open DB (needed for baseline + history)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        latest = _latest_snapshot_date(conn)
        target = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
        baseline = _snapshot_on_or_before(conn, target)

        if baseline is not None:
            baseline_rows = pd.read_sql_query(
                """
                SELECT scryfall_id, finish, usd AS usd_baseline
                FROM price_snapshots
                WHERE snapshot_date = ?;
                """,
                conn,
                params=(baseline,),
            )
    except Exception:
        # No DB / no snapshots. Dashboard can still show live holdings; no history/movers.
        latest = None
        baseline = None
        baseline_rows = pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()

    # -------------------------
    # Holdings source: live vs snapshot
    # -------------------------
    pricing_mode = "live" if live_prices else "snapshot"
    pricing_asof = date.today().isoformat() if live_prices else (latest or "N/A")

    if live_prices:
        latest_owned = _compute_live_owned(owned, cache_dir=cache_dir, ttl_hours=CACHE_TTL_HOURS)
    else:
        if latest is None:
            raise RuntimeError(
                "No snapshots found for snapshot-mode. Either run scripts/snapshot_prices.py or set live_prices=True."
            )

        conn2 = sqlite3.connect(db_path)
        try:
            latest_rows = pd.read_sql_query(
                """
                SELECT set_code, collector_number, finish, scryfall_id, name, rarity, type_line, usd
                FROM price_snapshots
                WHERE snapshot_date = ?;
                """,
                conn2,
                params=(latest,),
            )
        finally:
            conn2.close()

        latest_owned = owned.merge(
            latest_rows, on=["set_code", "collector_number", "finish"], how="inner"
        )
        latest_owned["position_value_usd"] = latest_owned["usd"] * latest_owned["qty"]

    # -------------------------
    # Top holdings / totals
    # -------------------------
    top_holdings = (
        latest_owned.dropna(subset=["usd"])
        .sort_values("position_value_usd", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    total_value = float(latest_owned["position_value_usd"].sum(skipna=True))
    num_positions = int(latest_owned.shape[0])

    # -------------------------
    # Rarity breakdown
    # -------------------------
    rarity_breakdown = (
        latest_owned.groupby("rarity", dropna=False)
        .agg(count=("qty", "sum"), value_usd=("position_value_usd", "sum"))
        .sort_values("value_usd", ascending=False)
        .reset_index()
    )

    # -------------------------
    # Type buckets breakdown
    # -------------------------
    def type_buckets(type_line: str) -> list[str]:
        if not type_line:
            return ["Unknown"]
        left = type_line.split("—")[0].strip()
        parts = [p.strip() for p in left.split() if p.strip()]
        return parts if parts else ["Unknown"]

    exploded = latest_owned.copy()
    exploded["type_bucket"] = exploded["type_line"].fillna("").map(type_buckets)
    exploded = exploded.explode("type_bucket")

    type_breakdown = (
        exploded.groupby("type_bucket", dropna=False)
        .agg(count=("qty", "sum"), value_usd=("position_value_usd", "sum"))
        .sort_values("value_usd", ascending=False)
        .reset_index()
    )

    # -------------------------
    # Movers: compare current (live or latest snapshot) vs baseline snapshot
    # -------------------------
    gainers = pd.DataFrame()
    losers = pd.DataFrame()

    if baseline is not None and not baseline_rows.empty:
        movers = latest_owned.merge(baseline_rows, on=["scryfall_id", "finish"], how="inner")
        movers = movers.dropna(subset=["usd", "usd_baseline"]).copy()

        # avoid div-by-zero for pct change
        movers["delta_usd"] = movers["usd"] - movers["usd_baseline"]
        movers["pct_change"] = movers.apply(
            lambda r: (r["delta_usd"] / r["usd_baseline"]) * 100.0 if r["usd_baseline"] not in (0, None) else None,
            axis=1,
        )

        moved = movers[movers["delta_usd"] != 0].copy()
        gainers = moved[moved["delta_usd"] > 0].sort_values("delta_usd", ascending=False).head(top_n)
        losers = moved[moved["delta_usd"] < 0].sort_values("delta_usd", ascending=True).head(top_n)

    return {
        "pricing_mode": pricing_mode,
        "pricing_asof": pricing_asof,
        "latest_snapshot": latest or "N/A",
        "baseline_snapshot": baseline,
        "total_value_usd": total_value,
        "num_positions": num_positions,
        "latest_owned": latest_owned,
        "top_holdings": top_holdings,
        "gainers": gainers,
        "losers": losers,
        "rarity_breakdown": rarity_breakdown,
        "type_breakdown": type_breakdown,
        "portfolio_ts": get_portfolio_timeseries(),
    }


def get_portfolio_timeseries() -> pd.DataFrame:
    """
    Returns DataFrame with columns:
      snapshot_date (YYYY-MM-DD), total_value_usd
    Computed from price_snapshots joined with current holdings (collection.csv).
    """
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    db_path = root / "data" / "mtg_prices.sqlite"

    df = load_collection(csv_path)

    owned = df[["set", "collector_number", "finish", "qty"]].copy()
    owned.rename(columns={"set": "set_code"}, inplace=True)
    owned["set_code"] = owned["set_code"].astype(str).str.strip().str.lower()
    owned["collector_number"] = owned["collector_number"].astype(str).str.strip()
    owned["finish"] = owned["finish"].astype(str).str.strip().str.lower()

    conn = sqlite3.connect(db_path)
    try:
        snaps = pd.read_sql_query(
            """
            SELECT snapshot_date, set_code, collector_number, finish, usd
            FROM price_snapshots
            WHERE usd IS NOT NULL;
            """,
            conn,
        )
    finally:
        conn.close()

    merged = owned.merge(
        snaps,
        on=["set_code", "collector_number", "finish"],
        how="inner",
    )
    merged["position_value_usd"] = merged["usd"] * merged["qty"]

    ts = (
        merged.groupby("snapshot_date", as_index=False)
        .agg(total_value_usd=("position_value_usd", "sum"))
        .sort_values("snapshot_date")
        .reset_index(drop=True)
    )
    return ts


if __name__ == "__main__":
    data = get_dashboard_data(days=1, top_n=5, live_prices=True)
    print("Pricing mode:", data["pricing_mode"], "asof:", data["pricing_asof"])
    print("Latest snapshot:", data["latest_snapshot"])
    print("Baseline snapshot:", data["baseline_snapshot"])
    print("Total value:", data["total_value_usd"])
