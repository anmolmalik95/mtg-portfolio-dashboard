from __future__ import annotations

import csv
import sys
from pathlib import Path

import requests

SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"


def search_printings(query: str, limit: int = 10) -> list[dict]:
    params = {
        "q": query,
        "unique": "prints",
        "order": "released",
        "dir": "desc",
    }
    r = requests.get(SCRYFALL_SEARCH, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("data", [])[:limit]


def fmt_price(card: dict) -> str:
    p = card.get("prices") or {}
    usd = p.get("usd")
    usd_foil = p.get("usd_foil")
    if usd and usd_foil:
        return f"${usd} / foil ${usd_foil}"
    if usd:
        return f"${usd}"
    if usd_foil:
        return f"foil ${usd_foil}"
    return "—"


def prompt(msg: str, validator=None):
    while True:
        val = input(msg).strip()
        if not validator or validator(val):
            return val
        print("Invalid input, try again.")


def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/add_card.py "Card Name"')
        sys.exit(1)

    raw = " ".join(sys.argv[1:]).strip()
    query = raw if any(x in raw for x in [":", "!", '"']) else f'!"{raw}"'

    cards = search_printings(query)
    if not cards:
        print("No results found.")
        return

    print("\nFound printings:\n")
    for i, c in enumerate(cards, start=1):
        print(
            f"{i}. {c['name']} — {c['set_name']} ({c['released_at']})\n"
            f"   set: {c['set']} | cn: {c['collector_number']} | "
            f"finishes: {', '.join(c['finishes'])} | {fmt_price(c)}\n"
        )

    choice = int(prompt("Select printing [1-{n}] (0 to cancel): ".format(n=len(cards)),
                         lambda x: x.isdigit() and 0 <= int(x) <= len(cards)))
    if choice == 0:
        print("Cancelled.")
        return

    card = cards[choice - 1]

    finish = prompt(
        "Finish (nonfoil/foil): ",
        lambda x: x in card["finishes"]
    )

    qty = int(prompt("Quantity: ", lambda x: x.isdigit() and int(x) > 0))

    row = {
        "set": card["set"],
        "collector_number": card["collector_number"],
        "qty": qty,
        "finish": finish,
        "acquired_price_usd": "",
    }

    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "collection.csv"

    print("\n→ Appending to collection.csv:")
    print(f"  {row['set']},{row['collector_number']},{row['qty']},{row['finish']},")

    confirm = prompt("Confirm? [y/N]: ", lambda x: x.lower() in {"y", "n", ""})
    if confirm.lower() != "y":
        print("Aborted.")
        return

    # Ensure file ends with a newline before appending
    needs_newline = False
    if csv_path.exists():
        with open(csv_path, "rb") as f:
            if f.tell() == 0:
                needs_newline = False
            else:
                f.seek(-1, 2)
                needs_newline = f.read(1) != b"\n"

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")

        writer = csv.DictWriter(
            f,
            fieldnames=["set", "collector_number", "qty", "finish", "acquired_price_usd"],
        )
        writer.writerow(row)


if __name__ == "__main__":
    main()
