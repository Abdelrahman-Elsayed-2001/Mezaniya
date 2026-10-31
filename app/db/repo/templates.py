"""Recurring items (income / expense templates) per user."""
from __future__ import annotations

import sqlite3

from . import base


def list_templates(conn: sqlite3.Connection, user_id: int, kind: str | None = None) -> list[dict]:
    return base.list_rows(conn, user_id, "recurring_templates", **({"kind": kind} if kind else {}))


def create_template(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    return base.insert(conn, user_id, "recurring_templates", values)


def update_template(conn: sqlite3.Connection, user_id: int, template_id: int, values: dict) -> dict:
    return base.update(conn, user_id, "recurring_templates", template_id, values)


def delete_template(conn: sqlite3.Connection, user_id: int, template_id: int) -> None:
    """Items created from it keep existing, just unlinked (trigger)."""
    base.delete(conn, user_id, "recurring_templates", template_id)
