"""Scryfall bulk download and SQLite card index."""

from __future__ import annotations

import gzip
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

BULK_META_URL = "https://api.scryfall.com/bulk-data"
DEFAULT_CARDS_TYPE = "default_cards"
INDEX_VERSION = "2"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "cards.db"
META_PATH = DATA_DIR / "bulk_meta.json"

RARITY_WILDCARD = {"common": 1, "uncommon": 2, "rare": 4, "mythic": 6, "special": 4}


@dataclass(frozen=True)
class CardRecord:
    name: str
    oracle_text: str
    mana_value: float
    type_line: str
    keywords: str
    color_identity: str
    power: Optional[str]
    toughness: Optional[str]
    brawl: str
    competitivebrawl: str
    image_url: str
    rarity: str
    is_brawler: bool
    layout: str

    @property
    def wildcard_cost(self) -> int:
        return RARITY_WILDCARD.get(self.rarity, 4)

    def legality(self, format_key: str) -> str:
        if format_key == "competitivebrawl":
            return self.competitivebrawl or self.brawl
        return self.brawl


def normalize_name(name: str, known_names: set[str] | None = None) -> str:
    n = name.strip()
    if n.startswith("A-") and (known_names is None or n[2:] in known_names):
        return n[2:]
    return n


def _front_face(card: dict[str, Any]) -> dict[str, Any]:
    if card.get("layout") in ("transform", "modal_dfc", "double_faced_token", "reversible_card"):
        faces = card.get("card_faces") or []
        if faces:
            merged = dict(card)
            for key in ("name", "mana_cost", "type_line", "oracle_text", "power", "toughness", "image_uris"):
                if key in faces[0]:
                    merged[key] = faces[0][key]
            return merged
    return card


def _image_url(card: dict[str, Any]) -> str:
    uris = card.get("image_uris") or {}
    if uris.get("normal"):
        return uris["normal"]
    faces = card.get("card_faces") or []
    if faces and faces[0].get("image_uris"):
        return faces[0]["image_uris"].get("normal", "")
    return ""


def _is_brawler(card: dict[str, Any]) -> bool:
    type_line = (card.get("type_line") or "").lower()
    if "legendary creature" in type_line:
        return True
    if "legendary planeswalker" in type_line:
        return True
    if "legendary vehicle" in type_line:
        return True
    if "legendary spacecraft" in type_line:
        return True
    return False


def _parse_card_row(card: dict[str, Any]) -> Optional[tuple[str, CardRecord, str]]:
    if card.get("lang") != "en":
        return None
    front = _front_face(card)
    name = front.get("name") or card.get("name")
    if not name:
        return None
    legalities = card.get("legalities") or {}
    released = card.get("released_at") or ""
    oracle_text = (front.get("oracle_text") or "").replace("\n", " ")
    record = CardRecord(
        name=name,
        oracle_text=oracle_text,
        mana_value=float(card.get("cmc") or 0),
        type_line=front.get("type_line") or card.get("type_line") or "",
        keywords=",".join(card.get("keywords") or []),
        color_identity="".join(sorted(card.get("color_identity") or [])),
        power=front.get("power") or card.get("power"),
        toughness=front.get("toughness") or card.get("toughness"),
        brawl=legalities.get("brawl") or legalities.get("historicbrawl") or "not_legal",
        competitivebrawl=legalities.get("competitivebrawl") or legalities.get("brawl") or "not_legal",
        image_url=_image_url(front if front != card else card),
        rarity=card.get("rarity") or "rare",
        is_brawler=_is_brawler(front if front != card else card),
        layout=card.get("layout") or "normal",
    )
    return name, record, released


