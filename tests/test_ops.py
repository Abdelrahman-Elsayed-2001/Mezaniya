"""Running on the network: config file, backups, admin commands, security headers."""
import os
import sqlite3
import time
from datetime import datetime

import pytest

import manage
from app.config import Config, load_env_file
from app.db import connect
from app.db.connection import migrate
from app.db.repo import users
from app.services import backup
from app.services.seed_user import create_seeded_user
from tests.api_helpers import Api, make_app
from tests.conftest import TODAY


# ---------- config file

def test_env_file(tmp_path, monkeypatch):
    f = tmp_path / "mezaniya.env"
    f.write_text('# comment\nMEZANIYA_T_PORT=9090\nMEZANIYA_T_NAME="with spaces"\nbroken line\n'
                 "MEZANIYA_T_KEEP=from-file\n", encoding="utf-8")
    monkeypatch.delenv("MEZANIYA_T_PORT", raising=False)
    monkeypatch.delenv("MEZANIYA_T_NAME", raising=False)
    monkeypatch.setenv("MEZANIYA_T_KEEP", "from-env")
    loaded = load_env_file(f)
    assert loaded["MEZANIYA_T_PORT"] == "9090"
    assert os.environ["MEZANIYA_T_PORT"] == "9090"
    assert os.environ["MEZANIYA_T_NAME"] == "with spaces"
    assert os.environ["MEZANIYA_T_KEEP"] == "from-env"           # real environment wins
    assert load_env_file(tmp_path / "missing.env") == {}


# ---------- backups

@pytest.fixture
def live_db(tmp_path):
    path = tmp_path / "live.db"
    conn = connect(path)
    migrate(conn)
    create_seeded_user(conn, "alice", "scrypt:x", TODAY, "ar")
    yield path, conn
    conn.close()


def count_users(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        c.close()


def test_backup_is_a_consistent_copy(live_db, tmp_path):
    path, conn = live_db
    out = backup.backup_now(path, tmp_path / "b", keep=5, now=datetime(2026, 11, 3, 10, 0, 0))
    assert out.name == "mezaniya-20261103-100000.db"
    assert count_users(out) == 1
    again = backup.backup_now(path, tmp_path / "b", keep=5, now=datetime(2026, 11, 3, 10, 0, 0))
    assert again.name == "mezaniya-20261103-100000-1.db"           # same second: no overwrite


def test_backups_are_pruned(live_db, tmp_path):
    path, _ = live_db
    for day in range(1, 6):
        backup.backup_now(path, tmp_path / "b", keep=3, now=datetime(2026, 11, day))
    names = [p.name for p in backup.list_backups(tmp_path / "b")]
    assert names == ["mezaniya-20261103-000000.db", "mezaniya-20261104-000000.db", "mezaniya-20261105-000000.db"]


def test_backup_due(tmp_path, live_db):
    path, _ = live_db
    d = tmp_path / "b"
    assert backup.backup_due(d, 24)
    backup.backup_now(path, d, keep=5)
    assert not backup.backup_due(d, 24)
    assert backup.backup_due(d, 24, now=time.time() + 25 * 3600)


def test_scheduler_tick(live_db, tmp_path):
    path, _ = live_db
    s = backup.BackupScheduler(path, tmp_path / "b", keep=3, interval_hours=24)
    assert s.tick() is not None
    assert s.tick() is None                                          # not due again yet


def test_scheduler_thread_runs_and_stops(live_db, tmp_path):
    path, _ = live_db
    s = backup.BackupScheduler(path, tmp_path / "b", keep=3, interval_hours=24, check_every_seconds=0.05).start()
    deadline = time.time() + 3
    while not backup.list_backups(tmp_path / "b") and time.time() < deadline:
        time.sleep(0.02)
    s.stop()
    assert len(backup.list_backups(tmp_path / "b")) == 1


def test_restore(live_db, tmp_path):
    path, conn = live_db
    snap = backup.backup_now(path, tmp_path / "b", keep=5)
    create_seeded_user(conn, "bob", "scrypt:y", TODAY, "ar")
    conn.close()
    assert count_users(path) == 2
    safety = backup.restore(path, snap, tmp_path / "b", keep=5)
    assert count_users(path) == 1 and count_users(safety) == 2


def test_restore_rejects_a_non_database(live_db, tmp_path):
    path, conn = live_db
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    with pytest.raises(ValueError):
        backup.restore(path, junk, tmp_path / "b", keep=5)
    assert count_users(path) == 1


# ---------- manage.py

@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(Config, "BACKUP_DIR", tmp_path / "backups")
    app = make_app(tmp_path)
    Api(app, "alice")
    return app


def test_reset_password_command(managed, capsys):
    assert manage.main(["reset-password", "ALICE", "--password", "temp99"]) == 0
    out = capsys.readouterr().out
    assert "#temp99#" in out
    api = Api(managed)
    r = api.post("/auth/login", {"username": "alice", "password": "#temp99#"})
    assert r.get_json()["must_change_password"] is True

    assert manage.main(["reset-password", "alice"]) == 0             # random temporary password
    line = capsys.readouterr().out.splitlines()[0]
    temp = line.rsplit(" ", 1)[-1]
    assert temp.startswith("#") and temp.endswith("#") and len(temp) >= 8
    assert manage.main(["reset-password", "nobody"]) == 1


def test_list_users_command(managed, capsys):
    c = connect(Config.DB_PATH)
    users.set_password_hash(c, users.find_by_username(c, "alice")["id"], "#x1#")
    c.close()
    assert manage.main(["--lang", "en", "list-users"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "must change" in out
    assert manage.main(["--lang", "ar", "list-users"]) == 0
    assert "يجب تغييرها" in capsys.readouterr().out


def test_backup_and_restore_commands(managed, capsys, tmp_path):
    assert manage.main(["backup"]) == 0
    files = backup.list_backups(Config.BACKUP_DIR)
    assert len(files) == 1
    assert manage.main(["list-backups"]) == 0
    assert manage.main(["restore", str(files[0])]) == 2              # needs --yes
    assert manage.main(["restore", str(tmp_path / "missing.db"), "--yes"]) == 1
    assert manage.main(["restore", str(files[0]), "--yes"]) == 0
    assert count_users(Config.DB_PATH) == 1


def test_migrate_command(managed, capsys):
    assert manage.main(["--lang", "en", "migrate"]) == 0
    assert "up to date" in capsys.readouterr().out


# ---------- security headers

def test_security_headers(managed):
    api = Api(managed, "bob")
    page = api.get("/")
    assert "script-src 'self' 'unsafe-eval'" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    data = api.get("/api/budgets")
    assert data.headers["Cache-Control"] == "no-store"
