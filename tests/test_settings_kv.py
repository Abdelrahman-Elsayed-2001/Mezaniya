"""user_settings as key/value rows over default_settings."""
import sqlite3

import pytest

from app.db.connection import IncompatibleDatabase, check_compatible, connect, transaction
from app.db.repo import Conflict, NotFound, settings


def test_registry_matches_default_settings(conn):
    rows = {r["key"]: r["type"] for r in conn.execute("SELECT key, type FROM default_settings")}
    assert rows == {k: s.type for k, s in settings.SPECS.items()}


def test_new_user_gets_financial_defaults_as_rows(conn, alice):
    keys = {r[0] for r in conn.execute("SELECT key FROM user_settings WHERE user_id = ?", (alice,))}
    assert keys == {k for k, s in settings.SPECS.items() if s.on_create} | {"currency"}


def test_later_default_change_skips_existing_users_rates(conn, alice):
    with transaction(conn):
        conn.execute("UPDATE default_settings SET default_value = '0.4' WHERE key = 'max_debt_rate'")
    assert settings.get_settings(conn, alice)["max_debt_rate"] == 0.33


def test_missing_row_falls_back_to_default(conn, alice):
    with transaction(conn):
        conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key = 'good_rate'", (alice,))
    assert settings.get_settings(conn, alice)["good_rate"] == 0.1


def test_reset_removes_the_row(conn, alice):
    settings.update_settings(conn, alice, {"good_rate": 0.15})
    assert settings.reset_settings(conn, alice, ["good_rate"])["good_rate"] == 0.1
    with pytest.raises(ValueError):
        settings.reset_settings(conn, alice, ["nope"])


def test_values_are_validated(conn, alice):
    for bad in ({"good_rate": "abc"}, {"good_rate": True}, {"good_rate": float("nan")},
                {"currency": 5}, {"currency": "x" * 11}):
        with pytest.raises(Conflict):
            settings.update_settings(conn, alice, bad)
    assert settings.get_settings(conn, alice)["good_rate"] == 0.1


def test_unknown_key_is_refused_by_the_database(conn, alice):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute("INSERT INTO user_settings (user_id, key, value) VALUES (?, 'typo_rate', '1')", (alice,))


def test_missing_user(conn):
    with pytest.raises(NotFound):
        settings.get_settings(conn, 999)
    with pytest.raises(NotFound):
        settings.update_settings(conn, 999, {"good_rate": 0.1})


def test_beta_010_database_is_refused(tmp_path):
    c = connect(tmp_path / "old.db")
    c.execute("CREATE TABLE user_settings (user_id INTEGER PRIMARY KEY, good_rate REAL, currency TEXT)")
    with pytest.raises(IncompatibleDatabase):
        check_compatible(c)
    c.close()
