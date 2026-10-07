"""Deck legality analysis for Brawl."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from app.cards import CardIndex, CardRecord, normalize_name
from app.deck import ParsedDeck
from app.suggest import (
    AVAILABLE_ROLES,
    default_major_role,
    source_attributes,
    singleton_exempt,
    suggest_brawlers,
    suggest_replacements,
)


@dataclass
class IllegalEntry:
    name: str
    card: Optional[CardRecord]
    reason: str
    is_commander: bool = False


@dataclass
class DeckAnalysis:
    commander: Optional[CardRecord]
    commander_name: str
    commander_candidates: list[str]
    legal_main: list[CardRecord]
    illegal: list[IllegalEntry]
    unresolved: list[str]
    color_identity: str
    deck_size: int
    expected_size: int = 100


def resolve_name(index: CardIndex, name: str) -> Optional[CardRecord]:
    rec = index.get(name)
    if rec:
        return rec
    known = index.all_names()
    alt = normalize_name(name, known)
    return index.get(alt)


def build_exclude(
    commander: Optional[CardRecord],
    legal_main: list[CardRecord],
    replacements: dict[str, str],
    index: CardIndex,
) -> set[str]:
    used: set[str] = set()
    if commander:
        used.add(commander.name)
    for c in legal_main:
        if not singleton_exempt(c):
            used.add(c.name)
    for _old, new_name in replacements.items():
        rec = index.get(new_name)
        if rec and not singleton_exempt(rec):
            used.add(rec.name)
    return used


def analyze_deck(
    index: CardIndex,
    parsed: ParsedDeck,
    format_key: str,
    commander_override: Optional[str] = None,
) -> DeckAnalysis:
    commander_name = ""
    if commander_override:
        commander_name = commander_override
    elif parsed.commander_names:
        commander_name = parsed.commander_names[0]
    elif parsed.unknown_commander_candidates:
        commander_name = ""

    commander = resolve_name(index, commander_name) if commander_name else None
    commander_candidates = list(parsed.unknown_commander_candidates)
    if not commander_name and parsed.commander_names:
        commander_name = parsed.commander_names[0]
        commander = resolve_name(index, commander_name)

    if not commander_name and not commander_candidates:
        brawlers_in_main = []
        for n in parsed.main:
            rec = resolve_name(index, n)
            if rec and rec.is_brawler:
                brawlers_in_main.append(n)
        if len(brawlers_in_main) > 1:
            commander_candidates = brawlers_in_main
        elif len(brawlers_in_main) == 1:
            commander_name = brawlers_in_main[0]
            commander = resolve_name(index, commander_name)

    main_names = list(parsed.main)
    if commander_name:
        main_names = [n for n in main_names if n.lower() != commander_name.lower()]

    unresolved: list[str] = []
    legal_main: list[CardRecord] = []
    illegal: list[IllegalEntry] = []

    ci = commander.color_identity if commander else ""

    def check_card(name: str, is_cmd: bool) -> None:
        rec = resolve_name(index, name)
        if not rec:
            unresolved.append(name)
            illegal.append(IllegalEntry(name=name, card=None, reason="not found on Arena", is_commander=is_cmd))
            return
        leg = rec.legality(format_key)
        if leg != "legal":
            reason = "banned in Brawl" if leg == "banned" else "not legal on Arena / in Brawl"
            illegal.append(IllegalEntry(name=rec.name, card=rec, reason=reason, is_commander=is_cmd))
            return
        if is_cmd:
            if not rec.is_brawler:
                illegal.append(
                    IllegalEntry(
                        name=rec.name,
                        card=rec,
                        reason="not a valid Brawl commander",
                        is_commander=True,
                    )
                )
            return
        if ci and not _fits_ci(rec.color_identity, ci):
            illegal.append(
                IllegalEntry(
                    name=rec.name,
                    card=rec,
                    reason="outside commander color identity",
                    is_commander=False,
                )
            )
            return
        legal_main.append(rec)

    if commander_name:
        check_card(commander_name, True)
    for n in main_names:
        check_card(n, False)

    if commander:
        ci = commander.color_identity

    return DeckAnalysis(
        commander=commander if commander and not any(i.is_commander for i in illegal) else commander,
        commander_name=commander_name,
        commander_candidates=commander_candidates,
        legal_main=legal_main,
        illegal=illegal,
        unresolved=unresolved,
        color_identity=ci,
        deck_size=(1 if commander_name else 0) + len(main_names),
        expected_size=100,
    )


def _fits_ci(card_ci: str, deck_ci: str) -> bool:
    if not deck_ci:
        return True
    return all(c in deck_ci for c in card_ci)


def analyze_sideboard(
    index: CardIndex,
    names: list[str],
    format_key: str,
    color_identity: str,
) -> list[dict]:
    """Review sideboard cards for Brawl legality (informational; not part of the 100)."""
    rows: list[dict] = []
    for name in names:
        rec = resolve_name(index, name)
        if not rec:
            rows.append(
                {
                    "name": name,
                    "card": _card_json_minimal(name),
                    "status": "not_found",
                    "reason": "not found on Arena",
                }
            )
            continue
        leg = rec.legality(format_key)
        if leg != "legal":
            reason = "banned in Brawl" if leg == "banned" else "not legal on Arena / in Brawl"
            rows.append(
                {
                    "name": rec.name,
                    "card": _card_json(rec),
                    "status": "illegal",
                    "reason": reason,
                }
            )
            continue
        if color_identity and not _fits_ci(rec.color_identity, color_identity):
            rows.append(
                {
                    "name": rec.name,
                    "card": _card_json(rec),
                    "status": "illegal",
                    "reason": "outside commander color identity",
                }
            )
            continue
        rows.append(
            {
                "name": rec.name,
                "card": _card_json(rec),
                "status": "legal",
                "reason": "legal in Brawl (sideboard is not used in Arena Brawl decks)",
            }
        )
    return rows


def sideboard_slot_key(name: str) -> str:
    return f"sb:{name}"


def build_sideboard_suggestion_rows(
    index: CardIndex,
    sideboard_review: list[dict],
    analysis: DeckAnalysis,
    format_key: str,
    owned: dict[str, int],
    replacements: dict[str, str],
    prefer_collection: bool,
    minimize_wildcards: bool,
    role_overrides: dict[str, str] | None = None,
    rolls: dict[str, int] | None = None,
    cheap_overrides: dict[str, bool] | None = None,
) -> list[dict]:
    """Same replacement flow as the main deck, for sideboard cards that are not Brawl-legal."""
    role_overrides = role_overrides or {}
    rolls = rolls or {}
    cheap_overrides = cheap_overrides or {}

    def cheap_for_slot(slot_key: str) -> bool:
        return minimize_wildcards or cheap_overrides.get(slot_key, False)

    commander = analysis.commander
    exclude = build_exclude(commander, analysis.legal_main, replacements, index)
    ci = analysis.color_identity
    rows: list[dict] = []
    for entry in sideboard_review:
        if entry.get("status") == "legal":
            continue
        name = entry["name"]
        slot = sideboard_slot_key(name)
        source = resolve_name(index, name)
        if not source:
            rows.append(
                {
                    "slot": slot,
                    "illegal_name": name,
                    "illegal": entry.get("card") or _card_json_minimal(name),
                    "reason": entry.get("reason") or "not found on Arena",
                    "suggestions": [],
                    "source_attributes": None,
                    "active_role": None,
                    "available_roles": AVAILABLE_ROLES,
                    "roll_page": rolls.get(slot, 0),
                    "selected_replacement": None,
                    "zone": "sideboard",
                }
            )
            continue
        page = rolls.get(slot, 0)
        role_override = role_overrides.get(slot)
        sugs = suggest_replacements(
            index,
            source,
            format_key,
            ci,
            exclude,
            owned,
            prefer_collection,
            cheap_for_slot(slot),
            role_override=role_override or None,
            page=page,
        )
        selected_name = replacements.get(slot)
        selected = None
        if selected_name:
            rec = resolve_name(index, selected_name)
            if rec:
                selected = _card_json(rec)
        row = _replacement_row(
            slot=slot,
            illegal_name=name,
            illegal=source,
            reason=entry.get("reason") or "not legal on Arena / in Brawl",
            suggestions=sugs,
            role_override=role_override,
            selected=selected,
            roll_page=page,
            prefer_cheap=cheap_for_slot(slot),
        )
        row["zone"] = "sideboard"
        rows.append(row)
    return rows


def build_suggestion_payload(
    index: CardIndex,
    analysis: DeckAnalysis,
    format_key: str,
    owned: dict[str, int],
    replacements: dict[str, str],
    prefer_collection: bool,
    minimize_wildcards: bool,
    role_overrides: dict[str, str] | None = None,
    rolls: dict[str, int] | None = None,
    cheap_overrides: dict[str, bool] | None = None,
) -> list[dict]:
    role_overrides = role_overrides or {}
    rolls = rolls or {}
    cheap_overrides = cheap_overrides or {}

    def cheap_for_slot(slot_key: str) -> bool:
        return minimize_wildcards or cheap_overrides.get(slot_key, False)
    commander = analysis.commander
    if commander and any(i.is_commander for i in analysis.illegal):
        slot_key = "__commander__"
        exclude = build_exclude(None, [], replacements, index)
        page = rolls.get(slot_key, 0)
        role_override = role_overrides.get(slot_key)
        brawlers = suggest_brawlers(
            index,
            format_key,
            analysis.color_identity or commander.color_identity,
            exclude,
            owned,
            prefer_collection,
            cheap_for_slot(slot_key),
            page=page,
        )
        selected_name = replacements.get(slot_key)
        selected = None
        if selected_name:
            rec = resolve_name(index, selected_name)
            if rec:
                selected = _card_json(rec)
        return [
            _replacement_row(
                slot="commander",
                illegal_name=commander.name,
                illegal=commander,
                reason=next((i.reason for i in analysis.illegal if i.is_commander), "illegal commander"),
                suggestions=brawlers,
                role_override=role_override,
                selected=selected,
                prefer_cheap=cheap_for_slot(slot_key),
            )
        ]

    rows: list[dict] = []
    exclude = build_exclude(commander, analysis.legal_main, replacements, index)
    ci = analysis.color_identity

    for entry in analysis.illegal:
        if entry.is_commander:
            continue
        source = entry.card or resolve_name(index, entry.name)
        if not source:
            rows.append(
                {
                    "slot": entry.name,
                    "illegal_name": entry.name,
                    "illegal": _card_json_minimal(entry.name),
                    "reason": entry.reason,
                    "suggestions": [],
                    "source_attributes": None,
                    "active_role": None,
                    "available_roles": AVAILABLE_ROLES,
                    "roll_page": rolls.get(entry.name, 0),
                    "selected_replacement": None,
                }
            )
            continue
        page = rolls.get(entry.name, 0)
        role_override = role_overrides.get(entry.name)
        sugs = suggest_replacements(
            index,
            source,
            format_key,
            ci,
            exclude,
            owned,
            prefer_collection,
            cheap_for_slot(entry.name),
            role_override=role_override or None,
            page=page,
        )
        selected_name = replacements.get(entry.name)
        selected = None
        if selected_name:
            rec = resolve_name(index, selected_name)
            if rec:
                selected = _card_json(rec)
        rows.append(
            _replacement_row(
                slot=entry.name,
                illegal_name=entry.name,
                illegal=source,
                reason=entry.reason,
                suggestions=sugs,
                role_override=role_override,
                selected=selected,
                roll_page=page,
                prefer_cheap=cheap_for_slot(entry.name),
            )
        )
    return rows


def _replacement_row(
    *,
    slot: str,
    illegal_name: str,
    illegal: CardRecord,
    reason: str,
    suggestions: list,
    role_override: Optional[str],
    selected: Optional[dict],
    roll_page: int = 0,
    prefer_cheap: bool = False,
) -> dict:
    active = role_override or default_major_role(illegal)
    return {
        "slot": slot,
        "illegal_name": illegal_name,
        "illegal": _card_json(illegal),
        "reason": reason,
        "suggestions": [_suggestion_json(s) for s in suggestions],
        "source_attributes": source_attributes(illegal),
        "active_role": active,
        "available_roles": AVAILABLE_ROLES,
        "roll_page": roll_page,
        "selected_replacement": selected,
        "prefer_cheap": prefer_cheap,
    }


def display_image_url(name: str, image_url: str) -> str:
    if image_url:
        return image_url
    return f"https://api.scryfall.com/cards/named?exact={quote(name)}&format=image"


def _card_json_minimal(name: str) -> dict:
    return {
        "name": name,
        "mana_value": 0,
        "type_line": "",
        "oracle_text": "",
        "image_url": display_image_url(name, ""),
        "rarity": "unknown",
        "color_identity": "",
    }


def _card_json(card: CardRecord) -> dict:
    return {
        "name": card.name,
        "mana_value": card.mana_value,
        "type_line": card.type_line,
        "oracle_text": card.oracle_text,
        "image_url": display_image_url(card.name, card.image_url),
        "rarity": card.rarity,
        "color_identity": card.color_identity,
    }


def _suggestion_json(s) -> dict:
    return {
        "name": s.card.name,
        "mana_value": s.card.mana_value,
        "type_line": s.card.type_line,
        "oracle_text": s.card.oracle_text,
        "image_url": display_image_url(s.card.name, s.card.image_url),
        "rarity": s.card.rarity,
        "score": round(s.score, 4),
        "roles": s.roles,
        "reasons": s.reasons,
        "owned": s.owned,
        "wildcard_cost": s.wildcard_cost,
    }


def build_final_deck(
    analysis: DeckAnalysis,
    replacements: dict[str, str],
    index: CardIndex,
    new_commander_name: Optional[str] = None,
) -> dict:
    commander_name = new_commander_name or analysis.commander_name
    commander = resolve_name(index, commander_name) if commander_name else None

    main_cards: list[CardRecord] = list(analysis.legal_main)
    replaced_from: set[str] = set()

    for entry in analysis.illegal:
        if entry.is_commander:
            continue
        old = entry.name
        if old not in replacements:
            continue
        new_name = replacements[old]
        rec = resolve_name(index, new_name)
        if rec:
            main_cards.append(rec)
            replaced_from.add(old)

    if commander and commander.name not in {c.name for c in main_cards}:
        pass
    else:
        commander = commander

    deck_list = main_cards
    if commander:
        deck_list = [c for c in main_cards if c.name != commander.name]

    return {
        "commander": _card_json(commander) if commander else None,
        "main": [_card_json(c) for c in deck_list],
        "sideboard": [],
        "count": (1 if commander else 0) + len(deck_list),
        "replaced": replacements,
    }


def build_export_sideboard(
    sideboard_review: list[dict],
    replacements: dict[str, str],
    index: CardIndex,
) -> list[dict]:
    """Legal sideboard cards plus the replacement chosen for each illegal one."""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in sideboard_review:
        name = entry["name"]
        if entry.get("status") == "legal":
            rec = resolve_name(index, name)
            card = _card_json(rec) if rec else entry.get("card")
        else:
            picked = replacements.get(sideboard_slot_key(name))
            if not picked:
                continue
            rec = resolve_name(index, picked)
            card = _card_json(rec) if rec else None
        if not card or card["name"] in seen:
            continue
        seen.add(card["name"])
        out.append(card)
    return out
