"""Commander / Arena decklist parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

SECTION_COMMANDER = re.compile(r"^\s*(//\s*)?(commander|commanders)\s*:?\s*$", re.I)
SECTION_DECK = re.compile(r"^\s*(//\s*)?(deck|main\s*deck|mainboard)\s*:?\s*$", re.I)
SECTION_SIDEBOARD = re.compile(r"^\s*(//\s*)?(sideboard|maybeboard)\s*:?\s*$", re.I)
LINE_WITH_QTY = re.compile(
    r"^\s*(?:(\d+)\s*x?\s+)?(.+?)\s*(?:\((?:[^)]+)\))?\s*$",
    re.I,
)


@dataclass
class ParsedDeck:
    commander_names: list[str] = field(default_factory=list)
    main: list[str] = field(default_factory=list)
    sideboard: list[str] = field(default_factory=list)
    unknown_commander_candidates: list[str] = field(default_factory=list)
    raw_line_count: int = 0

    @property
    def all_cards(self) -> list[str]:
        return self.commander_names + self.main

    @property
    def total_cards(self) -> int:
        return len(self.all_cards)


def _clean_card_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r"\s+#\d+\s*$", "", name)
    name = re.sub(r"\s+\[[^\]]+\]\s*$", "", name)
    if " // " in name:
        name = name.split(" // ")[0].strip()
    return name.strip()


def _cards_by_blank_line_blocks(text: str) -> list[list[str]]:
    """Split deck text into card blocks separated by one or more empty lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    in_sideboard = False
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append(current)
                current = []
            in_sideboard = False
            continue
        if SECTION_SIDEBOARD.match(stripped):
            if current:
                blocks.append(current)
                current = []
            in_sideboard = True
            continue
        if SECTION_COMMANDER.match(stripped) or SECTION_DECK.match(stripped):
            continue
        if in_sideboard:
            continue
        card = _parse_line(line)
        if card:
            current.append(card)
    if current:
        blocks.append(current)
    return blocks


def _commander_from_blank_line_separation(text: str) -> Optional[tuple[str, list[str]]]:
    """If exactly one blank-line block is a single card, treat it as commander."""
    blocks = _cards_by_blank_line_blocks(text)
    if not blocks:
        return None
    singleton_indexes = [i for i, block in enumerate(blocks) if len(block) == 1]
    if len(singleton_indexes) != 1:
        return None
    cmd_idx = singleton_indexes[0]
    commander = blocks[cmd_idx][0]
    main: list[str] = []
    for i, block in enumerate(blocks):
        if i == cmd_idx:
            continue
        main.extend(block)
    if not main:
        return None
    return commander, main


def _parse_line(line: str) -> Optional[str]:
    line = line.strip()
    if not line or line.startswith("//"):
        return None
    if line.startswith("#"):
        return None
    m = LINE_WITH_QTY.match(line)
    if not m:
        return _clean_card_name(line) if line else None
    qty = int(m.group(1) or "1")
    name = _clean_card_name(m.group(2))
    if not name or qty < 1:
        return None
    return name


def parse_decklist(text: str, commander_hint: Optional[str] = None) -> ParsedDeck:
    result = ParsedDeck()
    section = "deck"
    lines = text.replace("\r\n", "\n").split("\n")

    for line in lines:
        result.raw_line_count += 1
        stripped = line.strip()
        if not stripped:
            if section == "sideboard":
                section = "deck"
            continue
        if SECTION_COMMANDER.match(stripped):
            section = "commander"
            continue
        if SECTION_DECK.match(stripped):
            section = "deck"
            continue
        if SECTION_SIDEBOARD.match(stripped):
            section = "sideboard"
            continue

        card = _parse_line(line)
        if not card:
            continue
        if section == "commander":
            result.commander_names.append(card)
        elif section == "sideboard":
            result.sideboard.append(card)
        else:
            result.main.append(card)

    if commander_hint:
        hint = _clean_card_name(commander_hint)
        result.commander_names = [hint]
        result.main = [c for c in result.main if c.lower() != hint.lower()]
        result.unknown_commander_candidates = []
    elif not result.commander_names:
        blank = _commander_from_blank_line_separation(text)
        if blank:
            commander, main = blank
            result.commander_names = [commander]
            result.main = main
        elif result.main:
            commander = result.main.pop()
            result.commander_names = [commander]

    return result
