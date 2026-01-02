from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests

SCRYFALL_API = "https://api.scryfall.com"
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

def fetch_scryfall_card(set_code: str, collector_number: str) -> Dict[str, Any]:
    url = f"{SCRYFALL_API}/cards/{set_code}/{collector_number}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def _cache_filename(key: PrintingKey) -> str:
    safe_cn = key.collector_number.replace("/", "_").replace("\\", "_").replace(" ", "")
    return f"{key.set_code}__{safe_cn}.json"


def cache_get(cache_dir: Path, key: PrintingKey, ttl_hours: int) -> Optional[Dict[str, Any]]:
    path = cache_dir / _cache_filename(key)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
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
    card = dict(card)
    card["_fetched_at_epoch"] = time.time()
    (cache_dir / _cache_filename(key)).write_text(json.dumps(card), encoding="utf-8")


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