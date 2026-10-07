import json
from pathlib import Path

from app.deck import parse_decklist
from app.legality import analyze_deck, build_analysis_snapshot, build_partial_deck, pending_replacement_slots
from app.cards import CardIndex
from app.main import _arena_export

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture_partial.db")
    idx.connect()
    idx.connect().execute("DELETE FROM cards")
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
            "Cyclonic Rift",
            "Return all nonland permanents to their owners' hands.",
            2,
            "Instant",
            "",
            "U",
            None,
            None,
            "not_legal",
            "not_legal",
            "",
            "rare",
            0,
            "normal",
        ),
    )
    conn.commit()
    return idx


def test_partial_export_keeps_unpicked_illegal_and_applies_picks():
    idx = _fixture_index()
    deck = """Commander
1 Vannifar, Evolved Enigma
Maindeck
1 Cyclonic Rift
1 Counterspell
"""
    analysis = analyze_deck(idx, parse_decklist(deck), "brawl")
    snapshot = build_analysis_snapshot(analysis, [])
    slot = next(e.slot for e in analysis.illegal if not e.is_commander)
    partial = build_partial_deck(
        analysis,
        {slot: "Arcane Denial"},
        idx,
        [],
    )
    names = {c["name"] for c in partial["main"]}
    assert "Arcane Denial" in names
    assert "Counterspell" in names
    pending = pending_replacement_slots(snapshot, {slot: "Arcane Denial"})
    assert slot not in pending
    partial2 = build_partial_deck(analysis, {}, idx, [])
    assert "Cyclonic Rift" in {c["name"] for c in partial2["main"]}
    export = _arena_export(partial2, pending_slots=pending_replacement_slots(snapshot, {}))
    assert "Cyclonic Rift" in export
    assert "Incomplete:" in export
