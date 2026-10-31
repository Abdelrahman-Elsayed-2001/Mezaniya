"""Every API endpoint, called by Bob with Alice's ids, answers 404 and changes nothing."""
import pytest

from app.db import connect
from tests.api_helpers import Api, make_app

KINDS = ("incomes", "expenses", "goals")
ITEM_BODY = {"incomes": {"amount": 1}, "expenses": {"cash_amount": 1}, "goals": {"target_amount": 1}}
NEW_ITEM = {"incomes": {"source": "x"}, "goals": {"name": "x"}}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("iso"))
    alice, bob = Api(app, "alice"), Api(app, "bob")
    ab = alice.budget_id()
    ids = {
        "ab": ab,
        "bb": bob.budget_id(),
        "type": alice.post("/api/expense-types", {"name": "عربية"}).get_json()["id"],
        "cat": alice.get("/api/goal-categories").get_json()[0]["id"],
        "tpl": alice.post("/api/templates", {"kind": "income", "name": "راتب", "amount": 5000}).get_json()["id"],
        "bob_type": bob.type_id("أساسي"),
    }
    ids["incomes"] = alice.post(f"/api/budgets/{ab}/incomes", {"source": "راتب", "amount": 5000}).get_json()["item"]["id"]
    ids["expenses"] = alice.post(f"/api/budgets/{ab}/expenses", {"name": "انترنت", "type_id": ids["type"],
                                                                 "cash_amount": 120}).get_json()["item"]["id"]
    ids["goals"] = alice.post(f"/api/budgets/{ab}/goals", {"name": "جوال", "target_amount": 4000}).get_json()["item"]["id"]
    return {"app": app, "alice": alice, "bob": bob, "ids": ids}


def snapshot(app, user_id=1):
    c = connect(app.config["DB_PATH"])
    out = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t} WHERE user_id = ? ORDER BY rowid", (user_id,))]
           for t in ("budgets", "incomes", "expenses", "goals", "expense_types", "goal_categories",
                     "recurring_templates", "user_settings")}
    c.close()
    return out


def attacks(i):
    ab, bb = i["ab"], i["bb"]
    calls = [
        ("GET", f"/api/budgets/{ab}", None),
        ("GET", f"/api/budgets/{ab}/dashboard", None),
        ("PATCH", f"/api/budgets/{ab}", {"savings_current": 1}),
        ("DELETE", f"/api/budgets/{ab}", None),
        ("POST", f"/api/budgets/{ab}/apply-templates", None),
        ("PATCH", f"/api/expense-types/{i['type']}", {"name": "hacked"}),
        ("DELETE", f"/api/expense-types/{i['type']}", None),
        ("PATCH", f"/api/goal-categories/{i['cat']}", {"name": "hacked"}),
        ("DELETE", f"/api/goal-categories/{i['cat']}", None),
        ("PATCH", f"/api/templates/{i['tpl']}", {"amount": 1}),
        ("DELETE", f"/api/templates/{i['tpl']}", None),
        ("POST", "/api/templates", {"kind": "expense", "name": "x", "type_id": i["type"]}),
        ("POST", f"/api/budgets/{bb}/expenses", {"name": "x", "type_id": i["type"]}),
        ("POST", f"/api/budgets/{bb}/goals", {"name": "x", "category_id": i["cat"]}),
        ("POST", f"/api/budgets/{bb}/incomes", {"source": "x", "template_id": i["tpl"]}),
    ]
    for kind in KINDS:
        new = NEW_ITEM.get(kind, {"name": "x", "type_id": i["bob_type"]})
        calls += [
            ("GET", f"/api/budgets/{ab}/{kind}", None),
            ("POST", f"/api/budgets/{ab}/{kind}", new),
            ("PATCH", f"/api/budgets/{ab}/{kind}/{i[kind]}", ITEM_BODY[kind]),
            ("DELETE", f"/api/budgets/{ab}/{kind}/{i[kind]}", None),
            ("PATCH", f"/api/budgets/{bb}/{kind}/{i[kind]}", ITEM_BODY[kind]),    # via bob's own month
            ("DELETE", f"/api/budgets/{bb}/{kind}/{i[kind]}", None),
        ]
    return calls


ATTACK_COUNT = len(attacks({k: 1 for k in ("ab", "bb", "type", "cat", "tpl", "bob_type", *KINDS)}))


@pytest.mark.parametrize("n", range(ATTACK_COUNT))
def test_bob_cannot_touch_alice(world, n):
    method, url, body = attacks(world["ids"])[n]
    before = snapshot(world["app"])
    r = world["bob"].call(method, url, body)
    assert r.status_code == 404, (method, url, r.get_json())
    assert r.get_json()["error"] == "not_found"
    assert snapshot(world["app"]) == before


def test_lists_are_per_user(world):
    bob = world["bob"]
    assert [m["id"] for m in bob.get("/api/budgets").get_json()] == [world["ids"]["bb"]]
    assert "عربية" not in [t["name"] for t in bob.get("/api/expense-types").get_json()]
    assert bob.get("/api/templates").get_json() == []
    d = bob.get(f"/api/budgets/{world['ids']['bb']}").get_json()
    assert d["incomes"] == d["expenses"] == d["goals"] == []


def test_settings_are_per_user(world):
    world["alice"].patch("/api/settings", {"currency": "ج.م"})
    assert world["bob"].get("/api/settings").get_json()["currency"] == "ر.س"


@pytest.mark.parametrize("n", range(ATTACK_COUNT))
def test_anonymous_gets_401(world, n):
    method, url, body = attacks(world["ids"])[n]
    r = Api(world["app"]).call(method, url, body)
    assert r.status_code == 401
