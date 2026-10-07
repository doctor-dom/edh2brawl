from app.deck import _parse_line_cards, parse_decklist


def test_parse_line_expands_quantity():
    assert _parse_line_cards("2x Sol Ring") == ["Sol Ring", "Sol Ring"]
    assert _parse_line_cards("1 Lightning Bolt") == ["Lightning Bolt"]


def test_decklist_duplicate_main_entries():
    text = """Commander
1 Kenrith, the Returned King

Deck
2 Not Real Card
1 Island
"""
    parsed = parse_decklist(text, commander_hint="Kenrith, the Returned King")
    assert parsed.main.count("Not Real Card") == 2
