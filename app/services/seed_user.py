"""Initial data for a newly registered user, named in the user's language."""
from __future__ import annotations

import sqlite3
from datetime import date

from app.db.connection import transaction
from app.db.repo import base, budgets, settings, users
from app.engine import DEFAULT_TYPE_KEYS, Role
from app.i18n import t

DEFAULT_GOAL_CATEGORY_KEYS = ("essential", "luxury", "investment", "other")


def seed_user(conn: sqlite3.Connection, user_id: int, today: date, lang: str) -> dict:
    """Settings, expense types, goal categories and the current month. Returns the month."""
    with transaction(conn):
        settings.create_settings(conn, user_id, t("seed.currency", lang))
        for key, role in DEFAULT_TYPE_KEYS:
            base.insert(conn, user_id, "expense_types",
                        {"name": t(f"seed.type.{key}", lang), "role": role.value, "is_system": role != Role.OTHER})
        for key in DEFAULT_GOAL_CATEGORY_KEYS:
            base.insert(conn, user_id, "goal_categories", {"name": t(f"seed.category.{key}", lang)})
        return budgets.create_budget(conn, user_id, today.year, today.month)


def create_seeded_user(conn: sqlite3.Connection, username: str, password_hash: str, today: date,
                       lang: str) -> int:
    with transaction(conn):
        user_id = users.create_user(conn, username, password_hash)
        seed_user(conn, user_id, today, lang)
    return user_id
