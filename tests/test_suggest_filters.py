import json
from pathlib import Path

from app.cards import CardIndex, CardRecord
from app.edhrec import commander_slug, edhrec_score_bonus
from app.scryfall_filter import card_matches, compile_query
from app.suggest import (
    noticed_attributes,
    score_replacement,
    suggest_replacements,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cards.json"


def _fixture_index() -> CardIndex:
    idx = CardIndex(Path(__file__).parent / "fixture_filters.db")
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
    _extra_cards(idx)
    idx.connect().commit()
    return idx


def _extra_cards(idx: CardIndex) -> None:
    conn = idx.connect()
    rows = [
        (
            "ETB Artifact",
            "When this artifact enters the battlefield, draw a card.",
            2,
            "Artifact",
            "",
            "U",
            None,
            None,
            "legal",
            "common",
        ),
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
            "Vanilla Walker",
            "Trample",
            3,
            "Creature — Beast",
            "Trample",
            "U",
            "4",
            "4",
            "legal",
            "common",
        ),
        (
            "High CMC Trampler",
            "Trample",
            7,
            "Creature — Beast",
            "Trample",
            "U",
            "8",
            "8",
            "legal",
            "rare",
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


def _card(
    name: str,
    oracle: str,
    mv: float,
    type_line: str,
    keywords: str = "",
    power: str | None = None,
    toughness: str | None = None,
) -> CardRecord:
    return CardRecord(
        name=name,
        oracle_text=oracle,
        mana_value=mv,
        type_line=type_line,
        keywords=keywords,
        color_identity="U",
        power=power,
        toughness=toughness,
        brawl="legal",
        competitivebrawl="legal",
        image_url="",
        rarity="common",
        is_brawler=False,
        layout="normal",
    )


def test_noticed_attributes_groups():
    src = _card(
        "Big Trampler",
        "When this creature enters the battlefield, draw a card.",
        3,
        "Creature — Dragon",
        "Trample",
        "5",
        "5",
    )
    attrs = noticed_attributes(src)
    score_ids = {a["id"] for a in attrs if a["group"] == "score"}
    assert "text:oracle" in score_ids
    assert "trigger:etb" in score_ids
    assert any(a["id"] == "keyword:trample" and not a["used"] for a in attrs)


def test_default_order_unchanged_without_overrides():
    idx = _fixture_index()
    source = idx.get("Rhystic Study")
    a = suggest_replacements(idx, source, "brawl", "U", set(), {}, limit=4).suggestions
    b = suggest_replacements(idx, source, "brawl", "U", set(), {}, limit=4).suggestions
    assert [s.card.name for s in a] == [s.card.name for s in b]


def test_turning_etb_off_excludes_etb_cards():
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
    conn.commit()
    source = idx.get("ETB Source")
    overrides = {a["id"]: a["group"] == "score" for a in noticed_attributes(source)}
    overrides["trigger:etb"] = False
    run = suggest_replacements(
        idx,
        source,
        "brawl",
        "U",
        set(),
        {},
        attribute_overrides=overrides,
        limit=8,
    )
    names = {s.card.name for s in run.suggestions}
    assert "ETB Artifact" not in names
    assert "Trampler" in names or "Vanilla Walker" in names


def test_keyword_trample_required():
    idx = _fixture_index()
    source = idx.get("Trampler")
    overrides = {a["id"]: a["group"] == "score" for a in noticed_attributes(source)}
    overrides["keyword:trample"] = True
    run = suggest_replacements(
        idx,
        source,
        "brawl",
        "U",
        set(),
        {},
        attribute_overrides=overrides,
        limit=8,
    )
    for s in run.suggestions:
        assert "trample" in s.card.keywords.lower()


def test_scryfall_mv_spaces_and_o_keyword_trample():
    trampler = _card("KW Trampler", "", 3, "Creature — Beast", "Trample", "5", "5")
    ast = compile_query("mv <= 3")
    assert card_matches(trampler, ast)
    ast2 = compile_query("o:trample")
    assert card_matches(trampler, ast2)


def test_scryfall_filter_before_top_page():
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
            "Top Artifact",
            "Trample",
            3,
            "Artifact",
            "Trample",
            "U",
            "5",
            "5",
            "legal",
            "legal",
            "",
            "common",
            0,
            "normal",
        ),
    )
    conn.commit()
    source = idx.get("Trampler")
    wide = suggest_replacements(idx, source, "brawl", "U", set(), {}, limit=20).suggestions
    assert "Top Artifact" in {s.card.name for s in wide}
    filtered = suggest_replacements(
        idx,
        source,
        "brawl",
        "U",
        set(),
        {},
        scryfall_query="t:creature mv<=3",
        limit=4,
    ).suggestions
    names = {s.card.name for s in filtered}
    assert "Top Artifact" not in names
    assert "Trampler" in names or "Vanilla Walker" in names


def test_scryfall_trampler_query():
    idx = _fixture_index()
    source = idx.get("Trampler")
    q = 't:creature mv<=3 pow>=4 o:trample -o:"enters the battlefield"'
    ast = compile_query(q)
    assert card_matches(idx.get("Trampler"), ast)
    assert card_matches(idx.get("Vanilla Walker"), ast)
    assert not card_matches(idx.get("ETB Artifact"), ast)
    assert not card_matches(idx.get("High CMC Trampler"), ast)
    run = suggest_replacements(
        idx,
        source,
        "brawl",
        "U",
        set(),
        {},
        scryfall_query=q,
        limit=8,
    )
    names = {s.card.name for s in run.suggestions}
    assert "ETB Artifact" not in names
    assert "High CMC Trampler" not in names


def test_edhrec_blend_orders_synergy_card():
    from app.suggest import apply_external_ranking

    src = _card("Source", "Draw a card.", 2, "Instant")
    a = _card("Synergy Test", "Draw a card.", 2, "Instant")
    b = _card("Plain Instant", "Draw a card.", 2, "Instant")
    base_a = score_replacement(src, a, {})
    base_b = score_replacement(src, b, {})
    assert abs(base_a.score - base_b.score) < 0.001
    edhrec = {"Synergy Test": {"synergy": 40.0, "inclusion": 0.5}}
    boosted = apply_external_ranking(base_a, edhrec, None)
    plain = apply_external_ranking(base_b, edhrec, None)
    assert boosted.score > plain.score
    bonus, label = edhrec_score_bonus("Synergy Test", edhrec)
    assert bonus > 0 and label


def test_commander_slug():
    assert commander_slug("Atraxa, Praetors' Voice") == "atraxa-praetors-voice"
