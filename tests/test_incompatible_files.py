"""A mezaniya.db / languages.db this version can't open: delete, rename, or upgrade with fix scripts."""
import sqlite3
from datetime import datetime

import pytest

from app.db import upgrades
from app.db.connection import SCHEMA_GENERATION, check_compatible, connect, migrate
from app.i18n import store
from app.services import data_files


def make_db(path, generation):
    conn = connect(path)
    migrate(conn)
    conn.execute(f"PRAGMA user_version = {generation}")
    conn.close()


def test_nothing_to_do(tmp_path):
    make_db(tmp_path / "mezaniya.db", SCHEMA_GENERATION)
    store.open_db(tmp_path / "languages.db").close()
    assert data_files.problems(tmp_path / "mezaniya.db", tmp_path / "languages.db") == []
    assert data_files.problems(tmp_path / "none.db", tmp_path / "none2.db") == []


def test_rename_and_delete(tmp_path):
    db, ldb = tmp_path / "mezaniya.db", tmp_path / "languages.db"
    make_db(db, 2)
    conn = store.open_db(ldb)
    conn.execute(f"PRAGMA user_version = {store.GENERATION + 1}")
    conn.close()
    data, languages = data_files.problems(db, ldb)
    assert (data.kind, data.found, data.expected, data.upgradable) == ("data", 2, SCHEMA_GENERATION, False)
    assert (languages.kind, languages.found, languages.upgradable) == ("languages", store.GENERATION + 1, False)

    kept = data_files.set_aside(data, now=datetime(2026, 11, 3, 14, 0))
    assert kept.name == "mezaniya-old-v2-20261103-140000.db" and kept.is_file() and not db.exists()
    with sqlite3.connect(kept) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    data_files.delete(languages)
    assert not ldb.exists() and not any(p.name.startswith("languages.db-") for p in tmp_path.iterdir())
    assert data_files.problems(db, ldb) == []


def test_upgrade_only_with_fix_scripts(tmp_path, monkeypatch):
    db = tmp_path / "mezaniya.db"
    make_db(db, SCHEMA_GENERATION - 1)
    assert data_files.problems(db, tmp_path / "l.db")[0].upgradable is False      # none written yet
    with pytest.raises(ValueError):
        data_files.upgrade(data_files.problems(db, tmp_path / "l.db")[0])

    def fix(conn):
        conn.execute("CREATE TABLE fixed (x)")
    monkeypatch.setitem(upgrades.UPGRADES, SCHEMA_GENERATION - 1, fix)
    problem = data_files.problems(db, tmp_path / "l.db")[0]
    assert problem.upgradable
    copy = data_files.upgrade(problem)
    assert copy.is_file()
    conn = connect(db)
    check_compatible(conn)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'fixed'").fetchone()
    conn.close()


def test_upgrade_needs_every_step(monkeypatch):
    monkeypatch.setattr(upgrades, "UPGRADES", {4: lambda c: None})
    assert upgrades.can_upgrade(4, target=5)
    assert not upgrades.can_upgrade(3, target=5)          # 3 → 4 missing
    assert not upgrades.can_upgrade(5, target=5)


def test_failed_fix_changes_nothing(tmp_path):
    db = tmp_path / "mezaniya.db"
    make_db(db, 4)

    def broken(conn):
        conn.execute("CREATE TABLE half (x)")
        raise sqlite3.OperationalError("boom")
    conn = connect(db)
    with pytest.raises(sqlite3.OperationalError):
        upgrades.upgrade(conn, {4: broken}, target=5)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone()
    conn.close()
