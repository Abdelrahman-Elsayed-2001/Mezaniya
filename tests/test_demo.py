from datetime import date

import pytest

from app.db import connect
from app.db.repo import budgets, templates, users
from app.db.repo.loader import load_budget_input
from app.engine import validate
from app.i18n import t
from app.services import admin, demo
from tests.api_helpers import Api, make_app

TODAY = date(2026, 10, 20)


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def conn(app):
    c = connect(app.config["DB_PATH"])
    yield c
    c.close()


def by_name(rows, key, lang="ar"):
    return next(r for r in rows if r["name"] == t(f"demo.{key}", lang))


def test_demo_user_has_three_linked_months(conn):
    uid = demo.create_demo_user(conn, "ar", TODAY)
    months = budgets.list_budgets(conn, uid)
    assert [(m["year"], m["month"]) for m in months] == [(2026, 8), (2026, 9), (2026, 10)]
    assert months[0]["source_budget_id"] is None
    assert months[1]["source_budget_id"] == months[0]["id"] and months[2]["source_budget_id"] == months[1]["id"]
    assert len(templates.list_templates(conn, uid)) == 8

    first, second, third = (budgets.list_items(conn, uid, "expenses", m["id"]) for m in months)
    gift = by_name(first, "gift")
    assert (gift["status"], gift["deferred_amount"]) == ("deferred", 200)          # carried by the wizard
    assert by_name(second, "gift")["carried_in"] == 200
    assert by_name(second, "electricity")["carried_in"] == 120                      # partial carry-over
    phone = [by_name(rows, "phone")["inst_count"] for rows in (first, second, third)]
    assert phone == [12, 11, 10]
    paid_days = sorted(int(r["paid_at"][-2:]) for r in third if r["status"] == "paid")
    assert paid_days and max(paid_days) <= TODAY.day                                # nothing paid in the future
    assert any(r["status"] == "unpaid" for r in second) is True                     # the course
    for m in months:
        assert [i for i in validate(load_budget_input(conn, uid, m["id"])) if i.blocking] == []


def test_demo_user_can_log_in_and_see_every_month(app, conn):
    demo.create_demo_user(conn, "ar", TODAY)
    api = Api(app)
    r = api.post("/auth/login", {"username": demo.DEMO_USERNAME, "password": demo.DEMO_PASSWORD})
    assert r.status_code == 200 and r.get_json()["must_change_password"] is False
    for m in api.get("/api/budgets").get_json():
        d = api.get(f"/api/budgets/{m['id']}").get_json()
        assert d["summary"]["income"] >= 9000 and d["expenses"] and d["goals"]


def test_demo_in_english(conn):
    uid = demo.create_demo_user(conn, "en", TODAY)
    rows = budgets.list_items(conn, uid, "expenses", budgets.list_budgets(conn, uid)[0]["id"])
    assert {"Rent", "Electricity", "Car installment"} <= {r["name"] for r in rows}


def test_demo_exists_and_replace(conn):
    first = demo.create_demo_user(conn, "ar", TODAY)
    budgets.update_budget(conn, first, budgets.list_budgets(conn, first)[0]["id"], {"savings_current": 1})
    with pytest.raises(admin.AdminError):
        demo.create_demo_user(conn, "ar", TODAY)
    second = demo.create_demo_user(conn, "ar", TODAY, replace=True)
    assert conn.execute("SELECT COUNT(*) FROM users WHERE username = 'test'").fetchone()[0] == 1
    assert users.find_by_username(conn, "test")["id"] == second
    assert budgets.list_budgets(conn, second)[0]["savings_current"] == 6000      # fresh data, not the edited one
    assert conn.execute("SELECT COUNT(*) FROM budgets WHERE user_id = ?", (second,)).fetchone()[0] == 3


def test_demo_across_new_year(conn):
    uid = demo.create_demo_user(conn, "ar", date(2027, 1, 5))
    assert [(m["year"], m["month"]) for m in budgets.list_budgets(conn, uid)] == [(2026, 11), (2026, 12), (2027, 1)]
