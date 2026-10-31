import pytest

from tests.api_helpers import Api, make_app
from tests.sheet_data import sheet_input


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def api(app):
    return Api(app, "alice")


def expense(api, budget, **values):
    body = {"name": "بند", "type_id": api.type_id("أساسي"), **values}
    r = api.post(f"/api/budgets/{budget}/expenses", body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["item"]


# ---------- months

def test_requires_login(app):
    anon = Api(app)
    r = anon.get("/api/budgets")
    assert r.status_code == 401 and r.get_json()["error"] == "unauthorized"


def test_months(api):
    months = api.get("/api/budgets").get_json()
    assert len(months) == 1 and set(months[0]) == {"id", "year", "month", "label", "source_budget_id"}

    r = api.post("/api/budgets", {"year": 2030, "month": 2, "savings_current": 500})
    assert r.status_code == 201
    d = r.get_json()
    assert d["budget"]["days_in_month"] == 28 and d["savings"]["current"] == 500

    assert api.post("/api/budgets", {"year": 2030, "month": 2}).status_code == 409
    for bad in ({"year": 2030, "month": 13}, {"year": 2030, "month": 0}, {"year": 1999, "month": 1},
                {"month": 1}, {"year": "x", "month": 1}):
        assert api.post("/api/budgets", bad).status_code == 422, bad


def test_budget_balances_patch(api):
    b = api.budget_id()
    d = api.patch(f"/api/budgets/{b}", {"emergency_current": 250, "emergency_min": 260}).get_json()
    assert d["emergency"]["remaining"] == 10
    assert api.patch(f"/api/budgets/{b}", {"year": 2020}).status_code == 422
    assert api.patch(f"/api/budgets/{b}", {"savings_current": -5}).status_code == 422


def test_delete_month_rules(api):
    first = api.budget_id()
    r = api.delete(f"/api/budgets/{first}")
    assert r.status_code == 409 and r.get_json()["error"] == "last_budget"     # the only month
    copied = api.post("/api/budgets", {"year": 2040, "month": 1, "copy_from_previous": True}).get_json()["budget"]
    assert copied["source_budget_id"] == first
    r = api.delete(f"/api/budgets/{first}")                                     # a month was copied from it
    assert r.status_code == 409 and r.get_json()["error"] == "has_followers"
    blank = api.post("/api/budgets", {"year": 2041, "month": 1}).get_json()["budget"]["id"]
    assert api.delete(f"/api/budgets/{copied['id']}").status_code == 204       # any month without copies
    assert api.delete(f"/api/budgets/{first}").status_code == 204
    assert [m["id"] for m in api.get("/api/budgets").get_json()] == [blank]


def test_dashboard_shape(api):
    d = api.get(f"/api/budgets/{api.budget_id()}").get_json()
    for key in ("budget", "settings", "expense_types", "goal_categories", "incomes", "expenses", "goals",
                "goals_total", "summary", "suggestions", "emergency", "savings", "payments", "warnings",
                "warnings_count", "warnings_text", "charts", "debug", "issues"):
        assert key in d, key
    assert d["summary"]["status"] == {"key": "acceptable", "level": "orange", "text": "🟠 مقبول — الفائض قليل"}
    assert len(d["charts"]["daily"]["by_day"]) == d["budget"]["days_in_month"]


def test_sheet_through_api_matches_workbook(api):
    """Enter the workbook's data through the API and get the same results."""
    b = api.budget_id()
    src = sheet_input()
    api.patch(f"/api/budgets/{b}", {"emergency_current": 250, "emergency_min": 260, "savings_current": 90})
    for i in src.incomes:
        api.post(f"/api/budgets/{b}/incomes", {"source": i.source, "amount": float(i.amount),
                                               "is_savings_withdrawal": i.is_savings_withdrawal})
    for e in src.expenses:
        expense(api, b, name=e.name, type_id=api.type_id(e.type_name), cash_amount=float(e.cash),
                inst_balance=float(e.inst_balance), inst_count=e.inst_count)
    for g in src.goals:
        body = {"name": g.name, "target_amount": float(g.target)}
        if g.priority is not None:
            body["priority"] = float(g.priority)
        if g.manual_amount is not None:
            body["manual_allocation"] = float(g.manual_amount)
        assert api.post(f"/api/budgets/{b}/goals", body).status_code == 201

    d = api.get(f"/api/budgets/{b}").get_json()
    assert d["summary"]["income"] == 5600
    assert d["summary"]["net"] == pytest.approx(41.6667, abs=1e-3)
    assert d["summary"]["status"]["key"] == "squeezed"
    assert d["warnings_count"] == 8
    assert d["suggestions"]["return_to_savings"] == pytest.approx(41.6667, abs=1e-3)
    assert d["emergency"]["reference"] == 9450
    assert [e["base"] for e in d["expenses"][:2]] == [pytest.approx(233.3333, abs=1e-3), 1450]
    assert d["expenses"][0]["type_name"] == "كمالي" and d["expenses"][0]["inst_original_count"] == 3
    manual = d["goals"][3]
    assert (manual["name"], manual["mode"], manual["allocated"]) == ("هوبالالا", "manual", 0)
    assert d["issues"] == [{"message": d["issues"][0]["message"], "blocking": False, "field": "goals"}]


# ---------- expenses: linked fields

def test_deferral_defaults_and_resets(api):
    b = api.budget_id()
    e = expense(api, b, name="قسط", type_id=api.type_id("دين/أقساط"), inst_balance=1500, inst_count=3)
    url = f"/api/budgets/{b}/expenses/{e['id']}"

    r = api.patch(url, {"status": "deferred", "paid_at": "2026-11-05"}).get_json()
    assert r["item"]["deferred_amount"] == 500                        # whole amount by default
    r = api.patch(url, {"status": "deferred", "deferred_amount": 100}).get_json()
    line = r["dashboard"]["expenses"][0]
    assert (line["due"], line["deferred"], line["effective"], line["paid_part"]) == (500, 100, 400, 400)

    r = api.patch(url, {"status": "unpaid"}).get_json()["item"]
    assert (r["deferred_amount"], r["paid_at"]) == (0, None)
    month = api.get(f"/api/budgets/{b}").get_json()["budget"]
    r = api.patch(url, {"status": "paid", "paid_at": f"{month['year']}-{month['month']:02d}-07"}).get_json()
    assert r["dashboard"]["charts"]["daily"]["by_day"][6] == 500


def test_deferral_over_due_is_rejected_and_rolled_back(api):
    b = api.budget_id()
    e = expense(api, b, cash_amount=300)
    url = f"/api/budgets/{b}/expenses/{e['id']}"
    r = api.patch(url, {"status": "deferred", "deferred_amount": 400})
    assert r.status_code == 422
    body = r.get_json()
    assert body["field"] == "deferred_amount" and body["item_id"] == e["id"]
    assert api.get(f"/api/budgets/{b}/expenses").get_json()[0]["status"] == "unpaid"

    api.patch(url, {"status": "deferred", "deferred_amount": 300})
    r = api.patch(url, {"cash_amount": 100})                         # would leave deferred > due
    assert r.status_code == 422
    assert api.get(f"/api/budgets/{b}/expenses").get_json()[0]["cash_amount"] == 300


def test_deferred_amount_needs_deferred_status(api):
    b = api.budget_id()
    e = expense(api, b, cash_amount=300)
    r = api.patch(f"/api/budgets/{b}/expenses/{e['id']}", {"deferred_amount": 50})
    assert r.status_code == 422 and r.get_json()["field"] == "deferred_amount"


def test_needs_amount_cleared_when_price_entered(api):
    b = api.budget_id()
    e = expense(api, b, name="كهرباء", needs_amount=True)
    d = api.get(f"/api/budgets/{b}").get_json()
    assert next(w for w in d["warnings"] if w["key"] == "needs_amount")["active"]
    item = api.patch(f"/api/budgets/{b}/expenses/{e['id']}", {"cash_amount": 310}).get_json()["item"]
    assert item["needs_amount"] is False

    inc = api.post(f"/api/budgets/{b}/incomes", {"source": "حوافز", "needs_amount": True}).get_json()["item"]
    item = api.patch(f"/api/budgets/{b}/incomes/{inc['id']}", {"amount": 500}).get_json()["item"]
    assert item["needs_amount"] is False


def test_expense_field_validation(api):
    b = api.budget_id()
    t = api.type_id("أساسي")
    cases = [
        ({"type_id": t}, "name"),
        ({"name": "  ", "type_id": t}, "name"),
        ({"name": "x"}, "type_id"),
        ({"name": "x", "type_id": t, "cash_amount": -1}, "cash_amount"),
        ({"name": "x", "type_id": t, "cash_amount": "abc"}, "cash_amount"),
        ({"name": "x", "type_id": t, "cash_amount": True}, "cash_amount"),
        ({"name": "x", "type_id": t, "inst_count": 2.5}, "inst_count"),
        ({"name": "x", "type_id": t, "status": "maybe"}, "status"),
        ({"name": "x", "type_id": t, "paid_at": "5/11/2026"}, "paid_at"),
        ({"name": "x", "type_id": t, "paid_at": "2026-02-30"}, "paid_at"),
        ({"name": "x", "type_id": t, "prev_expense_id": 1}, "prev_expense_id"),   # internal, not writable
        ({"name": "x", "type_id": t, "budget_id": b}, "budget_id"),
        ({"name": "x", "type_id": 99999}, None),                                  # 404 below
    ]
    for body, field in cases:
        r = api.post(f"/api/budgets/{b}/expenses", body)
        if field is None:
            assert r.status_code == 404
        else:
            assert r.status_code == 422 and r.get_json()["field"] == field, (body, r.get_json())
    assert api.get(f"/api/budgets/{b}/expenses").get_json() == []


def test_money_rounding_and_string_numbers(api):
    b = api.budget_id()
    e = expense(api, b, cash_amount="120.456")
    assert e["cash_amount"] == 120.46


def test_wrong_month_path_and_unknown_kind(api):
    b = api.budget_id()
    other = api.post("/api/budgets", {"year": 2040, "month": 1}).get_json()["budget"]["id"]
    e = expense(api, b, cash_amount=1)
    assert api.patch(f"/api/budgets/{other}/expenses/{e['id']}", {"cash_amount": 2}).status_code == 404
    assert api.get(f"/api/budgets/{b}/bananas").status_code == 404


def test_delete_item_returns_dashboard(api):
    b = api.budget_id()
    e = expense(api, b, cash_amount=100)
    r = api.delete(f"/api/budgets/{b}/expenses/{e['id']}")
    assert r.status_code == 200 and r.get_json()["dashboard"]["summary"]["expenses"] == 0
    assert api.delete(f"/api/budgets/{b}/expenses/{e['id']}").status_code == 404


# ---------- goals

def test_goal_priority_and_manual_are_linked(api):
    b = api.budget_id()
    api.patch(f"/api/budgets/{b}", {"savings_current": 200})
    g = api.post(f"/api/budgets/{b}/goals", {"name": "جوال", "target_amount": 1000, "priority": 0.5}).get_json()
    assert g["dashboard"]["goals"][0]["requested"] == 100
    url = f"/api/budgets/{b}/goals/{g['item']['id']}"

    item = api.patch(url, {"manual_allocation": 100}).get_json()["item"]
    assert (item["priority"], item["manual_allocation"]) == (None, 100)
    item = api.patch(url, {"priority": 0.25}).get_json()
    assert (item["item"]["priority"], item["item"]["manual_allocation"]) == (0.25, None)
    assert item["dashboard"]["goals"][0]["allocated"] == 50

    r = api.patch(url, {"priority": 0.5, "manual_allocation": 10})
    assert r.status_code == 422
    assert api.patch(url, {"priority": 1.5}).status_code == 422
    item = api.patch(url, {"priority": None}).get_json()["dashboard"]["goals"][0]
    assert item["mode"] == "auto"


def test_goal_category(api):
    b = api.budget_id()
    cat = api.get("/api/goal-categories").get_json()[1]
    g = api.post(f"/api/budgets/{b}/goals", {"name": "x", "target_amount": 10, "category_id": cat["id"]}).get_json()
    assert g["dashboard"]["goals"][0]["category_name"] == cat["name"]


# ---------- settings / types / categories / templates

def test_settings(api):
    s = api.get("/api/settings").get_json()
    assert s["emergency_ref_factor"] == 1.25
    s = api.patch("/api/settings", {"emergency_ref_factor": 1.5, "currency": "ج.م"}).get_json()
    assert (s["emergency_ref_factor"], s["currency"]) == (1.5, "ج.م")
    assert api.patch("/api/settings", {"good_rate": 0.5}).status_code == 422          # > excellent 0.2
    assert api.patch("/api/settings", {"max_debt_rate": 1.2}).status_code == 422
    assert api.patch("/api/settings", {"user_id": 2}).status_code == 422
    assert api.patch("/api/settings", {"currency": ""}).status_code == 422


def test_expense_types(api):
    b = api.budget_id()
    t = api.post("/api/expense-types", {"name": "عربية"})
    assert t.status_code == 201 and t.get_json()["role"] == "other"
    tid = t.get_json()["id"]
    assert api.post("/api/expense-types", {"name": "عربية"}).status_code == 409
    assert api.patch(f"/api/expense-types/{tid}", {"name": "سيارة", "role": "luxury"}).get_json()["role"] == "luxury"
    assert api.post("/api/expense-types", {"name": "x", "is_system": True}).status_code == 422

    essential = api.type_id("أساسي")
    r = api.delete(f"/api/expense-types/{essential}")
    assert r.status_code == 409 and r.get_json()["error"] == "system_type"
    assert api.patch(f"/api/expense-types/{essential}", {"role": "luxury"}).status_code == 409

    expense(api, b, type_id=tid)
    r = api.delete(f"/api/expense-types/{tid}")
    assert r.status_code == 409 and r.get_json()["error"] == "in_use"


def test_goal_categories(api):
    c = api.post("/api/goal-categories", {"name": "سفر"}).get_json()
    assert api.patch(f"/api/goal-categories/{c['id']}", {"name": "رحلات"}).get_json()["name"] == "رحلات"
    assert api.delete(f"/api/goal-categories/{c['id']}").status_code == 204
    assert api.delete(f"/api/goal-categories/{c['id']}").status_code == 404


def test_templates(api):
    b = api.budget_id()
    essential = api.type_id("أساسي")
    internet = api.post("/api/templates", {"kind": "expense", "name": "انترنت", "type_id": essential, "amount": 120})
    assert internet.status_code == 201
    electricity = api.post("/api/templates", {"kind": "expense", "name": "كهرباء", "type_id": essential})
    assert electricity.get_json()["amount"] is None
    salary = api.post("/api/templates", {"kind": "income", "name": "راتب", "amount": 5000}).get_json()

    assert api.post("/api/templates", {"kind": "expense", "name": "x"}).status_code == 422
    assert api.post("/api/templates", {"kind": "income", "name": "x", "type_id": essential}).status_code == 422
    assert api.post("/api/templates", {"kind": "expense", "name": "x", "type_id": essential,
                                       "is_savings_withdrawal": True}).status_code == 422
    assert api.patch(f"/api/templates/{salary['id']}", {"kind": "expense"}).status_code == 422
    assert api.patch(f"/api/templates/{salary['id']}", {"active": False}).get_json()["active"] is False

    # an item may only link a template of its own kind
    r = api.post(f"/api/budgets/{b}/incomes", {"source": "راتب", "template_id": internet.get_json()["id"]})
    assert r.status_code == 422 and r.get_json()["field"] == "template_id"
    assert api.post(f"/api/budgets/{b}/incomes", {"source": "راتب", "amount": 5000,
                                                  "template_id": salary["id"]}).status_code == 201
    assert api.delete(f"/api/templates/{salary['id']}").status_code == 204
    assert api.get(f"/api/budgets/{b}/incomes").get_json()[0]["template_id"] is None


def test_writes_need_csrf_header(api):
    b = api.budget_id()
    r = api.client.patch(f"/api/budgets/{b}", json={"savings_current": 1})
    assert r.status_code == 403


def test_malformed_json(api):
    r = api.client.post("/api/expense-types", data="[1,2", headers={"X-Requested-With": "XMLHttpRequest",
                                                                    "Content-Type": "application/json"})
    assert r.status_code == 400
    assert api.post("/api/expense-types", [1, 2]).status_code == 400
