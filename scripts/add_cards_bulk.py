from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

SCRYFALL = "https://api.scryfall.com"
PRICE_CAP_USD = 50.0
FINISH = "nonfoil"
SLEEP_S = 0.08  # be polite


def _to_float(x: Any) -> float | None:
    try:
        if x in (None, "", "null"):
            return None
        return float(x)
    except Exception:
        return None


def _search_prints(name: str) -> List[Dict[str, Any]]:
    # exact name search, paper only, printings
    q = f'!"{name}" game:paper'
    r = requests.get(
        f"{SCRYFALL}/cards/search",
        params={"q": q, "unique": "prints", "order": "usd", "dir": "asc"},
        timeout=20,
    )
    r.raise_for_status()
    return (r.json() or {}).get("data", [])


def pick_printing(name: str, cap: float = PRICE_CAP_USD) -> Tuple[str, str]:
    """
    Pick a reasonable 'normal' printing:
    - must have nonfoil
    - must have USD price <= cap
    - ordered by cheapest first
    """
    prints = _search_prints(name)
    for card in prints:
        finishes = card.get("finishes") or []
        if FINISH not in finishes:
            continue

        usd = _to_float((card.get("prices") or {}).get("usd"))
        if usd is None:
            continue
        if usd > cap:
            continue

        set_code = (card.get("set") or "").lower()
        cn = str(card.get("collector_number") or "").strip()
        if set_code and cn:
            return set_code, cn

    raise RuntimeError(f"No nonfoil USD printing <= ${cap:.2f} found for: {name}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_path = root / "data" / "bulk_new_entries.csv"

    # ---- Paste your decklist here (qty, name) ----
    cards: List[Tuple[int, str]] = [
        # From your screenshot (non-basics + spells)
        (1, "Teyo, Geometric Tactician"),
        (1, "Brash Taunter"),
        (1, "Breena, the Demagogue"),
        (1, "Combat Calligrapher"),
        (1, "Dream Devourer"),
        (1, "Flumph"),
        (1, "Gisela, Blade of Goldnight"),
        (1, "Humble Defector"),
        (1, "Kambal, Consul of Allocation"),
        (1, "Kardur, Doomscourge"),
        (1, "Loran of the Third Path"),
        (1, "Mangara, the Diplomat"),
        (1, "Master of Ceremonies"),
        (1, "Michiko Konda, Truth Seeker"),
        (1, "Nighthawk Scavenger"),
        (1, "Nils, Discipline Enforcer"),
        (1, "Selfless Squire"),
        (1, "Shadrix Silverquill"),
        (1, "Vampire Nighthawk"),
        (1, "Verrak, Warped Sengir"),
        (1, "Wandering Archaic"),
        (1, "Weathered Wayfarer"),
        (1, "Blasphemous Act"),
        (1, "Cut a Deal"),
        (1, "Mob Rule"),
        (1, "Scheming Symmetry"),
        (1, "Secret Rendezvous"),
        (1, "Angel's Grace"),
        (1, "Anguished Unmaking"),
        (1, "Arcbond"),
        (1, "Backlash"),
        (1, "Boros Charm"),
        (1, "Chaos Warp"),
        (1, "Comeuppance"),
        (1, "Crackling Doom"),
        (1, "Deflecting Palm"),
        (1, "Delirium"),
        (1, "Display of Power"),
        (1, "Generous Gift"),
        (1, "Inkshield"),
        (1, "Malakir Rebirth"),
        (1, "Olorin's Searing Light"),
        (1, "Path to Exile"),
        (1, "Rakdos Charm"),
        (1, "Reverberate"),
        (1, "Stroke of Midnight"),
        (1, "Swords to Plowshares"),
        (1, "Tibalt's Trickery"),
        (1, "Valakut Awakening"),
        (1, "Wear // Tear"),
        (1, "Arcane Signet"),
        (1, "Boros Signet"),
        (1, "Fellwar Stone"),
        (1, "Orzhov Signet"),
        (1, "Rakdos Signet"),
        (1, "Sol Ring"),
        (1, "Sunforger"),
        (1, "Talisman of Conviction"),
        (1, "Talisman of Hierarchy"),
        (1, "Talisman of Indulgence"),
        (1, "Wishclaw Talisman"),
        (1, "Duelist's Heritage"),
        (1, "Battlefield Forge"),
        (1, "Blightstep Pathway"),
        (1, "Boros Garrison"),
        (1, "Brightclimb Pathway"),
        (1, "Caves of Koilos"),
        (1, "Command Tower"),
        (1, "Exotic Orchard"),
        (1, "Fabled Passage"),
        (1, "Fetid Heath"),
        (1, "Graven Cairns"),
        (1, "Mistveil Plains"),
        (3, "Mountain"),
        (1, "Needleverge Pathway"),
        (1, "Nomad Outpost"),
        (1, "Orzhov Basilica"),
        (1, "Path of Ancestry"),
        (4, "Plains"),
        (1, "Rakdos Carnarium"),
        (1, "Rogue's Passage"),
        (1, "Rugged Prairie"),
        (1, "Shadowblood Ridge"),
        (1, "Smoldering Marsh"),
        (1, "Sulfurous Springs"),
        (3, "Swamp"),
        (1, "Temple of Malice"),
        (1, "Temple of Silence"),
        (1, "Temple of Triumph"),
        (1, "Vault of the Archangel"),
    ]

    lines = []
    failures = []

    for qty, name in cards:
        try:
            set_code, cn = pick_printing(name, cap=PRICE_CAP_USD)
            lines.append(f"{set_code},{cn},{qty},{FINISH},")
            print(f"OK  x{qty:<2d} {name} -> {set_code} {cn}")
        except Exception as e:
            failures.append(f"{name}: {e}")
            print(f"FAIL {name}: {e}")
        time.sleep(SLEEP_S)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "set,collector_number,qty,finish,acquired_price_usd\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )

    print(f"\n✅ Wrote {len(lines)} rows to {out_path}")
    if failures:
        print("\n⚠️ Failures (usually name formatting / no USD):")
        for f in failures:
            print(" -", f)


if __name__ == "__main__":
    main()
