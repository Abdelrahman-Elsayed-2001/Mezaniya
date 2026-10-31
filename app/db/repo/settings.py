"""Per-user settings, stored as key/value rows (user_settings) over defaults (default_settings).

get_settings() still returns one flat dict ({"user_id": …, "excellent_rate": 0.2, …, "currency": …})
so callers don't see the storage. SQLite can't type or CHECK a TEXT value, so every key is
declared in SPECS below and values are validated here before they are written.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from app.db.connection import transaction
from app.engine import Settings

from .base import Conflict, NotFound


@dataclass(frozen=True)
class Spec:
    type: str                    # 'rate' | 'number' | 'text' | 'json', as in default_settings.type
    min: float | None = None
    max: float | None = None
    max_len: int = 200
    on_create: bool = False      # written for the user at registration (later default changes skip them)


_RATE = Spec("rate", min=0, on_create=True)
SPECS: dict[str, Spec] = {
    "excellent_rate": _RATE,
    "good_rate": _RATE,
    "max_debt_rate": _RATE,
    "max_luxury_rate": _RATE,
    "emergency_cut_rate": _RATE,
    "saving_rate_em_incomplete": _RATE,
    "saving_rate_em_complete": _RATE,
    "emergency_ref_months": Spec("number", min=0, on_create=True),
    "emergency_ref_factor": Spec("number", min=0, on_create=True),
    "currency": Spec("text", max_len=10),
}

RATE_FIELDS = (
    "excellent_rate", "good_rate", "max_debt_rate", "max_luxury_rate", "emergency_cut_rate",
    "saving_rate_em_incomplete", "saving_rate_em_complete", "emergency_ref_months", "emergency_ref_factor",
)
FIELDS = frozenset(SPECS)


def _encode(key: str, value) -> str:
    """Validate one value and turn it into its stored text. Bad values raise Conflict,
    as the old column CHECKs did."""
    spec = SPECS[key]
    if spec.type in ("rate", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise Conflict(f"user_settings: {key} must be a number", "integrity")
        try:
            x = Decimal(str(value))
        except ArithmeticError as e:
            raise Conflict(f"user_settings: {key} must be a number", "integrity") from e
        if not x.is_finite():
            raise Conflict(f"user_settings: {key} must be finite", "integrity")
        if (spec.min is not None and x < Decimal(str(spec.min))) or \
                (spec.max is not None and x > Decimal(str(spec.max))):
            raise Conflict(f"user_settings: {key} out of range", "integrity")
        return str(x)
    if not isinstance(value, str):
        raise Conflict(f"user_settings: {key} must be text", "integrity")
    if len(value) > spec.max_len:
        raise Conflict(f"user_settings: {key} is too long", "integrity")
    return value


def _decode(key: str, text: str):
    return float(text) if SPECS[key].type in ("rate", "number") else text


def _write(conn: sqlite3.Connection, user_id: int, rows: dict[str, str]) -> None:
    with transaction(conn):
        conn.executemany(
            "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value",
            [(user_id, k, v) for k, v in rows.items()])


def create_settings(conn: sqlite3.Connection, user_id: int, currency: str) -> None:
    """Write the user's currency, plus the financial defaults as they are today, so a later
    change to default_settings doesn't silently change this user's past and future months."""
    keys = [k for k, s in SPECS.items() if s.on_create]
    marks = ", ".join("?" * len(keys))
    defaults = dict(conn.execute(
        f"SELECT key, default_value FROM default_settings WHERE key IN ({marks})", keys).fetchall())
    _write(conn, user_id, {**defaults, "currency": _encode("currency", currency)})


def get_settings(conn: sqlite3.Connection, user_id: int) -> dict:
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise NotFound("user_settings", user_id)
    rows = conn.execute(
        "SELECT d.key, COALESCE(u.value, d.default_value) AS value FROM default_settings d"
        " LEFT JOIN user_settings u ON u.key = d.key AND u.user_id = ?", (user_id,)).fetchall()
    out = {"user_id": user_id}
    out.update({row["key"]: _decode(row["key"], row["value"]) for row in rows if row["key"] in SPECS})
    return out


def update_settings(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    unknown = set(values) - FIELDS
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    if values:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise NotFound("user_settings", user_id)
        _write(conn, user_id, {k: _encode(k, v) for k, v in values.items()})
    return get_settings(conn, user_id)


def reset_settings(conn: sqlite3.Connection, user_id: int, keys: list[str]) -> dict:
    """Back to the default for these keys: their rows are removed."""
    unknown = set(keys) - FIELDS
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    with transaction(conn):
        conn.executemany("DELETE FROM user_settings WHERE user_id = ? AND key = ?",
                         [(user_id, k) for k in keys])
    return get_settings(conn, user_id)


def to_engine(row: dict) -> Settings:
    return Settings(**{f: Decimal(str(row[f])) for f in RATE_FIELDS})
