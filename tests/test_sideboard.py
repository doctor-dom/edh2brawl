import json
from pathlib import Path

from app.cards import CardIndex
from app.deck import parse_decklist
from app.legality import analyze_deck, analyze_sideboard


def _fixture_index() -> CardIndex:
    fixture = Path(__file__).parent / "fixtures" / "cards.json"
    idx = CardIndex(Path(__file__).parent / "sideboard_fixture.db")
    idx.connect()
    idx.connect().execute("DELETE FROM cards")
    data = json.loads(fixture.read_text(encoding="utf-8"))
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


def test_parse_sideboard_section():
    text = """Commander
1 Baral, Chief of Compliance

Deck
1 Sol Ring
1 Counterspell

SIDEBOARD
1 Arcane Denial
1 Rhystic Study
"""
    parsed = parse_decklist(text)
    assert parsed.commander_names == ["Baral, Chief of Compliance"]
    assert parsed.main == ["Sol Ring", "Counterspell"]
    assert parsed.sideboard == ["Arcane Denial", "Rhystic Study"]


def test_sideboard_review_legality():
    idx = _fixture_index()
    parsed = parse_decklist(
        """Commander
1 Baral, Chief of Compliance

Deck
1 Sol Ring

SIDEBOARD
1 Arcane Denial
1 Rhystic Study
"""
    )
    analysis = analyze_deck(idx, parsed, "brawl")
    review = analyze_sideboard(idx, parsed.sideboard, "brawl", analysis.color_identity)
    by_name = {r["name"]: r for r in review}
    assert by_name["Arcane Denial"]["status"] == "legal"
    assert by_name["Rhystic Study"]["status"] == "illegal"


def test_suggestions_omit_cards_already_in_deck_or_sideboard():
    from app.legality import build_sideboard_suggestion_rows, build_suggestion_payload

    idx = _fixture_index()
    conn = idx.connect()
    conn.execute(
        """
        INSERT OR REPLACE INTO cards(
            name, oracle_text, mana_value, type_line, keywords, color_identity,
            power, toughness, brawl, competitivebrawl, image_url, rarity,
            is_brawler, layout
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "Opt",
            "Draw a card.",
            1,
            "Instant",
            "",
            "U",
            None,
            None,
            "legal",
            "legal",
            "",
            "common",
            0,
            "normal",
        ),
    )
    conn.commit()
    parsed = parse_decklist(
        """Commander
1 Baral, Chief of Compliance

Deck
1 Counterspell
1 Rhystic Study

SIDEBOARD
1 Arcane Denial
1 Sol Ring
"""
    )
    analysis = analyze_deck(idx, parsed, "brawl")
    review = analyze_sideboard(idx, parsed.sideboard, "brawl", analysis.color_identity)
    main_rows = build_suggestion_payload(
        idx, analysis, "brawl", {}, {}, False, False, sideboard_review=review
    )
    sb_rows = build_sideboard_suggestion_rows(
        idx, review, analysis, "brawl", {}, {}, False, False
    )
    suggested = {
        s["name"]
        for row in main_rows + sb_rows
        for s in row["suggestions"]
    }
    assert "Counterspell" not in suggested
    assert "Arcane Denial" not in suggested
    assert "Sol Ring" not in suggested
    assert "Baral, Chief of Compliance" not in suggested
    assert "Opt" in suggested


def test_illegal_sideboard_gets_replacement_rows():
    from app.legality import build_sideboard_suggestion_rows

    idx = _fixture_index()
    parsed = parse_decklist(
        """Commander
1 Baral, Chief of Compliance

Deck
1 Sol Ring

SIDEBOARD
1 Rhystic Study
"""
    )
    analysis = analyze_deck(idx, parsed, "brawl")
    review = analyze_sideboard(idx, parsed.sideboard, "brawl", analysis.color_identity)
    rows = build_sideboard_suggestion_rows(
        idx, review, analysis, "brawl", {}, {}, False, False
    )
    assert len(rows) == 1
    assert rows[0]["slot"] == "sb:Rhystic Study#0"
    assert rows[0]["zone"] == "sideboard"
    assert len(rows[0]["suggestions"]) >= 1


def test_export_sideboard_uses_selected_replacements():
    from app.legality import build_export_sideboard
    from app.main import _arena_export

    idx = _fixture_index()
    review = [
        {"name": "Arcane Denial", "status": "legal"},
        {"name": "Rhystic Study", "status": "illegal"},
    ]
    sideboard = build_export_sideboard(
        review,
        {"sb:Rhystic Study#0": "Counterspell"},
        idx,
    )
    names = [c["name"] for c in sideboard]
    assert names == ["Arcane Denial", "Counterspell"]
    export = _arena_export(
        {
            "commander": {"name": "Baral, Chief of Compliance"},
            "main": [{"name": "Sol Ring"}],
            "sideboard": sideboard,
        }
    )
    assert "Sideboard" in export
    assert "1 Arcane Denial" in export
    assert "1 Counterspell" in export
    assert "Rhystic Study" not in export
