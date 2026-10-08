import json
from pathlib import Path

from app.cards import CardIndex
from app.deck import parse_decklist
from app.legality import (
    analyze_deck,
    build_analysis_snapshot,
    build_partial_deck,
    decide_later_illegal_names,
    pending_replacement_slots,
)
from app.main import _arena_export
from app.suggest import SUGGESTION_POOL_SIZE, apply_external_ranking, score_replacement, suggest_replacements

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture_blend.db")
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


def test_pool_size_constant_is_twenty():
    assert SUGGESTION_POOL_SIZE == 20


def test_deckcheck_blend_raises_rank():
    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    a = idx.get("Counterspell")
    b = idx.get("Arcane Denial")
    internal_a = score_replacement(source, a, {})
    internal_b = score_replacement(source, b, {})
    deckcheck = {"Counterspell": 0.8, "Arcane Denial": 0.1}
    blended_a = apply_external_ranking(internal_a, None, deckcheck)
    blended_b = apply_external_ranking(internal_b, None, deckcheck)
    assert blended_a.score > blended_b.score


def test_scryfall_query_ignores_attribute_gate():
    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    overrides = {"subtype:human": True}
    without = suggest_replacements(
        idx,
        source,
        "brawl",
        "U",
        set(),
        {},
        attribute_overrides=overrides,
        limit=20,
    )
    with_query = suggest_replacements(
        idx,
        source,
        "brawl",
        "U",
        set(),
        {},
        attribute_overrides=overrides,
        scryfall_query="t:instant",
        limit=20,
    )
    assert with_query.query_matched is not None
    assert len(with_query.pool_names) <= SUGGESTION_POOL_SIZE
    assert without.suggestions or with_query.suggestions


def test_partial_export_deferred_omits_card():
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
    deferred = {slot}
    partial = build_partial_deck(analysis, {}, idx, [], deferred)
    names = {c["name"] for c in partial["main"]}
    assert "Cyclonic Rift" not in names
    assert "Counterspell" in names
    later = decide_later_illegal_names(snapshot, deferred)
    assert later == ["Cyclonic Rift"]
    pending = pending_replacement_slots(snapshot, {}, deferred)
    assert slot not in pending
    export = _arena_export(partial, pending_slots=pending, decide_later=later)
    assert "Cyclonic Rift" not in export.split("Deck")[1].split("#")[0]
    assert "# Decide later:" in export
    assert "Cyclonic Rift" in export
