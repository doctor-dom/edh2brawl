from app.pick_refresh import slots_to_refresh_on_pick


def _row(slot: str, suggestions: list[str], link_group: str | None = None) -> dict:
    return {
        "slot": slot,
        "link_group": link_group,
        "suggestions": [{"name": n} for n in suggestions],
    }


def test_pick_unseen_card_refreshes_no_other_slots():
    rows = [
        _row("main:A#0", ["X", "Y"]),
        _row("main:B#0", ["P", "Q"]),
    ]
    assert slots_to_refresh_on_pick("main:A#0", "Z", None, rows, {}) == []


def test_pick_shown_on_another_row_refreshes_that_row_only():
    rows = [
        _row("main:A#0", ["Shared", "Y"]),
        _row("main:B#0", ["Shared", "Q"]),
        _row("main:C#0", ["P", "Q"]),
    ]
    got = slots_to_refresh_on_pick("main:A#0", "Shared", None, rows, {})
    assert got == ["main:B#0"]


def test_linked_pending_sibling_always_refreshes():
    rows = [
        _row("main:A#0", ["X"], link_group="dup"),
        _row("main:A#1", ["Y"], link_group="dup"),
    ]
    got = slots_to_refresh_on_pick("main:A#0", "Z", "dup", rows, {"main:A#0": "Z"})
    assert got == ["main:A#1"]


def test_already_picked_slots_skipped():
    rows = [
        _row("main:A#0", ["Shared"]),
        _row("main:B#0", ["Shared"]),
    ]
    replacements = {"main:B#0": "Other"}
    assert slots_to_refresh_on_pick("main:A#0", "Shared", None, rows, replacements) == []
