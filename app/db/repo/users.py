from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .base import Conflict, NotFound


def create_user(conn: sqlite3.Connection, username: str, password_hash: str) -> int:
    try:
        cur = conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
    except sqlite3.IntegrityError as e:
        raise Conflict("username already taken", "username_taken") from e
    return cur.lastrowid


def get_user(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise NotFound("users", user_id)
    return dict(row)


def find_by_username(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def set_password_hash(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    if conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)).rowcount == 0:
        raise NotFound("users", user_id)


def touch_login(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP, last_seen_at = CURRENT_TIMESTAMP"
                 " WHERE id = ?", (user_id,))


SEEN_EVERY_SECONDS = 60


def touch_seen(conn: sqlite3.Connection, user: dict) -> None:
    """Record activity, writing at most once a minute per user (reads stay write-free)."""
    seen = user.get("last_seen_at")
    if seen:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(seen).replace(tzinfo=timezone.utc)).total_seconds()
        if age < SEEN_EVERY_SECONDS:
            return
    conn.execute("UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))


def bump_session_version(conn: sqlite3.Connection, user_id: int | None = None) -> int:
    """Log a user (or, with None, everybody) out of every device. Returns how many users."""
    if user_id is None:
        return conn.execute("UPDATE users SET session_version = session_version + 1").rowcount
    cur = conn.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))
    if cur.rowcount == 0:
        raise NotFound("users", user_id)
    return 1


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    """Removes the user and, through ON DELETE CASCADE, every row they own."""
    if conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount == 0:
        raise NotFound("users", user_id)
