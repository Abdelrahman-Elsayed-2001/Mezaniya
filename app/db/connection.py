"""SQLite connections, transactions and the migration runner."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_.+\.sql$")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode; group writes with `transaction()`."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE … COMMIT, or ROLLBACK on error. Nested use joins the outer one."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations in order, each in its own transaction. Returns applied versions."""
    current = schema_version(conn)
    applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        m = _MIGRATION_NAME.match(path.name)
        if not m or int(m.group(1)) <= current:
            continue
        version = int(m.group(1))
        sql = path.read_text(encoding="utf-8")
        # executescript() runs outside the connection's transaction handling, so wrap it ourselves
        try:
            conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({version});\nCOMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    check_compatible(conn)
    return applied


class IncompatibleDatabase(RuntimeError):
    def __init__(self, message: str, generation: int | None = None):
        super().__init__(message)
        self.generation = generation


# Bumped whenever 0001_initial.sql changes in a way older databases can't follow while in beta
# (the schema is edited in place, not migrated). 0001 stamps it with PRAGMA user_version.
#   1 = beta 0.1.0 (never stamped, reads as 0), 2 = beta 0.2.0 (never stamped either),
#   3 = 0.3.0 work in progress, 4 = beta 0.3.0: monthly notes, item names by language,
#   each user's own currencies, exchange rates.
SCHEMA_GENERATION = 4


def check_compatible(conn: sqlite3.Connection) -> None:
    """A database made by an older beta can't be opened: fail with a clear message instead of
    errors on the first request. An empty database (nothing but schema_version) is fine."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables - {"schema_version"}:
        return
    generation = conn.execute("PRAGMA user_version").fetchone()[0]
    if generation != SCHEMA_GENERATION:
        raise IncompatibleDatabase(
            f"This database was made by an older beta of Mezaniya (schema {generation}), which this "
            f"version (schema {SCHEMA_GENERATION}) can't open. Move or delete the data folder "
            "(mezaniya.db) and start again.", generation)
