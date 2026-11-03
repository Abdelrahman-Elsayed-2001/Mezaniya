"""languages.db: the languages and texts the app shows, kept apart from the users' data.

The language files (app/i18n/languages/<code>.json) ship with the program and are copied into
`system_text` whenever they change (a new version). Edits made in the server manager live in
`custom_text`, which a new version never touches, and languages added there have no system text
at all. The text shown is custom_text, else system_text; whatever a language lacks is taken from
its reference language and then the default language (see app.i18n).

`revision` in language_meta grows with every change, so a running server notices edits made by
the server manager and reloads.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.db.connection import connect, transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS language_info (
  id           TEXT PRIMARY KEY,                 -- code, e.g. ar, en, fr
  name         TEXT NOT NULL,                    -- in the language itself: English, Français
  dir          TEXT NOT NULL CHECK (dir IN ('rtl', 'ltr')),
  plural_rule  TEXT NOT NULL,                    -- a key of app.i18n.PLURAL_RULES
  reference_id TEXT REFERENCES language_info(id) ON DELETE SET NULL,  -- fills untranslated texts
  sort_order   INTEGER NOT NULL DEFAULT 99,
  is_system    INTEGER NOT NULL DEFAULT 0,       -- 1 = comes from a language file
  enabled      INTEGER NOT NULL DEFAULT 1        -- 0 = hidden from the language switch
);
CREATE TABLE IF NOT EXISTS language_text (
  language_id  TEXT NOT NULL REFERENCES language_info(id) ON DELETE CASCADE,
  key          TEXT NOT NULL,                    -- e.g. nav.dashboard
  plural       INTEGER NOT NULL DEFAULT 0,       -- 1 = the texts are JSON {"one": .., "other": ..}
  system_text  TEXT,                             -- from the language file
  custom_text  TEXT,                             -- edited in the server manager
  PRIMARY KEY (language_id, key)
);
CREATE TABLE IF NOT EXISTS language_meta (
  key   TEXT PRIMARY KEY,                        -- revision, files_hash
  value TEXT NOT NULL
);
"""


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn


def files_hash(files: dict[str, tuple[dict, dict]]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM language_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute("INSERT INTO language_meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def revision(conn: sqlite3.Connection) -> int:
    return int(_meta(conn, "revision") or 0)


def bump(conn: sqlite3.Connection) -> None:
    _set_meta(conn, "revision", revision(conn) + 1)


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value


def sync(conn: sqlite3.Connection, files: dict[str, tuple[dict, dict]], default: str) -> bool:
    """Copy the language files' texts into system_text. Returns False when nothing changed."""
    digest = files_hash(files)
    if _meta(conn, "files_hash") == digest:
        return False
    known_keys = set(files[default][1])
    with transaction(conn):
        for code, (meta, entries) in files.items():
            conn.execute(
                "INSERT INTO language_info (id, name, dir, plural_rule, sort_order, is_system) "
                "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "dir = excluded.dir, plural_rule = excluded.plural_rule, sort_order = excluded.sort_order, "
                "is_system = 1", (code, meta["name"], meta["dir"], meta["plural"], meta.get("order", 99)))
            conn.executemany(
                "INSERT INTO language_text (language_id, key, plural, system_text) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(language_id, key) DO UPDATE SET plural = excluded.plural, "
                "system_text = excluded.system_text",
                [(code, key, int(isinstance(value, dict)), _encode(value)) for key, value in entries.items()])
            stale = [(code, key) for (key,) in conn.execute(
                "SELECT key FROM language_text WHERE language_id = ? AND system_text IS NOT NULL", (code,))
                if key not in entries]
            conn.executemany("UPDATE language_text SET system_text = NULL WHERE language_id = ? AND key = ?", stale)
        # a language whose file is gone keeps its texts as edits
        for (code,) in conn.execute("SELECT id FROM language_info WHERE is_system = 1").fetchall():
            if code not in files:
                conn.execute("UPDATE language_text SET custom_text = COALESCE(custom_text, system_text), "
                             "system_text = NULL WHERE language_id = ?", (code,))
                conn.execute("UPDATE language_info SET is_system = 0 WHERE id = ?", (code,))
        # keys the program no longer uses
        obsolete = [(key,) for (key,) in conn.execute("SELECT DISTINCT key FROM language_text")
                    if key not in known_keys]
        conn.executemany("DELETE FROM language_text WHERE key = ?", obsolete)
        conn.execute("DELETE FROM language_text WHERE system_text IS NULL AND custom_text IS NULL")
        # plural keys of languages added in the manager follow the default language
        conn.execute("UPDATE language_text SET plural = (SELECT d.plural FROM language_text d "
                     "WHERE d.language_id = ? AND d.key = language_text.key) WHERE language_id != ?",
                     (default, default))
        conn.execute("UPDATE language_info SET enabled = 1 WHERE id = ?", (default,))
        _set_meta(conn, "files_hash", digest)
        bump(conn)
    return True


def load(conn: sqlite3.Connection) -> tuple[dict[str, dict], dict[str, dict[str, object]]]:
    """Every language's info (by sort order) and its own texts (custom, else system)."""
    metas: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM language_info ORDER BY sort_order, id"):
        metas[row["id"]] = {"name": row["name"], "dir": row["dir"], "plural": row["plural_rule"],
                            "order": row["sort_order"], "reference": row["reference_id"],
                            "system": bool(row["is_system"]), "enabled": bool(row["enabled"])}
    catalogs: dict[str, dict[str, object]] = {code: {} for code in metas}
    for row in conn.execute("SELECT language_id, key, plural, COALESCE(custom_text, system_text) AS text "
                            "FROM language_text WHERE COALESCE(custom_text, system_text) IS NOT NULL"):
        catalogs[row["language_id"]][row["key"]] = json.loads(row["text"]) if row["plural"] else row["text"]
    return metas, catalogs


def read_revision(path: str | Path) -> int:
    with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)) as conn:
        return revision(conn)


def set_custom(conn: sqlite3.Connection, lang: str, key: str, value: object | None) -> None:
    """Store an edit (None or "" restores the language file's text)."""
    plural = conn.execute("SELECT plural FROM language_text WHERE key = ? ORDER BY language_id != "
                          "(SELECT id FROM language_info ORDER BY sort_order, id LIMIT 1) LIMIT 1",
                          (key,)).fetchone()
    if plural is None:
        raise KeyError(key)
    text = _encode(value) if value not in (None, "", {}) else None
    with transaction(conn):
        conn.execute("INSERT INTO language_text (language_id, key, plural, custom_text) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(language_id, key) DO UPDATE SET custom_text = excluded.custom_text",
                     (lang, key, plural[0], text))
        conn.execute("DELETE FROM language_text WHERE language_id = ? AND key = ? "
                     "AND system_text IS NULL AND custom_text IS NULL", (lang, key))
        bump(conn)
