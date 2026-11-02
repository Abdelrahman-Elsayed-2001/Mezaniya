"""Build the engine's BudgetInput for one month from the database."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.engine import BudgetInput, Expense, ExpenseType, Goal, Income, Role
from app.engine.calc import ExpenseLine, expense_line

from . import base, settings
from . import rates as rates_repo


CENT = Decimal("0.01")


def _money(x) -> Decimal | None:
    return None if x is None else Decimal(str(x))


@dataclass
class Conversion:
    """How one month's amounts reach the base currency: each other currency's rate in force that
    month (rates.rate_for). A currency without a rate is `missing`: its amounts count as 0 until
    the user enters a rate, rather than being guessed."""
    base: str
    rates: dict[str, rates_repo.Rate | None] = field(default_factory=dict)

    def factor(self, code: str | None) -> Decimal:
        if code is None or code == self.base:
            return Decimal(1)
        r = self.rates.get(code)
        return Decimal(str(r.rate)) if r else Decimal(0)

    def convert(self, x, code: str | None) -> Decimal | None:
        if x is None:
            return None
        amount = Decimal(str(x))
        if code is None or code == self.base:
            return amount
        return (amount * self.factor(code)).quantize(CENT, ROUND_HALF_UP)

    @property
    def missing(self) -> list[str]:
        return sorted(c for c, r in self.rates.items() if r is None)

    def info(self, code: str | None) -> dict | None:
        """For the page: the rate an item of `code` uses this month (None in the base currency)."""
        if code is None or code == self.base:
            return None
        r = self.rates.get(code)
        return {"rate": r.rate if r else None, "source": r.source if r else None,
                "from_ym": r.from_ym if r else None, "missing": r is None}


def month_conversion(conn: sqlite3.Connection, user_id: int, budget: dict) -> Conversion:
    base_currency = settings.get_settings(conn, user_id)["currency"]
    codes = {row[0] for table in ("incomes", "expenses", "goals", "budget_balances") for row in conn.execute(
        f"SELECT DISTINCT currency FROM {table} WHERE user_id = ? AND budget_id = ? AND currency IS NOT NULL",
        (user_id, budget["id"]))}
    codes.discard(base_currency)
    return Conversion(base_currency, {c: rates_repo.rate_for(conn, user_id, base_currency, c, budget["year"],
                                                              budget["month"]) for c in sorted(codes)})


def expense_model(r: dict, type_name: str, conv: Conversion | None = None) -> Expense:
    """An expense row for the engine; with `conv`, its amounts in the base currency."""
    cur = r.get("currency")
    m = (lambda x: conv.convert(x, cur)) if conv else _money
    return Expense(name=r["name"], type_name=type_name, cash=m(r["cash_amount"]),
                   inst_balance=m(r["inst_balance"]), inst_count=r["inst_count"],
                   carried_in=m(r["carried_in"]), status=r["status"],
                   deferred_amount=m(r["deferred_amount"]),
                   paid_at=date.fromisoformat(r["paid_at"]) if r["paid_at"] else None,
                   needs_amount=r["needs_amount"], id=r["id"])


def expense_amounts(r: dict) -> ExpenseLine:
    """due / deferred / effective of one expense row, in the row's own currency (role doesn't
    matter for these)."""
    return expense_line(expense_model(r, ""), Role.OTHER)


def foreign_balances(conn: sqlite3.Connection, user_id: int, budget_id: int, conv: Conversion) -> dict[str, Decimal]:
    """The month's savings / emergency money held in other currencies, in the base currency."""
    out = {"savings": Decimal(0), "emergency": Decimal(0)}
    for r in base.list_rows(conn, user_id, "budget_balances", budget_id=budget_id):
        out[r["kind"]] += conv.convert(r["amount"], r["currency"])
    return out


def load_budget_input(conn: sqlite3.Connection, user_id: int, budget_id: int,
                      conv: Conversion | None = None) -> BudgetInput:
    """Everything in the base currency: items in other currencies are converted at their
    month's rate, and balances held in other currencies are added to savings / emergency."""
    budget = base.get(conn, user_id, "budgets", budget_id)
    conv = conv or month_conversion(conn, user_id, budget)
    types = base.list_rows(conn, user_id, "expense_types")
    type_names = {t["id"]: t["name"] for t in types}
    categories = {c["id"]: c["name"] for c in base.list_rows(conn, user_id, "goal_categories")}

    incomes = tuple(
        Income(source=r["source"], amount=conv.convert(r["amount"], r["currency"]),
               is_savings_withdrawal=r["is_savings_withdrawal"], needs_amount=r["needs_amount"], id=r["id"])
        for r in base.list_rows(conn, user_id, "incomes", budget_id=budget_id))
    expenses = tuple(expense_model(r, type_names[r["type_id"]], conv)
                     for r in base.list_rows(conn, user_id, "expenses", budget_id=budget_id))
    goals = tuple(
        Goal(name=r["name"], target=conv.convert(r["target_amount"], r["currency"]), priority=_money(r["priority"]),
             manual_amount=conv.convert(r["manual_allocation"], r["currency"]),
             category=categories.get(r["category_id"]), id=r["id"])
        for r in base.list_rows(conn, user_id, "goals", budget_id=budget_id))
    foreign = foreign_balances(conn, user_id, budget_id, conv)

    return BudgetInput(
        year=budget["year"], month=budget["month"],
        settings=settings.to_engine(settings.get_settings(conn, user_id)),
        types=tuple(ExpenseType(t["name"], Role(t["role"])) for t in types),
        incomes=incomes, expenses=expenses, goals=goals,
        emergency_current=_money(budget["emergency_current"]) + foreign["emergency"],
        emergency_min=_money(budget["emergency_min"]),
        savings_current=_money(budget["savings_current"]) + foreign["savings"],
    )
