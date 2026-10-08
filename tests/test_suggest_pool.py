import json
from pathlib import Path
from unittest.mock import patch

from app.cards import CardIndex
from app.legality import build_replacement_row_for_slot, build_analysis_snapshot
from app.deck import parse_decklist
from app.legality import analyze_deck
from app.suggest import (
    SUGGESTION_POOL_SIZE,
    apply_external_ranking,
    edhrec_map_for_slot,
    score_replacement,
    suggest_from_pool,
    suggest_replacements,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture_pool.db")
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
    idx.connect().commit()
    _extra_cards(idx)
    return idx


def _extra_cards(idx: CardIndex) -> None:
    conn = idx.connect()
    rows = [
        (
            "Trampler",
            "Trample",
            3,
            "Creature — Beast",
            "Trample",
            "U",
            "5",
            "5",
            "legal",
            "uncommon",
        ),
        (
            "Synergy Test",
            "Draw a card.",
            2,
            "Instant",
            "",
            "U",
            None,
            None,
            "legal",
            "common",
        ),
        (
            "Plain Instant",
            "Draw a card.",
            2,
            "Instant",
            "",
            "U",
            None,
            None,
            "legal",
            "common",
        ),
    ]
    for name, text, mv, tl, kw, ci, pow_, tou, brawl, rarity in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO cards(
                name, oracle_text, mana_value, type_line, keywords, color_identity,
                power, toughness, brawl, competitivebrawl, image_url, rarity,
                is_brawler, layout
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (name, text, mv, tl, kw, ci, pow_, tou, brawl, "legal", "", rarity, 0, "normal"),
        )
    conn.commit()


def test_full_run_returns_pool_and_pages_differ():
    idx = _fixture_index()
    source = idx.get("Trampler")
    assert source is not None
    run = suggest_replacements(idx, source, "brawl", "U", set(), {}, limit=4)
    assert len(run.suggestions) <= 4
    assert len(run.pool_names) <= SUGGESTION_POOL_SIZE
    assert run.pool_refresh == "full"
    if len(run.pool_names) >= 8:
        page0 = {s.card.name for s in run.suggestions}
        run2 = suggest_replacements(
            idx, source, "brawl", "U", set(), {}, limit=4, page=1
        )
        page1 = {s.card.name for s in run2.suggestions}
        assert page0 != page1


def test_edhrec_map_for_slot_and_scoring():
    edhrec = {"Synergy Test": {"synergy": 40.0, "inclusion": 0.5}}
    assert edhrec_map_for_slot(edhrec, {}, "main:x") == edhrec
    assert edhrec_map_for_slot(edhrec, {"main:x": False}, "main:x") is None

    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    a = idx.get("Synergy Test")
    b = idx.get("Plain Instant")
    base_a = score_replacement(source, a, {})
    base_b = score_replacement(source, b, {})
    boosted_a = apply_external_ranking(base_a, edhrec, None)
    boosted_b = apply_external_ranking(base_b, edhrec, None)
    assert boosted_a.score > base_a.score
    assert boosted_a.score > boosted_b.score


def test_light_refresh_skips_iter_candidates():
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
    deck = parse_decklist(
        """Commander
1 Vannifar, Evolved Enigma
Maindeck
1 Cyclonic Rift
"""
    )
    analysis = analyze_deck(idx, deck, "brawl")
    snapshot = build_analysis_snapshot(analysis, [])
    slot = next(e.slot for e in analysis.illegal if not e.is_commander)
    source = idx.get("Cyclonic Rift")
    full = suggest_replacements(idx, source, "brawl", analysis.color_identity, set(), {})
    pool = full.pool_names
    assert pool

    with patch.object(CardIndex, "iter_candidates") as mock_iter:
        row = build_replacement_row_for_slot(
            idx,
            snapshot,
            slot,
            "brawl",
            {},
            {},
            False,
            False,
            {},
            {slot: 1},
            {},
            {},
            {},
            None,
            {},
            pool_names_by_slot={slot: pool},
            edhrec_available=False,
        )
        mock_iter.assert_not_called()
    assert row["pool_refresh"] == "light"
    assert len(row["suggestions"]) <= 4
