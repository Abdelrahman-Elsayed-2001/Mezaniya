"""Months and their incomes / expenses / goals. Every write returns the recomputed dashboard."""
from __future__ import annotations

from flask import jsonify

from app.db import get_db, transaction
from app.db.repo import Conflict, base, budgets
from app.db.repo.loader import load_budget_input
from app.engine import validate
from app.i18n import current_lang
from app.services import carry, rollover

from . import bp, uid
from .errors import error, invalid
from .serialize import dashboard_json, issue_message, split_issue_field
from .validation import (Field, body, boolean, choice, count, id_list, iso_date, money, optional_text, parse,
                         ratio, ref_id, text)

BALANCES = {
    "emergency_current": Field(money),
    "emergency_min": Field(money),
    "savings_current": Field(money),
}
NEW_BUDGET = {
    "year": Field(count, required=True, max_value=2200),
    "month": Field(count, required=True, max_value=12),
    **BALANCES,
    # copy from the latest month (rollover); balances above then override the carried ones
    "copy_from_previous": Field(boolean),
    "carry_unpaid_ids": Field(id_list),
    "add_return_to_savings": Field(boolean),
    "drop_completed_goals": Field(boolean),
}
ROLLOVER_PREVIEW = {
    "year": Field(count, max_value=2200),
    "month": Field(count, max_value=12),
    "carry_unpaid_ids": Field(id_list),
}
ROLLOVER_OPTIONS = ("carry_unpaid_ids", "add_return_to_savings", "drop_completed_goals")
_NOTES = Field(optional_text, nullable=True, max_len=1000)
_SORT = Field(count)

ITEM_SPECS = {
    "incomes": {
        "source": Field(text, required=True, max_len=100),
        "amount": Field(money),
        "is_savings_withdrawal": Field(boolean),
        "needs_amount": Field(boolean),
        "template_id": Field(ref_id, nullable=True),
        "notes": _NOTES,
        "sort_order": _SORT,
    },
    "expenses": {
        "name": Field(text, required=True, max_len=100),
        "type_id": Field(ref_id, required=True),
        "cash_amount": Field(money),
        "inst_balance": Field(money),
        "inst_count": Field(count, max_value=600),
        "inst_original_count": Field(count, nullable=True, max_value=600),
        "carried_in": Field(money),
        "status": Field(choice, choices=("unpaid", "paid", "deferred")),
        "deferred_amount": Field(money),
        "paid_at": Field(iso_date, nullable=True),
        "needs_amount": Field(boolean),
        "template_id": Field(ref_id, nullable=True),
        "notes": _NOTES,
        "sort_order": _SORT,
    },
    "goals": {
        "name": Field(text, required=True, max_len=100),
        "category_id": Field(ref_id, nullable=True),
        "target_amount": Field(money, nullable=True),
        "priority": Field(ratio, nullable=True),
        "manual_allocation": Field(money, nullable=True),
        "notes": _NOTES,
        "sort_order": _SORT,
    },
}
EXPENSE_DEFAULTS = {"cash_amount": 0, "inst_balance": 0, "inst_count": 0, "carried_in": 0,
                    "status": "unpaid", "deferred_amount": 0, "needs_amount": False}


def _kind(kind: str) -> str:
    if kind not in ITEM_SPECS:
        raise error(404, "not_found", "api.err.not_found")
    return kind


def _due(e: dict) -> float:
    count_ = e["inst_count"] or 0
    installment = e["inst_balance"] / count_ if count_ > 0 else e["inst_balance"]
    return e["cash_amount"] + installment + e["carried_in"]


def _check_template(conn, user_id: int, kind: str, values: dict) -> None:
    tid = values.get("template_id")
    if tid is None:
        return
    template = base.get(conn, user_id, "recurring_templates", tid)
    if template["kind"] != ("income" if kind == "incomes" else "expense"):
        raise invalid("api.err.template_kind", "template_id")


