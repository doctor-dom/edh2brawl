from app.deck import parse_decklist


def test_commander_section():
    text = """Commander
1 Baral, Chief of Compliance

Deck
1 Sol Ring
1 Counterspell
"""
    d = parse_decklist(text)
    assert d.commander_names == ["Baral, Chief of Compliance"]
    assert d.main == ["Sol Ring", "Counterspell"]


def test_moxfield_style_qty():
    d = parse_decklist("1x Lightning Bolt\n2x Island")
    assert d.commander_names == ["Island"]
    assert d.main == ["Lightning Bolt"]


def test_last_line_is_commander_without_section():
    d = parse_decklist("1 Sol Ring\n1 Forest\n1 Atraxa, Praetors' Voice")
    assert d.commander_names == ["Atraxa, Praetors' Voice"]
    assert d.main == ["Sol Ring", "Forest"]


def test_blank_line_commander_above_main():
    d = parse_decklist("1 Atraxa, Praetors' Voice\n\n1 Sol Ring\n1 Forest")
    assert d.commander_names == ["Atraxa, Praetors' Voice"]
    assert d.main == ["Sol Ring", "Forest"]


def test_blank_line_commander_below_main():
    d = parse_decklist("1 Sol Ring\n1 Forest\n\n1 Atraxa, Praetors' Voice")
    assert d.commander_names == ["Atraxa, Praetors' Voice"]
    assert d.main == ["Sol Ring", "Forest"]


def test_blank_line_commander_among_multiple_main_blocks():
    d = parse_decklist(
        "1 Sol Ring\n1 Forest\n\n1 Atraxa, Praetors' Voice\n\n1 Island\n1 Mountain"
    )
    assert d.commander_names == ["Atraxa, Praetors' Voice"]
    assert d.main == ["Sol Ring", "Forest", "Island", "Mountain"]


def test_blank_line_commander_with_sideboard_section():
    d = parse_decklist(
        """1 Sol Ring
1 Forest

1 Atraxa, Praetors' Voice

SIDEBOARD
1 Negate
1 Dispel
"""
    )
    assert d.commander_names == ["Atraxa, Praetors' Voice"]
    assert d.main == ["Sol Ring", "Forest"]
    assert d.sideboard == ["Negate", "Dispel"]


def test_commander_after_sideboard_blank_line():
    text = """1 Access Tunnel
1 Withering Torment

SIDEBOARD:
1 Abyssal Harvester
1 Unstoppable Slasher

1 Satoru Umezawa
"""
    d = parse_decklist(text)
    assert d.commander_names == ["Satoru Umezawa"]
    assert d.main == ["Access Tunnel", "Withering Torment"]
    assert d.sideboard == ["Abyssal Harvester", "Unstoppable Slasher"]


def test_commander_hint_overrides_last_line():
    d = parse_decklist(
        "1 Sol Ring\n1 Wrong Last",
        commander_hint="Baral, Chief of Compliance",
    )
    assert d.commander_names == ["Baral, Chief of Compliance"]
    assert "Sol Ring" in d.main
    assert "Wrong Last" in d.main
