"""Items and balances kept in another currency (step 5: storage and copying; no conversion yet)."""
import pytest

from tests.api_helpers import Api, make_app


@pytest.fixture
def api(tmp_path):
    return Api(make_app(tmp_path), "alice")         # base currency SAR


def test_item_currency_and_base_is_stored_as_null(api):
    b = api.budget_id()
    r = api.post(f"/api/budgets/{b}/incomes", {"source": "إيجار شقة مصر", "amount": 5000, "currency": "EGP"})
    item = r.get_json()["item"]
    assert r.status_code == 201 and item["currency"] == "EGP"
    item = api.patch(f"/api/budgets/{b}/incomes/{item['id']}", {"currency": "SAR"}).get_json()["item"]
    assert item["currency"] is None                                       # the base currency
    assert api.patch(f"/api/budgets/{b}/incomes/{item['id']}", {"currency": "XYZ"}).status_code == 422
    r = api.post(f"/api/budgets/{b}/goals", {"name": "ذهب", "target_amount": 1000, "currency": "USD"})
    assert r.get_json()["item"]["currency"] == "USD"


def test_balances_in_other_currencies(api):
    b = api.budget_id()
    put = lambda body: api.put(f"/api/budgets/{b}/balances", body)
    d = put({"kind": "savings", "currency": "EGP", "amount": 10000}).get_json()
    assert [(x["kind"], x["currency"], x["amount"]) for x in d["balances"]] == [("savings", "EGP", 10000)]
    assert d["currencies"][0]["code"] == "SAR"
    assert put({"kind": "savings", "currency": "SAR", "amount": 5}).status_code == 422      # base currency
    assert put({"kind": "other", "currency": "EGP", "amount": 5}).status_code == 422
    put({"kind": "emergency", "currency": "USD", "amount": 300})
    d = put({"kind": "savings", "currency": "EGP", "amount": 0}).get_json()                 # 0 removes it
    assert [(x["kind"], x["currency"]) for x in d["balances"]] == [("emergency", "USD")]


def test_new_month_copies_currencies_and_balances(api):
    b = api.budget_id()
    tid = api.post("/api/templates", {"kind": "expense", "name": "قسط", "type_id": api.type_id("أساسي"),
                                      "amount": 100, "currency": "USD"}).get_json()["id"]
    api.post(f"/api/budgets/{b}/apply-templates")
    api.post(f"/api/budgets/{b}/expenses", {"name": "جهاز", "type_id": api.type_id("أساسي"),
                                            "inst_balance": 1200, "inst_count": 12, "currency": "EGP"})
    api.put(f"/api/budgets/{b}/balances", {"kind": "savings", "currency": "EGP", "amount": 10000})
    months = api.get("/api/budgets").get_json()
    y, m = months[-1]["year"], months[-1]["month"]
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = api.post("/api/budgets", {"year": y, "month": m, "copy_from_previous": True}).get_json()
    by_name = {e["name"]: e for e in d["expenses"]}
    assert by_name["قسط"]["currency"] == "USD" and by_name["قسط"]["template_id"] == tid
    assert by_name["جهاز"]["currency"] == "EGP" and by_name["جهاز"]["inst_count"] == 11
    assert [(x["currency"], x["amount"]) for x in d["balances"]] == [("EGP", 10000)]

