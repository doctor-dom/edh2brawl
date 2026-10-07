"""Unofficial EDHREC JSON cache for commander synergy hints."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx

EDHREC_BASE = "https://json.edhrec.com/pages/commanders"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "edhrec"
CACHE_TTL_SECONDS = 7 * 24 * 3600


def commander_slug(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s


def _inclusion_from_view(view: dict[str, Any]) -> float:
    if view.get("inclusion") is not None:
        try:
            return float(view["inclusion"])
        except (TypeError, ValueError):
            pass
    num = view.get("num_decks")
    pot = view.get("potential_decks")
    if num is not None and pot and pot > 0:
        return float(num) / float(pot)
    return 0.0


def _parse_response(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    cardlists = payload.get("container", {}).get("json_dict", {}).get("cardlists") or []
    out: dict[str, dict[str, float]] = {}
    for block in cardlists:
        for view in block.get("cardviews") or []:
            name = view.get("name")
            if not name or name in out:
                continue
            synergy = view.get("synergy")
            try:
                syn_f = float(synergy) if synergy is not None else 0.0
            except (TypeError, ValueError):
                syn_f = 0.0
            out[name] = {
                "synergy": syn_f,
                "inclusion": _inclusion_from_view(view),
            }
    return out


def _cache_path(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.json"


def _read_cache(slug: str) -> Optional[dict[str, dict[str, float]]]:
    path = _cache_path(slug)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(raw.get("fetched_at", 0))
        if time.time() - fetched_at > CACHE_TTL_SECONDS:
            return None
        data = raw.get("cards")
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _write_cache(slug: str, cards: dict[str, dict[str, float]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(slug)
    path.write_text(
        json.dumps({"fetched_at": time.time(), "cards": cards}, ensure_ascii=False),
        encoding="utf-8",
    )


def fetch_commander_synergy(commander_name: str, client: Optional[httpx.Client] = None) -> tuple[dict[str, dict[str, float]], str]:
    """Return (name -> {synergy, inclusion}, status). status is ok, cached, unavailable."""
    if not commander_name:
        return {}, "unavailable"
    slug = commander_slug(commander_name)
    if not slug:
        return {}, "unavailable"
    cached = _read_cache(slug)
    if cached is not None:
        return cached, "cached"

    url = f"{EDHREC_BASE}/{slug}.json"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=12.0)
    try:
        resp = client.get(url, headers={"Accept": "application/json", "User-Agent": "edh2brawl/0.1"})
        if resp.status_code != 200:
            return {}, "unavailable"
        cards = _parse_response(resp.json())
        _write_cache(slug, cards)
        return cards, "ok"
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError):
        return {}, "unavailable"
    finally:
        if own_client and client is not None:
            client.close()


def edhrec_score_bonus(
    card_name: str,
    synergy_map: dict[str, dict[str, float]],
) -> tuple[float, Optional[str]]:
    entry = synergy_map.get(card_name)
    if not entry:
        return 0.0, None
    synergy = entry.get("synergy") or 0.0
    inclusion = entry.get("inclusion") or 0.0
    if synergy > 0:
        bonus = min(0.10, (synergy / 40.0) * 0.10)
        return bonus, f"EDHREC synergy ({synergy:.0f})"
    if inclusion > 0:
        bonus = min(0.05, inclusion * 0.05)
        return bonus, f"EDHREC staple ({inclusion * 100:.0f}% decks)"
    return 0.0, None
