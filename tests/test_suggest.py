import json
from pathlib import Path

from app.cards import CardIndex, CardRecord
from app.suggest import (
    detect_roles,
    detect_trigger_kinds,
    score_replacement,
    suggest_replacements,
    SUGGESTIONS_PER_PAGE,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture.db")
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
    return idx


def test_detect_roles_counter():
    assert "counter" in detect_roles("Counter target spell.")


def test_singleton_exclude_set():
    idx = _fixture_index()
    sol = idx.get("Sol Ring")
    assert sol
    exclude = {"Sol Ring", "Counterspell"}
    sugs = suggest_replacements(
        idx,
        idx.get("Rhystic Study"),
        "brawl",
        "U",
        exclude,
        {},
    ).suggestions
    names = [s.card.name for s in sugs]
    assert "Sol Ring" not in names
    assert "Counterspell" not in names


def test_prefer_collection_reserves_owned_slot():
    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    owned = {"Arcane Denial": 1}
    baseline = suggest_replacements(idx, source, "brawl", "U", set(), {}, limit=4).suggestions
    sugs_owned = suggest_replacements(
        idx, source, "brawl", "U", set(), owned, prefer_collection=True, minimize_wildcards=False
    ).suggestions
    assert sugs_owned[0].card.name == "Arcane Denial"
    assert sugs_owned[0].owned
    assert "in your collection" in sugs_owned[0].reasons
    assert len(sugs_owned) == len(baseline)
    baseline_tail = [s.card.name for s in baseline if s.card.name != "Arcane Denial"]
    assert [s.card.name for s in sugs_owned[1:]] == baseline_tail[: len(sugs_owned) - 1]


def test_prefer_collection_skips_unreasonable_owned():
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
            "Bad Fit",
            "Target opponent reveals their hand.",
            7,
            "Sorcery",
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
    source = idx.get("Rhystic Study")
    owned = {"Bad Fit": 4}
    sugs = suggest_replacements(
        idx, source, "brawl", "U", set(), owned, prefer_collection=True, minimize_wildcards=False
    ).suggestions
    assert sugs[0].card.name != "Bad Fit"


def test_minimize_wildcards_reserves_cheap_option():
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
            "Blue Rare Bomb",
            "Draw three cards.",
            3,
            "Enchantment",
            "",
            "U",
            None,
            None,
            "legal",
            "legal",
            "",
            "rare",
            0,
            "normal",
        ),
    )
    conn.commit()
    source = idx.get("Rhystic Study")
    baseline = suggest_replacements(idx, source, "brawl", "U", set(), {}, limit=4).suggestions
    cheap = suggest_replacements(
        idx, source, "brawl", "U", set(), {}, prefer_collection=False, minimize_wildcards=True, limit=4
    ).suggestions
    assert any(s.card.rarity in ("common", "uncommon") for s in cheap)
    assert any("common or uncommon wildcard" in s.reasons for s in cheap)
    assert cheap[0].card.name == baseline[0].card.name or cheap[0].card.rarity in ("common", "uncommon")


def test_per_slot_cheap_via_override_equivalent():
    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    global_off = suggest_replacements(
        idx, source, "brawl", "U", set(), {}, prefer_collection=False, minimize_wildcards=False, limit=4
    ).suggestions
    slot_on = suggest_replacements(
        idx, source, "brawl", "U", set(), {}, prefer_collection=False, minimize_wildcards=True, limit=4
    ).suggestions
    assert not any("common or uncommon wildcard" in s.reasons for s in global_off)
    assert any("common or uncommon wildcard" in s.reasons for s in slot_on)


def test_minimize_wildcards_skips_unreasonable_cheap():
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
            "Weak Common",
            "Destroy target artifact.",
            9,
            "Artifact",
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
    source = idx.get("Rhystic Study")
    sugs = suggest_replacements(
        idx, source, "brawl", "U", set(), {}, minimize_wildcards=True, limit=4
    ).suggestions
    reserved = [s for s in sugs if "common or uncommon wildcard" in s.reasons]
    assert all(s.card.name != "Weak Common" for s in reserved)


def test_shared_etb_trigger_reason():
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
            "ETB Source",
            "When this creature enters the battlefield, draw a card.",
            3,
            "Creature — Human",
            "",
            "U",
            "1",
            "1",
            "not_legal",
            "not_legal",
            "",
            "rare",
            0,
            "normal",
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO cards(
            name, oracle_text, mana_value, type_line, keywords, color_identity,
            power, toughness, brawl, competitivebrawl, image_url, rarity,
            is_brawler, layout
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "ETB Match",
            "When this creature enters the battlefield, scry 1.",
            3,
            "Creature — Merfolk",
            "",
            "U",
            "2",
            "1",
            "legal",
            "legal",
            "",
            "common",
            0,
            "normal",
        ),
    )
    conn.commit()
    source = idx.get("ETB Source")
    cand = idx.get("ETB Match")
    assert "etb" in detect_trigger_kinds(source.oracle_text)
    scored = score_replacement(source, cand, {})
    assert "shared enters-the-battlefield trigger" in scored.reasons


def test_score_counterspell_similarity():
    idx = _fixture_index()
    src = idx.get("Rhystic Study")
    cand = idx.get("Counterspell")
    scored = score_replacement(src, cand, {})
    assert scored.score > 0


def test_role_override_prefers_matching_role():
    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    sugs = suggest_replacements(
        idx, source, "brawl", "U", set(), {}, role_override="counter", limit=SUGGESTIONS_PER_PAGE
    ).suggestions
    assert sugs
    names = {s.card.name for s in sugs}
    assert "Counterspell" in names
    assert all("counter" in s.roles for s in sugs)


def test_reroll_pagination():
    idx = _fixture_index()
    conn = idx.connect()
    extras = [
        ("Cancel", "Counter target spell.", 3, "Instant"),
        ("Dispel", "Counter target spell unless its controller pays {1}.", 1, "Instant"),
        ("Essence Scatter", "Counter target creature spell.", 2, "Instant"),
        ("Negate", "Counter target noncreature spell.", 2, "Instant"),
        ("Annul", "Counter target artifact or enchantment spell.", 2, "Instant"),
    ]
    for name, text, mv, tl in extras:
        conn.execute(
            """
            INSERT OR REPLACE INTO cards(
                name, oracle_text, mana_value, type_line, keywords, color_identity,
                power, toughness, brawl, competitivebrawl, image_url, rarity,
                is_brawler, layout
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (name, text, mv, tl, "", "U", None, None, "legal", "legal", "", "common", 0, "normal"),
        )
    conn.commit()
    source = idx.get("Rhystic Study")
    page0 = suggest_replacements(idx, source, "brawl", "U", set(), {}, page=0, limit=4).suggestions
    page1 = suggest_replacements(idx, source, "brawl", "U", set(), {}, page=1, limit=4).suggestions
    names0 = {s.card.name for s in page0}
    names1 = {s.card.name for s in page1}
    assert len(page0) == 4
    assert names0 != names1 or len(names1) < 4


def test_replacement_excluded_from_other_slot():
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
    source_a = idx.get("Rhystic Study")
    source_b = idx.get("Cyclonic Rift")
    exclude = {"Arcane Denial"}
    sugs = suggest_replacements(idx, source_b, "brawl", "U", exclude, {}).suggestions
    assert all(s.card.name != "Arcane Denial" for s in sugs)
