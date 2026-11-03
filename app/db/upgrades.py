"""Fix scripts for a mezaniya.db made by an older generation (see connection.SCHEMA_GENERATION).

During beta the schema is edited in place, so an older database is refused. When a change is small
enough to patch, add a function here that brings generation N to N + 1 and register it in UPGRADES;
the server manager then offers "Upgrade" next to "Delete" and "Rename" for databases it can bring
all the way to the current generation. The functions run in one transaction, then the database is
stamped with the new generation. Nothing is planned for 0.1.0 / 0.2.0 (generations 0–2).

Example, for a future generation 5:

    def _4_to_5(conn):
        conn.execute("ALTER TABLE budgets ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    UPGRADES = {4: _4_to_5}
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from app.db.connection import SCHEMA_GENERATION, transaction

UPGRADES: dict[int, Callable[[sqlite3.Connection], None]] = {}


def can_upgrade(generation: int, upgrades: dict | None = None, target: int = SCHEMA_GENERATION) -> bool:
    upgrades = UPGRADES if upgrades is None else upgrades
    return generation < target and all(g in upgrades for g in range(generation, target))


def upgrade(conn: sqlite3.Connection, upgrades: dict | None = None, target: int = SCHEMA_GENERATION) -> int:
    """Run the fix scripts from the database's generation up to `target`. Returns the new generation."""
    upgrades = UPGRADES if upgrades is None else upgrades
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if not can_upgrade(found, upgrades, target):
        raise ValueError(f"no upgrade from generation {found} to {target}")
    with transaction(conn):
        for generation in range(found, target):
            upgrades[generation](conn)
        conn.execute(f"PRAGMA user_version = {target}")
    return target
