"""MTGA collection CSV import."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import BinaryIO, TextIO


@dataclass
class CollectionParseResult:
    owned: dict[str, int]
    format_detected: str
    row_count: int


NAME_HEADERS = frozenset(
    {"name", "card", "card name", "cardname", "simple name", "product name"}
)
QTY_HEADERS = frozenset({"count", "quantity", "qty", "total quantity"})


def _normalize_header(h: str) -> str:
    return h.strip().lower()


def _pick_column(headers: list[str], candidates: frozenset[str]) -> str | None:
    norm = {_normalize_header(h): h for h in headers}
    for c in candidates:
        if c in norm:
            return norm[c]
    return None


def _strip_a_prefix(name: str, known: set[str]) -> str:
    n = name.strip()
    if n.startswith("A-") and n[2:] in known:
        return n[2:]
    return n


def parse_collection_csv(
    file_content: str | bytes,
    known_names: set[str] | None = None,
) -> CollectionParseResult:
    known = known_names or set()
    if isinstance(file_content, bytes):
        text = file_content.decode("utf-8-sig", errors="replace")
    else:
        text = file_content

    if not text.strip():
        return CollectionParseResult(owned={}, format_detected="empty", row_count=0)

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return CollectionParseResult(owned={}, format_detected="unknown", row_count=0)

    headers = list(reader.fieldnames)
    name_col = _pick_column(headers, NAME_HEADERS)
    qty_col = _pick_column(headers, QTY_HEADERS)
    if not name_col or not qty_col:
        raise ValueError(
            "Could not find card name and quantity columns. "
            "Expected headers like Name/Count or Card/Quantity."
        )

    fmt = _detect_format(headers)
    owned: dict[str, int] = {}
    rows = 0
    for row in reader:
        rows += 1
        raw_name = (row.get(name_col) or "").strip()
        if not raw_name:
            continue
        name = _strip_a_prefix(raw_name, known)
        try:
            qty = int(float((row.get(qty_col) or "0").strip() or "0"))
        except ValueError:
            qty = 0
        if qty <= 0:
            continue
        owned[name] = owned.get(name, 0) + qty

    return CollectionParseResult(owned=owned, format_detected=fmt, row_count=rows)


def _detect_format(headers: list[str]) -> str:
    h = {_normalize_header(x) for x in headers}
    if "edition" in h and "count" in h:
        return "moxfield"
    if "card" in h and "set" in h:
        return "mtggoldfish"
    if "card name" in h and "foil" in h:
        return "cardsphere"
    if "set code" in h:
        return "deckbox"
    return "generic"


def is_owned(owned: dict[str, int], name: str) -> bool:
    return owned.get(name, 0) >= 1