def _apply_rules(kind: str, current: dict | None, values: dict) -> dict:
    """Linked fields: what the UI expects to happen automatically when one field changes."""
    values = dict(values)
    if kind == "incomes":
        if "amount" in values and "needs_amount" not in values:
            values["needs_amount"] = False
    elif kind == "expenses":
        merged = {**EXPENSE_DEFAULTS, **(current or {}), **values}
        if current is None and merged["inst_count"] > 0 and "inst_original_count" not in values:
            values["inst_original_count"] = merged["inst_count"]
        if ("cash_amount" in values or "inst_balance" in values) and "needs_amount" not in values:
            values["needs_amount"] = False
        status = values.get("status")
        if status in ("unpaid", "paid"):
            values["deferred_amount"] = 0
            if status == "unpaid" and "paid_at" not in values:
                values["paid_at"] = None
        elif status == "deferred" and "deferred_amount" not in values:
            values["deferred_amount"] = round(_due(merged), 2)       # default: carry the whole amount
        elif status is None and values.get("deferred_amount") and merged["status"] != "deferred":
            raise invalid("api.err.deferred_needs_status", "deferred_amount")
    elif kind == "goals":
        if values.get("priority") is not None and values.get("manual_allocation") is not None:
            raise invalid("api.err.priority_and_manual", "manual_allocation")
        if values.get("priority") is not None:
            values["manual_allocation"] = None
        if values.get("manual_allocation") is not None:
            values["priority"] = None
    return values


def _reject_blocking_issues(conn, user_id: int, budget_id: int) -> None:
    """Run inside the write's transaction: an invalid month is rolled back."""
    inp = load_budget_input(conn, user_id, budget_id)
    for issue in validate(inp):
        if issue.blocking:
            _table, item_id, field = split_issue_field(issue, inp)
            err = invalid(issue.key, None, item_id=item_id)
            err.message = issue_message(issue, current_lang())
            raise err.with_field(field)


def _label(year: int, month: int) -> str:
    return rollover.label(year, month)


def _budget_summary(b: dict) -> dict:
    return {"id": b["id"], "year": b["year"], "month": b["month"], "label": _label(b["year"], b["month"]),
            "source_budget_id": b.get("source_budget_id")}


def _check_period(year: int, month: int) -> None:
    if year < 2000 or month < 1:
        raise invalid("api.err.period", "month" if month < 1 else "year")


# ---------- months

@bp.get("/budgets")
def list_budgets():
    return jsonify([_budget_summary(b) for b in budgets.list_budgets(get_db(), uid())])


@bp.post("/budgets")
def create_budget():
    """A new month. With copy_from_previous it is filled from the latest month (only for a month
    after it); otherwise it starts empty — any month, including ones before existing months."""
    values = parse(NEW_BUDGET, body(), partial=False)
    year, month = values.pop("year"), values.pop("month")
    _check_period(year, month)
    conn, user_id = get_db(), uid()
    if values.pop("copy_from_previous", False):
        options = {k: values.pop(k) for k in ROLLOVER_OPTIONS if k in values}
        b = rollover.create_from_previous(conn, user_id, year, month, lang=current_lang(),
                                          balances=values, **options)
    else:
        if any(k in values for k in ROLLOVER_OPTIONS):
            raise invalid("api.err.rollover_options", "copy_from_previous")
        if budgets.find_budget(conn, user_id, year, month):
            raise rollover.RolloverError("duplicate")
        b = budgets.create_budget(conn, user_id, year, month, **values)
    return jsonify(dashboard_json(conn, user_id, b["id"], current_lang())), 201


@bp.post("/budgets/rollover-preview")
def rollover_preview():
    """What a new month would contain (nothing is written). Without year/month the target is the
    month right after the latest one. A month before the latest one is created empty ("blank")."""
    values = parse(ROLLOVER_PREVIEW, body(), partial=True)
    conn, user_id = get_db(), uid()
    latest = rollover.latest_budget(conn, user_id)
    if "year" in values or "month" in values:
        if "year" not in values or "month" not in values:
            raise invalid("api.err.period_pair", "month" if "year" in values else "year")
        year, month = values["year"], values["month"]
        _check_period(year, month)
    else:
        if latest is None:
            raise rollover.RolloverError("no_previous")
        year, month = rollover.next_period(latest["year"], latest["month"])
    if budgets.find_budget(conn, user_id, year, month):
        raise rollover.RolloverError("duplicate")
    if latest is None or (year, month) < (latest["year"], latest["month"]):
        return jsonify({"mode": "blank", "target": {"year": year, "month": month, "label": _label(year, month)},
                        "latest": _budget_summary(latest) if latest else None})
    plan = rollover.plan_rollover(conn, user_id, year, month, values.get("carry_unpaid_ids", ()))
    return jsonify({"mode": "copy", **plan.to_json()})


