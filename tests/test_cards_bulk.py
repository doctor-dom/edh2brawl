import gzip
import json
from pathlib import Path

from app.cards import CardIndex


def _card(
    name: str,
    released_at: str,
    games: list[str] | None = None,
    oracle_text: str = "Draw a card.",
) -> dict:
    return {
        "name": name,
        "lang": "en",
        "games": games or ["arena"],
        "layout": "normal",
        "cmc": 1,
        "type_line": "Artifact",
        "oracle_text": oracle_text,
        "legalities": {"brawl": "legal"},
        "rarity": "common",
        "released_at": released_at,
        "keywords": [],
        "color_identity": [],
    }


def test_ingest_gzipped_jsonl(tmp_path: Path):
    bulk = tmp_path / "default_cards.jsonl.gz"
    lines = [
        _card("Older Printing", "2020-01-01", oracle_text="Old text."),
        _card("Older Printing", "2024-06-01", oracle_text="New text."),
        _card("Paper Only", "2024-01-01", games=["paper"]),
        _card("Arena Card", "2023-01-01"),
    ]
    with gzip.open(bulk, "wt", encoding="utf-8") as f:
        for row in lines:
            f.write(json.dumps(row) + "\n")

    idx = CardIndex(tmp_path / "cards.db")
    idx._ingest_bulk(bulk)

    assert idx.row_count() == 3
    older = idx.get("Older Printing")
    assert older is not None
    assert older.oracle_text == "New text."
    assert idx.get("Paper Only") is not None
    assert idx.get("Arena Card") is not None
