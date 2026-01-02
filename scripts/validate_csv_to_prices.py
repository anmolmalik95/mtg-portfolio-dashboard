from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests

SCRYFALL_API = "https://api.scryfall.com"

# Cache freshness (prices change; we want periodic refresh)
CACHE_TTL_HOURS = 12


@dataclass(frozen=True)
class PrintingKey:
    set_code: str
    collector_number: str


def load_collection(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)

    required = ["set", "collector_number", "qty", "finish"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")

    df["set"] = df["set"].astype(str).str.strip().str.lower()
    df["collector_number"] = df["collector_number"].astype(str).str.strip()
    df["finish"] = df["finish"].astype(str).str.strip().str.lower()
    df["qty"] = pd.to_numeric(df["qty"], errors="raise").astype(int)

    if "acquired_price_usd" in df.columns:
        df["acquired_price_usd"] = pd.to_numeric(df["acquired_price_usd"], errors="coerce")
    else:
        df["acquired_price_usd"] = pd.NA

    bad_finish = df[~df["finish"].isin(["nonfoil", "foil"])]
    if not bad_finish.empty:
        raise ValueError(
            f"Invalid finish values: {bad_finish['finish'].unique().tolist()} "
            f"(allowed: nonfoil, foil)"
        )

    return df


def _cache_filename(key: PrintingKey) -> str:
    # Collector numbers can contain slashes/hyphens; normalize safely
    safe_cn = key.collector_number.replace("/", "_").replace("\\", "_").replace(" ", "")
    return f"{key.set_code}__{safe_cn}.json"


def cache_get(cache_dir: Path, key: PrintingKey, ttl_hours: int) -> Optional[Dict[str, Any]]:
    path = cache_dir / _cache_filename(key)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Corrupted cache; treat as miss
        return None

    fetched_at = data.get("_fetched_at_epoch")
    if not isinstance(fetched_at, (int, float)):
        return None

    age_seconds = time.time() - float(fetched_at)
    if age_seconds > ttl_hours * 3600:
        return None

    return data


def cache_set(cache_dir: Path, key: PrintingKey, card: Dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    card = dict(card)  # copy
    card["_fetched_at_epoch"] = time.time()
    (cache_dir / _cache_filename(key)).write_text(json.dumps(card), encoding="utf-8")


def fetch_scryfall_card(set_code: str, collector_number: str) -> Dict[str, Any]:
    url = f"{SCRYFALL_API}/cards/{set_code}/{collector_number}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_scryfall_card_cached(cache_dir: Path, key: PrintingKey, ttl_hours: int) -> Tuple[Dict[str, Any], str]:
    cached = cache_get(cache_dir, key, ttl_hours=ttl_hours)
    if cached is not None:
        return cached, "cache_hit"

    card = fetch_scryfall_card(key.set_code, key.collector_number)
    cache_set(cache_dir, key, card)
    return card, "cache_miss_fetched"


def choose_unit_price_usd(card: Dict[str, Any], finish: str) -> Tuple[Optional[float], str]:
    prices = card.get("prices") or {}

    def to_float(x: Any) -> Optional[float]:
        return float(x) if x not in (None, "", "null") else None

    usd = to_float(prices.get("usd"))
    usd_foil = to_float(prices.get("usd_foil"))

    if finish == "foil":
        if usd_foil is not None:
            return usd_foil, "ok"
        if usd is not None:
            return usd, "fallback_used:usd"
        return None, "missing_price_usd"
    else:
        if usd is not None:
            return usd, "ok"
        if usd_foil is not None:
            return usd_foil, "fallback_used:usd_foil"
        return None, "missing_price_usd"

def type_buckets(type_line: str) -> list[str]:
    """
    Turn Scryfall type_line into broad buckets.
    Examples:
      "Enchantment" -> ["Enchantment"]
      "Artifact Creature — Golem" -> ["Artifact", "Creature"]
      "Legendary Creature — Human Wizard" -> ["Legendary", "Creature"]
      "Instant" -> ["Instant"]
    """
    if not type_line:
        return ["Unknown"]

    left = type_line.split("—")[0].strip()  # use left side only
    parts = [p.strip() for p in left.split() if p.strip()]
    return parts if parts else ["Unknown"]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"
    cache_dir = root / "data" / "cache" / "scryfall"

    df = load_collection(csv_path)

    unique = df[["set", "collector_number"]].drop_duplicates()
    card_cache: Dict[PrintingKey, Dict[str, Any]] = {}
    fetch_notes: Dict[PrintingKey, str] = {}

    # Build cache (fetch once per unique printing)
    for _, row in unique.iterrows():
        key = PrintingKey(row["set"], row["collector_number"])
        card, note = fetch_scryfall_card_cached(cache_dir, key, ttl_hours=CACHE_TTL_HOURS)
        card_cache[key] = card
        fetch_notes[key] = note

    # Enrich + value
    names, scry_ids, unit_prices, price_notes, fetch_sources = [], [], [], [], []

    for _, row in df.iterrows():
        key = PrintingKey(row["set"], row["collector_number"])
        card = card_cache[key]
        unit, price_note = choose_unit_price_usd(card, row["finish"])

        names.append(card.get("name"))
        scry_ids.append(card.get("id"))
        unit_prices.append(unit)
        price_notes.append(price_note)
        fetch_sources.append(fetch_notes.get(key, "unknown"))

    df["name"] = names
    df["scryfall_id"] = scry_ids
    df["unit_price_usd"] = unit_prices
    df["price_note"] = price_notes
    df["fetch_source"] = fetch_sources
    df["position_value_usd"] = df["unit_price_usd"] * df["qty"]
        # ---- Metadata enrichment (rarity/type) ----
    rarities = []
    type_lines = []
    buckets_list = []

    for _, row in df.iterrows():
        key = PrintingKey(row["set"], row["collector_number"])
        card = card_cache[key]

        rarity = (card.get("rarity") or "unknown").lower()
        type_line = card.get("type_line") or ""
        buckets = type_buckets(type_line)

        rarities.append(rarity)
        type_lines.append(type_line)
        buckets_list.append(buckets)

    df["rarity"] = rarities
    df["type_line"] = type_lines
    df["type_buckets"] = buckets_list


    print("\n=== Valuation Preview (with caching) ===")
    cols = [
        "set",
        "collector_number",
        "finish",
        "qty",
        "name",
        "unit_price_usd",
        "position_value_usd",
        "price_note",
        "fetch_source",
        "rarity",
        "type_line",
    ]
    print(df[cols].to_string(index=False))

    total = df["position_value_usd"].sum(skipna=True)
    print(f"\nTotal collection value (USD, priced rows only): {total:.2f}")
    print(f"Cache TTL hours: {CACHE_TTL_HOURS}")
    print(f"Cache dir: {cache_dir}")

    missing = df[df["unit_price_usd"].isna()]
    if not missing.empty:
        print("\nRows missing USD price:")
        print(missing[["set", "collector_number", "finish", "name", "price_note"]].to_string(index=False))
        # ---- Breakdown: rarity ----
    print("\n=== Breakdown: Rarity (count & value) ===")
    rarity_summary = (
        df.groupby("rarity", dropna=False)
          .agg(count=("qty", "sum"), value_usd=("position_value_usd", "sum"))
          .sort_values("value_usd", ascending=False)
    )
    print(rarity_summary.to_string())

    # ---- Breakdown: type buckets ----
    print("\n=== Breakdown: Type Buckets (count & value) ===")
    # explode: one row per (card, bucket) so a card can belong to multiple buckets
    exploded = df.copy()
    exploded["type_bucket"] = exploded["type_buckets"]
    exploded = exploded.explode("type_bucket")

    type_summary = (
        exploded.groupby("type_bucket", dropna=False)
                .agg(count=("qty", "sum"), value_usd=("position_value_usd", "sum"))
                .sort_values("value_usd", ascending=False)
    )
    print(type_summary.to_string())



if __name__ == "__main__":
    main()
