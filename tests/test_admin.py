"""Server manager actions: sessions, users, passwords, deletion, port, process control."""
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import manage
from app.config import BASE_DIR, Config
from app.db import connect
from app.db.repo import users
from app.services import admin, backup
from app.services.server_control import (ServerControl, is_our_server, parse_netstat_listener, process_name,
                                         read_pid_file, remove_pid_file, write_pid_file)
from tests.api_helpers import Api, make_app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def conn(app):
    c = connect(app.config["DB_PATH"])
    yield c
    c.close()


def uid(conn, name):
    return users.find_by_username(conn, name)["id"]


def logged_in(app, name):
    return Api(app, name).client.get("/auth/me").status_code == 200


# ---------- sessions

def test_logout_all_ends_every_session_immediately(app, conn):
    alice, bob = Api(app, "alice"), Api(app, "bob")
    assert alice.get("/auth/me").status_code == bob.get("/auth/me").status_code == 200
    assert admin.logout_all(conn) == 2
    assert alice.get("/auth/me").status_code == 401
    assert bob.get("/api/budgets").status_code == 401
    again = Api(app)
    assert again.post("/auth/login", {"username": "alice", "password": "secret1"}).status_code == 200


def test_logout_one_user(app, conn):
    alice, bob = Api(app, "alice"), Api(app, "bob")
    admin.logout_user(conn, uid(conn, "alice"))
    assert alice.get("/auth/me").status_code == 401
    assert bob.get("/auth/me").status_code == 200
    with pytest.raises(admin.AdminError):
        admin.logout_user(conn, 999)


def test_activity_marks_users_online(app, conn):
    Api(app, "alice")
    Api(app, "bob")
    conn.execute("UPDATE users SET last_seen_at = datetime('now', '-1 hour') WHERE username = 'bob'")
    listed = {u["username"]: u for u in admin.list_users(conn)}
    assert listed["alice"]["online"] and not listed["bob"]["online"]
    assert listed["alice"]["months"] == 1 and listed["alice"]["created_at"]


def test_activity_is_written_at_most_once_a_minute(app, conn):
    api = Api(app, "alice")
    stamp = "2000-01-01 00:00:00"
    conn.execute("UPDATE users SET last_seen_at = ?", (stamp,))
    api.get("/auth/me")                                         # stale → refreshed
    fresh = users.get_user(conn, uid(conn, "alice"))["last_seen_at"]
    assert fresh != stamp
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE users SET last_seen_at = ?", (recent,))
    api.get("/auth/me")                                         # recent → left alone
    assert users.get_user(conn, uid(conn, "alice"))["last_seen_at"] == recent


# ---------- users & passwords

def test_create_user(app, conn):
    new_id = admin.create_user(conn, "  sara ", "secret9")
    assert users.get_user(conn, new_id)["username"] == "sara"
    assert Api(app).post("/auth/login", {"username": "sara", "password": "secret9"}).status_code == 200
    for name, pw in (("sara", "secret9"), ("x", "secret9"), ("ok_name", "123")):
        with pytest.raises(admin.AdminError):
            admin.create_user(conn, name, pw)


def test_set_password(app, conn):
    alice = Api(app, "alice")
    admin.set_password(conn, uid(conn, "alice"), "newpass1")
    assert alice.get("/auth/me").status_code == 401             # old sessions end
    api = Api(app)
    assert api.post("/auth/login", {"username": "alice", "password": "newpass1"}).status_code == 200
    with pytest.raises(admin.AdminError):
        admin.set_password(conn, uid(conn, "alice"), "123")


def test_set_temporary_password(app, conn):
    Api(app, "alice")
    shown = admin.set_password(conn, uid(conn, "alice"), "temp99", temporary=True)
    assert shown == "#temp99#"
    assert users.get_user(conn, uid(conn, "alice"))["password_hash"] == "#temp99#"
    r = Api(app).post("/auth/login", {"username": "alice", "password": "#temp99#"})
    assert r.get_json()["must_change_password"] is True
    assert admin.set_password(conn, uid(conn, "alice"), "#again1#", temporary=True) == "#again1#"


