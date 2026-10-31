"""A ready-made demo user with three months of realistic data, to try the app.

The months are built the way a real user would: recurring items, installments, partial
carry-overs, unpaid items carried by the new-month wizard, and payments with dates.
Item names come from strings.json ("demo.*") in the requested language.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date

from app.db.connection import transaction
from app.db.repo import base, budgets, templates, users
from app.engine import DEFAULT_TYPE_KEYS
from app.i18n import t as tr

from . import admin, rollover

DEMO_USERNAME = "test"
DEMO_PASSWORD = "test123"
DEMO_MONTHS = 3


def _months_ending(today: date, count: int) -> list[tuple[int, int]]:
    year, month = today.year, today.month
    out = []
    for _ in range(count):
        out.append((year, month))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return out[::-1]


class _Builder:
    def __init__(self, conn: sqlite3.Connection, user_id: int, lang: str, today: date):
        self.conn, self.uid, self.lang, self.today = conn, user_id, lang, today
        keys = {r["name_key"]: r["id"] for r in base.list_rows(conn, user_id, "expense_types")}
        self.type = {key: keys[key] for key, _role in DEFAULT_TYPE_KEYS}
        cats = {r["name_key"]: r["id"] for r in base.list_rows(conn, user_id, "goal_categories")}
        self.category = {key: cats[key]
                         for key in ("essential", "luxury", "investment", "other")}

    def name(self, key: str) -> str:
        return tr(f"demo.{key}", self.lang)

    def day(self, year: int, month: int, d: int) -> str:
        return date(year, month, min(d, calendar.monthrange(year, month)[1])).isoformat()

    def template(self, kind: str, key: str, amount: float | None, type_key: str | None = None) -> None:
        values = {"kind": kind, "name": self.name(key), "amount": amount}
        if type_key:
            values["type_id"] = self.type[type_key]
        templates.create_template(self.conn, self.uid, values)

    def expense(self, budget_id: int, key: str, type_key: str, **values) -> dict:
        if values.get("inst_count"):
            values.setdefault("inst_original_count", values["inst_count"])
        return budgets.create_item(self.conn, self.uid, "expenses", budget_id,
                                   {"name": self.name(key), "type_id": self.type[type_key], **values})

    def line(self, budget_id: int, key: str) -> dict:
        name = self.name(key)
        return next(r for r in budgets.list_items(self.conn, self.uid, "expenses", budget_id) if r["name"] == name)

    def update(self, budget_id: int, key: str, **values) -> None:
        row = self.line(budget_id, key)
        if "cash_amount" in values:
            values.setdefault("needs_amount", False)
        budgets.update_item(self.conn, self.uid, "expenses", budget_id, row["id"], values)

    def pay(self, budget: dict, plan: dict[str, int], up_to: int | None = None) -> None:
        """Mark items paid on the given day of the month (only days up to `up_to`)."""
        for key, d in plan.items():
            if up_to is None or d <= up_to:
                self.update(budget["id"], key, status="paid", paid_at=self.day(budget["year"], budget["month"], d))


def create_demo_user(conn: sqlite3.Connection, lang: str | None = None, today: date | None = None,
                     replace: bool = False) -> int:
    """Creates user "test" / "test123". With replace=True an existing demo user is deleted first."""
    today = today or date.today()
    existing = users.find_by_username(conn, DEMO_USERNAME)
    if existing:
        if not replace:
            raise admin.AdminError("admin.err.demo_exists", name=DEMO_USERNAME)
        with transaction(conn):
            users.delete_user(conn, existing["id"])
    user_id = admin.create_user(conn, DEMO_USERNAME, DEMO_PASSWORD, lang, today)

    with transaction(conn):
        for b in budgets.list_budgets(conn, user_id):       # the demo builds its own months
            budgets.delete_budget(conn, user_id, b["id"])
        _fill(_Builder(conn, user_id, lang, today))
    return user_id


def _fill(b: _Builder) -> None:
    conn, uid, lang = b.conn, b.uid, b.lang
    (y1, m1), (y2, m2), (y3, m3) = _months_ending(b.today, DEMO_MONTHS)

    # recurring items
    b.template("income", "salary", 9000)
    b.template("expense", "rent", 2500, "essential")
    b.template("expense", "internet", 150, "essential")
    b.template("expense", "electricity", None, "essential")
    b.template("expense", "groceries", None, "essential")
    b.template("expense", "subscriptions", 60, "luxury")
    b.template("expense", "saving", 500, "savings")
    b.template("expense", "emergency", 400, "emergency")

    # ---- month 1
    first = budgets.create_budget(conn, uid, y1, m1, emergency_current=3000, emergency_min=15000,
                                  savings_current=6000)
    rollover.apply_templates(conn, uid, first["id"])
    budgets.create_item(conn, uid, "incomes", first["id"], {"source": b.name("bonus"), "amount": 750})
    b.expense(first["id"], "phone", "debt", inst_balance=3600, inst_count=12)
    b.expense(first["id"], "car", "debt", inst_balance=54000, inst_count=36)
    b.expense(first["id"], "outing", "luxury", cash_amount=450)
    gift = b.expense(first["id"], "gift", "luxury", cash_amount=200)
    b.expense(first["id"], "charity", "giving", cash_amount=150, notes=b.name("note_card"))
    b.update(first["id"], "electricity", cash_amount=320)
    b.update(first["id"], "groceries", cash_amount=1400)
    b.pay(first, {"rent": 1, "saving": 1, "emergency": 1, "car": 2, "internet": 3, "subscriptions": 5,
                  "groceries": 6, "phone": 10, "outing": 15, "charity": 20})
    b.update(first["id"], "electricity", status="deferred", deferred_amount=120,
             paid_at=b.day(y1, m1, 12))                                    # paid 200, 120 to next month
    for key, target, extra in (("goal_car", 60000, {"priority": 0.4, "category_id": b.category["essential"]}),
                               ("goal_trip", 8000, {"category_id": b.category["luxury"]}),
                               ("goal_laptop", 4500, {"manual_allocation": 1500,
                                                      "category_id": b.category["other"]})):
        budgets.create_item(conn, uid, "goals", first["id"], {"name": b.name(key), "target_amount": target, **extra})

    # ---- month 2: the unpaid gift is carried by the wizard
    second = rollover.create_from_previous(conn, uid, y2, m2, lang=lang, carry_unpaid_ids=[gift["id"]])
    b.update(second["id"], "electricity", cash_amount=290)
    b.update(second["id"], "groceries", cash_amount=1300)
    b.expense(second["id"], "outing", "luxury", cash_amount=600)
    b.expense(second["id"], "course", "luxury", cash_amount=900)
    b.pay(second, {"rent": 1, "saving": 1, "emergency": 1, "car": 2, "internet": 3, "subscriptions": 5,
                   "groceries": 7, "gift": 8, "phone": 10, "electricity": 14, "outing": 22})

    # ---- month 3 (the current month): only what is already due by today is paid
    third = rollover.create_from_previous(conn, uid, y3, m3, lang=lang)
    b.update(third["id"], "electricity", cash_amount=340)
    b.update(third["id"], "groceries", cash_amount=1350)
    b.pay(third, {"rent": 1, "saving": 1, "emergency": 1, "car": 2, "internet": 3, "subscriptions": 5,
                  "groceries": 6, "phone": 10, "electricity": 13}, up_to=b.today.day)
