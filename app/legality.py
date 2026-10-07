"""Deck legality analysis for Brawl."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from app.cards import CardIndex, CardRecord, normalize_name
from app.deck import ParsedDeck
from app.suggest import (
    AVAILABLE_ROLES,
    SuggestionRunResult,
    default_major_role,
    edhrec_map_for_slot,
    noticed_attributes,
    source_attributes,
    singleton_exempt,
    suggest_brawlers,
    suggest_from_pool,
    suggest_replacements,
)


COMMANDER_SLOT_KEY = "__commander__"


def _run_slot_suggestions(
    index: CardIndex,
    source: CardRecord,
    slot: str,
    format_key: str,
    commander_ci: str,
    exclude: set[str],
    owned: dict[str, int],
    prefer_collection: bool,
    cheap_slot: bool,
    role_override: Optional[str],
    page: int,
    attribute_overrides_slot: Optional[dict[str, bool]],
    scryfall_query: Optional[str],
    edhrec_map: dict[str, dict[str, float]] | None,
    edhrec_overrides: dict[str, bool],
    pool_names_by_slot: dict[str, list[str]],
) -> SuggestionRunResult:
    emap = edhrec_map_for_slot(edhrec_map, edhrec_overrides, slot)
    pool = pool_names_by_slot.get(slot)
    if pool:
        return suggest_from_pool(
            index,
            source,
            pool,
            owned,
            prefer_collection,
            cheap_slot,
            page=page,
            role_override=role_override or None,
            attribute_overrides=attribute_overrides_slot,
            edhrec_map=emap,
        )
    return suggest_replacements(
        index,
        source,
        format_key,
        commander_ci,
        exclude,
        owned,
        prefer_collection,
        cheap_slot,
        role_override=role_override or None,
        page=page,
        attribute_overrides=attribute_overrides_slot,
        scryfall_query=scryfall_query,
        edhrec_map=emap,
    )


def link_group_for_illegal(name: str) -> str:
    return f"illegal:{name}"


@dataclass
class IllegalEntry:
    name: str
    card: Optional[CardRecord]
    reason: str
    is_commander: bool = False
    slot: str = ""
    link_group: str = ""
    occurrence_index: int = 0


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

    main_deck_occ: dict[str, int] = defaultdict(int)

    def _illegal_entry(
        name: str,
        card: Optional[CardRecord],
        reason: str,
        is_cmd: bool,
        slot: str,
        link_group: str,
        occurrence_index: int,
    ) -> IllegalEntry:
        return IllegalEntry(
            name=name,
            card=card,
            reason=reason,
            is_commander=is_cmd,
            slot=slot,
            link_group=link_group,
            occurrence_index=occurrence_index,
        )

    def check_card(
        name: str,
        is_cmd: bool,
        slot: str,
        link_group: str,
        occurrence_index: int,
    ) -> None:
        rec = resolve_name(index, name)
        if not rec:
            unresolved.append(name)
            illegal.append(
                _illegal_entry(name, None, "not found on Arena", is_cmd, slot, link_group, occurrence_index)
            )
            return
        leg = rec.legality(format_key)
        if leg != "legal":
            reason = "banned in Brawl" if leg == "banned" else "not legal on Arena / in Brawl"
            illegal.append(
                _illegal_entry(rec.name, rec, reason, is_cmd, slot, link_group, occurrence_index)
            )
            return
        if is_cmd:
            if not rec.is_brawler:
                illegal.append(
                    _illegal_entry(
                        rec.name,
                        rec,
                        "not a valid Brawl commander",
                        True,
                        slot,
                        link_group,
                        occurrence_index,
                    )
                )
            return
        if ci and not _fits_ci(rec.color_identity, ci):
            illegal.append(
                _illegal_entry(
                    rec.name,
                    rec,
                    "outside commander color identity",
                    False,
                    slot,
                    link_group,
                    occurrence_index,
                )
            )
            return
        legal_main.append(rec)

    if commander_name:
        check_card(
            commander_name,
            True,
            COMMANDER_SLOT_KEY,
            link_group_for_illegal(commander_name),
            0,
        )
    for n in main_names:
        occ = main_deck_occ[n]
        main_deck_occ[n] += 1
        slot = f"main:{n}#{occ}"
        check_card(n, False, slot, link_group_for_illegal(n), occ)

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


def sideboard_slot_key(name: str, occurrence_index: int = 0) -> str:
    return f"sb:{name}#{occurrence_index}"


def _linked_counts_for_entries(entries: list[IllegalEntry]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for e in entries:
        if e.link_group:
            counts[e.link_group] += 1
    return counts


def _sideboard_link_counts(sideboard_review: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for entry in sideboard_review:
        if entry.get("status") == "legal":
            continue
        counts[link_group_for_illegal(entry["name"])] += 1
    return counts


def build_analysis_snapshot(analysis: DeckAnalysis, sideboard_review: list[dict]) -> dict[str, Any]:
    illegal_occurrences: list[dict[str, Any]] = []
    for entry in analysis.illegal:
        illegal_occurrences.append(
            {
                "slot": entry.slot or entry.name,
                "illegal_name": entry.name,
                "link_group": entry.link_group or link_group_for_illegal(entry.name),
                "zone": "commander" if entry.is_commander else "main",
                "reason": entry.reason,
                "is_commander": entry.is_commander,
                "occurrence_index": entry.occurrence_index,
            }
        )
    return {
        "commander_name": analysis.commander_name,
        "color_identity": analysis.color_identity,
        "legal_main_names": [c.name for c in analysis.legal_main],
        "illegal_occurrences": illegal_occurrences,
        "sideboard_review": sideboard_review,
    }


def _slot_specs_from_snapshot(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for occ in snapshot.get("illegal_occurrences") or []:
        specs[occ["slot"]] = occ
    sb_occ: dict[str, int] = defaultdict(int)
    for entry in snapshot.get("sideboard_review") or []:
        if entry.get("status") == "legal":
            continue
        name = entry["name"]
        idx = sb_occ[name]
        sb_occ[name] += 1
        slot = sideboard_slot_key(name, idx)
        specs[slot] = {
            "slot": slot,
            "illegal_name": name,
            "link_group": link_group_for_illegal(name),
            "zone": "sideboard",
            "reason": entry.get("reason") or "not legal on Arena / in Brawl",
            "is_commander": False,
            "occurrence_index": idx,
        }
    return specs


def _deck_analysis_from_snapshot(index: CardIndex, snapshot: dict[str, Any]) -> DeckAnalysis:
    commander_name = snapshot.get("commander_name") or ""
    commander = resolve_name(index, commander_name) if commander_name else None
    legal_main: list[CardRecord] = []
    for name in snapshot.get("legal_main_names") or []:
        rec = resolve_name(index, name)
        if rec:
            legal_main.append(rec)
    illegal: list[IllegalEntry] = []
    for occ in snapshot.get("illegal_occurrences") or []:
        name = occ["illegal_name"]
        card = resolve_name(index, name) if not occ.get("is_commander") else commander
        illegal.append(
            IllegalEntry(
                name=name,
                card=card,
                reason=occ.get("reason") or "",
                is_commander=bool(occ.get("is_commander")),
                slot=occ["slot"],
                link_group=occ.get("link_group") or link_group_for_illegal(name),
                occurrence_index=int(occ.get("occurrence_index") or 0),
            )
        )
    return DeckAnalysis(
        commander=commander,
        commander_name=commander_name,
        commander_candidates=[],
        legal_main=legal_main,
        illegal=illegal,
        unresolved=[],
        color_identity=snapshot.get("color_identity") or "",
        deck_size=(1 if commander_name else 0) + len(snapshot.get("legal_main_names") or []),
        expected_size=100,
    )


def build_replacement_row_for_slot(
    index: CardIndex,
    snapshot: dict[str, Any],
    slot: str,
    format_key: str,
    replacements: dict[str, str],
    owned: dict[str, int],
    prefer_collection: bool,
    minimize_wildcards: bool,
    role_overrides: dict[str, str],
    rolls: dict[str, int],
    cheap_overrides: dict[str, bool],
    attribute_overrides: dict[str, dict[str, bool]],
    query_overrides: dict[str, str],
    edhrec_map: dict[str, dict[str, float]] | None,
    linked_counts: dict[str, int],
    edhrec_overrides: dict[str, bool] | None = None,
    pool_names_by_slot: dict[str, list[str]] | None = None,
    edhrec_available: bool = False,
) -> dict:
    edhrec_overrides = edhrec_overrides or {}
    pool_names_by_slot = pool_names_by_slot or {}
    specs = _slot_specs_from_snapshot(snapshot)
    spec = specs.get(slot)
    if not spec:
        raise KeyError(slot)

    def cheap_for_slot(slot_key: str) -> bool:
        return minimize_wildcards or cheap_overrides.get(slot_key, False)

    analysis = _deck_analysis_from_snapshot(index, snapshot)
    commander = analysis.commander
    exclude = build_exclude(commander, analysis.legal_main, replacements, index)
    ci = analysis.color_identity
    zone = spec.get("zone") or "main"
    illegal_name = spec["illegal_name"]
    link_group = spec.get("link_group") or link_group_for_illegal(illegal_name)
    occurrence_index = int(spec.get("occurrence_index") or 0)
    linked_count = linked_counts.get(link_group, 1)

    if spec.get("is_commander") and commander:
        page = rolls.get(COMMANDER_SLOT_KEY, 0)
        role_override = role_overrides.get(COMMANDER_SLOT_KEY)
        brawlers = suggest_brawlers(
            index,
            format_key,
            ci or commander.color_identity,
            exclude,
            owned,
            prefer_collection,
            cheap_for_slot(COMMANDER_SLOT_KEY),
            page=page,
        )
        selected = None
        picked = replacements.get(COMMANDER_SLOT_KEY)
        if picked:
            rec = resolve_name(index, picked)
            if rec:
                selected = _card_json(rec)
        row = _replacement_row(
            slot=COMMANDER_SLOT_KEY,
            illegal_name=commander.name,
            illegal=commander,
            reason=spec.get("reason") or "illegal commander",
            suggestions=brawlers,
            role_override=role_override,
            selected=selected,
            roll_page=page,
            prefer_cheap=cheap_for_slot(COMMANDER_SLOT_KEY),
            link_group=link_group,
            occurrence_index=0,
            linked_count=1,
        )
        row["zone"] = "commander"
        return row

    source = resolve_name(index, illegal_name)
    if not source:
        return {
            "slot": slot,
            "illegal_name": illegal_name,
            "illegal": _card_json_minimal(illegal_name),
            "reason": spec.get("reason") or "not found on Arena",
            "suggestions": [],
            "source_attributes": None,
            "active_role": None,
            "available_roles": AVAILABLE_ROLES,
            "roll_page": rolls.get(slot, 0),
            "selected_replacement": None,
            "link_group": link_group,
            "occurrence_index": occurrence_index,
            "linked_count": linked_count,
            "zone": zone,
        }

    page = rolls.get(slot, 0)
    role_override = role_overrides.get(slot)
    run = _run_slot_suggestions(
        index,
        source,
        slot,
        format_key,
        ci,
        exclude,
        owned,
        prefer_collection,
        cheap_for_slot(slot),
        role_override,
        page,
        attribute_overrides.get(slot) if slot in attribute_overrides else None,
        query_overrides.get(slot),
        edhrec_map,
        edhrec_overrides,
        pool_names_by_slot,
    )
    selected = None
    picked = replacements.get(slot)
    if picked:
        rec = resolve_name(index, picked)
        if rec:
            selected = _card_json(rec)
    row = _replacement_row(
        slot=slot,
        illegal_name=illegal_name,
        illegal=source,
        reason=spec.get("reason") or "",
        suggestions=run.suggestions,
        role_override=role_override,
        selected=selected,
        roll_page=page,
        prefer_cheap=cheap_for_slot(slot),
        query_error=run.query_error,
        query_matched=run.query_matched,
        link_group=link_group,
        occurrence_index=occurrence_index,
        linked_count=linked_count,
        suggestion_pool_names=run.pool_names,
        prefer_edhrec=edhrec_overrides.get(slot, True),
        edhrec_available=edhrec_available,
        pool_refresh=run.pool_refresh,
    )
    row["zone"] = zone
    return row


def refresh_suggestion_slots(
    index: CardIndex,
    snapshot: dict[str, Any],
    slots: list[str],
    format_key: str,
    replacements: dict[str, str],
    owned: dict[str, int],
    prefer_collection: bool,
    minimize_wildcards: bool,
    role_overrides: dict[str, str] | None = None,
    rolls: dict[str, int] | None = None,
    cheap_overrides: dict[str, bool] | None = None,
    attribute_overrides: dict[str, dict[str, bool]] | None = None,
    query_overrides: dict[str, str] | None = None,
    edhrec_map: dict[str, dict[str, float]] | None = None,
    edhrec_overrides: dict[str, bool] | None = None,
    pool_names_by_slot: dict[str, list[str]] | None = None,
    edhrec_available: bool = False,
) -> list[dict]:
    role_overrides = role_overrides or {}
    rolls = rolls or {}
    cheap_overrides = cheap_overrides or {}
    attribute_overrides = attribute_overrides or {}
    query_overrides = query_overrides or {}
    edhrec_overrides = edhrec_overrides or {}
    pool_names_by_slot = pool_names_by_slot or {}
    analysis = _deck_analysis_from_snapshot(index, snapshot)
    linked_counts = _linked_counts_for_entries([e for e in analysis.illegal if not e.is_commander])
    sb_counts = _sideboard_link_counts(snapshot.get("sideboard_review") or [])
    for k, v in sb_counts.items():
        linked_counts[k] = max(linked_counts.get(k, 0), v)

    rows: list[dict] = []
    for slot in slots:
        try:
            rows.append(
                build_replacement_row_for_slot(
                    index,
                    snapshot,
                    slot,
                    format_key,
                    replacements,
                    owned,
                    prefer_collection,
                    minimize_wildcards,
                    role_overrides,
                    rolls,
                    cheap_overrides,
                    attribute_overrides,
                    query_overrides,
                    edhrec_map,
                    linked_counts,
                    edhrec_overrides,
                    pool_names_by_slot,
                    edhrec_available,
                )
            )
        except KeyError:
            continue
    return rows


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
    attribute_overrides: dict[str, dict[str, bool]] | None = None,
    query_overrides: dict[str, str] | None = None,
    edhrec_map: dict[str, dict[str, float]] | None = None,
    edhrec_overrides: dict[str, bool] | None = None,
    pool_names_by_slot: dict[str, list[str]] | None = None,
    edhrec_available: bool = False,
) -> list[dict]:
    """Same replacement flow as the main deck, for sideboard cards that are not Brawl-legal."""
    role_overrides = role_overrides or {}
    rolls = rolls or {}
    cheap_overrides = cheap_overrides or {}
    attribute_overrides = attribute_overrides or {}
    query_overrides = query_overrides or {}
    edhrec_overrides = edhrec_overrides or {}
    pool_names_by_slot = pool_names_by_slot or {}

    def cheap_for_slot(slot_key: str) -> bool:
        return minimize_wildcards or cheap_overrides.get(slot_key, False)

    commander = analysis.commander
    exclude = build_exclude(commander, analysis.legal_main, replacements, index)
    ci = analysis.color_identity
    sb_occ: dict[str, int] = defaultdict(int)
    sb_linked = _sideboard_link_counts(sideboard_review)
    rows: list[dict] = []
    for entry in sideboard_review:
        if entry.get("status") == "legal":
            continue
        name = entry["name"]
        occ = sb_occ[name]
        sb_occ[name] += 1
        slot = sideboard_slot_key(name, occ)
        link_group = link_group_for_illegal(name)
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
                    "link_group": link_group,
                    "occurrence_index": occ,
                    "linked_count": sb_linked.get(link_group, 1),
                }
            )
            continue
        page = rolls.get(slot, 0)
        role_override = role_overrides.get(slot)
        run = _run_slot_suggestions(
            index,
            source,
            slot,
            format_key,
            ci,
            exclude,
            owned,
            prefer_collection,
            cheap_for_slot(slot),
            role_override,
            page,
            attribute_overrides.get(slot) if slot in attribute_overrides else None,
            query_overrides.get(slot),
            edhrec_map,
            edhrec_overrides,
            pool_names_by_slot,
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
            suggestions=run.suggestions,
            role_override=role_override,
            selected=selected,
            roll_page=page,
            prefer_cheap=cheap_for_slot(slot),
            query_error=run.query_error,
            query_matched=run.query_matched,
            link_group=link_group,
            occurrence_index=occ,
            linked_count=sb_linked.get(link_group, 1),
            suggestion_pool_names=run.pool_names,
            prefer_edhrec=edhrec_overrides.get(slot, True),
            edhrec_available=edhrec_available,
            pool_refresh=run.pool_refresh,
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
    attribute_overrides: dict[str, dict[str, bool]] | None = None,
    query_overrides: dict[str, str] | None = None,
    edhrec_map: dict[str, dict[str, float]] | None = None,
    edhrec_overrides: dict[str, bool] | None = None,
    pool_names_by_slot: dict[str, list[str]] | None = None,
    edhrec_available: bool = False,
) -> list[dict]:
    role_overrides = role_overrides or {}
    rolls = rolls or {}
    cheap_overrides = cheap_overrides or {}
    attribute_overrides = attribute_overrides or {}
    query_overrides = query_overrides or {}
    edhrec_overrides = edhrec_overrides or {}
    pool_names_by_slot = pool_names_by_slot or {}

    def cheap_for_slot(slot_key: str) -> bool:
        return minimize_wildcards or cheap_overrides.get(slot_key, False)
    commander = analysis.commander
    if commander and any(i.is_commander for i in analysis.illegal):
        slot_key = COMMANDER_SLOT_KEY
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
                slot=COMMANDER_SLOT_KEY,
                illegal_name=commander.name,
                illegal=commander,
                reason=next((i.reason for i in analysis.illegal if i.is_commander), "illegal commander"),
                suggestions=brawlers,
                role_override=role_override,
                selected=selected,
                prefer_cheap=cheap_for_slot(slot_key),
                link_group=link_group_for_illegal(commander.name),
                occurrence_index=0,
                linked_count=1,
            )
        ]

    rows: list[dict] = []
    exclude = build_exclude(commander, analysis.legal_main, replacements, index)
    ci = analysis.color_identity
    linked_counts = _linked_counts_for_entries([e for e in analysis.illegal if not e.is_commander])

    for entry in analysis.illegal:
        if entry.is_commander:
            continue
        slot_key = entry.slot or entry.name
        source = entry.card or resolve_name(index, entry.name)
        if not source:
            rows.append(
                {
                    "slot": slot_key,
                    "illegal_name": entry.name,
                    "illegal": _card_json_minimal(entry.name),
                    "reason": entry.reason,
                    "suggestions": [],
                    "source_attributes": None,
                    "active_role": None,
                    "available_roles": AVAILABLE_ROLES,
                    "roll_page": rolls.get(slot_key, 0),
                    "selected_replacement": None,
                    "link_group": entry.link_group,
                    "occurrence_index": entry.occurrence_index,
                    "linked_count": linked_counts.get(entry.link_group, 1),
                }
            )
            continue
        page = rolls.get(slot_key, 0)
        role_override = role_overrides.get(slot_key)
        run = _run_slot_suggestions(
            index,
            source,
            slot_key,
            format_key,
            ci,
            exclude,
            owned,
            prefer_collection,
            cheap_for_slot(slot_key),
            role_override,
            page,
            attribute_overrides.get(slot_key) if slot_key in attribute_overrides else None,
            query_overrides.get(slot_key),
            edhrec_map,
            edhrec_overrides,
            pool_names_by_slot,
        )
        selected_name = replacements.get(slot_key)
        selected = None
        if selected_name:
            rec = resolve_name(index, selected_name)
            if rec:
                selected = _card_json(rec)
        rows.append(
            _replacement_row(
                slot=slot_key,
                illegal_name=entry.name,
                illegal=source,
                reason=entry.reason,
                suggestions=run.suggestions,
                role_override=role_override,
                selected=selected,
                roll_page=page,
                prefer_cheap=cheap_for_slot(slot_key),
                query_error=run.query_error,
                query_matched=run.query_matched,
                link_group=entry.link_group,
                occurrence_index=entry.occurrence_index,
                linked_count=linked_counts.get(entry.link_group, 1),
                suggestion_pool_names=run.pool_names,
                prefer_edhrec=edhrec_overrides.get(slot_key, True),
                edhrec_available=edhrec_available,
                pool_refresh=run.pool_refresh,
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
    query_error: Optional[str] = None,
    query_matched: Optional[int] = None,
    link_group: str = "",
    occurrence_index: int = 0,
    linked_count: int = 1,
    suggestion_pool_names: list[str] | None = None,
    prefer_edhrec: bool = True,
    edhrec_available: bool = False,
    pool_refresh: str = "full",
) -> dict:
    active = role_override or default_major_role(illegal)
    return {
        "slot": slot,
        "illegal_name": illegal_name,
        "illegal": _card_json(illegal),
        "reason": reason,
        "suggestions": [_suggestion_json(s) for s in suggestions],
        "source_attributes": source_attributes(illegal),
        "noticed_attributes": noticed_attributes(illegal),
        "active_role": active,
        "available_roles": AVAILABLE_ROLES,
        "roll_page": roll_page,
        "selected_replacement": selected,
        "prefer_cheap": prefer_cheap,
        "query_error": query_error,
        "query_matched": query_matched,
        "link_group": link_group,
        "occurrence_index": occurrence_index,
        "linked_count": linked_count,
        "suggestion_pool_names": suggestion_pool_names or [],
        "prefer_edhrec": prefer_edhrec,
        "edhrec_available": edhrec_available,
        "pool_refresh": pool_refresh,
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
        slot = entry.slot or entry.name
        if slot not in replacements:
            continue
        new_name = replacements[slot]
        rec = resolve_name(index, new_name)
        if rec:
            main_cards.append(rec)
            replaced_from.add(entry.name)

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
    sb_occ: dict[str, int] = defaultdict(int)
    for entry in sideboard_review:
        name = entry["name"]
        if entry.get("status") == "legal":
            rec = resolve_name(index, name)
            card = _card_json(rec) if rec else entry.get("card")
        else:
            occ = sb_occ[name]
            sb_occ[name] += 1
            picked = replacements.get(sideboard_slot_key(name, occ))
            if not picked:
                continue
            rec = resolve_name(index, picked)
            card = _card_json(rec) if rec else None
        if card:
            out.append(card)
    return out
