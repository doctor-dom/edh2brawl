"""Local subset of Scryfall search syntax for suggestion filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.cards import CardRecord

COMP_RE = re.compile(r"^(mv|cmc|pow|tou)(=|!=|<=|>=|<|>)(-?\d+(?:\.\d+)?)$", re.I)
CMP_SPACE_RE = re.compile(r"\b(mv|cmc|pow|tou)\s*(<=|>=|!=|=|<|>)\s*", re.I)
FIELD_RE = re.compile(
    r"^(t|type|o|oracle|keyword|kw|r|is):(.+)$",
    re.I,
)


@dataclass
class ParseError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def _parse_pt(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    v = value.strip()
    if v in ("*", "X", "∞"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _card_stat(card: CardRecord, field: str) -> Optional[float]:
    f = field.lower()
    if f in ("mv", "cmc"):
        return card.mana_value
    if f == "pow":
        return _parse_pt(card.power)
    if f == "tou":
        return _parse_pt(card.toughness)
    return None


def _type_line_lower(card: CardRecord) -> str:
    return (card.type_line or "").lower()


def _oracle_lower(card: CardRecord) -> str:
    return (card.oracle_text or "").lower()


def _keywords_lower(card: CardRecord) -> set[str]:
    return {k.strip().lower() for k in (card.keywords or "").split(",") if k.strip()}


def _match_is(card: CardRecord, kind: str) -> bool:
    k = kind.lower()
    tl = _type_line_lower(card)
    if k == "legendary":
        return "legendary" in tl
    if k in ("creature", "artifact", "enchantment", "instant", "sorcery", "planeswalker", "land"):
        return k in tl.split("—")[0]
    return False


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    q = query.strip()
    while i < len(q):
        while i < len(q) and q[i].isspace():
            i += 1
        if i >= len(q):
            break
        if q[i] == "(":
            tokens.append("(")
            i += 1
            continue
        if q[i] == ")":
            tokens.append(")")
            i += 1
            continue
        if q[i] == '"':
            i += 1
            start = i
            while i < len(q) and q[i] != '"':
                i += 1
            tokens.append(q[start:i])
            if i < len(q):
                i += 1
            continue
        start = i
        while i < len(q) and not q[i].isspace() and q[i] not in "()":
            if q[i] == ":" and i + 1 < len(q) and q[i + 1] == '"':
                i += 2
                while i < len(q) and q[i] != '"':
                    i += 1
                if i < len(q):
                    i += 1
                break
            i += 1
        tokens.append(q[start:i])
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[str]:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def pop(self) -> Optional[str]:
        t = self.peek()
        if t is not None:
            self.pos += 1
        return t

    def parse(self) -> Any:
        node = self.parse_or()
        if self.peek() is not None:
            raise ParseError(f"Unexpected token: {self.peek()}")
        return node

    def parse_or(self) -> Any:
        left = self.parse_and()
        while self.peek() and self.peek().lower() == "or":
            self.pop()
            right = self.parse_and()
            left = ("or", left, right)
        return left

    def parse_and(self) -> Any:
        left = self.parse_unary()
        while self.peek() is not None and self.peek() not in (")",) and self.peek().lower() != "or":
            right = self.parse_unary()
            left = ("and", left, right)
        return left

    def parse_unary(self) -> Any:
        if self.peek() == "-":
            self.pop()
            return ("not", self.parse_unary())
        if self.peek() == "(":
            self.pop()
            inner = self.parse_or()
            if self.pop() != ")":
                raise ParseError("Expected ')'")
            return inner
        return self.parse_atom()

    def _atom_from_tok(self, tok: str) -> Any:
        m = COMP_RE.match(tok)
        if m:
            return ("cmp", m.group(1).lower(), m.group(2), float(m.group(3)))
        m = FIELD_RE.match(tok)
        if m:
            field = m.group(1).lower()
            value = m.group(2).strip().lower()
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            return ("field", field, value)
        return ("word", tok.lower())

    def parse_atom(self) -> Any:
        tok = self.pop()
        if tok is None:
            raise ParseError("Unexpected end of query")
        if tok.startswith("-") and len(tok) > 1:
            return ("not", self._atom_from_tok(tok[1:]))
        return self._atom_from_tok(tok)


def _normalize_query(query: str) -> str:
    return CMP_SPACE_RE.sub(lambda m: f"{m.group(1).lower()}{m.group(2)}", query.strip())


def parse_query(query: str) -> Any:
    q = _normalize_query(query or "")
    if not q:
        raise ParseError("Empty query")
    tokens = _tokenize(q)
    if not tokens:
        raise ParseError("Empty query")
    return _Parser(tokens).parse()


def _eval_cmp(card: CardRecord, field: str, op: str, target: float) -> bool:
    val = _card_stat(card, field)
    if val is None:
        return False
    if op == "=":
        return val == target
    if op == "!=":
        return val != target
    if op == "<":
        return val < target
    if op == ">":
        return val > target
    if op == "<=":
        return val <= target
    if op == ">=":
        return val >= target
    return False


def _eval_field(card: CardRecord, field: str, value: str) -> bool:
    if field in ("t", "type"):
        return value in _type_line_lower(card)
    if field in ("o", "oracle"):
        return value in _oracle_lower(card) or value in _keywords_lower(card)
    if field in ("keyword", "kw"):
        return value in _keywords_lower(card)
    if field == "r":
        return (card.rarity or "").lower() == value
    if field == "is":
        return _match_is(card, value)
    return False


def _eval_node(card: CardRecord, node: Any) -> bool:
    kind = node[0]
    if kind == "and":
        return _eval_node(card, node[1]) and _eval_node(card, node[2])
    if kind == "or":
        return _eval_node(card, node[1]) or _eval_node(card, node[2])
    if kind == "not":
        return not _eval_node(card, node[1])
    if kind == "cmp":
        return _eval_cmp(card, node[1], node[2], node[3])
    if kind == "field":
        return _eval_field(card, node[1], node[2])
    if kind == "word":
        w = node[1]
        name = (card.name or "").lower()
        return w in name or w in _oracle_lower(card)
    return False


def compile_query(query: str) -> Any:
    return parse_query(query)


def card_matches(card: CardRecord, ast: Any) -> bool:
    return _eval_node(card, ast)
