"""Keeps deferred amounts in sync with the month copied from this one (PLAN.md §4.3).

Once a month has been rolled over into a new month, that new month's `carried_in` on the
line continuing an expense always equals what the expense defers. Changing / clearing /
deleting a deferral updates it, creates a carry line if nothing continues the expense, or
removes a carry-only line that has nothing left. A change that pushes a carried line's own
deferral over its new due amount is clamped and propagated further down the chain.

Only the rollover link (budgets.source_budget_id) is followed: a month added later in
between, or an older month, never receives or sends automatic carry-overs.
"""
from __future__ import annotations

import sqlite3

from app.db.repo import base, budgets
from app.db.repo.loader import expense_amounts
from app.i18n import t as tr

from .rollover import label

MAX_DEPTH = 240


def _is_carry_only(row: dict) -> bool:
    """A line that only holds a carried amount (possibly passed on again) and wasn't paid."""
    return (row["cash_amount"] == 0 and row["inst_balance"] == 0 and row["inst_count"] == 0
            and row["template_id"] is None and row["status"] in ("unpaid", "deferred"))


def sync_expense(conn: sqlite3.Connection, user_id: int, expense_id: int, *, deleting: bool = False,
                 lang: str | None = None, _depth: int = 0) -> None:
    """Call inside the write's transaction, after an update/create — or before a delete with deleting=True."""
    if _depth > MAX_DEPTH:
        return
    row = base.get(conn, user_id, "expenses", expense_id)
    month = budgets.get_budget(conn, user_id, row["budget_id"])
    copies = budgets.followers(conn, user_id, month["id"])
    if not copies:
        return
    nxt = copies[0]
    amount = 0.0 if deleting else float(round(expense_amounts(row).deferred, 2))
    linked = [r for r in base.list_rows(conn, user_id, "expenses", budget_id=nxt["id"])
              if r["prev_expense_id"] == expense_id]

    if not linked:
        if amount > 0:
            base.insert(conn, user_id, "expenses", {
                "budget_id": nxt["id"], "name": row["name"], "type_id": row["type_id"], "carried_in": amount,
                "prev_expense_id": expense_id,
                "notes": tr("rollover.carry_note", lang, label=label(month["year"], month["month"]))})
        return

    target = linked[0]
    if target["carried_in"] == amount:
        return
    if amount == 0 and _is_carry_only(target):
        sync_expense(conn, user_id, target["id"], deleting=True, lang=lang, _depth=_depth + 1)
        base.delete(conn, user_id, "expenses", target["id"])
        return
    changes = {"carried_in": amount}
    updated = {**target, **changes}
    if updated["status"] == "deferred":
        due = float(round(expense_amounts(updated).due, 2))
        if updated["deferred_amount"] > due:
            changes["deferred_amount"] = due
            if due == 0:
                changes["status"] = "unpaid"
                changes["deferred_amount"] = 0
    base.update(conn, user_id, "expenses", target["id"], changes)
    if "deferred_amount" in changes:
        sync_expense(conn, user_id, target["id"], lang=lang, _depth=_depth + 1)
