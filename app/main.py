"""FastAPI application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.cards import get_index
from app.collection import parse_collection_csv
from app.deck import parse_decklist
from app.legality import (
    COMMANDER_SLOT_KEY,
    analyze_deck,
    analyze_sideboard,
    build_analysis_snapshot,
    build_export_sideboard,
    build_partial_deck,
    decide_later_illegal_names,
    pending_replacement_slots,
    build_final_deck,
    build_sideboard_suggestion_rows,
    build_suggestion_payload,
    display_image_url,
    refresh_suggestion_slots,
    resolve_name,
    _deck_analysis_from_snapshot,
)
from app.deckcheck import fetch_commander_inclusion
from app.edhrec import fetch_commander_synergy
from app.suggest import suggest_replacements

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="edh2brawl", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AnalyzeRequest(BaseModel):
    decklist: str
    format_key: str = Field(default="brawl", pattern="^(brawl|competitivebrawl)$")
    commander: Optional[str] = None
    replacements: dict[str, str] = Field(default_factory=dict)
    role_overrides: dict[str, str] = Field(default_factory=dict)
    rolls: dict[str, int] = Field(default_factory=dict)
    cheap_overrides: dict[str, bool] = Field(default_factory=dict)
    edhrec_overrides: dict[str, bool] = Field(default_factory=dict)
    pool_names_by_slot: dict[str, list[str]] = Field(default_factory=dict)
    attribute_overrides: dict[str, dict[str, bool]] = Field(default_factory=dict)
    query_overrides: dict[str, str] = Field(default_factory=dict)
    prefer_collection: bool = False
    minimize_wildcards: bool = False
    owned: dict[str, int] = Field(default_factory=dict)
    include_suggestions: bool = True


class SuggestSlotsRequest(BaseModel):
    snapshot: dict[str, Any]
    slots: list[str]
    format_key: str = Field(default="brawl", pattern="^(brawl|competitivebrawl)$")
    replacements: dict[str, str] = Field(default_factory=dict)
    role_overrides: dict[str, str] = Field(default_factory=dict)
    rolls: dict[str, int] = Field(default_factory=dict)
    cheap_overrides: dict[str, bool] = Field(default_factory=dict)
    edhrec_overrides: dict[str, bool] = Field(default_factory=dict)
    pool_names_by_slot: dict[str, list[str]] = Field(default_factory=dict)
    attribute_overrides: dict[str, dict[str, bool]] = Field(default_factory=dict)
    query_overrides: dict[str, str] = Field(default_factory=dict)
    prefer_collection: bool = False
    minimize_wildcards: bool = False
    owned: dict[str, int] = Field(default_factory=dict)


class FinalizeRequest(BaseModel):
    snapshot: dict[str, Any]
    format_key: str = Field(default="brawl", pattern="^(brawl|competitivebrawl)$")
    replacements: dict[str, str] = Field(default_factory=dict)
    owned: dict[str, int] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    format_key: str = "brawl"
    color_identity: str = ""
    exclude: list[str] = Field(default_factory=list)
    limit: int = 15


@app.on_event("startup")
def startup() -> None:
    idx = get_index()
    try:
        idx.refresh_if_needed()
    except Exception:
        pass


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    idx = get_index()
    return {
        "cards_indexed": idx.row_count(),
        "bulk_updated_at": idx.get_meta("bulk_updated_at"),
    }


@app.post("/api/index/refresh")
def refresh_index(force: bool = True) -> dict[str, Any]:
    idx = get_index()
    try:
        idx.build_from_bulk(force_download=force) if force else idx.refresh_if_needed()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"cards_indexed": idx.row_count(), "bulk_updated_at": idx.get_meta("bulk_updated_at")}


@app.post("/api/collection/parse")
async def parse_collection(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    idx = get_index()
    known = idx.all_names() if idx.row_count() else set()
    try:
        result = parse_collection_csv(raw, known)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "format": result.format_detected,
        "row_count": result.row_count,
        "unique_cards": len(result.owned),
        "owned": result.owned,
    }


@app.post("/api/analyze")
def analyze(body: AnalyzeRequest) -> dict[str, Any]:
    idx = get_index()
    if idx.row_count() == 0:
        raise HTTPException(
            status_code=503,
            detail="Card database not ready. Call POST /api/index/refresh or wait for startup download.",
        )

    parsed = parse_decklist(body.decklist, commander_hint=body.commander)
    commander_name = body.replacements.get(COMMANDER_SLOT_KEY) or body.commander
    if not commander_name and parsed.commander_names:
        commander_name = parsed.commander_names[0]

    analysis = analyze_deck(idx, parsed, body.format_key, commander_override=commander_name or None)
    sideboard_review = analyze_sideboard(
        idx,
        parsed.sideboard,
        body.format_key,
        analysis.color_identity,
    )

    edhrec_map: dict[str, dict[str, float]] = {}
    edhrec_status = "unavailable"
    commander_for_edhrec = body.replacements.get(COMMANDER_SLOT_KEY) or analysis.commander_name
    deckcheck_map: dict[str, float] = {}
    deckcheck_status = "unavailable"
    if commander_for_edhrec:
        edhrec_map, edhrec_status = fetch_commander_synergy(commander_for_edhrec)
        deckcheck_map, deckcheck_status = fetch_commander_inclusion(commander_for_edhrec)
    edhrec_available = edhrec_status in ("ok", "cached") and bool(edhrec_map)
    deckcheck_available = deckcheck_status in ("ok", "cached") and bool(deckcheck_map)

    rows: list[dict] = []
    sideboard_rows: list[dict] = []
    if body.include_suggestions:
        rows = build_suggestion_payload(
            idx,
            analysis,
            body.format_key,
            body.owned,
            body.replacements,
            body.prefer_collection,
            body.minimize_wildcards,
            body.role_overrides,
            body.rolls,
            body.cheap_overrides,
            body.attribute_overrides,
            body.query_overrides,
            edhrec_map,
            body.pool_names_by_slot,
            edhrec_available,
            deckcheck_map,
            deckcheck_available,
            sideboard_review,
        )
        sideboard_rows = build_sideboard_suggestion_rows(
            idx,
            sideboard_review,
            analysis,
            body.format_key,
            body.owned,
            body.replacements,
            body.prefer_collection,
            body.minimize_wildcards,
            body.role_overrides,
            body.rolls,
            body.cheap_overrides,
            body.attribute_overrides,
            body.query_overrides,
            edhrec_map,
            body.pool_names_by_slot,
            edhrec_available,
            deckcheck_map,
            deckcheck_available,
        )

    pending = []
    for r in rows:
        slot = r.get("slot")
        if slot == COMMANDER_SLOT_KEY:
            if COMMANDER_SLOT_KEY not in body.replacements:
                pending.append(r)
        elif slot not in body.replacements:
            pending.append(r)
    for r in sideboard_rows:
        if r.get("slot") not in body.replacements:
            pending.append(r)

    snapshot = build_analysis_snapshot(analysis, sideboard_review)

    final = None
    wildcard_summary = None
    if not pending:
        cmd_name = body.replacements.get(COMMANDER_SLOT_KEY, analysis.commander_name)
        final = build_final_deck(analysis, body.replacements, idx, new_commander_name=cmd_name or None)
        final["sideboard"] = build_export_sideboard(sideboard_review, body.replacements, idx)
        wildcard_summary = _wildcard_summary(final, body.owned, idx)

    return {
        "commander": analysis.commander_name,
        "commander_candidates": analysis.commander_candidates,
        "color_identity": analysis.color_identity,
        "deck_size": analysis.deck_size,
        "expected_size": analysis.expected_size,
        "illegal_count": len(rows) if body.include_suggestions else len(analysis.illegal),
        "replacement_rows": rows,
        "replacements": body.replacements,
        "final_deck": final,
        "wildcard_summary": wildcard_summary,
        "has_collection": bool(body.owned),
        "sideboard_count": len(parsed.sideboard),
        "sideboard_review": sideboard_review,
        "sideboard_replacement_rows": sideboard_rows,
        "arena_export": _arena_export(final) if final else None,
        "edhrec": edhrec_status,
        "deckcheck": deckcheck_status,
        "analysis_snapshot": snapshot,
    }


@app.post("/api/suggest/slots")
def suggest_slots(body: SuggestSlotsRequest) -> dict[str, Any]:
    idx = get_index()
    if idx.row_count() == 0:
        raise HTTPException(status_code=503, detail="Card database not ready.")
    edhrec_map: dict[str, dict[str, float]] = {}
    edhrec_status = "unavailable"
    commander_name = body.snapshot.get("commander_name") or body.replacements.get(COMMANDER_SLOT_KEY)
    deckcheck_map: dict[str, float] = {}
    deckcheck_status = "unavailable"
    if commander_name:
        edhrec_map, edhrec_status = fetch_commander_synergy(commander_name)
        deckcheck_map, deckcheck_status = fetch_commander_inclusion(commander_name)
    edhrec_available = edhrec_status in ("ok", "cached") and bool(edhrec_map)
    deckcheck_available = deckcheck_status in ("ok", "cached") and bool(deckcheck_map)
    rows = refresh_suggestion_slots(
        idx,
        body.snapshot,
        body.slots,
        body.format_key,
        body.replacements,
        body.owned,
        body.prefer_collection,
        body.minimize_wildcards,
        body.role_overrides,
        body.rolls,
        body.cheap_overrides,
        body.attribute_overrides,
        body.query_overrides,
        edhrec_map,
        body.pool_names_by_slot,
        edhrec_available,
        deckcheck_map,
        deckcheck_available,
    )
    return {"rows": rows, "edhrec": edhrec_status, "deckcheck": deckcheck_status}


class PartialExportRequest(BaseModel):
    snapshot: dict[str, Any]
    format_key: str = Field(default="brawl", pattern="^(brawl|competitivebrawl)$")
    replacements: dict[str, str] = Field(default_factory=dict)
    deferred_slots: list[str] = Field(default_factory=list)


@app.post("/api/export/partial")
def export_partial(body: PartialExportRequest) -> dict[str, Any]:
    idx = get_index()
    analysis = _deck_analysis_from_snapshot(idx, body.snapshot)
    sideboard_review = body.snapshot.get("sideboard_review") or []
    deferred = set(body.deferred_slots)
    partial = build_partial_deck(analysis, body.replacements, idx, sideboard_review, deferred)
    pending = pending_replacement_slots(body.snapshot, body.replacements, deferred)
    decide_later = decide_later_illegal_names(body.snapshot, deferred)
    return {
        "partial_deck": partial,
        "arena_export": _arena_export(partial, pending_slots=pending, decide_later=decide_later),
        "pending_slots": pending,
        "pending_count": len(pending),
        "decide_later": decide_later,
    }


@app.post("/api/finalize")
def finalize_deck(body: FinalizeRequest) -> dict[str, Any]:
    idx = get_index()
    analysis = _deck_analysis_from_snapshot(idx, body.snapshot)
    sideboard_review = body.snapshot.get("sideboard_review") or []
    from app.legality import _slot_specs_from_snapshot

    all_slots = _slot_specs_from_snapshot(body.snapshot)
    if any(slot not in body.replacements for slot in all_slots):
        return {"final_deck": None, "wildcard_summary": None, "arena_export": None, "pending": True}

    cmd_name = body.replacements.get(COMMANDER_SLOT_KEY, analysis.commander_name)
    final = build_final_deck(analysis, body.replacements, idx, new_commander_name=cmd_name or None)
    final["sideboard"] = build_export_sideboard(sideboard_review, body.replacements, idx)
    wildcard_summary = _wildcard_summary(final, body.owned, idx)
    return {
        "final_deck": final,
        "wildcard_summary": wildcard_summary,
        "arena_export": _arena_export(final),
        "pending": False,
    }


@app.post("/api/search")
def search_cards(body: SearchRequest) -> dict[str, Any]:
    idx = get_index()
    names = idx.search_names(body.query, limit=body.limit * 3)
    exclude = set(body.exclude)
    out = []
    for name in names:
        if name in exclude:
            continue
        rec = idx.get(name)
        if not rec:
            continue
        if rec.legality(body.format_key) != "legal":
            continue
        if body.color_identity and not all(c in body.color_identity for c in rec.color_identity):
            continue
        out.append(
            {
                "name": rec.name,
                "mana_value": rec.mana_value,
                "type_line": rec.type_line,
                "image_url": display_image_url(rec.name, rec.image_url),
                "rarity": rec.rarity,
            }
        )
        if len(out) >= body.limit:
            break
    return {"results": out}


@app.post("/api/suggest/one")
def suggest_one(
    illegal_name: str = Form(...),
    format_key: str = Form("brawl"),
    color_identity: str = Form(""),
    exclude_json: str = Form("[]"),
    owned_json: str = Form("{}"),
    prefer_collection: bool = Form(False),
    minimize_wildcards: bool = Form(False),
) -> dict[str, Any]:
    idx = get_index()
    source = resolve_name(idx, illegal_name)
    if not source:
        raise HTTPException(status_code=404, detail="Card not found")
    exclude = set(json.loads(exclude_json))
    owned = json.loads(owned_json)
    run = suggest_replacements(
        idx,
        source,
        format_key,
        color_identity,
        exclude,
        owned,
        prefer_collection,
        minimize_wildcards,
    )
    from app.legality import _suggestion_json

    return {
        "suggestions": [_suggestion_json(s) for s in run.suggestions],
        "query_error": run.query_error,
        "query_matched": run.query_matched,
    }


def _wildcard_summary(final: dict, owned: dict[str, int], idx) -> dict[str, int]:
    from app.collection import is_owned
    from app.suggest import singleton_exempt

    counts = {"common": 0, "uncommon": 0, "rare": 0, "mythic": 0, "owned": 0}
    cards = []
    if final.get("commander"):
        cards.append(final["commander"]["name"])
    cards.extend(c["name"] for c in final.get("main", []))
    cards.extend(c["name"] for c in final.get("sideboard", []))
    for name in cards:
        rec = idx.get(name)
        if rec and singleton_exempt(rec):
            if is_owned(owned, name):
                counts["owned"] += 1
            continue
        if is_owned(owned, name):
            counts["owned"] += 1
            continue
        if not rec:
            continue
        r = rec.rarity
        if r in counts:
            counts[r] += 1
    return counts


def _arena_export(
    final: dict,
    pending_slots: list[str] | None = None,
    decide_later: list[str] | None = None,
) -> str:
    lines: list[str] = []
    if final.get("commander"):
        lines.append("Commander")
        lines.append(f"1 {final['commander']['name']}")
        lines.append("")
    lines.append("Deck")
    for c in final.get("main", []):
        lines.append(f"1 {c['name']}")
    sideboard = final.get("sideboard") or []
    if sideboard:
        lines.append("")
        lines.append("Sideboard")
        for c in sideboard:
            lines.append(f"1 {c['name']}")
    if decide_later:
        lines.append("")
        lines.append("# Decide later:")
        for name in decide_later:
            lines.append(f"#   {name}")
    if pending_slots:
        lines.append("")
        lines.append(f"# Incomplete: {len(pending_slots)} slot(s) still need an Arena replacement.")
        specs_pending = pending_slots
        for slot in specs_pending:
            lines.append(f"#   {slot}")
    return "\n".join(lines)