def test_delete_user_removes_everything_after_a_backup(app, conn, tmp_path):
    alice, _bob = Api(app, "alice"), Api(app, "bob")
    b = alice.budget_id()
    alice.post(f"/api/budgets/{b}/expenses", {"name": "x", "type_id": alice.type_id("أساسي"), "cash_amount": 5})
    alice.post("/api/templates", {"kind": "income", "name": "راتب", "amount": 1})
    alice_id = uid(conn, "alice")
    safety = admin.delete_user(conn, alice_id, app.config["DB_PATH"], tmp_path / "bk", keep=5)
    assert safety.exists()
    for t in ("users", "user_settings", "expense_types", "goal_categories", "recurring_templates",
              "budgets", "incomes", "expenses", "goals"):
        col = "id" if t == "users" else "user_id"
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} = ?", (alice_id,)).fetchone()[0] == 0, t
    assert alice.get("/auth/me").status_code == 401
    assert [u["username"] for u in admin.list_users(conn)] == ["bob"]
    with pytest.raises(admin.AdminError):
        admin.delete_user(conn, alice_id, app.config["DB_PATH"], tmp_path / "bk", keep=5)


def test_manage_logout_and_delete_commands(app, conn, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Config, "DB_PATH", app.config["DB_PATH"])
    monkeypatch.setattr(Config, "BACKUP_DIR", tmp_path / "bk")
    alice = Api(app, "alice")
    assert manage.main(["logout-all"]) == 0
    assert alice.get("/auth/me").status_code == 401
    assert manage.main(["delete-user", "alice"]) == 2               # needs --yes
    assert manage.main(["delete-user", "alice", "--yes"]) == 0
    assert manage.main(["delete-user", "alice", "--yes"]) == 1
    assert backup.list_backups(tmp_path / "bk")


# ---------- mezaniya.env / port

def test_env_value_write_and_read(tmp_path):
    f = tmp_path / "m.env"
    admin.write_env_value(f, "MEZANIYA_PORT", "9000")                  # new file
    assert admin.read_env_value(f, "MEZANIYA_PORT") == "9000"
    f.write_text("# header\nMEZANIYA_HOST=0.0.0.0\n# MEZANIYA_PORT=8080\nOTHER=1\n", encoding="utf-8")
    admin.write_env_value(f, "MEZANIYA_PORT", "9100")                  # replaces the commented line
    text = f.read_text(encoding="utf-8")
    assert "MEZANIYA_PORT=9100" in text and "# MEZANIYA_PORT" not in text and "OTHER=1" in text
    admin.write_env_value(f, "MEZANIYA_PORT", "9200")
    assert f.read_text(encoding="utf-8").count("MEZANIYA_PORT") == 1
    assert admin.read_env_value(f, "MISSING") is None


@pytest.mark.parametrize("value,ok", [("8080", True), (" 9000 ", True), ("80", False), ("70000", False), ("abc", False)])
def test_validate_port(value, ok):
    if ok:
        assert admin.validate_port(value) == int(value)
    else:
        with pytest.raises(admin.AdminError):
            admin.validate_port(value)


# ---------- process control

def test_pid_file(tmp_path):
    f = tmp_path / "server.pid"
    write_pid_file(f, 8123)
    assert read_pid_file(f) == {"pid": os.getpid(), "port": 8123}
    remove_pid_file(f)
    assert not f.exists()
    f.write_text("garbage")
    assert read_pid_file(f) is None


def test_process_detection():
    assert is_our_server(os.getpid())                  # pytest itself runs in python
    assert process_name(4_000_000) is None             # no such PID


def test_netstat_parsing():
    sample = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1100
  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       23104
  TCP    127.0.0.1:8080         127.0.0.1:50000        ESTABLISHED     23104
  TCP    0.0.0.0:18080          0.0.0.0:0              LISTENING       999
"""
    assert parse_netstat_listener(sample, 8080) == 23104
    assert parse_netstat_listener(sample, 18080) == 999
    assert parse_netstat_listener(sample, 9999) is None


def test_stale_pid_file_means_stopped(tmp_path):
    f = tmp_path / "server.pid"
    f.write_text('{"pid": 4000000, "port": 8080}')
    assert ServerControl(BASE_DIR, f).state().running is False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform != "win32", reason="manager targets Windows")
def test_start_and_stop_a_real_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEZANIYA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEZANIYA_DB_PATH", str(tmp_path / "srv.db"))
    monkeypatch.setenv("MEZANIYA_HOST", "127.0.0.1")
    monkeypatch.setenv("MEZANIYA_BACKUP_INTERVAL_HOURS", "0")
    port = free_port()
    control = ServerControl(BASE_DIR, tmp_path / "server.pid", python=Path(sys.executable))
    control.start(port)
    try:
        assert control.wait_until_healthy(port, timeout=30)
        st = control.state()
        assert st.running and st.healthy and st.port == port
    finally:
        assert control.stop()
    assert control.state().running is False
    assert not (tmp_path / "server.pid").exists()
