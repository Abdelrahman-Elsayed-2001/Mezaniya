"""Expense types and goal categories (per user).

Rows made at registration have a name_key and are shown translated (see base.row_to_dict);
renaming one stores the new name, and renaming it back to one of its translations clears it
again so it follows the language once more. Names must stay distinct in every language, since
the engine and the dashboard tell types apart by their shown name.
"""
from __future__ import annotations

import sqlite3

from app.i18n import languages

from . import base
from .base import Conflict, translated_name


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def _all_names(table: str, row: dict) -> set[str]:
    """Every name this row can show: its own, or its translations in all languages."""
    if row["custom_name"]:
        return {_norm(row["name"])}
    prefix = base.TABLES[table].name_prefix
    return {_norm(translated_name(prefix, row["name_key"], lang)) for lang in languages()}


def _prepare_name(conn: sqlite3.Connection, user_id: int, table: str, values: dict,
                  current: dict | None = None) -> dict:
    if "name_key" in values:
        raise ValueError("name_key can't be set")
    if "name" not in values:
        return values
    name = _norm(values["name"])
    values = dict(values)
    if current is not None and current["name_key"]:
        own = {_norm(translated_name(base.TABLES[table].name_prefix, current["name_key"], lang))
               for lang in languages()}
        if name in own:
            values["name"] = None          # back to the translated name
            return values
    for row in base.list_rows(conn, user_id, table):
        if (current is None or row["id"] != current["id"]) and name in _all_names(table, row):
            raise Conflict(f"{table}: name already used", "duplicate")
    return values


def list_expense_types(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return base.list_rows(conn, user_id, "expense_types")


def create_expense_type(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    if "is_system" in values:
        raise ValueError("is_system can't be set")
    values = _prepare_name(conn, user_id, "expense_types", values)
    return base.insert(conn, user_id, "expense_types", values)


def update_expense_type(conn: sqlite3.Connection, user_id: int, type_id: int, values: dict) -> dict:
    if "is_system" in values:
        raise ValueError("is_system can't be changed")
    current = base.get(conn, user_id, "expense_types", type_id)
    if current["is_system"] and "role" in values and values["role"] != current["role"]:
        raise Conflict("system type role can't be changed", "system_role")
    values = _prepare_name(conn, user_id, "expense_types", values, current)
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
    values = _prepare_name(conn, user_id, "goal_categories", values)
    return base.insert(conn, user_id, "goal_categories", values)


def update_goal_category(conn: sqlite3.Connection, user_id: int, category_id: int, values: dict) -> dict:
    current = base.get(conn, user_id, "goal_categories", category_id)
    values = _prepare_name(conn, user_id, "goal_categories", values, current)
    return base.update(conn, user_id, "goal_categories", category_id, values)


def delete_goal_category(conn: sqlite3.Connection, user_id: int, category_id: int) -> None:
    """Goals in this category become uncategorised (trigger)."""
    base.delete(conn, user_id, "goal_categories", category_id)
