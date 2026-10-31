from __future__ import annotations

import sqlite3
from decimal import Decimal

from app.engine import Settings

from .base import Conflict, NotFound

RATE_FIELDS = (
    "excellent_rate", "good_rate", "max_debt_rate", "max_luxury_rate", "emergency_cut_rate",
    "saving_rate_em_incomplete", "saving_rate_em_complete", "emergency_ref_months", "emergency_ref_factor",
)
FIELDS = frozenset((*RATE_FIELDS, "currency"))


def create_settings(conn: sqlite3.Connection, user_id: int, currency: str) -> None:
    conn.execute("INSERT INTO user_settings (user_id, currency) VALUES (?, ?)", (user_id, currency))


def get_settings(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise NotFound("user_settings", user_id)
    return dict(row)


def update_settings(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    unknown = set(values) - FIELDS
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    if values:
        sets = ", ".join(f"{c} = ?" for c in values)
        try:
            cur = conn.execute(f"UPDATE user_settings SET {sets} WHERE user_id = ?", (*values.values(), user_id))
        except sqlite3.IntegrityError as e:
            raise Conflict(f"user_settings: {e}", "integrity") from e
        if cur.rowcount == 0:
            raise NotFound("user_settings", user_id)
    return get_settings(conn, user_id)


def to_engine(row: dict) -> Settings:
    return Settings(**{f: Decimal(str(row[f])) for f in RATE_FIELDS})
