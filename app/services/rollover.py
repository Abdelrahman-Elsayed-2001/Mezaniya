"""Creating the next month from the latest one (PLAN.md §4.2).

What moves to the new month:
* balances: emergency after this month's contributions, savings after withdrawal
  (+ optionally the amount the user returned to savings);
* recurring items (templates): fixed price, or 0 + "needs amount" for variable prices;
* installments: one payment less — balance and count both shrink, the monthly share stays;
  the cash part of an installment row is a one-off and is not copied;
* deferred amounts: added as `carried_in` to the line that continues the deferred one,
  or to a new line if nothing continues it;
* unpaid items the user chose to carry: the old month marks them deferred (whole amount);
* goals: copied as they are (optionally without the completed ones).
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from app.db.connection import transaction
from app.db.repo import base, budgets, templates
from app.db.repo.loader import load_budget_input
from app.engine import compute
from app.i18n import t as tr


class RolloverError(Exception):
    """`code` maps to the message "rollover.err.<code>" (filled with `params`)."""

    def __init__(self, code: str, status: int = 409, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


@dataclass
class PlannedIncome:
    source: str
    amount: float
    is_savings_withdrawal: bool
    needs_amount: bool
    template_id: int | None
    notes: str | None


@dataclass
class PlannedExpense:
    origin: str                       # template | installment | carry
    name: str
    type_id: int
    cash_amount: float = 0
    inst_balance: float = 0
    inst_count: int = 0
    inst_original_count: int | None = None
    carried_in: float = 0
    needs_amount: bool = False
    template_id: int | None = None
    prev_expense_id: int | None = None
    notes: str | None = None


@dataclass
class PlannedGoal:
    name: str
    category_id: int | None
    target_amount: float | None
    priority: float | None
    manual_allocation: float | None
    notes: str | None
    completed: bool


@dataclass
class RolloverPlan:
    source: dict
    year: int
    month: int
    balances: dict
    return_to_savings: float
    incomes: list[PlannedIncome] = field(default_factory=list)
    expenses: list[PlannedExpense] = field(default_factory=list)
    goals: list[PlannedGoal] = field(default_factory=list)
    unpaid: list[dict] = field(default_factory=list)      # candidates for "carry it"

    def to_json(self) -> dict:
        return {
            "source": {"id": self.source["id"], "year": self.source["year"], "month": self.source["month"],
                       "label": label(self.source["year"], self.source["month"])},
            "target": {"year": self.year, "month": self.month, "label": label(self.year, self.month)},
            "balances": self.balances,
            "return_to_savings": self.return_to_savings,
            "incomes": [asdict(i) for i in self.incomes],
            "expenses": [asdict(e) for e in self.expenses],
            "goals": [asdict(g) for g in self.goals],
            "unpaid": self.unpaid,
        }


def label(year: int, month: int) -> str:
    return f"{month:02d}/{year}"


def next_period(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _f(x: Decimal) -> float:
    return float(round(x, 2))


def latest_budget(conn: sqlite3.Connection, user_id: int) -> dict | None:
    months = budgets.list_budgets(conn, user_id)
    return months[-1] if months else None


def _source_for(conn, user_id: int, year: int, month: int) -> dict:
    """The new month must come after every existing month; its source is the latest one."""
    if budgets.find_budget(conn, user_id, year, month):
        raise RolloverError("duplicate")
    latest = latest_budget(conn, user_id)
    if latest is None:
        raise RolloverError("no_previous")
    if (year, month) <= (latest["year"], latest["month"]):
        raise RolloverError("not_after_latest", label=label(latest["year"], latest["month"]))
    return latest


def plan_rollover(conn: sqlite3.Connection, user_id: int, year: int, month: int,
                  carry_unpaid_ids=(), lang: str | None = None) -> RolloverPlan:
    src = _source_for(conn, user_id, year, month)
    carry_note = tr("rollover.carry_note", lang, label=label(src["year"], src["month"]))
    d = compute(load_budget_input(conn, user_id, src["id"]))
    rows = base.list_rows(conn, user_id, "expenses", budget_id=src["id"])
    active = [t for t in templates.list_templates(conn, user_id) if t["active"]]
    expense_templates = {t["id"]: t for t in active if t["kind"] == "expense"}

    unpaid = [{"id": r["id"], "name": r["name"], "due": _f(line.due)}
              for r, line in zip(rows, d.expenses) if r["status"] == "unpaid" and line.due > 0]
    carry_unpaid_ids = set(carry_unpaid_ids)
    unknown = carry_unpaid_ids - {u["id"] for u in unpaid}
    if unknown:
        raise RolloverError("invalid", status=422)

    def template_line(t: dict, prev_id: int | None) -> PlannedExpense:
        return PlannedExpense("template", t["name"], t["type_id"], cash_amount=t["amount"] or 0,
                              needs_amount=t["amount"] is None, template_id=t["id"],
                              prev_expense_id=prev_id, notes=t["notes"])

    plan = RolloverPlan(
        source=src, year=year, month=month,
        balances={"emergency_current": _f(d.emergency.after), "emergency_min": src["emergency_min"],
                  "savings_current": _f(max(Decimal(0), d.savings.total))},
        return_to_savings=_f(d.suggestions.return_to_savings),
        unpaid=unpaid,
    )

    used_templates: set[int] = set()
    for r, line in zip(rows, d.expenses):
        tid = r["template_id"]
        out: PlannedExpense | None = None
        if tid in expense_templates and tid not in used_templates:
            out = template_line(expense_templates[tid], r["id"])
            used_templates.add(tid)
        elif r["inst_count"] > 1:
            balance, count = Decimal(str(r["inst_balance"])), r["inst_count"]
            out = PlannedExpense("installment", r["name"], r["type_id"],
                                 inst_balance=_f(balance - balance / count), inst_count=count - 1,
                                 inst_original_count=r["inst_original_count"], prev_expense_id=r["id"],
                                 notes=r["notes"])
        carry = line.due if r["id"] in carry_unpaid_ids else line.deferred
        if carry > 0:
            if out is None:
                out = PlannedExpense("carry", r["name"], r["type_id"], prev_expense_id=r["id"], notes=carry_note)
            out.carried_in = _f(carry)
        if out is not None:
            plan.expenses.append(out)
    plan.expenses += [template_line(t, None) for tid, t in expense_templates.items() if tid not in used_templates]

    plan.incomes = [PlannedIncome(t["name"], t["amount"] or 0, t["is_savings_withdrawal"], t["amount"] is None,
                                  t["id"], t["notes"])
                    for t in active if t["kind"] == "income"]

    goal_rows = base.list_rows(conn, user_id, "goals", budget_id=src["id"])
    plan.goals = [PlannedGoal(g["name"], g["category_id"], g["target_amount"], g["priority"],
                              g["manual_allocation"], g["notes"], completed=line.valid and line.remaining == 0)
                  for g, line in zip(goal_rows, d.goals.lines)]
    return plan


def create_from_previous(conn: sqlite3.Connection, user_id: int, year: int, month: int, *,
                         lang: str | None = None, carry_unpaid_ids=(), add_return_to_savings: bool = False,
                         drop_completed_goals: bool = False, balances: dict | None = None) -> dict:
    with transaction(conn):
        plan = plan_rollover(conn, user_id, year, month, carry_unpaid_ids, lang)
        for row_id in set(carry_unpaid_ids):
            due = next(u["due"] for u in plan.unpaid if u["id"] == row_id)
            base.update(conn, user_id, "expenses", row_id, {"status": "deferred", "deferred_amount": due})

        opening = dict(plan.balances)
        if add_return_to_savings:
            opening["savings_current"] = round(opening["savings_current"] + plan.return_to_savings, 2)
        opening.update(balances or {})
        budget = budgets.create_budget(conn, user_id, year, month, source_budget_id=plan.source["id"], **opening)

        for i in plan.incomes:
            budgets.create_item(conn, user_id, "incomes", budget["id"], asdict(i))
        for e in plan.expenses:
            values = asdict(e)
            del values["origin"]
            budgets.create_item(conn, user_id, "expenses", budget["id"], values)
        for g in plan.goals:
            if drop_completed_goals and g.completed:
                continue
            values = asdict(g)
            del values["completed"]
            budgets.create_item(conn, user_id, "goals", budget["id"], values)
    return budget


def apply_templates(conn: sqlite3.Connection, user_id: int, budget_id: int) -> int:
    """Add active recurring items missing from a month (matched by template). Returns how many were added."""
    budget = budgets.get_budget(conn, user_id, budget_id)
    prev = budgets.previous_budget(conn, user_id, budget["year"], budget["month"])
    added = 0
    with transaction(conn):
        present = {r["template_id"] for t in ("incomes", "expenses")
                   for r in base.list_rows(conn, user_id, t, budget_id=budget_id) if r["template_id"]}
        prev_rows = {r["template_id"]: r["id"] for r in reversed(base.list_rows(
            conn, user_id, "expenses", budget_id=prev["id"])) if r["template_id"]} if prev else {}
        for t in templates.list_templates(conn, user_id):
            if not t["active"] or t["id"] in present:
                continue
            if t["kind"] == "income":
                budgets.create_item(conn, user_id, "incomes", budget_id, {
                    "source": t["name"], "amount": t["amount"] or 0, "needs_amount": t["amount"] is None,
                    "is_savings_withdrawal": t["is_savings_withdrawal"], "template_id": t["id"], "notes": t["notes"]})
            else:
                budgets.create_item(conn, user_id, "expenses", budget_id, {
                    "name": t["name"], "type_id": t["type_id"], "cash_amount": t["amount"] or 0,
                    "needs_amount": t["amount"] is None, "template_id": t["id"], "notes": t["notes"],
                    "prev_expense_id": prev_rows.get(t["id"])})
            added += 1
    return added
