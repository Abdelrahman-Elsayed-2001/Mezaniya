"""The dashboard's widgets and the user's dashboards (layouts).

Each widget is app/templates/widgets/<id>.html, a self-contained card that reads the loaded
budget (`d`) from the page's Alpine component. A user has one or more boards; a board is a list
of rows, and a row holds 1 to 3 widgets that share its width (on narrow screens they stack).
A widget may appear on several boards, or twice on one; it's simply absent where it's hidden.

All of it is one JSON value, the 'dashboards' setting:
    {"v": 1, "boards": [{"id": "main", "name": null, "rows": [["summary", ...], ...]}, ...],
     "seen": [every widget id that existed when the layout was saved]}
`seen` tells a widget added in a later version (show it, at the end of the first board) from
one the user hid (leave it hidden). A null name shows the default name in the viewer's language.
Only ids from WIDGETS are kept, so a stored layout can never name another template.
"""
from __future__ import annotations

import re

# id → strings.json key of its title (for the layout editor)
WIDGETS: dict[str, str] = {
    "summary": "summary.title",
    "suggestions": "suggestions.title",
    "warnings": "warnings.title",
    "incomes": "incomes.title",
    "emergency": "emergency.title",
    "savings": "savings.title",
    "expenses": "expenses.title",
    "goals": "goals.title",
    "payments": "payments.title",
    "by_type": "charts.by_type_title",
    "progress": "charts.progress_title",
    "daily": "charts.daily_title",
    "details": "details.title",
}

DEFAULT_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("summary", "suggestions", "warnings"),
    ("incomes", "emergency", "savings"),
    ("expenses",),
    ("goals",),
    ("payments", "by_type", "progress"),
    ("daily",),
    ("details",),
)

MAX_BOARDS = 10
MAX_ROWS = 40
MAX_PER_ROW = 3
MAX_WIDGETS_PER_BOARD = 40
MAX_NAME = 40
_BOARD_ID = re.compile(r"^[a-z0-9_-]{1,24}$")


def default_dashboards() -> dict:
    return {"v": 1, "boards": [{"id": "main", "name": None, "rows": [list(r) for r in DEFAULT_LAYOUT]}],
            "seen": list(WIDGETS)}


def validate(value) -> dict:
    """A layout sent by the user, checked strictly: ValueError names the first problem."""
    if not isinstance(value, dict) or not isinstance(value.get("boards"), list):
        raise ValueError("dashboards must be an object with a boards list")
    boards = value["boards"]
    if not 1 <= len(boards) <= MAX_BOARDS:
        raise ValueError(f"1 to {MAX_BOARDS} boards")
    out, ids = [], set()
    for b in boards:
        if not isinstance(b, dict):
            raise ValueError("a board must be an object")
        bid, name, rows = b.get("id"), b.get("name"), b.get("rows")
        if not isinstance(bid, str) or not _BOARD_ID.match(bid) or bid in ids:
            raise ValueError("board ids must be unique, 1-24 of a-z 0-9 _ -")
        ids.add(bid)
        if name is not None:
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
                raise ValueError(f"board names are 1-{MAX_NAME} characters")
            name = " ".join(name.split())
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise ValueError(f"at most {MAX_ROWS} rows")
        clean_rows = []
        for row in rows:
            if not isinstance(row, list) or not 1 <= len(row) <= MAX_PER_ROW:
                raise ValueError(f"a row holds 1 to {MAX_PER_ROW} widgets")
            if any(not isinstance(w, str) or w not in WIDGETS for w in row):
                raise ValueError("unknown widget")
            clean_rows.append(list(row))
        if sum(len(r) for r in clean_rows) > MAX_WIDGETS_PER_BOARD:
            raise ValueError(f"at most {MAX_WIDGETS_PER_BOARD} widgets on a board")
        out.append({"id": bid, "name": name, "rows": clean_rows})
    return {"v": 1, "boards": out, "seen": list(WIDGETS)}


def normalize(value) -> dict:
    """A stored layout, made safe to draw: never fails. Unknown widgets are dropped, broken
    boards skipped, and widgets newer than the layout are added at the end of the first board."""
    if not isinstance(value, dict) or not isinstance(value.get("boards"), list):
        return default_dashboards()
    boards, ids = [], set()
    for b in value["boards"][:MAX_BOARDS]:
        if not isinstance(b, dict) or not isinstance(b.get("id"), str) or not _BOARD_ID.match(b["id"]) \
                or b["id"] in ids or not isinstance(b.get("rows"), list):
            continue
        ids.add(b["id"])
        rows = [[w for w in row if isinstance(w, str) and w in WIDGETS][:MAX_PER_ROW]
                for row in b["rows"][:MAX_ROWS] if isinstance(row, list)]
        name = b.get("name") if isinstance(b.get("name"), str) and b["name"].strip() else None
        boards.append({"id": b["id"], "name": name and name[:MAX_NAME], "rows": [r for r in rows if r]})
    if not boards:
        return default_dashboards()
    seen = value.get("seen") if isinstance(value.get("seen"), list) else []
    boards[0]["rows"] += [[w] for w in WIDGETS if w not in seen]
    return {"v": 1, "boards": boards, "seen": list(WIDGETS)}
