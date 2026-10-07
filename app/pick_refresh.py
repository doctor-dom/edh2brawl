"""Rules for which suggestion slots to refresh after a replacement pick."""

from __future__ import annotations


def slots_to_refresh_on_pick(
    slot_key: str,
    card_name: str,
    link_group: str | None,
    rows: list[dict],
    replacements: dict[str, str],
) -> list[str]:
    """Mirror of client slotsToRefreshOnPick — never includes the picked slot."""
    refresh: set[str] = set()
    for row in rows:
        sk = row.get("slot") or ""
        if sk == slot_key or not sk:
            continue
        if replacements.get(sk):
            continue
        if link_group and row.get("link_group") == link_group:
            refresh.add(sk)
            continue
        suggestions = row.get("suggestions") or []
        if any(s.get("name") == card_name for s in suggestions):
            refresh.add(sk)
    return sorted(refresh)
