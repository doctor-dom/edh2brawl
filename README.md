# edh2brawl

Turn a **Commander** decklist into a **100-card Arena Brawl** list by flagging cards that are not legal on MTG Arena, suggesting up to three replacements, and exporting an Arena-ready deck.

## Requirements

- Python 3.11+
- Network access on first run (downloads Scryfall `default_cards` as gzipped JSONL, ~80MB compressed)

## Install and run

```bash
cd C:\Users\dfili\Projects\edh2brawl
pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## Decklist format

Paste Arena, Moxfield, or `1x Card Name` lists. Use `Commander` and `Deck` sections when possible.

## MTGA collection import

Arena does not export your full collection. Use [MTGA collection exporter](https://github.com/NthPhantom10/MTGA-collection-exporter):

1. Open MTG Arena **Decks** tab and leave the client running.
2. Run `MTGA_Exporter.exe` or `python mtg.py` from the exporter (Administrator if needed).
3. Enter at least 3 anchor cards with exact quantities (5 rares/mythics recommended).
4. Upload `mtga_collection_moxfield.csv` in edh2brawl (Goldfish/Cardsphere CSV also work).

## Tests

```bash
pytest
```

Card images and data © [Scryfall](https://scryfall.com).
