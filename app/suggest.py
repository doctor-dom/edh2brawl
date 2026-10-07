"""Replacement scoring and suggestion engine."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.cards import CardIndex, CardRecord
from app.collection import is_owned
from app.edhrec import edhrec_score_bonus
from app.scryfall_filter import ParseError, card_matches, compile_query

REMINDER_RE = re.compile(r"\([^)]*\)")
WORD_RE = re.compile(r"[a-z0-9']+")

ROLE_PATTERNS: dict[str, re.Pattern[str]] = {
    "ramp": re.compile(
        r"add \{[wubrgc]/\}|search your library for .{0,40}land|put .{0,30}land.{0,20}onto the battlefield",
        re.I,
    ),
    "draw": re.compile(r"draw (a |one |two |three |\d+ )?card", re.I),
    "removal": re.compile(
        r"destroy target|exile target|damage target|fight target|return target .{0,20} to its owner",
        re.I,
    ),
    "sweeper": re.compile(r"destroy all|each (creature|permanent|nonland)", re.I),
    "counter": re.compile(r"counter target", re.I),
    "recursion": re.compile(r"return .{0,40} from (your )?graveyard", re.I),
    "tutor": re.compile(r"search your library", re.I),
    "protection": re.compile(r"hexproof|indestructible|protection from|can't be (the target|destroyed)", re.I),
    "tokens": re.compile(r"create .{0,30}token", re.I),
    "anthem": re.compile(r"gets \+\d/\+\d|other .{0,20} you control get", re.I),
    "lifegain": re.compile(r"gain .* life", re.I),
    "mill": re.compile(r"mill", re.I),
}

AVAILABLE_ROLES: list[str] = list(ROLE_PATTERNS.keys())

ROLE_PRIORITY: list[str] = [
    "counter",
    "removal",
    "draw",
    "ramp",
    "sweeper",
    "recursion",
    "tutor",
    "protection",
    "tokens",
    "anthem",
    "lifegain",
    "mill",
]

SUGGESTIONS_PER_PAGE = 4
SUGGESTION_POOL_SIZE = 12

ETB_TRIGGER_RE = re.compile(
    r"enters the battlefield|when (?:this|~|[^\n]{0,40}) enters(?: the battlefield)?",
    re.I,
)
ATTACK_TRIGGER_RE = re.compile(
    r"whenever (?:this|~|[^\n]{0,40}) attacks",
    re.I,
)

TRIGGER_LABELS = {
    "etb": "enters-the-battlefield",
    "attack": "attack",
}


def detect_trigger_kinds(text: str) -> set[str]:
    clean = REMINDER_RE.sub("", text or "")
    kinds: set[str] = set()
    if ETB_TRIGGER_RE.search(clean):
        kinds.add("etb")
    if ATTACK_TRIGGER_RE.search(clean):
        kinds.add("attack")
    return kinds


def detect_roles(text: str) -> set[str]:
    clean = REMINDER_RE.sub("", text or "").lower()
    roles: set[str] = set()
    for role, pat in ROLE_PATTERNS.items():
        if pat.search(clean):
            roles.add(role)
    if "land" in clean and "ramp" not in roles and "search your library" in clean:
        roles.add("ramp")
    return roles


def default_major_role(source: CardRecord) -> Optional[str]:
    roles = detect_roles(source.oracle_text)
    if not roles:
        return None
    for role in ROLE_PRIORITY:
        if role in roles:
            return role
    return sorted(roles)[0]


def subtypes_from_type_line(type_line: str) -> list[str]:
    if "—" not in (type_line or ""):
        return []
    return [s.strip() for s in type_line.split("—", 1)[1].split() if s.strip()]


def source_attributes(card: CardRecord) -> dict:
    triggers = detect_trigger_kinds(card.oracle_text)
    return {
        "detected_roles": sorted(detect_roles(card.oracle_text)),
        "mana_value": card.mana_value,
        "type_line": card.type_line,
        "subtypes": subtypes_from_type_line(card.type_line),
        "keywords": [k.strip() for k in card.keywords.split(",") if k.strip()],
        "triggers": [TRIGGER_LABELS[k] for k in sorted(triggers)],
    }


def primary_types(type_line: str) -> list[str]:
    head = (type_line or "").split("—")[0]
    return [t.strip().lower() for t in head.split() if t.strip()]


def noticed_attributes(card: CardRecord) -> list[dict[str, Any]]:
    """Attributes shown in the UI; defaults match legacy scorer weights."""
    items: list[dict[str, Any]] = []
    items.append(
        {
            "id": "text:oracle",
            "group": "score",
            "label": "Similar rules text",
            "used": True,
            "weight": 0.24,
        }
    )
    items.append(
        {
            "id": "stat:mana_value",
            "group": "score",
            "label": f"Mana value ({card.mana_value:g})",
            "used": True,
            "weight": 0.18,
        }
    )
    types = primary_types(card.type_line)
    type_share = 0.14 / max(1, len(types))
    for t in types:
        items.append(
            {
                "id": f"type:{t}",
                "group": "score",
                "label": f"Type: {t.title()}",
                "used": True,
                "weight": round(type_share, 4),
            }
        )
    roles = sorted(detect_roles(card.oracle_text))
    role_share = 0.28 / max(1, len(roles)) if roles else 0.28
    for role in roles:
        items.append(
            {
                "id": f"role:{role}",
                "group": "score",
                "label": f"Role: {role}",
                "used": True,
                "weight": round(role_share, 4),
            }
        )
    triggers = detect_trigger_kinds(card.oracle_text)
    for trig in sorted(triggers):
        items.append(
            {
                "id": f"trigger:{trig}",
                "group": "score",
                "label": f"Trigger: {TRIGGER_LABELS.get(trig, trig)}",
                "used": True,
                "weight": 0.07,
            }
        )
    keywords = [k.strip() for k in card.keywords.split(",") if k.strip()]
    for kw in keywords:
        items.append(
            {
                "id": f"keyword:{kw.lower()}",
                "group": "optional",
                "label": f"Keyword: {kw}",
                "used": False,
                "weight": 0.03,
            }
        )
    for sub in subtypes_from_type_line(card.type_line):
        items.append(
            {
                "id": f"subtype:{sub.lower()}",
                "group": "optional",
                "label": f"Subtype: {sub}",
                "used": False,
                "weight": 0.06,
            }
        )
    if card.power is not None:
        items.append(
            {
                "id": "stat:power",
                "group": "optional",
                "label": f"Power ({card.power})",
                "used": False,
                "weight": 0.12,
            }
        )
    if card.toughness is not None:
        items.append(
            {
                "id": "stat:toughness",
                "group": "optional",
                "label": f"Toughness ({card.toughness})",
                "used": False,
                "weight": 0.12,
            }
        )
    return items


def _attr_enabled(overrides: dict[str, bool], attr_id: str, default: bool) -> bool:
    if attr_id not in overrides:
        return default
    return bool(overrides[attr_id])


def _parse_pt(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    v = value.strip()
    if v in ("*", "X"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _pt_similarity(source: Optional[str], candidate: Optional[str]) -> float:
    a, b = _parse_pt(source), _parse_pt(candidate)
    if a is None or b is None:
        return 0.0
    dist = abs(a - b)
    return max(0.0, 1.0 - dist / 6.0)


def candidate_matches_attribute_requirements(
    source: CardRecord,
    candidate: CardRecord,
    overrides: dict[str, bool],
) -> bool:
    for trig in detect_trigger_kinds(source.oracle_text):
        key = f"trigger:{trig}"
        if not _attr_enabled(overrides, key, True) and trig in detect_trigger_kinds(candidate.oracle_text):
            return False
    for kw in [k.strip().lower() for k in source.keywords.split(",") if k.strip()]:
        key = f"keyword:{kw}"
        if _attr_enabled(overrides, key, False):
            cand_kw = {k.strip().lower() for k in candidate.keywords.split(",") if k.strip()}
            if kw not in cand_kw:
                return False
    for sub in [s.lower() for s in subtypes_from_type_line(source.type_line)]:
        key = f"subtype:{sub}"
        if _attr_enabled(overrides, key, False):
            cand_sub = {s.lower() for s in subtypes_from_type_line(candidate.type_line)}
            if sub not in cand_sub:
                return False
    return True


def _type_overlap_enabled(source_line: str, cand_line: str, overrides: dict[str, bool]) -> float:
    src_types = {t for t in primary_types(source_line) if _attr_enabled(overrides, f"type:{t}", True)}
    cand_types = {t.strip().lower() for t in cand_line.split("—")[0].split() if t.strip()}
    if not src_types or not cand_types:
        return 0.0
    return len(src_types & cand_types) / len(src_types | cand_types)


def tokenize_oracle(text: str) -> set[str]:
    clean = REMINDER_RE.sub("", (text or "").lower())
    stop = {
        "a", "an", "the", "you", "your", "that", "target", "card", "cards", "creature", "creatures",
        "permanent", "permanents", "until", "end", "turn", "if", "when", "each", "other", "control",
        "may", "choose", "one", "or", "and", "to", "of", "in", "on", "with", "this", "its",
    }
    return {w for w in WORD_RE.findall(clean) if w not in stop and len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def type_overlap(a: str, b: str) -> float:
    ta = {t.strip().lower() for t in a.split("—")[0].split() if t.strip()}
    tb = {t.strip().lower() for t in b.split("—")[0].split() if t.strip()}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def subtype_overlap(a: str, b: str) -> float:
    def subs(line: str) -> set[str]:
        if "—" not in line:
            return set()
        return {s.strip().lower() for s in line.split("—", 1)[1].split() if s.strip()}

    sa, sb = subs(a), subs(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def keyword_overlap(a: str, b: str) -> float:
    ka = {k.strip().lower() for k in a.split(",") if k.strip()}
    kb = {k.strip().lower() for k in b.split(",") if k.strip()}
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


def allows_any_number(oracle_text: str) -> bool:
    t = (oracle_text or "").lower()
    return "any number of" in t and "basic land" in t


def is_basic_land(type_line: str) -> bool:
    return "basic land" in (type_line or "").lower()


def singleton_exempt(card: CardRecord) -> bool:
    return is_basic_land(card.type_line) or allows_any_number(card.oracle_text)


@dataclass
class ScoredSuggestion:
    card: CardRecord
    score: float
    roles: list[str]
    reasons: list[str]
    owned: bool
    wildcard_cost: int


@dataclass
class SuggestionRunResult:
    suggestions: list[ScoredSuggestion]
    query_error: Optional[str] = None
    query_matched: Optional[int] = None
    pool_names: list[str] = field(default_factory=list)
    pool_refresh: str = "full"


def _apply_edhrec_bonus(
    score: float,
    reasons: list[str],
    card_name: str,
    edhrec_map: Optional[dict[str, dict[str, float]]],
) -> tuple[float, list[str]]:
    if not edhrec_map:
        return score, reasons
    bonus, label = edhrec_score_bonus(card_name, edhrec_map)
    if bonus <= 0 or not label:
        return score, reasons
    out_reasons = list(reasons)
    out_reasons.insert(0, label)
    return score + bonus, out_reasons


def score_replacement(
    source: CardRecord,
    candidate: CardRecord,
    owned: dict[str, int],
    active_role: Optional[str] = None,
    attribute_overrides: Optional[dict[str, bool]] = None,
    edhrec_map: Optional[dict[str, dict[str, float]]] = None,
) -> ScoredSuggestion:
    src_roles = detect_roles(source.oracle_text)
    cand_roles = detect_roles(candidate.oracle_text)
    src_triggers = detect_trigger_kinds(source.oracle_text)
    cand_triggers = detect_trigger_kinds(candidate.oracle_text)

    if attribute_overrides is not None:
        overrides = attribute_overrides
        enabled_src_roles = {
            r for r in src_roles if _attr_enabled(overrides, f"role:{r}", True)
        }
        role_score = 0.0
        if enabled_src_roles:
            role_score = len(enabled_src_roles & cand_roles) / len(enabled_src_roles)

        oracle_sim = (
            jaccard(tokenize_oracle(source.oracle_text), tokenize_oracle(candidate.oracle_text))
            if _attr_enabled(overrides, "text:oracle", True)
            else 0.0
        )
        cmc_score = 0.0
        if _attr_enabled(overrides, "stat:mana_value", True):
            cmc_dist = abs(source.mana_value - candidate.mana_value)
            cmc_score = max(0.0, 1.0 - cmc_dist / 6.0)
        type_score = _type_overlap_enabled(source.type_line, candidate.type_line, overrides)
        sub_score = (
            subtype_overlap(source.type_line, candidate.type_line)
            if any(
                _attr_enabled(overrides, f"subtype:{s.lower()}", False)
                for s in subtypes_from_type_line(source.type_line)
            )
            else 0.0
        )
        kw_score = 0.0
        if any(
            _attr_enabled(overrides, f"keyword:{k.lower()}", False)
            for k in source.keywords.split(",")
            if k.strip()
        ):
            kw_score = keyword_overlap(source.keywords, candidate.keywords)
        trigger_score = 0.0
        if src_triggers & cand_triggers:
            shared = src_triggers & cand_triggers
            if all(_attr_enabled(overrides, f"trigger:{t}", True) for t in shared):
                trigger_score = 1.0

        score = (
            0.28 * role_score
            + 0.24 * oracle_sim
            + 0.18 * cmc_score
            + 0.14 * type_score
            + 0.06 * sub_score
            + 0.03 * kw_score
            + 0.07 * trigger_score
        )
        if _attr_enabled(overrides, "stat:power", False):
            score += 0.12 * _pt_similarity(source.power, candidate.power)
        if _attr_enabled(overrides, "stat:toughness", False):
            score += 0.12 * _pt_similarity(source.toughness, candidate.toughness)

        reasons: list[str] = []
        if enabled_src_roles & cand_roles:
            reasons.append(f"shared role: {', '.join(sorted(enabled_src_roles & cand_roles))}")
        if cmc_score > 0.85:
            reasons.append("similar mana value")
        elif cmc_score > 0.5:
            reasons.append("close mana value")
        if oracle_sim > 0.2:
            reasons.append("similar oracle text")
        if type_score > 0.4:
            reasons.append("similar card type")
        if sub_score > 0.3:
            reasons.append("shared subtypes (tribal)")
        if trigger_score > 0 and "etb" in src_triggers & cand_triggers:
            reasons.append("shared enters-the-battlefield trigger")
        if trigger_score > 0 and "attack" in src_triggers & cand_triggers:
            reasons.append("shared attack trigger")
        if _attr_enabled(overrides, "stat:power", False) and _pt_similarity(source.power, candidate.power) > 0.5:
            reasons.append("similar power")
        if _attr_enabled(overrides, "stat:toughness", False) and _pt_similarity(
            source.toughness, candidate.toughness
        ) > 0.5:
            reasons.append("similar toughness")
        if not reasons:
            reasons.append("general functional fit")
    else:
        role_score = 0.0
        if active_role:
            role_score = 1.0 if active_role in cand_roles else 0.0
        elif src_roles:
            role_score = len(src_roles & cand_roles) / len(src_roles)
        elif cand_roles:
            role_score = 0.15

        oracle_sim = jaccard(tokenize_oracle(source.oracle_text), tokenize_oracle(candidate.oracle_text))
        cmc_dist = abs(source.mana_value - candidate.mana_value)
        cmc_score = max(0.0, 1.0 - cmc_dist / 6.0)
        type_score = type_overlap(source.type_line, candidate.type_line)
        sub_score = subtype_overlap(source.type_line, candidate.type_line)
        kw_score = keyword_overlap(source.keywords, candidate.keywords)
        trigger_score = 1.0 if src_triggers & cand_triggers else 0.0

        score = (
            0.28 * role_score
            + 0.24 * oracle_sim
            + 0.18 * cmc_score
            + 0.14 * type_score
            + 0.06 * sub_score
            + 0.03 * kw_score
            + 0.07 * trigger_score
        )

        reasons: list[str] = []
        if active_role and active_role in cand_roles:
            reasons.append(f"matches role: {active_role}")
        elif src_roles & cand_roles:
            reasons.append(f"shared role: {', '.join(sorted(src_roles & cand_roles))}")
        if cmc_score > 0.85:
            reasons.append("similar mana value")
        elif cmc_score > 0.5:
            reasons.append("close mana value")
        if oracle_sim > 0.2:
            reasons.append("similar oracle text")
        if type_score > 0.4:
            reasons.append("similar card type")
        if sub_score > 0.3:
            reasons.append("shared subtypes (tribal)")
        if "etb" in src_triggers & cand_triggers:
            reasons.append("shared enters-the-battlefield trigger")
        if "attack" in src_triggers & cand_triggers:
            reasons.append("shared attack trigger")
        if not reasons:
            reasons.append("general functional fit")

    score, reasons = _apply_edhrec_bonus(score, reasons, candidate.name, edhrec_map)

    return ScoredSuggestion(
        card=candidate,
        score=score,
        roles=sorted(cand_roles),
        reasons=reasons,
        owned=is_owned(owned, candidate.name),
        wildcard_cost=0 if is_owned(owned, candidate.name) else candidate.wildcard_cost,
    )


def is_reasonable_replacement(
    source: CardRecord,
    suggestion: ScoredSuggestion,
    best_score: float,
) -> bool:
    src_roles = detect_roles(source.oracle_text)
    cand_roles = detect_roles(suggestion.card.oracle_text)
    structural = (
        bool(src_roles & cand_roles)
        or abs(source.mana_value - suggestion.card.mana_value) <= 2
        or type_overlap(source.type_line, suggestion.card.type_line) > 0.4
        or bool(detect_trigger_kinds(source.oracle_text) & detect_trigger_kinds(suggestion.card.oracle_text))
    )
    if not structural:
        return False
    threshold = max(0.25, best_score - 0.20)
    if suggestion.score >= threshold:
        return True
    return suggestion.score >= best_score - 0.05


def _tag_reason(suggestion: ScoredSuggestion, tag: str) -> ScoredSuggestion:
    reasons = list(suggestion.reasons)
    if tag not in reasons:
        reasons.insert(0, tag)
    return ScoredSuggestion(
        card=suggestion.card,
        score=suggestion.score,
        roles=suggestion.roles,
        reasons=reasons,
        owned=suggestion.owned,
        wildcard_cost=suggestion.wildcard_cost,
    )


def _is_cheap_rarity(card: CardRecord) -> bool:
    return card.rarity in ("common", "uncommon")


def _assemble_suggestion_page(
    source: CardRecord,
    scored: list[ScoredSuggestion],
    owned: dict[str, int],
    prefer_collection: bool,
    minimize_wildcards: bool,
    page: int,
    limit: int,
    skip_reasonable_check: bool = False,
) -> list[ScoredSuggestion]:
    scored.sort(key=lambda x: -x.score)
    best_score = scored[0].score
    reserved: list[ScoredSuggestion] = []
    reserved_names: set[str] = set()

    def reasonable(s: ScoredSuggestion, ref: float) -> bool:
        if skip_reasonable_check:
            return True
        return is_reasonable_replacement(source, s, ref)

    if prefer_collection and owned:
        owned_scored = [s for s in scored if s.owned]
        if owned_scored:
            owned_ref = max(s.score for s in owned_scored)
            owned_candidates = [
                s for s in owned_scored if reasonable(s, owned_ref)
            ]
            owned_candidates.sort(key=lambda x: -x.score)
            if owned_candidates:
                pick = _tag_reason(owned_candidates[0], "in your collection")
                reserved.append(pick)
                reserved_names.add(pick.card.name)

    if minimize_wildcards:
        cheap_scored = [
            s for s in scored if _is_cheap_rarity(s.card) and s.card.name not in reserved_names
        ]
        if cheap_scored:
            cheap_ref = max(s.score for s in cheap_scored)
            cheap_candidates = [
                s for s in cheap_scored if reasonable(s, cheap_ref)
            ]
            cheap_candidates.sort(key=lambda s: (-s.score, s.wildcard_cost))
            if cheap_candidates:
                pick = _tag_reason(cheap_candidates[0], "common or uncommon wildcard")
                reserved.append(pick)
                reserved_names.add(pick.card.name)

    remainder = [s for s in scored if s.card.name not in reserved_names]
    slots = limit - len(reserved)
    if slots <= 0:
        return reserved[:limit]

    total_pages = max(1, (len(remainder) + slots - 1) // slots)
    page_idx = page % total_pages
    start = page_idx * slots
    return reserved + remainder[start : start + slots]


def rescore_pool_by_names(
    index: CardIndex,
    source: CardRecord,
    names: list[str],
    owned: dict[str, int],
    active_role: Optional[str] = None,
    attribute_overrides: Optional[dict[str, bool]] = None,
    edhrec_map: Optional[dict[str, dict[str, float]]] = None,
) -> list[ScoredSuggestion]:
    scored: list[ScoredSuggestion] = []
    for name in names:
        cand = index.get(name)
        if not cand or cand.name == source.name:
            continue
        scored.append(
            score_replacement(
                source,
                cand,
                owned,
                active_role=active_role,
                attribute_overrides=attribute_overrides,
                edhrec_map=edhrec_map,
            )
        )
    scored.sort(key=lambda x: -x.score)
    return scored


def suggest_from_pool(
    index: CardIndex,
    source: CardRecord,
    pool_names: list[str],
    owned: dict[str, int],
    prefer_collection: bool,
    minimize_wildcards: bool,
    page: int = 0,
    limit: int = SUGGESTIONS_PER_PAGE,
    role_override: Optional[str] = None,
    attribute_overrides: Optional[dict[str, bool]] = None,
    edhrec_map: Optional[dict[str, dict[str, float]]] = None,
) -> SuggestionRunResult:
    use_attributes = attribute_overrides is not None
    active_role = None if use_attributes else (role_override or None)
    scored = rescore_pool_by_names(
        index,
        source,
        pool_names,
        owned,
        active_role=active_role,
        attribute_overrides=attribute_overrides,
        edhrec_map=edhrec_map,
    )
    if not scored:
        return SuggestionRunResult(suggestions=[], pool_names=[], pool_refresh="light")
    pool_out = [s.card.name for s in scored]
    suggestions = _assemble_suggestion_page(
        source,
        scored,
        owned,
        prefer_collection,
        minimize_wildcards,
        page,
        limit,
        skip_reasonable_check=False,
    )
    return SuggestionRunResult(
        suggestions=suggestions,
        pool_names=pool_out,
        pool_refresh="light",
    )


def edhrec_map_for_slot(
    edhrec_map: Optional[dict[str, dict[str, float]]],
    edhrec_overrides: dict[str, bool],
    slot: str,
) -> Optional[dict[str, dict[str, float]]]:
    if not edhrec_map:
        return None
    if not edhrec_overrides.get(slot, True):
        return None
    return edhrec_map


def suggest_replacements(
    index: CardIndex,
    source: CardRecord,
    format_key: str,
    commander_ci: str,
    exclude: set[str],
    owned: dict[str, int],
    prefer_collection: bool = False,
    minimize_wildcards: bool = False,
    role_override: Optional[str] = None,
    page: int = 0,
    limit: int = SUGGESTIONS_PER_PAGE,
    pool_size: int = SUGGESTION_POOL_SIZE,
    attribute_overrides: Optional[dict[str, bool]] = None,
    scryfall_query: Optional[str] = None,
    edhrec_map: Optional[dict[str, dict[str, float]]] = None,
) -> SuggestionRunResult:
    """Pipeline: Brawl-legal candidates → attribute filters → Scryfall match → score → sort → page."""
    query_ast: Any = None
    query_error: Optional[str] = None
    if scryfall_query and scryfall_query.strip():
        try:
            query_ast = compile_query(scryfall_query.strip())
        except ParseError as exc:
            return SuggestionRunResult(
                suggestions=[], query_error=str(exc), query_matched=0, pool_names=[], pool_refresh="full"
            )

    use_attributes = attribute_overrides is not None
    active_role = None if use_attributes else (role_override or None)

    scored: list[ScoredSuggestion] = []
    matched_count = 0
    for cand in index.iter_candidates(format_key, commander_ci, exclude):
        if cand.name == source.name:
            continue
        if not source.is_brawler and cand.is_brawler:
            continue
        if source.is_brawler and not cand.is_brawler:
            continue
        if not use_attributes and role_override and role_override not in detect_roles(cand.oracle_text):
            continue
        if use_attributes and not candidate_matches_attribute_requirements(source, cand, attribute_overrides or {}):
            continue
        if query_ast and not card_matches(cand, query_ast):
            continue
        matched_count += 1
        scored.append(
            score_replacement(
                source,
                cand,
                owned,
                active_role=active_role,
                attribute_overrides=attribute_overrides if use_attributes else None,
                edhrec_map=edhrec_map,
            )
        )

    if not scored:
        return SuggestionRunResult(
            suggestions=[],
            query_matched=matched_count if query_ast else None,
            pool_names=[],
            pool_refresh="full",
        )

    scored.sort(key=lambda x: -x.score)
    pool_names = [s.card.name for s in scored[:pool_size]]
    scored = scored[:pool_size]
    suggestions = _assemble_suggestion_page(
        source,
        scored,
        owned,
        prefer_collection,
        minimize_wildcards,
        page,
        limit,
        skip_reasonable_check=bool(query_ast),
    )
    return SuggestionRunResult(
        suggestions=suggestions,
        query_matched=matched_count if query_ast else None,
        pool_names=pool_names,
        pool_refresh="full",
    )


def suggest_brawlers(
    index: CardIndex,
    format_key: str,
    color_identity: str,
    exclude: set[str],
    owned: dict[str, int],
    prefer_collection: bool,
    minimize_wildcards: bool,
    page: int = 0,
    limit: int = SUGGESTIONS_PER_PAGE,
) -> list[ScoredSuggestion]:
    out: list[ScoredSuggestion] = []
    for cand in index.iter_candidates(format_key, color_identity, exclude):
        if not cand.is_brawler:
            continue
        out.append(
            ScoredSuggestion(
                card=cand,
                score=1.0,
                roles=sorted(detect_roles(cand.oracle_text)),
                reasons=["legal Brawl commander"],
                owned=is_owned(owned, cand.name),
                wildcard_cost=0 if is_owned(owned, cand.name) else cand.wildcard_cost,
            )
        )
    if not out:
        return []
    out.sort(key=lambda s: (s.card.name,))
    best_score = max(s.score for s in out)

    def brawler_reasonable(s: ScoredSuggestion) -> bool:
        return s.score >= max(0.25, best_score - 0.20)

    reserved: list[ScoredSuggestion] = []
    reserved_names: set[str] = set()

    if prefer_collection and owned:
        owned_candidates = [s for s in out if s.owned and brawler_reasonable(s)]
        owned_candidates.sort(key=lambda s: s.card.name)
        if owned_candidates:
            pick = _tag_reason(owned_candidates[0], "in your collection")
            reserved.append(pick)
            reserved_names.add(pick.card.name)

    if minimize_wildcards:
        cheap_candidates = [
            s
            for s in out
            if _is_cheap_rarity(s.card)
            and s.card.name not in reserved_names
            and brawler_reasonable(s)
        ]
        cheap_candidates.sort(key=lambda s: (s.wildcard_cost, s.card.name))
        if cheap_candidates:
            pick = _tag_reason(cheap_candidates[0], "common or uncommon wildcard")
            reserved.append(pick)
            reserved_names.add(pick.card.name)

    remainder = [s for s in out if s.card.name not in reserved_names]
    slots = limit - len(reserved)
    if slots <= 0:
        return reserved[:limit]
    total_pages = max(1, (len(remainder) + slots - 1) // slots)
    page_idx = page % total_pages
    start = page_idx * slots
    return reserved + remainder[start : start + slots]
