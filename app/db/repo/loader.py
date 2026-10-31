"""Build the engine's BudgetInput for one month from the database."""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

from app.engine import BudgetInput, Expense, ExpenseType, Goal, Income, Role
from app.engine.calc import ExpenseLine, expense_line

from . import base, settings


def _money(x) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def expense_model(r: dict, type_name: str) -> Expense:
    return Expense(name=r["name"], type_name=type_name, cash=_money(r["cash_amount"]),
                   inst_balance=_money(r["inst_balance"]), inst_count=r["inst_count"],
                   carried_in=_money(r["carried_in"]), status=r["status"],
                   deferred_amount=_money(r["deferred_amount"]),
                   paid_at=date.fromisoformat(r["paid_at"]) if r["paid_at"] else None,
                   needs_amount=r["needs_amount"], id=r["id"])


def expense_amounts(r: dict) -> ExpenseLine:
    """due / deferred / effective of one expense row (role doesn't matter for these)."""
    return expense_line(expense_model(r, ""), Role.OTHER)


def load_budget_input(conn: sqlite3.Connection, user_id: int, budget_id: int) -> BudgetInput:
    budget = base.get(conn, user_id, "budgets", budget_id)
    types = base.list_rows(conn, user_id, "expense_types")
    type_names = {t["id"]: t["name"] for t in types}
    categories = {c["id"]: c["name"] for c in base.list_rows(conn, user_id, "goal_categories")}

    incomes = tuple(
        Income(source=r["source"], amount=_money(r["amount"]), is_savings_withdrawal=r["is_savings_withdrawal"],
               needs_amount=r["needs_amount"], id=r["id"])
        for r in base.list_rows(conn, user_id, "incomes", budget_id=budget_id))
    expenses = tuple(expense_model(r, type_names[r["type_id"]])
                     for r in base.list_rows(conn, user_id, "expenses", budget_id=budget_id))
    goals = tuple(
        Goal(name=r["name"], target=_money(r["target_amount"]), priority=_money(r["priority"]),
             manual_amount=_money(r["manual_allocation"]), category=categories.get(r["category_id"]), id=r["id"])
        for r in base.list_rows(conn, user_id, "goals", budget_id=budget_id))

    return BudgetInput(
        year=budget["year"], month=budget["month"],
        settings=settings.to_engine(settings.get_settings(conn, user_id)),
        types=tuple(ExpenseType(t["name"], Role(t["role"])) for t in types),
        incomes=incomes, expenses=expenses, goals=goals,
        emergency_current=_money(budget["emergency_current"]),
        emergency_min=_money(budget["emergency_min"]),
        savings_current=_money(budget["savings_current"]),
    )
