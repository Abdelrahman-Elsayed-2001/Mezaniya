import sqlite3
from decimal import Decimal

import pytest

from app.db.connection import migrate, schema_version, transaction
from app.db.repo import Conflict, NotFound, base, budgets, settings, templates, types, users
from app.db.repo.loader import load_budget_input
from app.engine import compute
from tests.db_helpers import add_expense, current_budget, type_id
from tests.sheet_data import sheet_input


def test_migrations_are_idempotent(conn):
    assert schema_version(conn) == 1
    assert migrate(conn) == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_seed(conn, alice):
    ts = types.list_expense_types(conn, alice)
    assert [t["name"] for t in ts] == ["طوارئ", "ادخار", "كمالي", "دين/أقساط", "أساسي",
                                       "العطاء والمساندة", "تحويل خارجي", "الاستثمار"]
    assert sum(t["is_system"] for t in ts) == 5
    assert [c["name"] for c in types.list_goal_categories(conn, alice)] == ["أساسي", "كمالي", "استثمار", "أخرى"]
    s = settings.get_settings(conn, alice)
    assert (s["max_debt_rate"], s["emergency_ref_factor"], s["currency"]) == (0.33, 1.25, "ر.س")
    assert [(b["year"], b["month"]) for b in budgets.list_budgets(conn, alice)] == [(2026, 11)]


def test_seeding_is_atomic(conn, alice):
    with pytest.raises(Conflict):
        from app.services.seed_user import create_seeded_user
        from tests.conftest import TODAY
        create_seeded_user(conn, "ALICE", "h", TODAY, "ar")   # username is case-insensitive
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_item_crud_and_sort_order(conn, alice):
    b = current_budget(conn, alice)["id"]
    e1 = add_expense(conn, alice, b, "انترنت", cash_amount=120)
    e2 = add_expense(conn, alice, b, "كهرباء", needs_amount=True)
    assert (e1["sort_order"], e2["sort_order"]) == (1, 2)
    assert e2["needs_amount"] is True and e1["status"] == "unpaid"

    upd = budgets.update_item(conn, alice, "expenses", b, e1["id"],
                              {"status": "paid", "paid_at": "2026-11-03", "notes": "اتدفع بالبطاقة"})
    assert (upd["status"], upd["paid_at"], upd["notes"]) == ("paid", "2026-11-03", "اتدفع بالبطاقة")
    assert [e["name"] for e in budgets.list_items(conn, alice, "expenses", b)] == ["انترنت", "كهرباء"]

    budgets.delete_item(conn, alice, "expenses", b, e2["id"])
    with pytest.raises(NotFound):
        budgets.get_item(conn, alice, "expenses", b, e2["id"])


def test_items_cannot_move_or_be_addressed_through_another_month(conn, alice):
    b1 = current_budget(conn, alice)["id"]
    b2 = budgets.create_budget(conn, alice, 2026, 12)["id"]
    e = add_expense(conn, alice, b1)
    with pytest.raises(NotFound):
        budgets.get_item(conn, alice, "expenses", b2, e["id"])
    with pytest.raises(ValueError):
        budgets.update_item(conn, alice, "expenses", b1, e["id"], {"budget_id": b2})


@pytest.mark.parametrize("values", [
    {"cash_amount": -1},
    {"inst_count": -1},
    {"status": "maybe"},
    {"paid_at": "2026-13-40"},
    {"paid_at": "3/11/2026"},
])
def test_expense_checks(conn, alice, values):
    b = current_budget(conn, alice)["id"]
    with pytest.raises(Conflict):
        add_expense(conn, alice, b, **values)


def test_goal_priority_and_manual_are_exclusive(conn, alice):
    b = current_budget(conn, alice)["id"]
    with pytest.raises(Conflict):
        budgets.create_item(conn, alice, "goals", b, {"name": "x", "target_amount": 100,
                                                      "priority": 0.5, "manual_allocation": 10})


def test_unknown_column_rejected(conn, alice):
    b = current_budget(conn, alice)["id"]
    for bad in ({"user_id": 999}, {"id": 5}, {"name; DROP TABLE users": 1}):
        with pytest.raises(ValueError):
            base.insert(conn, alice, "expenses", {"budget_id": b, "name": "x",
                                                  "type_id": type_id(conn, alice, "أساسي"), **bad})


