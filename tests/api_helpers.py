from app import create_app
from app.config import Config

H = {"X-Requested-With": "XMLHttpRequest"}


def make_app(tmp_path, **overrides):
    attrs = {"DATA_DIR": tmp_path, "DB_PATH": tmp_path / "t.db", "TESTING": True, "SECRET_KEY": "test", **overrides}
    return create_app(type("TestConfig", (Config,), attrs))


class Api:
    """Test client wrapper: JSON + CSRF header on every call."""

    def __init__(self, app, username=None, password="secret1"):
        self.client = app.test_client()
        self.username = username
        if username:
            r = self.post("/auth/register", {"username": username, "password": password,
                                             "password_confirm": password})
            assert r.status_code == 201, r.get_json()

    def get(self, url):
        return self.client.get(url, headers=H)

    def post(self, url, body=None):
        return self.client.post(url, json=body if body is not None else {}, headers=H)

    def patch(self, url, body=None):
        return self.client.patch(url, json=body if body is not None else {}, headers=H)

    def delete(self, url):
        return self.client.delete(url, headers=H)

    def call(self, method, url, body=None):
        if method == "GET":
            return self.get(url)
        if method == "DELETE":
            return self.delete(url)
        return getattr(self, method.lower())(url, body)

    # convenience
    def budget_id(self):
        return self.get("/api/budgets").get_json()[0]["id"]

    def type_id(self, name):
        return next(t["id"] for t in self.get("/api/expense-types").get_json() if t["name"] == name)