class CardIndex:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self.connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cards (
                name TEXT PRIMARY KEY,
                oracle_text TEXT NOT NULL,
                mana_value REAL NOT NULL,
                type_line TEXT NOT NULL,
                keywords TEXT NOT NULL,
                color_identity TEXT NOT NULL,
                power TEXT,
                toughness TEXT,
                brawl TEXT NOT NULL,
                competitivebrawl TEXT NOT NULL,
                image_url TEXT NOT NULL,
                rarity TEXT NOT NULL,
                is_brawler INTEGER NOT NULL,
                layout TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cards_brawl ON cards(brawl);
            CREATE INDEX IF NOT EXISTS idx_cards_ci ON cards(color_identity);
            """
        )
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def row_count(self) -> int:
        row = self.connect().execute("SELECT COUNT(*) AS c FROM cards").fetchone()
        return int(row["c"]) if row else 0

    def get_meta(self, key: str) -> Optional[str]:
        row = self.connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.connect().execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connect().commit()

    def needs_refresh(self) -> bool:
        if self.get_meta("index_version") != INDEX_VERSION:
            return True
        if self.row_count() == 0:
            return True
        stored = self.get_meta("bulk_updated_at")
        if not stored:
            return True
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(BULK_META_URL)
                resp.raise_for_status()
                for entry in resp.json().get("data", []):
                    if entry.get("type") == DEFAULT_CARDS_TYPE:
                        return entry.get("updated_at") != stored
        except httpx.HTTPError:
            return False
        return False

    def refresh_if_needed(self, force: bool = False) -> bool:
        if self.get_meta("index_version") != INDEX_VERSION:
            bulk_path = DATA_DIR / "default_cards.jsonl.gz"
            if bulk_path.exists():
                self._ingest_bulk(bulk_path)
                self.set_meta("index_version", INDEX_VERSION)
                return True
        if force or self.needs_refresh():
            return self.build_from_bulk()
        return False

    def build_from_bulk(self, force_download: bool = False) -> bool:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        bulk_path = DATA_DIR / "default_cards.jsonl.gz"
        version_stale = self.get_meta("index_version") != INDEX_VERSION
        if force_download or not bulk_path.exists():
            self._download_and_store_bulk(bulk_path)
        elif not version_stale and self._scryfall_bulk_stale():
            self._download_and_store_bulk(bulk_path)
        self._ingest_bulk(bulk_path)
        self.set_meta("index_version", INDEX_VERSION)
        return True

    def _scryfall_bulk_stale(self) -> bool:
        stored = self.get_meta("bulk_updated_at")
        if not stored:
            return True
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(BULK_META_URL)
                resp.raise_for_status()
                for entry in resp.json().get("data", []):
                    if entry.get("type") == DEFAULT_CARDS_TYPE:
                        return entry.get("updated_at") != stored
        except httpx.HTTPError:
            return False
        return False

    def _download_and_store_bulk(self, bulk_path: Path) -> None:
        download_uri, updated_at = self._fetch_bulk_uri()
        self._download_bulk(download_uri, bulk_path)
        self.set_meta("bulk_updated_at", updated_at)

    def _fetch_bulk_uri(self) -> tuple[str, str]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(BULK_META_URL)
            resp.raise_for_status()
            for entry in resp.json().get("data", []):
                if entry.get("type") == DEFAULT_CARDS_TYPE:
                    uri = entry.get("jsonl_download_uri")
                    if not uri:
                        raise RuntimeError("Scryfall default_cards bulk entry has no jsonl_download_uri")
                    return uri, entry.get("updated_at", "")
        raise RuntimeError("Scryfall default_cards bulk entry not found")

    def _download_bulk(self, uri: str, dest: Path) -> None:
        with httpx.Client(timeout=None, follow_redirects=True) as client:
            with client.stream("GET", uri) as resp:
                resp.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes(1024 * 1024):
                        f.write(chunk)

    def _ingest_bulk(self, bulk_path: Path) -> None:
        best: dict[str, tuple[CardRecord, str]] = {}
        with gzip.open(bulk_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                card = json.loads(line)
                if not isinstance(card, dict):
                    continue
                parsed = _parse_card_row(card)
                if not parsed:
                    continue
                name, record, released = parsed
                prev = best.get(name)
                if prev is None or released > prev[1]:
                    best[name] = (record, released)

        conn = self.connect()
        conn.execute("DELETE FROM cards")
        conn.executemany(
            """
            INSERT INTO cards(
                name, oracle_text, mana_value, type_line, keywords, color_identity,
                power, toughness, brawl, competitivebrawl, image_url, rarity,
                is_brawler, layout
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    r.name,
                    r.oracle_text,
                    r.mana_value,
                    r.type_line,
                    r.keywords,
                    r.color_identity,
                    r.power,
                    r.toughness,
                    r.brawl,
                    r.competitivebrawl,
                    r.image_url,
                    r.rarity,
                    1 if r.is_brawler else 0,
                    r.layout,
                )
                for r, _ in best.values()
            ],
        )
        conn.commit()
        self.set_meta("index_version", INDEX_VERSION)

    def get(self, name: str) -> Optional[CardRecord]:
        row = self.connect().execute("SELECT * FROM cards WHERE name = ?", (name,)).fetchone()
        if not row:
            alt = normalize_name(name)
            if alt != name:
                row = self.connect().execute("SELECT * FROM cards WHERE name = ?", (alt,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_many(self, names: list[str]) -> dict[str, CardRecord]:
        out: dict[str, CardRecord] = {}
        for n in names:
            c = self.get(n)
            if c:
                out[n] = c
        return out

    def all_names(self) -> set[str]:
        rows = self.connect().execute("SELECT name FROM cards").fetchall()
        return {r["name"] for r in rows}

    def search_names(self, query: str, limit: int = 20) -> list[str]:
        q = f"%{query.strip()}%"
        rows = self.connect().execute(
            "SELECT name FROM cards WHERE name LIKE ? ORDER BY name LIMIT ?",
            (q, limit),
        ).fetchall()
        return [r["name"] for r in rows]

    def iter_candidates(
        self,
        format_key: str,
        color_identity: str,
        exclude: set[str],
    ) -> Iterator[CardRecord]:
        leg_col = "competitivebrawl" if format_key == "competitivebrawl" else "brawl"
        sql = f"""
            SELECT * FROM cards
            WHERE {leg_col} = 'legal'
        """
        for row in self.connect().execute(sql):
            rec = self._row_to_record(row)
            if rec.name in exclude:
                continue
            if not _fits_color_identity(rec.color_identity, color_identity):
                continue
            yield rec

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CardRecord:
        return CardRecord(
            name=row["name"],
            oracle_text=row["oracle_text"],
            mana_value=row["mana_value"],
            type_line=row["type_line"],
            keywords=row["keywords"],
            color_identity=row["color_identity"],
            power=row["power"],
            toughness=row["toughness"],
            brawl=row["brawl"],
            competitivebrawl=row["competitivebrawl"],
            image_url=row["image_url"],
            rarity=row["rarity"],
            is_brawler=bool(row["is_brawler"]),
            layout=row["layout"],
        )


def _fits_color_identity(card_ci: str, deck_ci: str) -> bool:
    if not deck_ci:
        return not card_ci
    return all(c in deck_ci for c in card_ci)


_index: Optional[CardIndex] = None


def get_index() -> CardIndex:
    global _index
    if _index is None:
        _index = CardIndex()
        _index.connect()
    return _index