def test_month_unique_per_user(conn, alice, bob):
    with pytest.raises(Conflict):
        budgets.create_budget(conn, alice, 2026, 11)
    budgets.create_budget(conn, alice, 2027, 1)
    assert [(b["year"], b["month"]) for b in budgets.list_budgets(conn, alice)] == [(2026, 11), (2027, 1)]


def test_previous_and_next_month(conn, alice):
    budgets.create_budget(conn, alice, 2026, 9)
    budgets.create_budget(conn, alice, 2027, 1)
    assert (budgets.previous_budget(conn, alice, 2027, 1)["month"]) == 11
    assert (budgets.next_budget(conn, alice, 2026, 11)["year"]) == 2027
    assert budgets.previous_budget(conn, alice, 2026, 9) is None


def test_budget_update_only_balances(conn, alice):
    b = current_budget(conn, alice)["id"]
    assert budgets.update_budget(conn, alice, b, {"savings_current": 90})["savings_current"] == 90
    with pytest.raises(ValueError):
        budgets.update_budget(conn, alice, b, {"month": 5})


def test_deleting_month_cascades_items(conn, alice):
    b = current_budget(conn, alice)["id"]
    add_expense(conn, alice, b)
    budgets.create_item(conn, alice, "incomes", b, {"source": "راتب", "amount": 5000})
    budgets.create_item(conn, alice, "goals", b, {"name": "جوال", "target_amount": 4000})
    budgets.delete_budget(conn, alice, b)
    for t in ("incomes", "expenses", "goals"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_expense_type_rules(conn, alice):
    b = current_budget(conn, alice)["id"]
    essential = type_id(conn, alice, "أساسي")
    renamed = types.update_expense_type(conn, alice, essential, {"name": "ضروري"})
    assert renamed["name"] == "ضروري" and renamed["role"] == "essential"
    with pytest.raises(Conflict):
        types.update_expense_type(conn, alice, essential, {"role": "luxury"})
    with pytest.raises(Conflict):
        types.delete_expense_type(conn, alice, essential)

    custom = types.create_expense_type(conn, alice, {"name": "عربية"})
    add_expense(conn, alice, b, "بنزين", "عربية")
    with pytest.raises(Conflict):
        types.delete_expense_type(conn, alice, custom["id"])
    with pytest.raises(Conflict):
        types.create_expense_type(conn, alice, {"name": "عربية"})
    with pytest.raises(ValueError):
        types.create_expense_type(conn, alice, {"name": "x", "is_system": True})


def test_deleting_category_uncategorises_goals(conn, alice):
    b = current_budget(conn, alice)["id"]
    cat = types.list_goal_categories(conn, alice)[0]["id"]
    g = budgets.create_item(conn, alice, "goals", b, {"name": "جوال", "target_amount": 4000, "category_id": cat})
    types.delete_goal_category(conn, alice, cat)
    assert budgets.get_item(conn, alice, "goals", b, g["id"])["category_id"] is None


def test_templates(conn, alice):
    b = current_budget(conn, alice)["id"]
    essential = type_id(conn, alice, "أساسي")
    internet = templates.create_template(conn, alice, {"kind": "expense", "name": "انترنت",
                                                       "type_id": essential, "amount": 120})
    templates.create_template(conn, alice, {"kind": "expense", "name": "كهرباء", "type_id": essential})
    templates.create_template(conn, alice, {"kind": "income", "name": "راتب", "amount": 5000})
    with pytest.raises(Conflict):     # an expense template needs a type, an income one must not have it
        templates.create_template(conn, alice, {"kind": "expense", "name": "x"})
    with pytest.raises(Conflict):
        templates.create_template(conn, alice, {"kind": "income", "name": "x", "type_id": essential})
    assert [t["name"] for t in templates.list_templates(conn, alice, "expense")] == ["انترنت", "كهرباء"]
    assert templates.list_templates(conn, alice, "expense")[1]["amount"] is None

    e = add_expense(conn, alice, b, template_id=internet["id"])
    templates.delete_template(conn, alice, internet["id"])
    assert budgets.get_item(conn, alice, "expenses", b, e["id"])["template_id"] is None


def test_deleting_expense_unlinks_its_continuation(conn, alice):
    b1 = current_budget(conn, alice)["id"]
    b2 = budgets.create_budget(conn, alice, 2026, 12)["id"]
    first = add_expense(conn, alice, b1, "قسط", "دين/أقساط", inst_balance=1000, inst_count=2)
    nxt = add_expense(conn, alice, b2, "قسط", "دين/أقساط", inst_balance=500, inst_count=1,
                      prev_expense_id=first["id"])
    budgets.delete_budget(conn, alice, b1)
    assert budgets.get_item(conn, alice, "expenses", b2, nxt["id"])["prev_expense_id"] is None


def test_deleting_user_removes_everything(conn, alice, bob):
    b = current_budget(conn, alice)["id"]
    add_expense(conn, alice, b)
    templates.create_template(conn, alice, {"kind": "income", "name": "راتب", "amount": 5000})
    with transaction(conn):
        conn.execute("DELETE FROM users WHERE id = ?", (alice,))
    for t in ("user_settings", "expense_types", "goal_categories", "recurring_templates",
              "budgets", "incomes", "expenses", "goals"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (alice,)).fetchone()[0] == 0
    assert len(types.list_expense_types(conn, bob)) == 8


def test_users(conn, alice):
    assert users.find_by_username(conn, "ALICE")["id"] == alice
    users.set_password_hash(conn, alice, "#1234#")
    users.touch_login(conn, alice)
    u = users.get_user(conn, alice)
    assert u["password_hash"] == "#1234#" and u["last_login_at"]
    with pytest.raises(NotFound):
        users.get_user(conn, 999)


def test_settings_update(conn, alice):
    s = settings.update_settings(conn, alice, {"emergency_ref_factor": 1.5, "currency": "ج.م"})
    eng = settings.to_engine(s)
    assert eng.emergency_ref_factor == Decimal("1.5") and s["currency"] == "ج.م"
    assert eng.max_debt_rate == Decimal("0.33")
    with pytest.raises(ValueError):
        settings.update_settings(conn, alice, {"user_id": 2})
    with pytest.raises(Conflict):
        settings.update_settings(conn, alice, {"good_rate": -1})


def test_sheet_round_trip_through_db_matches_engine(conn, alice):
    """Store the workbook's data in the DB, load it back, and get the same dashboard."""
    budget = current_budget(conn, alice)
    b = budget["id"]
    src = sheet_input(year=budget["year"], month=budget["month"])
    budgets.update_budget(conn, alice, b, {"emergency_current": 250, "emergency_min": 260, "savings_current": 90})
    for i in src.incomes:
        budgets.create_item(conn, alice, "incomes", b, {"source": i.source, "amount": float(i.amount),
                                                        "is_savings_withdrawal": i.is_savings_withdrawal})
    for e in src.expenses:
        add_expense(conn, alice, b, e.name, e.type_name, cash_amount=float(e.cash),
                    inst_balance=float(e.inst_balance), inst_count=e.inst_count)
    for g in src.goals:
        budgets.create_item(conn, alice, "goals", b, {
            "name": g.name, "target_amount": float(g.target),
            "priority": None if g.priority is None else float(g.priority),
            "manual_allocation": None if g.manual_amount is None else float(g.manual_amount)})

    loaded = load_budget_input(conn, alice, b)
    got, want = compute(loaded), compute(src)
    assert got.summary == want.summary
    assert got.suggestions == want.suggestions
    assert got.savings == want.savings
    assert got.goals.total == want.goals.total
    assert got.warnings == want.warnings
    assert got.warnings_count == 8
    assert all(e.id is not None for e in loaded.expenses)


def test_loader_reads_payment_fields(conn, alice):
    b = current_budget(conn, alice)["id"]
    add_expense(conn, alice, b, "قسط", "دين/أقساط", inst_balance=1500, inst_count=3, carried_in=100,
                status="deferred", deferred_amount=150, paid_at="2026-11-05")
    e = load_budget_input(conn, alice, b).expenses[0]
    assert (e.carried_in, e.status, e.deferred_amount, e.paid_at.day) == (100, "deferred", 150, 5)
    line = compute(load_budget_input(conn, alice, b)).expenses[0]
    assert (line.due, line.effective) == (600, 450)


def test_composite_fk_is_enforced_by_sqlite(conn, alice):
    """Even raw SQL can't point at a parent row of a different user_id."""
    b = current_budget(conn, alice)["id"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO expenses (user_id, budget_id, name, type_id) VALUES (?, ?, 'x', 999999)", (alice, b))
