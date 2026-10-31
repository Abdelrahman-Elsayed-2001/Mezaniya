"""Per-request database access for Flask."""
from __future__ import annotations

import sqlite3

from flask import Flask, current_app, g

from .connection import connect, migrate, transaction

__all__ = ["get_db", "init_app", "connect", "migrate", "transaction"]


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def _close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app: Flask) -> None:
    conn = connect(app.config["DB_PATH"])
    try:
        migrate(conn)
    finally:
        conn.close()
    app.teardown_appcontext(_close_db)
