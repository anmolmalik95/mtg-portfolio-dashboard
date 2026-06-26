from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from scripts._shared import (
    CACHE_TTL_HOURS,
    PrintingKey,
    choose_unit_price_usd,
    fetch_scryfall_card_cached,
    load_collection,
)

# -------------------------
# DB helpers (Supabase in prod, SQLite locally)
# -------------------------
def _get_engine() -> Engine:
    """
    If DATABASE_URL is set (Streamlit Cloud), use Supabase Postgres.
    Otherwise use local SQLite at data/mtg_prices.sqlite.
    """
    root = Path(__file__).resolve().parents[1]
    db_path = root / "data" / "mtg_prices.sqlite"

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return create_engine(db_url, pool_pre_ping=True)

    return create_engine(f"sqlite:///{db_path.as_posix()}", pool_pre_ping=True)


def _ensure_schema(engine: Engine) -> None:
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


# -------------------------
# Snapshot helpers
# -------------------------
def _latest_snapshot_date(engine: Engine) -> str:
    df = pd.read_sql_query(
        text("SELECT MAX(snapshot_date) AS max_date FROM price_snapshots;"),
        engine,
    )
    latest = df.iloc[0]["max_date"] if not df.empty else None
    if not latest:
        raise RuntimeError("No snapshots found. Run scripts/snapshot_prices.py first.")
    return str(latest)


def _latest_priced_snapshot_date(engine: Engine) -> str | None:
    """Latest snapshot date that has at least one non-null usd price.

    Used so the KPI/holdings fall back to the last usable snapshot when today's
    run failed to fetch prices (all-NULL), instead of reporting $0.00.
    """
    df = pd.read_sql_query(
        text(
            "SELECT MAX(snapshot_date) AS max_date "
            "FROM price_snapshots WHERE usd IS NOT NULL;"
        ),
        engine,
    )
    val = df.iloc[0]["max_date"] if not df.empty else None
    return str(val) if val else None


def _snapshot_on_or_before(engine: Engine, target_date: str) -> str | None:
    df = pd.read_sql_query(
        text(
            """
            SELECT MAX(snapshot_date) AS max_date
            FROM price_snapshots
            WHERE snapshot_date <= :target_date;
            """
        ),
        engine,
        params={"target_date": target_date},
    )
    val = df.iloc[0]["max_date"] if not df.empty else None
    return str(val) if val else None


# -------------------------
# Live pricing (no persistence)
# -------------------------
def _compute_live_owned(
    owned: pd.DataFrame,
    cache_dir: Path,
    ttl_hours: int = CACHE_TTL_HOURS,
) -> pd.DataFrame:
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
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    cache_dir = root / "data" / "cache" / "scryfall"

    df = load_collection(csv_path)

    owned = df[["set", "collector_number", "finish", "qty"]].copy()
    owned.rename(columns={"set": "set_code"}, inplace=True)
    owned["set_code"] = owned["set_code"].astype(str).str.strip().str.lower()
    owned["collector_number"] = owned["collector_number"].astype(str).str.strip()
    owned["finish"] = owned["finish"].astype(str).str.strip().str.lower()

    engine = _get_engine()
    _ensure_schema(engine)

    latest: str | None = None
    valuation: str | None = None
    baseline: str | None = None
    baseline_rows = pd.DataFrame()

    try:
        latest = _latest_snapshot_date(engine)
        # Value the portfolio off the latest *priced* snapshot. If today's run
        # failed (all-NULL prices), this falls back to the last usable day so the
        # KPIs don't collapse to $0.00 / empty while the chart shows real value.
        valuation = _latest_priced_snapshot_date(engine) or latest
        target = (date.fromisoformat(valuation) - timedelta(days=days)).isoformat()
        baseline = _snapshot_on_or_before(engine, target)

        if baseline is not None:
            baseline_rows = pd.read_sql_query(
                text(
                    """
                    SELECT scryfall_id, finish, usd AS usd_baseline
                    FROM price_snapshots
                    WHERE snapshot_date = :baseline;
                    """
                ),
                engine,
                params={"baseline": baseline},
            )
    except Exception as e:
        print(f"[snapshot lookup failed] {type(e).__name__}: {e}")
        latest = None
        valuation = None
        baseline = None
        baseline_rows = pd.DataFrame()

    pricing_mode = "live" if live_prices else "snapshot"
    pricing_asof = date.today().isoformat() if live_prices else (valuation or "N/A")

    if live_prices:
        latest_owned = _compute_live_owned(owned, cache_dir=cache_dir, ttl_hours=CACHE_TTL_HOURS)
    else:
        if latest is None:
            raise RuntimeError(
                "No snapshots found for snapshot-mode. Either run scripts/snapshot_prices.py or set live_prices=True."
            )

        latest_rows = pd.read_sql_query(
            text(
                """
                SELECT set_code, collector_number, finish, scryfall_id, name, rarity, type_line, usd
                FROM price_snapshots
                WHERE snapshot_date = :valuation;
                """
            ),
            engine,
            params={"valuation": valuation},
        )

        latest_owned = owned.merge(latest_rows, on=["set_code", "collector_number", "finish"], how="inner")
        latest_owned["position_value_usd"] = latest_owned["usd"] * latest_owned["qty"]

    top_holdings = (
        latest_owned.dropna(subset=["usd"])
        .sort_values("position_value_usd", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    total_value = float(latest_owned["position_value_usd"].sum(skipna=True))
    num_positions = int(latest_owned.shape[0])

    rarity_breakdown = (
        latest_owned.groupby("rarity", dropna=False)
        .agg(count=("qty", "sum"), value_usd=("position_value_usd", "sum"))
        .sort_values("value_usd", ascending=False)
        .reset_index()
    )

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

    gainers = pd.DataFrame()
    losers = pd.DataFrame()

    if baseline is not None and not baseline_rows.empty:
        movers = latest_owned.merge(baseline_rows, on=["scryfall_id", "finish"], how="inner")
        movers = movers.dropna(subset=["usd", "usd_baseline"]).copy()

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
        "valuation_snapshot": valuation or "N/A",
        "prices_stale": bool(valuation and latest and valuation != latest),
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
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"

    df = load_collection(csv_path)

    owned = df[["set", "collector_number", "finish", "qty"]].copy()
    owned.rename(columns={"set": "set_code"}, inplace=True)
    owned["set_code"] = owned["set_code"].astype(str).str.strip().str.lower()
    owned["collector_number"] = owned["collector_number"].astype(str).str.strip()
    owned["finish"] = owned["finish"].astype(str).str.strip().str.lower()

    engine = _get_engine()
    _ensure_schema(engine)

    snaps = pd.read_sql_query(
        text(
            """
            SELECT snapshot_date, set_code, collector_number, finish, usd
            FROM price_snapshots
            WHERE usd IS NOT NULL;
            """
        ),
        engine,
    )

    merged = owned.merge(snaps, on=["set_code", "collector_number", "finish"], how="inner")
    merged["position_value_usd"] = merged["usd"] * merged["qty"]

    ts = (
        merged.groupby("snapshot_date", as_index=False)
        .agg(total_value_usd=("position_value_usd", "sum"))
        .sort_values("snapshot_date")
        .reset_index(drop=True)
    )
    return ts
