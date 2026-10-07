import json
from pathlib import Path

from app.cards import CardIndex
from app.deck import parse_decklist
from app.legality import analyze_deck, build_suggestion_payload, display_image_url

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture.db")
    idx.connect()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in data:
        idx.connect().execute(
            """
            INSERT OR REPLACE INTO cards(
                name, oracle_text, mana_value, type_line, keywords, color_identity,
                power, toughness, brawl, competitivebrawl, image_url, rarity,
                is_brawler, layout
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["name"],
                row["oracle_text"],
                row["mana_value"],
                row["type_line"],
                row["keywords"],
                row["color_identity"],
                row["power"],
                row["toughness"],
                row["brawl"],
                row["competitivebrawl"],
                row["image_url"],
                row["rarity"],
                1 if row["is_brawler"] else 0,
                row["layout"],
            ),
        )
    idx.connect().commit()
    return idx


def test_flags_non_arena_card():
    idx = _fixture_index()
    text = """Commander
1 Baral, Chief of Compliance

Deck
1 Sol Ring
1 Rhystic Study
"""
    parsed = parse_decklist(text)
    analysis = analyze_deck(idx, parsed, "brawl")
    illegal_names = {e.name for e in analysis.illegal}
    assert "Rhystic Study" in illegal_names
    assert "Sol Ring" not in illegal_names


def test_illegal_card_has_suggestions_and_attributes():
    idx = _fixture_index()
    text = """Commander
1 Baral, Chief of Compliance

Deck
1 Rhystic Study
"""
    parsed = parse_decklist(text)
    analysis = analyze_deck(idx, parsed, "brawl")
    rows = build_suggestion_payload(idx, analysis, "brawl", {}, {}, False, False)
    row = next(r for r in rows if r["illegal_name"] == "Rhystic Study")
    assert row["illegal"]["image_url"]
    assert "draw" in row["source_attributes"]["detected_roles"]
    assert len(row["suggestions"]) >= 1
    assert row["suggestions"][0]["image_url"]


def test_selected_replacement_row_stays_visible():
    idx = _fixture_index()
    text = """Commander
1 Baral, Chief of Compliance

Deck
1 Rhystic Study
"""
    parsed = parse_decklist(text)
    analysis = analyze_deck(idx, parsed, "brawl")
    rows = build_suggestion_payload(
        idx,
        analysis,
        "brawl",
        {},
        {"Rhystic Study": "Arcane Denial"},
        False,
        False,
    )
    assert len(rows) == 1
    assert rows[0]["selected_replacement"]["name"] == "Arcane Denial"


def test_display_image_fallback():
    url = display_image_url("Lightning Bolt", "")
    assert "Lightning%20Bolt" in url or "Lightning+Bolt" in url
