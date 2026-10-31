"""Expense types and goal categories (per user)."""
from __future__ import annotations

import sqlite3

from . import base
from .base import Conflict


def list_expense_types(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return base.list_rows(conn, user_id, "expense_types")


def create_expense_type(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    if "is_system" in values:
        raise ValueError("is_system can't be set")
    return base.insert(conn, user_id, "expense_types", values)


def update_expense_type(conn: sqlite3.Connection, user_id: int, type_id: int, values: dict) -> dict:
    if "is_system" in values:
        raise ValueError("is_system can't be changed")
    current = base.get(conn, user_id, "expense_types", type_id)
    if current["is_system"] and "role" in values and values["role"] != current["role"]:
        raise Conflict("system type role can't be changed", "system_role")
    return base.update(conn, user_id, "expense_types", type_id, values)


def expense_type_in_use(conn: sqlite3.Connection, user_id: int, type_id: int) -> bool:
    sql = ("SELECT EXISTS (SELECT 1 FROM expenses WHERE user_id = ? AND type_id = ?)"
           " OR EXISTS (SELECT 1 FROM recurring_templates WHERE user_id = ? AND type_id = ?)")
    return bool(conn.execute(sql, (user_id, type_id, user_id, type_id)).fetchone()[0])


def delete_expense_type(conn: sqlite3.Connection, user_id: int, type_id: int) -> None:
    current = base.get(conn, user_id, "expense_types", type_id)
    if current["is_system"]:
        raise Conflict("system type can't be deleted", "system_type")
    if expense_type_in_use(conn, user_id, type_id):
        raise Conflict("type is in use", "in_use")
    base.delete(conn, user_id, "expense_types", type_id)


def list_goal_categories(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return base.list_rows(conn, user_id, "goal_categories")


def create_goal_category(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    return base.insert(conn, user_id, "goal_categories", values)


def update_goal_category(conn: sqlite3.Connection, user_id: int, category_id: int, values: dict) -> dict:
    return base.update(conn, user_id, "goal_categories", category_id, values)


def delete_goal_category(conn: sqlite3.Connection, user_id: int, category_id: int) -> None:
    """Goals in this category become uncategorised (trigger)."""
    base.delete(conn, user_id, "goal_categories", category_id)
