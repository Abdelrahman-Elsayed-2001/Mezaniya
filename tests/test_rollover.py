"""New month from the previous one + live carry sync, exercised through the API."""
import pytest

from app.services.rollover import label, next_period
from tests.api_helpers import Api, make_app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def api(app):
    return Api(app, "alice")


def months(api):
    return api.get("/api/budgets").get_json()


def first(api):
    return months(api)[0]


def new_month(api, **options):
    latest = months(api)[-1]
    year, month = next_period(latest["year"], latest["month"])
    r = api.post("/api/budgets", {"year": year, "month": month, "copy_from_previous": True, **options})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def add_expense(api, budget_id, name, type_name="أساسي", **values):
    r = api.post(f"/api/budgets/{budget_id}/expenses", {"name": name, "type_id": api.type_id(type_name), **values})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["item"]


def patch_expense(api, budget_id, expense_id, **values):
    r = api.patch(f"/api/budgets/{budget_id}/expenses/{expense_id}", values)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def expenses(api, budget_id):
    return api.get(f"/api/budgets/{budget_id}").get_json()["expenses"]


def by_name(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_next_period():
    assert next_period(2026, 11) == (2026, 12)
    assert next_period(2026, 12) == (2027, 1)


# ---------- installments

def test_installment_continues_until_the_last_payment(api):
    m1 = first(api)["id"]
    ps = add_expense(api, m1, "بلايستيشن", "كمالي", inst_balance=700, inst_count=3)

    d2 = new_month(api)
    line = by_name(d2["expenses"], "بلايستيشن")
    assert (line["inst_balance"], line["inst_count"], line["inst_original_count"]) == (466.67, 2, 3)
    assert line["prev_expense_id"] == ps["id"] and line["cash_amount"] == 0
    assert line["base"] == pytest.approx(233.33, abs=0.01)

    d3 = new_month(api)
    line = by_name(d3["expenses"], "بلايستيشن")
    assert line["inst_count"] == 1 and line["base"] == pytest.approx(233.33, abs=0.01)

    d4 = new_month(api)           # last payment was made in month 3
    assert d4["expenses"] == []


def test_installment_cash_part_is_one_off(api):
    m1 = first(api)["id"]
    add_expense(api, m1, "اقساط تابي وتمارا", "دين/أقساط", cash_amount=800, inst_balance=3250, inst_count=5)
    add_expense(api, m1, "خروجة البر", "كمالي", cash_amount=200)      # one-off: not copied
    line, = new_month(api)["expenses"]
    assert (line["name"], line["cash_amount"], line["inst_balance"], line["inst_count"]) == (
        "اقساط تابي وتمارا", 0, 2600, 4)
    assert line["base"] == 650


def test_partial_deferral_of_an_installment(api):
    """قسط 500، اتدفع 400 واترحّل 100 → الشهر الجاي 600."""
    m1 = first(api)["id"]
    e = add_expense(api, m1, "قسط", "دين/أقساط", inst_balance=1500, inst_count=3)
    patch_expense(api, m1, e["id"], status="deferred", deferred_amount=100)
    line = by_name(new_month(api)["expenses"], "قسط")
    assert (line["inst_count"], line["carried_in"], line["due"]) == (2, 100, 600)


# ---------- recurring items

def test_recurring_items_and_a_fully_deferred_bill(api):
    essential = api.type_id("أساسي")
    api.post("/api/templates", {"kind": "expense", "name": "انترنت", "type_id": essential, "amount": 120})
    api.post("/api/templates", {"kind": "expense", "name": "كهرباء", "type_id": essential})
    api.post("/api/templates", {"kind": "income", "name": "راتب", "amount": 5000})
    api.post("/api/templates", {"kind": "expense", "name": "قديم", "type_id": essential, "amount": 1,
                                "active": False})
    m1 = first(api)["id"]
    r = api.post(f"/api/budgets/{m1}/apply-templates").get_json()
    assert r["added"] == 3
    assert api.post(f"/api/budgets/{m1}/apply-templates").get_json()["added"] == 0    # no duplicates
    rows = r["dashboard"]["expenses"]
    assert by_name(rows, "كهرباء")["needs_amount"] is True
    assert r["dashboard"]["incomes"][0]["amount"] == 5000

    electricity = by_name(rows, "كهرباء")
    patch_expense(api, m1, electricity["id"], cash_amount=300)
    patch_expense(api, m1, electricity["id"], status="deferred")            # whole bill moves on

    d2 = new_month(api)
    assert [e["name"] for e in d2["expenses"]] == ["انترنت", "كهرباء"]
    internet, bill = d2["expenses"]
    assert internet["cash_amount"] == 120 and internet["prev_expense_id"] == by_name(rows, "انترنت")["id"]
    assert (bill["needs_amount"], bill["cash_amount"], bill["carried_in"]) == (True, 0, 300)
    assert d2["incomes"][0]["source"] == "راتب"

    new_bill = patch_expense(api, d2["budget"]["id"], bill["id"], cash_amount=250)
    assert by_name(new_bill["dashboard"]["expenses"], "كهرباء")["due"] == 550     # two months together
    assert new_bill["item"]["needs_amount"] is False


# ---------- unpaid items

def test_preview_lists_unpaid_and_carry_option(api):
    m1 = first(api)
    gift = add_expense(api, m1["id"], "هدية", cash_amount=200)
    paid = add_expense(api, m1["id"], "انترنت", cash_amount=120, status="paid")
    add_expense(api, m1["id"], "مش متسعّر", cash_amount=0)

    preview = api.post("/api/budgets/rollover-preview").get_json()
    year, month = next_period(m1["year"], m1["month"])
    assert preview["target"] == {"year": year, "month": month, "label": label(year, month)}
    assert preview["unpaid"] == [{"id": gift["id"], "name": "هدية", "due": 200}]
    assert preview["expenses"] == []

    with_carry = api.post("/api/budgets/rollover-preview", {"carry_unpaid_ids": [gift["id"]]}).get_json()
    assert with_carry["expenses"][0]["carried_in"] == 200
    assert api.get(f"/api/budgets/{m1['id']}/expenses").get_json()[0]["status"] == "unpaid"   # preview writes nothing

    bad = api.post("/api/budgets/rollover-preview", {"carry_unpaid_ids": [paid["id"]]})
    assert bad.status_code == 422

    d2 = new_month(api, carry_unpaid_ids=[gift["id"]])
    line, = d2["expenses"]
    assert (line["name"], line["carried_in"], line["prev_expense_id"]) == ("هدية", 200, gift["id"])
    assert line["notes"] == f"مرحَّل من {label(m1['year'], m1['month'])}"
    old = by_name(expenses(api, m1["id"]), "هدية")
    assert (old["status"], old["deferred_amount"], old["effective"]) == ("deferred", 200, 0)


# ---------- balances and goals

def test_balances_move_forward(api):
    m1 = first(api)["id"]
    api.patch(f"/api/budgets/{m1}", {"emergency_current": 250, "emergency_min": 1000, "savings_current": 500})
    api.post(f"/api/budgets/{m1}/incomes", {"source": "راتب", "amount": 1000})
    api.post(f"/api/budgets/{m1}/incomes", {"source": "سحب", "amount": 100, "is_savings_withdrawal": True})
    add_expense(api, m1, "طوارئ", "طوارئ", cash_amount=50)
    add_expense(api, m1, "ادخار", "ادخار", cash_amount=30)
    add_expense(api, m1, "أكل", cash_amount=1000)
    # net = 1100 - 1080 = 20 → return_to_savings = min(100, 20) = 20

    preview = api.post("/api/budgets/rollover-preview").get_json()
    assert preview["balances"] == {"emergency_current": 300, "emergency_min": 1000, "savings_current": 430}
    assert preview["return_to_savings"] == 20

    d2 = new_month(api, add_return_to_savings=True)
    assert (d2["emergency"]["current"], d2["emergency"]["minimum"], d2["savings"]["current"]) == (300, 1000, 450)


def test_balance_overrides_and_negative_savings(api):
    m1 = first(api)["id"]
    api.post(f"/api/budgets/{m1}/incomes", {"source": "سحب", "amount": 100, "is_savings_withdrawal": True})
    assert api.post("/api/budgets/rollover-preview").get_json()["balances"]["savings_current"] == 0
    d2 = new_month(api, savings_current=75)
    assert d2["savings"]["current"] == 75


def test_goals_are_copied(api):
    m1 = first(api)["id"]
    api.patch(f"/api/budgets/{m1}", {"savings_current": 100})
    cat = api.get("/api/goal-categories").get_json()[0]["id"]
    api.post(f"/api/budgets/{m1}/goals", {"name": "بطيخ", "target_amount": 20, "category_id": cat})
    api.post(f"/api/budgets/{m1}/goals", {"name": "سيارة", "target_amount": 35000, "priority": 0.5})
    d2 = new_month(api)
    assert [(g["name"], g["category_id"], g["priority"]) for g in d2["goals"]] == [
        ("بطيخ", cat, None), ("سيارة", None, 0.5)]
    d3 = new_month(api, drop_completed_goals=True)
    assert [g["name"] for g in d3["goals"]] == ["سيارة"]


# ---------- rules

def test_month_order_rules(api):
    m1 = first(api)
    later_year, later_month = next_period(*next_period(m1["year"], m1["month"]))
    assert api.post("/api/budgets", {"year": later_year, "month": later_month,
                                     "copy_from_previous": True}).status_code == 201   # skipping a month is fine
    r = api.post("/api/budgets", {"year": m1["year"], "month": m1["month"], "copy_from_previous": True})
    assert r.status_code == 409 and r.get_json()["error"] == "duplicate"
    y, mo = next_period(m1["year"], m1["month"])
    r = api.post("/api/budgets", {"year": y, "month": mo, "copy_from_previous": True})
    assert r.status_code == 409 and r.get_json()["error"] == "not_after_latest"
    r = api.post("/api/budgets", {"year": 2099, "month": 1, "carry_unpaid_ids": []})
    assert r.status_code == 422 and r.get_json()["field"] == "copy_from_previous"
    assert api.post("/api/budgets/rollover-preview", {"year": 2099}).status_code == 422


def test_earlier_months_are_created_empty(api):
    m1 = first(api)
    prev_y, prev_m = (m1["year"] - 1, 12) if m1["month"] == 1 else (m1["year"], m1["month"] - 1)
    preview = api.post("/api/budgets/rollover-preview", {"year": prev_y, "month": prev_m}).get_json()
    assert preview["mode"] == "blank" and preview["latest"]["id"] == m1["id"]
    r = api.post("/api/budgets", {"year": prev_y, "month": prev_m, "copy_from_previous": True})
    assert r.status_code == 409 and r.get_json()["error"] == "not_after_latest"
    d = api.post("/api/budgets", {"year": prev_y, "month": prev_m, "savings_current": 100}).get_json()
    assert d["expenses"] == [] and d["budget"]["source_budget_id"] is None and d["savings"]["current"] == 100
    assert [m["id"] for m in months(api)][1] == m1["id"]                      # listed in calendar order
    assert api.post("/api/budgets/rollover-preview", {"year": prev_y, "month": prev_m}).status_code == 409


def test_carry_only_follows_the_month_it_was_copied_into(api):
    m1 = first(api)
    y, m = next_period(*next_period(m1["year"], m1["month"]))
    copy = api.post("/api/budgets", {"year": y, "month": m, "copy_from_previous": True}).get_json()["budget"]
    gap_y, gap_m = next_period(m1["year"], m1["month"])
    gap = api.post("/api/budgets", {"year": gap_y, "month": gap_m}).get_json()["budget"]   # added later, in between
    gift = add_expense(api, m1["id"], "هدية", cash_amount=200)
    patch_expense(api, m1["id"], gift["id"], status="deferred")
    assert expenses(api, gap["id"]) == []                                     # never the month in between
    assert expenses(api, copy["id"])[0]["carried_in"] == 200
    older = add_expense(api, gap["id"], "x", cash_amount=50)
    patch_expense(api, gap["id"], older["id"], status="deferred")             # a blank month has no copy
    assert len(expenses(api, copy["id"])) == 1


def test_december_rolls_into_january(api):
    api.post("/api/budgets", {"year": 2040, "month": 12})
    d = new_month(api)
    assert (d["budget"]["year"], d["budget"]["month"]) == (2041, 1)


# ---------- live carry sync (the next month already exists)

def test_changing_a_deferral_updates_the_next_month(api):
    m1 = first(api)["id"]
    inst = add_expense(api, m1, "قسط", "دين/أقساط", inst_balance=1500, inst_count=3)
    m2 = new_month(api)["budget"]["id"]

    patch_expense(api, m1, inst["id"], status="deferred", deferred_amount=100)
    assert by_name(expenses(api, m2), "قسط")["carried_in"] == 100
    patch_expense(api, m1, inst["id"], deferred_amount=50)
    assert by_name(expenses(api, m2), "قسط")["carried_in"] == 50
    patch_expense(api, m1, inst["id"], status="paid")
    line = by_name(expenses(api, m2), "قسط")
    assert (line["carried_in"], line["due"]) == (0, 500)          # installment line stays


def test_late_deferral_creates_and_removes_a_carry_line(api):
    m1 = first(api)["id"]
    gift = add_expense(api, m1, "هدية", cash_amount=200)
    m2 = new_month(api)["budget"]["id"]
    assert expenses(api, m2) == []

    patch_expense(api, m1, gift["id"], status="deferred")
    line, = expenses(api, m2)
    assert (line["name"], line["carried_in"], line["prev_expense_id"]) == ("هدية", 200, gift["id"])

    patch_expense(api, m1, gift["id"], status="unpaid")
    assert expenses(api, m2) == []


def test_deleting_a_deferred_expense_removes_its_carry(api):
    m1 = first(api)["id"]
    gift = add_expense(api, m1, "هدية", cash_amount=200)
    patch_expense(api, m1, gift["id"], status="deferred")
    m2 = new_month(api)["budget"]["id"]
    assert len(expenses(api, m2)) == 1
    assert api.delete(f"/api/budgets/{m1}/expenses/{gift['id']}").status_code == 200
    assert expenses(api, m2) == []


def test_carry_line_already_paid_is_kept(api):
    m1 = first(api)["id"]
    gift = add_expense(api, m1, "هدية", cash_amount=200)
    patch_expense(api, m1, gift["id"], status="deferred")
    m2 = new_month(api)["budget"]["id"]
    carried, = expenses(api, m2)
    patch_expense(api, m2, carried["id"], status="paid")
    patch_expense(api, m1, gift["id"], status="paid")
    line, = expenses(api, m2)
    assert (line["status"], line["carried_in"]) == ("paid", 0)


def test_reducing_a_deferral_clamps_and_propagates(api):
    m1 = first(api)["id"]
    bill = add_expense(api, m1, "كهرباء", cash_amount=300)
    patch_expense(api, m1, bill["id"], status="deferred")                     # 300 → month 2
    m2 = new_month(api)["budget"]["id"]
    carried = expenses(api, m2)[0]
    patch_expense(api, m2, carried["id"], status="deferred")                  # 300 → month 3
    m3 = new_month(api)["budget"]["id"]
    assert expenses(api, m3)[0]["carried_in"] == 300

    patch_expense(api, m1, bill["id"], deferred_amount=100)
    line2 = expenses(api, m2)[0]
    assert (line2["carried_in"], line2["status"], line2["deferred_amount"]) == (100, "deferred", 100)
    assert expenses(api, m3)[0]["carried_in"] == 100

    patch_expense(api, m1, bill["id"], status="paid")                         # nothing carried any more
    assert expenses(api, m2) == []
    assert expenses(api, m3) == []


def test_editing_the_latest_month_needs_no_sync(api):
    m1 = first(api)["id"]
    gift = add_expense(api, m1, "هدية", cash_amount=200)
    r = patch_expense(api, m1, gift["id"], status="deferred")
    assert r["item"]["deferred_amount"] == 200 and len(months(api)) == 1


# ---------- other users

def test_rollover_uses_only_own_data(app, api):
    bob = Api(app, "bob")
    alice_month = first(api)["id"]
    gift = add_expense(api, alice_month, "هدية", cash_amount=200)
    api.post("/api/templates", {"kind": "income", "name": "راتب", "amount": 5000})

    r = bob.post("/api/budgets/rollover-preview", {"carry_unpaid_ids": [gift["id"]]})
    assert r.status_code == 422
    d = new_month(bob)
    assert d["incomes"] == [] and d["expenses"] == []
    assert expenses(api, alice_month)[0]["status"] == "unpaid"
    assert bob.post(f"/api/budgets/{alice_month}/apply-templates").status_code == 404
