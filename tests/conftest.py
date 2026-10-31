from datetime import date

import pytest

from app.db.connection import connect, migrate
from app.services.seed_user import create_seeded_user

TODAY = date(2026, 11, 15)


def seeded(conn, username, password_hash="scrypt:x", lang="ar"):
    return create_seeded_user(conn, username, password_hash, TODAY, lang)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def alice(conn):
    return seeded(conn, "alice")


@pytest.fixture
def bob(conn):
    return seeded(conn, "bob", "scrypt:y")
