import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.deckcheck import fetch_commander_inclusion


def test_fetch_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("DECKCHECK_API_KEY", raising=False)
    cards, status = fetch_commander_inclusion("Atraxa, Praetors' Voice")
    assert status == "unavailable"
    assert cards == {}


def test_fetch_parses_deck_sample(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKCHECK_API_KEY", "test-key")
    cache_dir = tmp_path / "deckcheck"
    monkeypatch.setattr("app.deckcheck.CACHE_DIR", cache_dir)

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {"data": [{"id": "deck1"}]}

    deck_resp = MagicMock()
    deck_resp.status_code = 200
    deck_resp.json.return_value = {
        "boards": {
            "mainboard": {
                "cards": {
                    "a": {"card": {"name": "Sol Ring"}},
                    "b": {"card": {"name": "Counterspell"}},
                }
            }
        }
    }

    client = MagicMock()
    client.get.side_effect = [search_resp, deck_resp]

    cards, status = fetch_commander_inclusion("Atraxa", client=client)
    assert status == "ok"
    assert cards["Sol Ring"] == 1.0
    assert cards["Counterspell"] == 1.0

    cached, cached_status = fetch_commander_inclusion("Atraxa", client=client)
    assert cached_status == "cached"
    assert cached == cards
    assert client.get.call_count == 2
