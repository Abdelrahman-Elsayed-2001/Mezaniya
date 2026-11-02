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


def _names(app, conn, uid, table, budget_id, lang, col="name"):
    with app.test_request_context(headers={"Cookie": f"mezaniya_lang={lang}"}):
        return {r[col] for r in budgets.list_items(conn, uid, table, budget_id)}


def test_demo_names_follow_the_viewers_language(app, conn):
    uid = demo.create_demo_user(conn, "en", TODAY)
    months = budgets.list_budgets(conn, uid)
    first, last = months[0]["id"], months[-1]["id"]
    for b in (first, last):                       # copied months keep the translation too
        assert {"Rent", "Electricity", "Car installment"} <= _names(app, conn, uid, "expenses", b, "en")
        assert {t("demo.rent", "ar"), t("demo.car", "ar")} <= _names(app, conn, uid, "expenses", b, "ar")
    assert t("demo.salary", "ar") in _names(app, conn, uid, "incomes", last, "ar", "source")
    assert t("demo.goal_car", "ar") in _names(app, conn, uid, "goals", last, "ar")
    with app.test_request_context(headers={"Cookie": "mezaniya_lang=ar"}):
        assert t("demo.rent", "ar") in {r["name"] for r in templates.list_templates(conn, uid)}
    # stored as keys, not text
    assert conn.execute("SELECT COUNT(*) FROM expenses WHERE user_id = ? AND name IS NOT NULL",
                        (uid,)).fetchone()[0] == 0

    # a renamed item is the user's text in every language, and the next month copies that text
    rent = next(r for r in budgets.list_items(conn, uid, "expenses", last) if r["name_key"] == "rent")
    budgets.update_item(conn, uid, "expenses", last, rent["id"], {"name": "سكن"})
    assert "سكن" in _names(app, conn, uid, "expenses", last, "en")


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


def test_new_month_from_an_english_session_keeps_demo_names_translated(app, conn):
    uid = demo.create_demo_user(conn, "ar", TODAY)
    api = Api(app)
    api.post("/auth/login", {"username": demo.DEMO_USERNAME, "password": demo.DEMO_PASSWORD})
    api.client.set_cookie("mezaniya_lang", "en")
    last = budgets.list_budgets(conn, uid)[-1]
    # the car installment is copied from the month itself (not from a recurring item)
    car = next(r for r in budgets.list_items(conn, uid, "expenses", last["id"]) if r["name_key"] == "car")
    api.patch(f"/api/budgets/{last['id']}/expenses/{car['id']}", {"name": "My car"})
    r = api.post("/api/budgets", {"year": 2026, "month": 11, "copy_from_previous": True})
    assert r.status_code == 201, r.get_json()
    new = r.get_json()["budget"]["id"]
    ar = _names(app, conn, uid, "expenses", new, "ar")
    assert "My car" in ar and t("demo.internet", "ar") in ar and "Internet" not in ar
    assert t("demo.salary", "ar") in _names(app, conn, uid, "incomes", new, "ar", "source")
