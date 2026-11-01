"""Per-user settings, expense types, goal categories and recurring items."""
from __future__ import annotations

from flask import jsonify

from app import dashboard, themes
from app.db import get_db, transaction
from app.db.repo import base, settings, templates, types
from app.i18n import current_lang, t

from . import bp, uid
from .errors import invalid
from .validation import Field, body, boolean, choice, count, money, number, optional_text, parse, ratio, ref_id, text

SETTINGS = {
    "excellent_rate": Field(ratio),
    "good_rate": Field(ratio),
    "max_debt_rate": Field(ratio),
    "max_luxury_rate": Field(ratio),
    "emergency_cut_rate": Field(ratio),
    "saving_rate_em_incomplete": Field(ratio),
    "saving_rate_em_complete": Field(ratio),
    "emergency_ref_months": Field(number, max_value=60),
    "emergency_ref_factor": Field(number, max_value=10),
    "currency": Field(text, required=True, max_len=10),
    "theme": Field(text, max_len=40),
    "theme_colors": Field(lambda v, f: themes.parse_colors(v), nullable=True),
    "dashboards": Field(lambda v, f: dashboard.validate(v)),
}
ROLES = ("essential", "luxury", "debt", "savings", "emergency", "other")
EXPENSE_TYPE = {
    "name": Field(text, required=True, max_len=40),
    "role": Field(choice, choices=ROLES),
    "sort_order": Field(count),
}
GOAL_CATEGORY = {
    "name": Field(text, required=True, max_len=40),
    "sort_order": Field(count),
}
TEMPLATE = {
    "kind": Field(choice, required=True, choices=("income", "expense")),
    "name": Field(text, required=True, max_len=100),
    "type_id": Field(ref_id, nullable=True),
    "amount": Field(money, nullable=True),          # empty = variable price
    "is_savings_withdrawal": Field(boolean),
    "active": Field(boolean),
    "notes": Field(optional_text, nullable=True, max_len=1000),
    "sort_order": Field(count),
}


# ---------- settings

def _with_theme(conn, row: dict) -> dict:
    """Settings plus the :root rule for the chosen theme, so the page can apply it without a reload."""
    return {**row, "theme_css": settings.theme_css(row, conn)}


@bp.get("/settings")
def get_settings():
    conn = get_db()
    return jsonify(_with_theme(conn, settings.get_settings(conn, uid())))


@bp.patch("/settings")
def update_settings():
    values = parse(SETTINGS, body(), partial=True)
    conn = get_db()
    merged = {**settings.get_settings(conn, uid()), **values}
    if merged["good_rate"] > merged["excellent_rate"]:
        raise invalid("api.err.good_gt_excellent", "good_rate" if "good_rate" in values else "excellent_rate")
    if "theme" in values:
        known = {th["id"] for th in settings.list_themes(conn)} | set(themes.SPECIAL)
        if values["theme"] not in known:
            raise invalid("api.err.invalid_value", "theme")
    if merged["theme"] == "custom" and not merged.get("theme_colors"):
        raise invalid("api.err.required", "theme_colors")
    return jsonify(_with_theme(conn, settings.update_settings(conn, uid(), values)))


# ---------- themes

@bp.get("/themes")
def list_themes():
    lang = current_lang()
    return jsonify([{**th, "name": t(f"settings.theme.{th['id']}", lang), "css": themes.css(th["colors"], th["mode"])}
                    for th in settings.list_themes(get_db())])


@bp.post("/themes/preview")
def preview_theme():
    """The :root rule and warnings for colours the user is still picking (nothing is saved)."""
    values = parse({"theme_colors": SETTINGS["theme_colors"]}, body(), partial=False)
    colors = values.get("theme_colors")
    if not colors:
        raise invalid("api.err.required", "theme_colors")
    return jsonify({"css": themes.css(colors), "mode": themes.mode_of(colors),
                    "warnings": [t(f"settings.warn_{w}", current_lang()) for w in themes.warnings(colors)]})


# ---------- expense types

@bp.get("/expense-types")
def list_expense_types():
    return jsonify(types.list_expense_types(get_db(), uid()))


@bp.post("/expense-types")
def create_expense_type():
    values = parse(EXPENSE_TYPE, body(), partial=False)
    return jsonify(types.create_expense_type(get_db(), uid(), values)), 201


@bp.patch("/expense-types/<int:type_id>")
def update_expense_type(type_id: int):
    values = parse(EXPENSE_TYPE, body(), partial=True)
    return jsonify(types.update_expense_type(get_db(), uid(), type_id, values))


@bp.delete("/expense-types/<int:type_id>")
def delete_expense_type(type_id: int):
    conn = get_db()
    with transaction(conn):
        types.delete_expense_type(conn, uid(), type_id)
    return "", 204


# ---------- goal categories

@bp.get("/goal-categories")
def list_goal_categories():
    return jsonify(types.list_goal_categories(get_db(), uid()))


@bp.post("/goal-categories")
def create_goal_category():
    values = parse(GOAL_CATEGORY, body(), partial=False)
    return jsonify(types.create_goal_category(get_db(), uid(), values)), 201


@bp.patch("/goal-categories/<int:category_id>")
def update_goal_category(category_id: int):
    values = parse(GOAL_CATEGORY, body(), partial=True)
    return jsonify(types.update_goal_category(get_db(), uid(), category_id, values))


@bp.delete("/goal-categories/<int:category_id>")
def delete_goal_category(category_id: int):
    types.delete_goal_category(get_db(), uid(), category_id)
    return "", 204


# ---------- recurring items

def _check_template(item: dict) -> None:
    if item["kind"] == "expense":
        if item.get("type_id") is None:
            raise invalid("api.err.template_needs_type", "type_id")
        if item.get("is_savings_withdrawal"):
            raise invalid("api.err.withdrawal_income_only", "is_savings_withdrawal")
    elif item.get("type_id") is not None:
        raise invalid("api.err.income_no_type", "type_id")


@bp.get("/templates")
def list_templates():
    return jsonify(templates.list_templates(get_db(), uid()))


@bp.post("/templates")
def create_template():
    values = parse(TEMPLATE, body(), partial=False)
    _check_template(values)
    return jsonify(templates.create_template(get_db(), uid(), values)), 201


@bp.patch("/templates/<int:template_id>")
def update_template(template_id: int):
    values = parse({k: v for k, v in TEMPLATE.items() if k != "kind"}, body(), partial=True)
    conn = get_db()
    current = base.get(conn, uid(), "recurring_templates", template_id)
    _check_template({**current, **values})
    return jsonify(templates.update_template(conn, uid(), template_id, values))


@bp.delete("/templates/<int:template_id>")
def delete_template(template_id: int):
    templates.delete_template(get_db(), uid(), template_id)
    return "", 204
