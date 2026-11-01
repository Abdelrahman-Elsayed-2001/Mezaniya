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
    pass


def check_compatible(conn: sqlite3.Connection) -> None:
    """Databases made by beta 0.1.0 (one column per setting) can't be opened by 0.2.0: while
    in beta the schema is edited in place, not migrated. Fail with a clear message instead of
    errors on the first request."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(user_settings)")}
    if cols and "key" not in cols:
        raise IncompatibleDatabase(
            "This database was made by Mezaniya beta 0.1.0, which beta 0.2.0 can't open. "
            "Move or delete the data folder (mezaniya.db) and start again.")
