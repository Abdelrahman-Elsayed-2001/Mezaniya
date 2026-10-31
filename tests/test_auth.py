import pytest

from app import create_app
from app.config import Config
from app.db import connect
from app.db.repo import budgets, users

H = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def make_app(tmp_path):
    def factory(**overrides):
        attrs = {"DATA_DIR": tmp_path, "DB_PATH": tmp_path / "t.db", "TESTING": True, **overrides}
        return create_app(type("TestConfig", (Config,), attrs))
    return factory


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, url, body=None, headers=H):
    return client.post(url, json=body if body is not None else {}, headers=headers)


def register(client, username="alice", password="secret1", confirm=None):
    return post(client, "/auth/register", {"username": username, "password": password,
                                           "password_confirm": password if confirm is None else confirm})


def login(client, username="alice", password="secret1"):
    return post(client, "/auth/login", {"username": username, "password": password})


def db(app):
    return connect(app.config["DB_PATH"])


def set_stored_password(app, username, value):
    c = db(app)
    users.set_password_hash(c, users.find_by_username(c, username)["id"], value)
    c.close()


# ---------- registration

def test_register_logs_in_and_seeds(app, client):
    r = register(client)
    assert r.status_code == 201
    me = client.get("/auth/me").get_json()
    assert me["username"] == "alice" and me["must_change_password"] is False
    c = db(app)
    stored = users.find_by_username(c, "alice")
    assert stored["password_hash"].startswith("scrypt:")
    assert len(budgets.list_budgets(c, stored["id"])) == 1


@pytest.mark.parametrize("username,password,confirm,field", [
    ("ab", "secret1", None, "username"),
    ("has space", "secret1", None, "username"),
    ("x" * 33, "secret1", None, "username"),
    ("alice", "12345", None, "password"),
    ("alice", "secret1", "secret2", "password_confirm"),
])
def test_register_validation(client, username, password, confirm, field):
    r = register(client, username, password, confirm)
    assert r.status_code == 422 and r.get_json()["field"] == field


def test_arabic_username_allowed(client):
    assert register(client, "عبدالرحمن_1").status_code == 201


def test_duplicate_username_case_insensitive(client, app):
    register(client)
    other = app.test_client()
    r = register(other, "ALICE")
    assert r.status_code == 409 and r.get_json()["error"] == "username_taken"


def test_registration_can_be_closed(make_app):
    client = make_app(ALLOW_REGISTRATION=False).test_client()
    assert register(client).status_code == 403
    assert client.get("/register").status_code == 404
    assert "تسجيل مستخدم جديد" not in client.get("/login").get_data(as_text=True)


# ---------- login / logout / guards

def test_login_logout(app, client):
    register(client)
    post(client, "/auth/logout")
    assert client.get("/auth/me").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.get_json()["must_change_password"] is False
    assert client.get("/auth/me").status_code == 200
    assert login(app.test_client(), "ALICE").status_code == 200    # case-insensitive


def test_wrong_password_and_unknown_user_look_the_same(client):
    register(client)
    a, b = login(client, password="wrong!!"), login(client, username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.get_json() == b.get_json()


def test_pages_require_login(client):
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].startswith("/login")
    assert client.get("/change-password").status_code == 302
    assert client.get("/login").status_code == 200


def test_logged_in_user_skips_login_page(client):
    register(client)
    assert client.get("/login").status_code == 302
    assert client.get("/").status_code == 200


def test_next_redirect_is_same_site_only(client):
    register(client)
    post(client, "/auth/logout")
    ok = post(client, "/auth/login", {"username": "alice", "password": "secret1", "next": "/settings"})
    assert ok.get_json()["next"] == "/settings"
    post(client, "/auth/logout")
    evil = post(client, "/auth/login", {"username": "alice", "password": "secret1", "next": "//evil.com"})
    assert evil.get_json()["next"] == "/"


def test_writes_without_csrf_header_rejected(client):
    r = post(client, "/auth/register", {"username": "alice", "password": "secret1",
                                        "password_confirm": "secret1"}, headers={})
    assert r.status_code == 403 and r.get_json()["error"] == "csrf"
    assert client.post("/auth/login", data={"username": "a"}).status_code == 403   # a classic form post


