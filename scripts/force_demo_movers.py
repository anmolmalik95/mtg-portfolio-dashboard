from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text


BACKUP_DIR = Path("data") / "demo_backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class RowKey:
    snapshot_date: str
    scryfall_id: str
    finish: str


def _engine():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set in your environment.")
    return create_engine(db_url, pool_pre_ping=True)


def _get_latest_snapshot(e) -> str:
    df = pd.read_sql_query(text("select max(snapshot_date) as d from price_snapshots;"), e)
    latest = df.iloc[0]["d"] if not df.empty else None
    if not latest:
        raise RuntimeError("No snapshots found in price_snapshots.")
    return str(latest)



def _get_baseline_snapshot(e, latest: str, days: int) -> str:
    from datetime import date, timedelta

    target = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()

    df = pd.read_sql_query(
        text(
            """
            select max(snapshot_date) as d
            from price_snapshots
            where snapshot_date <= :target
            """
        ),
        e,
        params={"target": target},
    )
    baseline = df.iloc[0]["d"] if not df.empty else None
    if not baseline:
        raise RuntimeError(f"No baseline snapshot on/before {target}.")
    return str(baseline)


def _pick_rows_to_patch(e, baseline: str, n: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        text(
            """
            select snapshot_date, scryfall_id, finish, usd
            from price_snapshots
            where snapshot_date = :d and usd is not null
            order by usd desc
            limit :n
            """
        ),
        e,
        params={"d": baseline, "n": int(n)},
    )
    return df


def apply_patch(days: int, n: int, pct: float) -> None:
    e = _engine()
    latest = _get_latest_snapshot(e)
    baseline = _get_baseline_snapshot(e, latest=latest, days=days)

    rows = _pick_rows_to_patch(e, baseline=baseline, n=n)

    # Backup file (so we can revert exactly)
    backup_path = BACKUP_DIR / f"patch_{baseline}_days{days}_n{n}.json"
    backup_payload: list[dict[str, Any]] = rows.to_dict(orient="records")
    backup_path.write_text(json.dumps(backup_payload, indent=2), encoding="utf-8")

    # Apply alternating +/- pct so you get both gainers and losers
    with e.begin() as conn:
        for i, r in rows.iterrows():
            direction = 1 if i % 2 == 0 else -1
            new_usd = float(r["usd"]) * (1.0 + direction * pct)

            conn.execute(
                text(
                    """
                    update price_snapshots
                    set usd = :new_usd
                    where snapshot_date = :snapshot_date
                      and scryfall_id = :scryfall_id
                      and finish = :finish;
                    """
                ),
                {
                    "new_usd": new_usd,
                    "snapshot_date": str(r["snapshot_date"]),
                    "scryfall_id": str(r["scryfall_id"]),
                    "finish": str(r["finish"]),
                },
            )

    print("✅ Applied demo patch")
    print(f"   latest   = {latest}")
    print(f"   baseline = {baseline}")
    print(f"   changed  = {len(rows)} rows")
    print(f"   pct      = {pct:.2%}")
    print(f"   backup   = {backup_path.as_posix()}")
    print("")
    print("Now go to Streamlit, set 'Price change window' to the same 'days' you used, and Movers should show.")


def revert_patch(backup_file: str) -> None:
    e = _engine()
    backup_path = Path(backup_file)
    if not backup_path.exists():
        raise RuntimeError(f"Backup file not found: {backup_file}")

    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Backup file is empty or invalid.")

    with e.begin() as conn:
        for r in payload:
            conn.execute(
                text(
                    """
                    update price_snapshots
                    set usd = :usd
                    where snapshot_date = :snapshot_date
                      and scryfall_id = :scryfall_id
                      and finish = :finish;
                    """
                ),
                {
                    "usd": float(r["usd"]),
                    "snapshot_date": str(r["snapshot_date"]),
                    "scryfall_id": str(r["scryfall_id"]),
                    "finish": str(r["finish"]),
                },
            )

    print("✅ Reverted demo patch")
    print(f"   restored rows = {len(payload)}")
    print(f"   from backup   = {backup_path.as_posix()}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("apply")
    a.add_argument("--days", type=int, default=1, help="Match the dashboard window (1/7/30). Default: 1")
    a.add_argument("--n", type=int, default=12, help="How many rows to modify. Default: 12")
    a.add_argument("--pct", type=float, default=0.12, help="Percent change as decimal. Default: 0.12 (12 percent)")


    r = sub.add_parser("revert")
    r.add_argument("--backup", type=str, required=True, help="Path to the backup json file produced by apply")

    args = ap.parse_args()

    if args.cmd == "apply":
        apply_patch(days=args.days, n=args.n, pct=args.pct)
    else:
        revert_patch(backup_file=args.backup)


if __name__ == "__main__":
    main()
