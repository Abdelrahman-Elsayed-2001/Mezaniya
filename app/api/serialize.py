"""Turn a month (DB rows + engine results) into the dashboard JSON, worded in `lang`.

The engine returns keys and formatted numbers; this is where they become sentences.
"""
from __future__ import annotations

import dataclasses
import re
import sqlite3
from datetime import date
from decimal import Decimal
from enum import Enum

from app.db.repo import base, budgets, settings, types
from app.db.repo.loader import load_budget_input
from app.engine import compute, validate
from app.engine.calc import Dashboard
from app.engine.rules import Warning
from app.i18n import plural, t

_ISSUE_FIELD = re.compile(r"^(incomes|expenses|goals)\[(\d+)\](?:\.(\w+))?$")


def plain(obj):
    """Dataclasses / Decimal / Enum / date → JSON-ready values."""
    if isinstance(obj, Decimal):
        return float(round(obj, 4))
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj):
        return {f.name: plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return obj


def _without(d: dict, *keys) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


def split_issue_field(issue, inp) -> tuple[str | None, int | None, str]:
    """'expenses[2].deferred_amount' → ("expenses", <id of that expense>, "deferred_amount")."""
    if m := _ISSUE_FIELD.match(issue.field):
        table, idx, attr = m.group(1), int(m.group(2)), m.group(3) or m.group(1)
        return table, getattr(inp, table)[idx].id, attr
    return None, None, issue.field


def issue_message(issue, lang: str) -> str:
    attr = issue.field.rsplit(".", 1)[-1]
    label = t(f"field.{attr}", lang) if not attr.endswith("]") else attr
    return t(issue.key, lang, field=label, **issue.params)


def issue_json(issue, inp, lang: str) -> dict:
    table, item_id, field = split_issue_field(issue, inp)
    out = {"message": issue_message(issue, lang), "blocking": issue.blocking, "field": field}
    if table:
        out.update(table=table, item_id=item_id)
    return out


def warning_json(w: Warning, lang: str, currency: str) -> dict:
    params = dict(w.params)
    if "n" in params:
        params["items"] = plural("count.items", params["n"], lang)
    return {
        "key": w.key,
        "active": w.active,
        "title": t(f"warnings.{w.key}.title", lang),
        "details": t(f"warnings.{w.key}.details", lang, currency=currency, **params) if w.active else "",
        "state_text": t("warnings.state_active" if w.active else "warnings.state_ok", lang),
    }


def months_text(line, lang: str) -> str:
    if line.months_state == "covered":
        return t("goals.months_covered", lang)
    if line.months_state == "count":
        return plural("count.months", line.months, lang)
    if line.months_state == "unknown":
        return t("goals.months_unknown", lang)
    return ""


def dashboard_json(conn: sqlite3.Connection, user_id: int, budget_id: int, lang: str) -> dict:
    budget = budgets.get_budget(conn, user_id, budget_id)
    user_settings = settings.get_settings(conn, user_id)
    currency = user_settings["currency"]
    inp = load_budget_input(conn, user_id, budget_id)
    d: Dashboard = compute(inp)

    expense_rows = base.list_rows(conn, user_id, "expenses", budget_id=budget_id)
    type_rows = types.list_expense_types(conn, user_id)
    type_by_id = {row["id"]: row for row in type_rows}
    expenses = [
        {**row, "type_name": type_by_id[row["type_id"]]["name"], "role": line.role.value,
         **plain(_without(dataclasses.asdict(line), "expense", "role"))}
        for row, line in zip(expense_rows, d.expenses)
    ]
    goal_rows = base.list_rows(conn, user_id, "goals", budget_id=budget_id)
    category_names = {c["id"]: c["name"] for c in types.list_goal_categories(conn, user_id)}
    goals = [
        {**row, "category_name": category_names.get(row["category_id"]),
         **plain({"valid": line.valid, "mode": line.mode, "effective_priority": line.priority,
                  "requested": line.requested, "allocated": line.allocated, "remaining": line.remaining,
                  "progress": line.progress, "monthly": line.monthly, "months": line.months}),
         "months_text": months_text(line, lang)}
        for row, line in zip(goal_rows, d.goals.lines)
    ]
    total = d.goals.total
    status = d.summary.status

    return {
        "budget": {**budget, "days_in_month": d.suggestions.days_in_month},
        "settings": user_settings,
        "expense_types": type_rows,
        "goal_categories": types.list_goal_categories(conn, user_id),
        "incomes": base.list_rows(conn, user_id, "incomes", budget_id=budget_id),
        "expenses": expenses,
        "goals": goals,
        "goals_total": {**plain(_without(dataclasses.asdict(total), "status_key")),
                        "status_text": t(f"goals.{total.status_key}", lang) if total.status_key else ""},
        "summary": {**plain(_without(dataclasses.asdict(d.summary), "status")),
                    "status": {"key": status.key, "level": status.level, "text": t(f"status.{status.key}", lang)}},
        "suggestions": {**plain(d.suggestions),
                        "days_text": plural("count.days", d.suggestions.days_in_month, lang)},
        "emergency": {**plain(d.emergency),
                      "status_text": t("emergency.complete" if d.emergency.complete else "emergency.incomplete", lang)},
        "savings": {**plain(_without(dataclasses.asdict(d.savings), "status_key")),
                    "status_text": t(f"savings.status.{d.savings.status_key}", lang)},
        "payments": plain(d.payments),
        "warnings": [warning_json(w, lang, currency) for w in d.warnings],
        "warnings_count": d.warnings_count,
        "warnings_text": t(f"warnings.{d.warnings_key}", lang),
        "charts": {
            "by_type": [{**plain(s), "label": t("charts.surplus", lang) if s.key == "surplus" else s.label}
                        for s in d.charts.by_type],
            "progress": [{**plain(p), "label": t(f"charts.progress_{p.key}", lang)} for p in d.charts.progress],
            "daily": plain(d.charts.daily),
        },
        "debug": plain(d.debug),
        "issues": [issue_json(i, inp, lang) for i in validate(inp)],
    }
