"""Months (budgets) and their items: incomes, expenses, goals."""
from __future__ import annotations

import sqlite3

from . import base

BALANCE_FIELDS = frozenset({"emergency_current", "emergency_min", "savings_current"})
ITEM_TABLES = ("incomes", "expenses", "goals")


def create_budget(conn: sqlite3.Connection, user_id: int, year: int, month: int,
                  source_budget_id: int | None = None, **balances) -> dict:
    """source_budget_id: the month this one is copied from (rollover), if any."""
    unknown = set(balances) - BALANCE_FIELDS
    if unknown:
        raise ValueError(f"unknown balances: {sorted(unknown)}")
    return base.insert(conn, user_id, "budgets",
                       {"year": year, "month": month, "source_budget_id": source_budget_id, **balances})


def followers(conn: sqlite3.Connection, user_id: int, budget_id: int) -> list[dict]:
    """Months that were copied from this one."""
    return base.list_rows(conn, user_id, "budgets", source_budget_id=budget_id)


def list_budgets(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return base.list_rows(conn, user_id, "budgets")


def get_budget(conn: sqlite3.Connection, user_id: int, budget_id: int) -> dict:
    return base.get(conn, user_id, "budgets", budget_id)


def find_budget(conn: sqlite3.Connection, user_id: int, year: int, month: int) -> dict | None:
    rows = base.list_rows(conn, user_id, "budgets", year=year, month=month)
    return rows[0] if rows else None


def previous_budget(conn: sqlite3.Connection, user_id: int, year: int, month: int) -> dict | None:
    """The latest month strictly before (year, month)."""
    row = conn.execute(
        "SELECT * FROM budgets WHERE user_id = ? AND (year * 12 + month) < ? ORDER BY year DESC, month DESC LIMIT 1",
        (user_id, year * 12 + month)).fetchone()
    return base.row_to_dict(base.TABLES["budgets"], row) if row else None


def next_budget(conn: sqlite3.Connection, user_id: int, year: int, month: int) -> dict | None:
    """The earliest month strictly after (year, month)."""
    row = conn.execute(
        "SELECT * FROM budgets WHERE user_id = ? AND (year * 12 + month) > ? ORDER BY year, month LIMIT 1",
        (user_id, year * 12 + month)).fetchone()
    return base.row_to_dict(base.TABLES["budgets"], row) if row else None


def update_budget(conn: sqlite3.Connection, user_id: int, budget_id: int, values: dict) -> dict:
    unknown = set(values) - BALANCE_FIELDS
    if unknown:
        raise ValueError(f"only balances can be changed, got {sorted(unknown)}")
    return base.update(conn, user_id, "budgets", budget_id, values)


def delete_budget(conn: sqlite3.Connection, user_id: int, budget_id: int) -> None:
    base.delete(conn, user_id, "budgets", budget_id)


def list_items(conn: sqlite3.Connection, user_id: int, table: str, budget_id: int) -> list[dict]:
    if table not in ITEM_TABLES:
        raise ValueError(table)
    base.get(conn, user_id, "budgets", budget_id)   # NotFound for someone else's month
    return base.list_rows(conn, user_id, table, budget_id=budget_id)


def get_item(conn: sqlite3.Connection, user_id: int, table: str, budget_id: int, item_id: int) -> dict:
    """An item addressed through its month: the item must belong to that month and user."""
    if table not in ITEM_TABLES:
        raise ValueError(table)
    item = base.get(conn, user_id, table, item_id)
    if item["budget_id"] != budget_id:
        raise base.NotFound(table, item_id)
    return item


def create_item(conn: sqlite3.Connection, user_id: int, table: str, budget_id: int, values: dict) -> dict:
    if table not in ITEM_TABLES:
        raise ValueError(table)
    if "budget_id" in values:
        raise ValueError("budget_id comes from the path")
    return base.insert(conn, user_id, table, {**values, "budget_id": budget_id})


def update_item(conn: sqlite3.Connection, user_id: int, table: str, budget_id: int, item_id: int,
                values: dict) -> dict:
    if "budget_id" in values:
        raise ValueError("items can't move between months")
    get_item(conn, user_id, table, budget_id, item_id)
    return base.update(conn, user_id, table, item_id, values)


def delete_item(conn: sqlite3.Connection, user_id: int, table: str, budget_id: int, item_id: int) -> None:
    get_item(conn, user_id, table, budget_id, item_id)
    base.delete(conn, user_id, table, item_id)
