from pathlib import Path

from app.cards import CardIndex
from app.main import _wildcard_summary


def test_wildcard_summary_skips_owned_and_basics(tmp_path: Path):
    idx = CardIndex(tmp_path / "wc.db")
    idx.connect()
    idx.connect().execute(
        """
        INSERT INTO cards(
            name, oracle_text, mana_value, type_line, keywords, color_identity,
            power, toughness, brawl, competitivebrawl, image_url, rarity,
            is_brawler, layout
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "Island",
            "",
            0,
            "Basic Land — Island",
            "",
            "U",
            None,
            None,
            "legal",
            "legal",
            "",
            "common",
            0,
            "land",
        ),
    )
    idx.connect().execute(
        """
        INSERT OR REPLACE INTO cards(
            name, oracle_text, mana_value, type_line, keywords, color_identity,
            power, toughness, brawl, competitivebrawl, image_url, rarity,
            is_brawler, layout
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "Rare Pick",
            "Draw a card.",
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
    idx.connect().commit()

    final = {
        "commander": None,
        "main": [{"name": "Island"}, {"name": "Rare Pick"}],
    }
    owned = {"Island": 4}
    counts = _wildcard_summary(final, owned, idx)
    assert counts["owned"] == 1
    assert counts["rare"] == 1
    assert counts["common"] == 0
