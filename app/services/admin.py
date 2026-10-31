"""Operator actions used by the server manager (GUI) and manage.py."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.auth import passwords
from app.db.connection import transaction
from app.db.repo import Conflict, NotFound, users
from app.i18n import t

from . import backup
from .seed_user import create_seeded_user

ONLINE_MINUTES = 15


class AdminError(Exception):
    """Why an action was refused: a translation key + params; message(lang) words it."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key, self.params = key, params

    def message(self, lang: str | None = None) -> str:
        return t(self.key, lang, **self.params)


def _raise(error: tuple[str, dict] | None) -> None:
    if error:
        key, params = error
        raise AdminError(key, **params)


def list_users(conn: sqlite3.Connection, online_minutes: int = ONLINE_MINUTES,
               now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT u.id, u.username, u.password_hash, u.created_at, u.last_login_at, u.last_seen_at,"
        " (SELECT COUNT(*) FROM budgets b WHERE b.user_id = u.id) AS months"
        " FROM users u ORDER BY u.username COLLATE NOCASE").fetchall()
    out = []
    for r in rows:
        seen = _utc(r["last_seen_at"])
        out.append({
            "id": r["id"],
            "username": r["username"],
            "months": r["months"],
            "created_at": _local(r["created_at"]),
            "last_login_at": _local(r["last_login_at"]),
            "last_seen_at": _local(r["last_seen_at"]),
            "online": bool(seen and now - seen <= timedelta(minutes=online_minutes)),
            "must_change_password": passwords.is_manual(r["password_hash"]),
        })
    return out


def _utc(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) if value else None


def _local(value: str | None) -> str:
    """SQLite CURRENT_TIMESTAMP is UTC; show it in the computer's local time."""
    dt = _utc(value)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else ""


def _user(conn, user_id: int) -> dict:
    try:
        return users.get_user(conn, user_id)
    except NotFound:
        raise AdminError("admin.err.user_missing") from None


def create_user(conn: sqlite3.Connection, username: str, password: str, lang: str | None = None,
                today: date | None = None) -> int:
    """`lang` names the user's default expense types and goal types."""
    username = username.strip()
    _raise(passwords.username_error(username))
    _raise(passwords.password_error(password))
    try:
        return create_seeded_user(conn, username, passwords.hash_password(password), today or date.today(),
                                  lang)
    except Conflict:
        raise AdminError("auth.err.username_taken") from None


def set_password(conn: sqlite3.Connection, user_id: int, password: str, temporary: bool = False) -> str:
    """temporary=True stores the #password# convention: the user types it with the # marks
    and must pick a new one right after logging in. Either way every session of that user ends."""
    _user(conn, user_id)
    if temporary:
        core = password.strip("#")
        _raise(passwords.password_error(core))
        stored = shown = f"#{core}#"
    else:
        _raise(passwords.password_error(password))
        stored, shown = passwords.hash_password(password), password
    with transaction(conn):
        users.set_password_hash(conn, user_id, stored)
    return shown


def delete_user(conn: sqlite3.Connection, user_id: int, db_path: Path, backup_dir: Path, keep: int) -> Path:
    """Backs the database up first, then deletes the user and everything they own."""
    _user(conn, user_id)
    safety = backup.backup_now(db_path, backup_dir, keep)
    with transaction(conn):
        users.delete_user(conn, user_id)
    return safety


def logout_user(conn: sqlite3.Connection, user_id: int) -> None:
    _user(conn, user_id)
    with transaction(conn):
        users.bump_session_version(conn, user_id)


def logout_all(conn: sqlite3.Connection) -> int:
    with transaction(conn):
        return users.bump_session_version(conn)


# ---------- mezaniya.env

def read_env_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = (p.strip() for p in stripped.split("=", 1))
        if k == key:
            return v.strip("\"'")
    return None


def write_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value, replacing an existing (or commented-out) line, else appending."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_port(value: str) -> int:
    try:
        port = int(str(value).strip())
    except ValueError:
        raise AdminError("admin.err.port_number") from None
    if not 1024 <= port <= 65535:
        raise AdminError("admin.err.port_range")
    return port
