"""Bob must never see, change or reference anything of Alice's — every attempt
looks exactly like a missing row, and Alice's data stays untouched."""
import sqlite3

import pytest

from app.db.repo import NotFound, base, budgets, settings, templates, types
from tests.db_helpers import add_expense, current_budget, type_id


@pytest.fixture
def world(conn, alice, bob):
    ab = current_budget(conn, alice)["id"]
    a = {
        "budget": ab,
        "income": budgets.create_item(conn, alice, "incomes", ab, {"source": "راتب", "amount": 5000})["id"],
        "expense": add_expense(conn, alice, ab, cash_amount=120)["id"],
        "goal": budgets.create_item(conn, alice, "goals", ab, {"name": "جوال", "target_amount": 4000})["id"],
        "type": types.create_expense_type(conn, alice, {"name": "عربية"})["id"],
        "category": types.list_goal_categories(conn, alice)[0]["id"],
        "template": templates.create_template(conn, alice, {"kind": "income", "name": "راتب", "amount": 5000})["id"],
    }
    return {"conn": conn, "alice": alice, "bob": bob, "a": a, "bob_budget": current_budget(conn, bob)["id"]}


def snapshot(conn, user_id):
    out = {}
    for t in ("budgets", "incomes", "expenses", "goals", "expense_types", "goal_categories", "recurring_templates"):
        out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} WHERE user_id = ? ORDER BY id", (user_id,))]
    out["settings"] = settings.get_settings(conn, user_id)
    return out


ATTACKS = {
    "get budget": lambda c, bob, a, bb: budgets.get_budget(c, bob, a["budget"]),
    "update budget": lambda c, bob, a, bb: budgets.update_budget(c, bob, a["budget"], {"savings_current": 1}),
    "delete budget": lambda c, bob, a, bb: budgets.delete_budget(c, bob, a["budget"]),
    "list incomes": lambda c, bob, a, bb: budgets.list_items(c, bob, "incomes", a["budget"]),
    "list expenses": lambda c, bob, a, bb: budgets.list_items(c, bob, "expenses", a["budget"]),
    "list goals": lambda c, bob, a, bb: budgets.list_items(c, bob, "goals", a["budget"]),
    "get expense": lambda c, bob, a, bb: budgets.get_item(c, bob, "expenses", a["budget"], a["expense"]),
    "get expense via own month": lambda c, bob, a, bb: budgets.get_item(c, bob, "expenses", bb, a["expense"]),
    "update income": lambda c, bob, a, bb: budgets.update_item(c, bob, "incomes", a["budget"], a["income"],
                                                                {"amount": 1}),
    "update expense via own month": lambda c, bob, a, bb: budgets.update_item(c, bob, "expenses", bb, a["expense"],
                                                                               {"cash_amount": 1}),
    "delete goal": lambda c, bob, a, bb: budgets.delete_item(c, bob, "goals", a["budget"], a["goal"]),
    "create item in alice month": lambda c, bob, a, bb: budgets.create_item(c, bob, "incomes", a["budget"],
                                                                           {"source": "x", "amount": 1}),
    "use alice type": lambda c, bob, a, bb: budgets.create_item(c, bob, "expenses", bb,
                                                               {"name": "x", "type_id": a["type"]}),
    "use alice category": lambda c, bob, a, bb: budgets.create_item(c, bob, "goals", bb,
                                                                   {"name": "x", "category_id": a["category"]}),
    "use alice template": lambda c, bob, a, bb: budgets.create_item(c, bob, "incomes", bb,
                                                                   {"source": "x", "template_id": a["template"]}),
    "link alice expense": lambda c, bob, a, bb: budgets.create_item(
        c, bob, "expenses", bb, {"name": "x", "type_id": type_id(c, bob, "أساسي"), "prev_expense_id": a["expense"]}),
    "update type": lambda c, bob, a, bb: types.update_expense_type(c, bob, a["type"], {"name": "hacked"}),
    "delete type": lambda c, bob, a, bb: types.delete_expense_type(c, bob, a["type"]),
    "update category": lambda c, bob, a, bb: types.update_goal_category(c, bob, a["category"], {"name": "hacked"}),
    "delete category": lambda c, bob, a, bb: types.delete_goal_category(c, bob, a["category"]),
    "update template": lambda c, bob, a, bb: templates.update_template(c, bob, a["template"], {"amount": 1}),
    "delete template": lambda c, bob, a, bb: templates.delete_template(c, bob, a["template"]),
    "template with alice type": lambda c, bob, a, bb: templates.create_template(
        c, bob, {"kind": "expense", "name": "x", "type_id": a["type"]}),
    "generic get": lambda c, bob, a, bb: base.get(c, bob, "expenses", a["expense"]),
}


@pytest.mark.parametrize("attack", ATTACKS)
def test_cross_user_access_is_not_found(world, attack):
    c, alice, bob, a = world["conn"], world["alice"], world["bob"], world["a"]
    before = snapshot(c, alice)
    with pytest.raises(NotFound):
        ATTACKS[attack](c, bob, a, world["bob_budget"])
    assert snapshot(c, alice) == before


def test_lists_only_show_own_rows(world):
    c, alice, bob = world["conn"], world["alice"], world["bob"]
    assert [b["id"] for b in budgets.list_budgets(c, bob)] == [world["bob_budget"]]
    assert "عربية" not in [t["name"] for t in types.list_expense_types(c, bob)]
    assert templates.list_templates(c, bob) == []
    assert budgets.list_items(c, bob, "incomes", world["bob_budget"]) == []
    assert base.list_rows(c, bob, "expenses") == []


def test_same_names_and_months_are_allowed_per_user(world):
    c, bob = world["conn"], world["bob"]
    types.create_expense_type(c, bob, {"name": "عربية"})          # alice has one too
    assert len(types.list_expense_types(c, bob)) == 9
    assert current_budget(c, bob)["month"] == current_budget(c, world["alice"])["month"]


def test_settings_are_independent(world):
    c, alice, bob = world["conn"], world["alice"], world["bob"]
    settings.update_settings(c, alice, {"max_debt_rate": 0.5, "currency": "ج.م"})
    s = settings.get_settings(c, bob)
    assert (s["max_debt_rate"], s["currency"]) == (0.33, "ر.س")


def test_sort_order_is_per_user_and_month(world):
    c, bob = world["conn"], world["bob"]
    e = add_expense(c, bob, world["bob_budget"])
    assert e["sort_order"] == 1          # alice already has one expense; bob's numbering starts fresh


def test_raw_sql_cross_user_reference_rejected(world):
    c, bob, a = world["conn"], world["bob"], world["a"]
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO expenses (user_id, budget_id, name, type_id) VALUES (?, ?, 'x', ?)",
                  (bob, world["bob_budget"], a["type"]))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO incomes (user_id, budget_id, source) VALUES (?, ?, 'x')", (bob, a["budget"]))
