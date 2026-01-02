from __future__ import annotations

import sys
import requests

SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"

def search_printings(query: str, limit: int = 10) -> list[dict]:
    # unique=prints ensures we see distinct printings
    params = {
        "q": query,
        "unique": "prints",
        "order": "released",
        "dir": "desc",
    }
    r = requests.get(SCRYFALL_SEARCH, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("data", [])[:limit]

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

def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/find_printing.py "Card Name"')
        print('Tip: You can add filters, e.g.  !"Sol Ring" or name:"Sol Ring" set:tdc')
        sys.exit(1)

    raw = " ".join(sys.argv[1:]).strip()

    # If user just gives a name, make it a name search.
    # You can override by passing your own Scryfall syntax like set:tdc, !"Ghostly Prison", etc.
    query = raw
    if not any(token in raw for token in [":", "!", '"', "set:", "oracle:", "collector:"]):
        query = f'!"{raw}"'

    print(f"\nScryfall query: {query}\n")

    cards = search_printings(query=query, limit=12)
    if not cards:
        print("No results.")
        return

    for i, c in enumerate(cards, start=1):
        name = c.get("name")
        set_code = c.get("set")
        set_name = c.get("set_name")
        cn = c.get("collector_number")
        released = c.get("released_at")
        finishes = ",".join(c.get("finishes") or [])
        type_line = c.get("type_line")
        rarity = c.get("rarity")
        price = fmt_price(c)

        print(f"{i:>2}. {name} — {type_line} ({rarity})")
        print(f"    set: {set_code} | cn: {cn} | {set_name} | released: {released}")
        print(f"    finishes: {finishes} | price: {price}")
        print(f"    scryfall: {c.get('scryfall_uri')}")
        print()

if __name__ == "__main__":
    main()