@bp.post("/budgets/<int:budget_id>/apply-templates")
def apply_templates(budget_id: int):
    conn, user_id = get_db(), uid()
    added = rollover.apply_templates(conn, user_id, budget_id)
    return jsonify({"added": added, "dashboard": dashboard_json(conn, user_id, budget_id, current_lang())})


@bp.get("/budgets/<int:budget_id>")
@bp.get("/budgets/<int:budget_id>/dashboard")
def get_dashboard(budget_id: int):
    return jsonify(dashboard_json(get_db(), uid(), budget_id, current_lang()))


@bp.patch("/budgets/<int:budget_id>")
def update_budget(budget_id: int):
    values = parse(BALANCES, body(), partial=True)
    conn = get_db()
    budgets.update_budget(conn, uid(), budget_id, values)
    return jsonify(dashboard_json(conn, uid(), budget_id, current_lang()))


@bp.delete("/budgets/<int:budget_id>")
def delete_budget(budget_id: int):
    """Any month can go, except one another month was copied from (its carry-overs live there)
    and the very last remaining month."""
    conn, user_id = get_db(), uid()
    budgets.get_budget(conn, user_id, budget_id)
    with transaction(conn):
        if budgets.followers(conn, user_id, budget_id):
            raise Conflict("has followers", "has_followers")
        if len(budgets.list_budgets(conn, user_id)) == 1:
            raise Conflict("last month", "last_budget")
        budgets.delete_budget(conn, user_id, budget_id)
    return "", 204


# ---------- incomes / expenses / goals

@bp.get("/budgets/<int:budget_id>/<kind>")
def list_items(budget_id: int, kind: str):
    return jsonify(budgets.list_items(get_db(), uid(), _kind(kind), budget_id))


@bp.post("/budgets/<int:budget_id>/<kind>")
def create_item(budget_id: int, kind: str):
    kind = _kind(kind)
    conn, user_id = get_db(), uid()
    budgets.get_budget(conn, user_id, budget_id)
    values = _apply_rules(kind, None, parse(ITEM_SPECS[kind], body(), partial=False))
    with transaction(conn):
        _check_template(conn, user_id, kind, values)
        item = budgets.create_item(conn, user_id, kind, budget_id, values)
        _reject_blocking_issues(conn, user_id, budget_id)
        if kind == "expenses":
            carry.sync_expense(conn, user_id, item["id"], lang=current_lang())
    return jsonify({"item": item, "dashboard": dashboard_json(conn, user_id, budget_id, current_lang())}), 201


@bp.patch("/budgets/<int:budget_id>/<kind>/<int:item_id>")
def update_item(budget_id: int, kind: str, item_id: int):
    kind = _kind(kind)
    conn, user_id = get_db(), uid()
    current = budgets.get_item(conn, user_id, kind, budget_id, item_id)
    values = _apply_rules(kind, current, parse(ITEM_SPECS[kind], body(), partial=True))
    with transaction(conn):
        _check_template(conn, user_id, kind, values)
        item = budgets.update_item(conn, user_id, kind, budget_id, item_id, values)
        _reject_blocking_issues(conn, user_id, budget_id)
        if kind == "expenses":
            carry.sync_expense(conn, user_id, item_id, lang=current_lang())
    return jsonify({"item": item, "dashboard": dashboard_json(conn, user_id, budget_id, current_lang())})


@bp.delete("/budgets/<int:budget_id>/<kind>/<int:item_id>")
def delete_item(budget_id: int, kind: str, item_id: int):
    kind = _kind(kind)
    conn, user_id = get_db(), uid()
    budgets.get_item(conn, user_id, kind, budget_id, item_id)
    with transaction(conn):
        if kind == "expenses":
            carry.sync_expense(conn, user_id, item_id, deleting=True, lang=current_lang())
        budgets.delete_item(conn, user_id, kind, budget_id, item_id)
    return jsonify({"dashboard": dashboard_json(conn, user_id, budget_id, current_lang())})
