"""The dashboard's widgets and their default layout.

Each widget is app/templates/widgets/<id>.html, a self-contained card that reads the loaded
budget (`d`) from the page's Alpine component. A layout is a list of rows; a row of several
widgets shares one line (three columns on wide screens), a row of one takes the full width.
Only ids from WIDGETS are ever included, so a stored layout can't name another template.
"""
from __future__ import annotations

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
