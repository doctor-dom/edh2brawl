"""DeckCheck public deck sample for commander card inclusion hints."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import httpx

from app.edhrec import commander_slug

DECKCHECK_SEARCH_URL = "https://deckcheck.co/api/external/deck-search"
DECKCHECK_DECK_URL = "https://deckcheck.co/api/external/deck"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "deckcheck"
CACHE_TTL_SECONDS = 7 * 24 * 3600
SAMPLE_DECK_COUNT = 15


def _cache_path(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.json"


def _read_cache(slug: str) -> Optional[dict[str, float]]:
    path = _cache_path(slug)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(raw.get("fetched_at", 0))
        if time.time() - fetched_at > CACHE_TTL_SECONDS:
            return None
        cards = raw.get("cards")
        if isinstance(cards, dict):
            return {k: float(v) for k, v in cards.items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _write_cache(slug: str, cards: dict[str, float]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(slug)
    path.write_text(
        json.dumps({"fetched_at": time.time(), "cards": cards}, ensure_ascii=False),
        encoding="utf-8",
    )


def _mainboard_names(payload: dict[str, Any]) -> set[str]:
    boards = payload.get("boards") or {}
    main = boards.get("mainboard") or {}
    cards = main.get("cards") or {}
    names: set[str] = set()
    for entry in cards.values():
        if not isinstance(entry, dict):
            continue
        card = entry.get("card") or {}
        name = card.get("name")
        if name:
            names.add(name)
    return names


def _search_deck_ids(commander_name: str, client: httpx.Client, api_key: str) -> list[str]:
    params = {
        "commander": commander_name,
        "formats": "commander",
        "sort": "power",
        "sort_direction": "desc",
        "per_page": SAMPLE_DECK_COUNT,
        "page": 1,
    }
    resp = client.get(
        DECKCHECK_SEARCH_URL,
        params=params,
        headers={"X-Api-Key": api_key, "Accept": "application/json", "User-Agent": "edh2brawl/0.1"},
    )
    if resp.status_code != 200:
        return []
    body = resp.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids[:SAMPLE_DECK_COUNT]


def _fetch_deck_names(deck_id: str, client: httpx.Client) -> set[str]:
    resp = client.get(
        DECKCHECK_DECK_URL,
        params={"deck_id": deck_id},
        headers={"Accept": "application/json", "User-Agent": "edh2brawl/0.1"},
        timeout=12.0,
    )
    if resp.status_code != 200:
        return set()
    try:
        return _mainboard_names(resp.json())
    except (json.JSONDecodeError, TypeError):
        return set()


def fetch_commander_inclusion(
    commander_name: str,
    client: Optional[httpx.Client] = None,
) -> tuple[dict[str, float], str]:
    """Return (card name -> inclusion rate 0–1, status). status is ok, cached, or unavailable."""
    if not commander_name:
        return {}, "unavailable"
    slug = commander_slug(commander_name)
    if not slug:
        return {}, "unavailable"
    cached = _read_cache(slug)
    if cached is not None:
        return cached, "cached"

    api_key = (os.environ.get("DECKCHECK_API_KEY") or "").strip()
    if not api_key:
        return {}, "unavailable"

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=15.0)
    try:
        deck_ids = _search_deck_ids(commander_name, client, api_key)
        if not deck_ids:
            return {}, "unavailable"
        counts: Counter[str] = Counter()
        decks_with_data = 0
        for deck_id in deck_ids:
            names = _fetch_deck_names(deck_id, client)
            if not names:
                continue
            decks_with_data += 1
            for name in names:
                counts[name] += 1
        if decks_with_data == 0:
            return {}, "unavailable"
        inclusion = {name: count / decks_with_data for name, count in counts.items()}
        _write_cache(slug, inclusion)
        return inclusion, "ok"
    except httpx.HTTPError:
        return {}, "unavailable"
    finally:
        if own_client and client is not None:
            client.close()
