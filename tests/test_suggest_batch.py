import json
from pathlib import Path

from app.deck import parse_decklist
from app.legality import (
    analyze_deck,
    build_analysis_snapshot,
    build_final_deck,
    build_suggestion_payload,
    refresh_suggestion_slots,
)
from app.cards import CardIndex

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture_batch.db")
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


def test_duplicate_illegal_slots_and_finalize():
    idx = _fixture_index()
    deck = """Commander
1 Kenrith, the Returned King

Deck
1 Cyclonic Rift
1 Cyclonic Rift
1 Island
""" + "\n".join([f"1 Island" for _ in range(97)])
    parsed = parse_decklist(deck, commander_hint="Kenrith, the Returned King")
    analysis = analyze_deck(idx, parsed, "brawl")
    illegal_rift = [e for e in analysis.illegal if e.name == "Cyclonic Rift"]
    assert len(illegal_rift) == 2
    assert illegal_rift[0].slot != illegal_rift[1].slot
    assert illegal_rift[0].link_group == illegal_rift[1].link_group

    rows = build_suggestion_payload(idx, analysis, "brawl", {}, {}, False, False)
    rift_rows = [r for r in rows if r["illegal_name"] == "Cyclonic Rift"]
    assert len(rift_rows) == 2
    assert rift_rows[0]["linked_count"] == 2

    slot_a, slot_b = rift_rows[0]["slot"], rift_rows[1]["slot"]
    pick_a, pick_b = "Arcane Denial", "Counterspell"
    replacements = {slot_a: pick_a, slot_b: pick_b}
    final = build_final_deck(analysis, replacements, idx)
    names = [c["name"] for c in final["main"]]
    assert names.count(pick_a) == 1
    assert names.count(pick_b) == 1


def test_refresh_slot_matches_full_analyze():
    idx = _fixture_index()
    parsed = parse_decklist(
        "Commander\n1 Kenrith, the Returned King\n\nDeck\n1 Cyclonic Rift\n" + "1 Island\n" * 99,
        commander_hint="Kenrith, the Returned King",
    )
    analysis = analyze_deck(idx, parsed, "brawl")
    snapshot = build_analysis_snapshot(analysis, [])
    full = build_suggestion_payload(idx, analysis, "brawl", {}, {}, False, False)
    slot = full[0]["slot"]
    batch = refresh_suggestion_slots(idx, snapshot, [slot], "brawl", {}, {}, False, False)
    assert batch[0]["slot"] == slot
    assert [s["name"] for s in batch[0]["suggestions"]] == [
        s["name"] for s in full[0]["suggestions"]
    ]
