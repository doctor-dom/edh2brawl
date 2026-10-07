"""Replacement scoring and suggestion engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.cards import CardIndex, CardRecord
from app.collection import is_owned

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
SUGGESTION_POOL_SIZE = 16

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


def score_replacement(
    source: CardRecord,
    candidate: CardRecord,
    owned: dict[str, int],
    active_role: Optional[str] = None,
) -> ScoredSuggestion:
    src_roles = detect_roles(source.oracle_text)
    cand_roles = detect_roles(candidate.oracle_text)
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
    src_triggers = detect_trigger_kinds(source.oracle_text)
    cand_triggers = detect_trigger_kinds(candidate.oracle_text)
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
) -> list[ScoredSuggestion]:
    scored.sort(key=lambda x: -x.score)
    best_score = scored[0].score
    reserved: list[ScoredSuggestion] = []
    reserved_names: set[str] = set()

    if prefer_collection and owned:
        owned_scored = [s for s in scored if s.owned]
        if owned_scored:
            owned_ref = max(s.score for s in owned_scored)
            owned_candidates = [
                s for s in owned_scored if is_reasonable_replacement(source, s, owned_ref)
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
                s for s in cheap_scored if is_reasonable_replacement(source, s, cheap_ref)
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
) -> list[ScoredSuggestion]:
    active_role = role_override or default_major_role(source)
    scored: list[ScoredSuggestion] = []
    for cand in index.iter_candidates(format_key, commander_ci, exclude):
        if cand.name == source.name:
            continue
        if not source.is_brawler and cand.is_brawler:
            continue
        if source.is_brawler and not cand.is_brawler:
            continue
        if role_override and active_role and active_role not in detect_roles(cand.oracle_text):
            continue
        scored.append(
            score_replacement(
                source,
                cand,
                owned,
                active_role=role_override or None,
            )
        )

    if not scored:
        return []

    scored.sort(key=lambda x: -x.score)
    scored = scored[:pool_size]
    return _assemble_suggestion_page(
        source,
        scored,
        owned,
        prefer_collection,
        minimize_wildcards,
        page,
        limit,
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
