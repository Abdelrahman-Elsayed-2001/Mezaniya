"""Per-user settings, stored as key/value rows (user_settings) over defaults (default_settings).

get_settings() still returns one flat dict ({"user_id": …, "excellent_rate": 0.2, …, "currency": …})
so callers don't see the storage. SQLite can't type or CHECK a TEXT value, so every key is
declared in SPECS below and values are validated here before they are written.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from app.db.connection import transaction
from app import themes
from app.engine import Settings

from .base import Conflict, NotFound


@dataclass(frozen=True)
class Spec:
    type: str                    # 'rate' | 'number' | 'text' | 'json', as in default_settings.type
    min: float | None = None
    max: float | None = None
    max_len: int = 200
    on_create: bool = False      # written for the user at registration (later default changes skip them)
    value2: str | None = None    # name under which this key's value2 (JSON) is read and written


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
    # a theme id, 'auto' or 'custom'; the custom colours live in value2 (theme_colors)
    "theme": Spec("text", max_len=40, value2="theme_colors"),
}
VALUE2 = {s.value2: k for k, s in SPECS.items() if s.value2}     # theme_colors → theme

RATE_FIELDS = (
    "excellent_rate", "good_rate", "max_debt_rate", "max_luxury_rate", "emergency_cut_rate",
    "saving_rate_em_incomplete", "saving_rate_em_complete", "emergency_ref_months", "emergency_ref_factor",
)
FIELDS = frozenset(SPECS) | frozenset(VALUE2)


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


def _check_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Checks that need the database."""
    if key == "theme" and value not in themes.SPECIAL and \
            conn.execute("SELECT 1 FROM themes WHERE id = ?", (value,)).fetchone() is None:
        raise Conflict(f"user_settings: unknown theme {value!r}", "integrity")


def _encode_value2(name: str, value) -> str | None:
    if value is None:
        return None
    if name == "theme_colors":
        try:
            return json.dumps(themes.parse_colors(value), separators=(",", ":"))
        except ValueError as e:
            raise Conflict(f"user_settings: {e}", "integrity") from e
    raise ValueError(name)


def _decode_value2(text: str | None):
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


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
        "SELECT d.key, COALESCE(u.value, d.default_value) AS value, u.value2 FROM default_settings d"
        " LEFT JOIN user_settings u ON u.key = d.key AND u.user_id = ?", (user_id,)).fetchall()
    out = {"user_id": user_id}
    for row in rows:
        spec = SPECS.get(row["key"])
        if spec is None:
            continue
        out[row["key"]] = _decode(row["key"], row["value"])
        if spec.value2:
            out[spec.value2] = _decode_value2(row["value2"])
    return out


def update_settings(conn: sqlite3.Connection, user_id: int, values: dict) -> dict:
    unknown = set(values) - FIELDS
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    if values:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise NotFound("user_settings", user_id)
        plain = {k: _encode(k, v) for k, v in values.items() if k in SPECS}
        extra = {VALUE2[k]: _encode_value2(k, v) for k, v in values.items() if k in VALUE2}
        for k, v in plain.items():
            _check_value(conn, k, v)
        with transaction(conn):
            _write(conn, user_id, plain)
            if extra:
                current = get_settings(conn, user_id)
                conn.executemany(
                    "INSERT INTO user_settings (user_id, key, value, value2) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (user_id, key) DO UPDATE SET value2 = excluded.value2",
                    [(user_id, k, plain.get(k, str(current[k])), v2) for k, v2 in extra.items()])
    return get_settings(conn, user_id)


def reset_settings(conn: sqlite3.Connection, user_id: int, keys: list[str]) -> dict:
    """Back to the default for these keys: their rows are removed."""
    unknown = set(keys) - set(SPECS)
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    with transaction(conn):
        conn.executemany("DELETE FROM user_settings WHERE user_id = ? AND key = ?",
                         [(user_id, k) for k in keys])
    return get_settings(conn, user_id)


def theme_css(row: dict, conn: sqlite3.Connection) -> str | None:
    """The :root rule for this user's theme, or None for 'auto' (app.css follows Windows)."""
    theme = row.get("theme", "auto")
    if theme == "custom":
        colors = row.get("theme_colors")
        return themes.css(themes.parse_colors(colors)) if colors else None
    if theme in themes.SPECIAL:
        return None
    found = conn.execute("SELECT mode, colors FROM themes WHERE id = ?", (theme,)).fetchone()
    return themes.css(themes.parse_colors(json.loads(found["colors"])), found["mode"]) if found else None


def list_themes(conn: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "mode": r["mode"], "colors": json.loads(r["colors"])}
            for r in conn.execute("SELECT id, mode, colors FROM themes ORDER BY sort_order")]


def to_engine(row: dict) -> Settings:
    return Settings(**{f: Decimal(str(row[f])) for f in RATE_FIELDS})