def test_malformed_body(client):
    r = client.post("/auth/login", data="not json", headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_session_cookie_flags(client):
    r = register(client)
    cookie = r.headers["Set-Cookie"]
    assert "mezaniya_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_deleted_user_session_is_dropped(app, client):
    register(client)
    c = db(app)
    c.execute("DELETE FROM users")
    c.close()
    assert client.get("/auth/me").status_code == 401


# ---------- rate limiting

def test_lockout_after_five_failures(app, client):
    register(client)
    post(client, "/auth/logout")
    clock = [1000.0]
    app.extensions["login_limiter"].clock = lambda: clock[0]
    for _ in range(5):
        assert login(client, password="wrong!!").status_code == 401
    locked = login(client)                        # even the right password is refused now
    assert locked.status_code == 429 and locked.get_json()["retry_after"] == 300
    assert "5 دقائق" in locked.get_json()["message"]
    clock[0] += 301
    assert login(client).status_code == 200


def test_success_resets_failures(app, client):
    register(client)
    for _ in range(4):
        login(client, password="wrong!!")
    assert login(client).status_code == 200
    for _ in range(4):
        assert login(client, password="wrong!!").status_code == 401


def test_lockout_is_per_username(app, client):
    register(client)
    register(app.test_client(), "bob")
    for _ in range(5):
        login(client, password="wrong!!")
    assert login(client).status_code == 429
    assert login(client, "bob").status_code == 200


# ---------- manual reset (#password#)

def test_manual_password_requires_hash_marks_and_forces_change(app, client):
    register(client)
    post(client, "/auth/logout")
    set_stored_password(app, "alice", "#1234#")
    assert login(client, password="1234").status_code == 401      # without the marks: wrong
    r = login(client, password="#1234#")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "must_change_password": True, "next": "/change-password"}

    # everything else is blocked until the password is changed
    page = client.get("/")
    assert page.status_code == 302 and page.headers["Location"] == "/change-password"
    assert client.get("/auth/me").get_json()["must_change_password"] is True
    assert "كلمة مرور مؤقتة" in client.get("/change-password").get_data(as_text=True)

    # no current password needed in this forced flow
    r = post(client, "/auth/change-password", {"new_password": "newpass1", "new_password_confirm": "newpass1"})
    assert r.status_code == 200
    assert client.get("/").status_code == 200

    post(client, "/auth/logout")
    assert login(client, password="#1234#").status_code == 401
    assert login(client, password="newpass1").status_code == 200
    c = db(app)
    assert users.find_by_username(c, "alice")["password_hash"].startswith("scrypt:")


def test_forced_change_cannot_reuse_manual_value(app, client):
    register(client)
    set_stored_password(app, "alice", "#temp99#")
    login(client, password="#temp99#")
    r = post(client, "/auth/change-password", {"new_password": "#temp99#", "new_password_confirm": "#temp99#"})
    assert r.status_code == 422


def test_hash_text_itself_is_not_a_password(app, client):
    register(client)
    c = db(app)
    stored = users.find_by_username(c, "alice")["password_hash"]
    assert login(app.test_client(), password=stored).status_code == 401


def test_manual_reset_logs_out_existing_sessions(app, client):
    register(client)
    set_stored_password(app, "alice", "#1234#")
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("stored", ["#", "##", "garbage", ""])
def test_odd_stored_values_never_log_in(app, client, stored):
    register(client)
    post(client, "/auth/logout")
    set_stored_password(app, "alice", stored)
    for attempt in (stored, "#", "##", "secret1"):
        assert login(app.test_client(), password=attempt).status_code == 401


# ---------- normal password change

def test_change_password(app, client):
    register(client)
    other_device = app.test_client()
    login(other_device)

    bad = post(client, "/auth/change-password", {"current_password": "nope", "new_password": "newpass1",
                                                 "new_password_confirm": "newpass1"})
    assert bad.status_code == 422 and bad.get_json()["field"] == "current_password"
    same = post(client, "/auth/change-password", {"current_password": "secret1", "new_password": "secret1",
                                                  "new_password_confirm": "secret1"})
    assert same.status_code == 422 and same.get_json()["field"] == "new_password"
    mismatch = post(client, "/auth/change-password", {"current_password": "secret1", "new_password": "newpass1",
                                                      "new_password_confirm": "newpass2"})
    assert mismatch.get_json()["field"] == "new_password_confirm"

    ok = post(client, "/auth/change-password", {"current_password": "secret1", "new_password": "newpass1",
                                                "new_password_confirm": "newpass1"})
    assert ok.status_code == 200
    assert client.get("/auth/me").status_code == 200            # this session survives
    assert other_device.get("/auth/me").status_code == 401      # other sessions are logged out
    assert login(app.test_client(), password="newpass1").status_code == 200


def test_change_password_requires_login(client):
    r = post(client, "/auth/change-password", {"new_password": "x" * 8, "new_password_confirm": "x" * 8})
    assert r.status_code == 401


def test_wrong_current_password_is_rate_limited(client):
    register(client)
    body = {"current_password": "nope", "new_password": "newpass1", "new_password_confirm": "newpass1"}
    for _ in range(5):
        assert post(client, "/auth/change-password", body).status_code == 422
    assert post(client, "/auth/change-password", body).status_code == 429


# ---------- secret key

def test_secret_key_is_generated_once_and_reused(make_app, tmp_path):
    a = make_app(SECRET_KEY=None)
    key_file = tmp_path / "secret_key"
    assert key_file.exists() and a.secret_key == key_file.read_text().strip()
    assert make_app(SECRET_KEY=None).secret_key == a.secret_key
