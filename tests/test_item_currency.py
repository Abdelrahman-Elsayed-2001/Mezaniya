"""Items and balances kept in another currency (step 5: storage and copying; no conversion yet)."""
import pytest

from tests.api_helpers import Api, make_app


@pytest.fixture
def api(tmp_path):
    return Api(make_app(tmp_path), "alice", currencies=("EGP", "USD"))         # base currency SAR


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



def test_amounts_are_converted_at_the_months_rate(api):
    b = api.budget_id()
    month = api.get(f"/api/budgets/{b}").get_json()["budget"]
    api.post(f"/api/budgets/{b}/incomes", {"source": "راتب", "amount": 9000})
    api.post(f"/api/budgets/{b}/incomes", {"source": "إيجار مصر", "amount": 5000, "currency": "EGP"})
    api.put(f"/api/budgets/{b}/balances", {"kind": "savings", "currency": "EGP", "amount": 10000})

    # no rate yet: the EGP amounts count as zero and the month says which rate is missing
    d = api.get(f"/api/budgets/{b}").get_json()
    assert d["summary"]["income"] == 9000 and d["missing_rates"] == ["EGP"]
    assert d["incomes"][1]["fx"]["missing"] is True and d["incomes"][0]["fx"] is None

    api.put("/api/rates", {"from_currency": "EGP", "to_currency": "SAR", "from_year": month["year"], "from_month": month["month"], "rate": 0.08})
    d = api.get(f"/api/budgets/{b}").get_json()
    assert d["summary"]["income"] == 9400 and d["missing_rates"] == []
    assert d["incomes"][1]["fx"]["amount_base"] == 400 and d["incomes"][1]["amount"] == 5000
    assert d["savings"]["current"] == 800                    # 0 SAR + 10000 EGP × 0.08

    # the next month keeps the EGP balance apart (not folded into the SAR balance) and uses its own rate
    y, m = (month["year"] + 1, 1) if month["month"] == 12 else (month["year"], month["month"] + 1)
    api.put("/api/rates", {"from_currency": "EGP", "to_currency": "SAR", "from_year": y, "from_month": m, "rate": 0.07})
    d2 = api.post("/api/budgets", {"year": y, "month": m, "copy_from_previous": True}).get_json()
    assert d2["budget"]["savings_current"] == pytest.approx(d["savings"]["total"] - 800)
    assert [(x["currency"], x["amount"]) for x in d2["balances"]] == [("EGP", 10000)]
    assert d2["balances"][0]["fx"]["amount_base"] == 700


def test_carry_over_stays_in_the_items_currency(api):
    b = api.budget_id()
    month = api.get(f"/api/budgets/{b}").get_json()["budget"]
    api.put("/api/rates", {"from_currency": "EGP", "to_currency": "SAR", "from_year": month["year"], "from_month": month["month"], "rate": 0.08})
    e = api.post(f"/api/budgets/{b}/expenses", {"name": "كهرباء مصر", "type_id": api.type_id("أساسي"),
                                                "cash_amount": 1000, "currency": "EGP"}).get_json()["item"]
    api.post(f"/api/budgets/{b}/incomes", {"source": "راتب", "amount": 9000})
    d = api.get(f"/api/budgets/{b}").get_json()
    assert next(x for x in d["expenses"] if x["id"] == e["id"])["due"] == 80          # in SAR
    preview = api.post("/api/budgets/rollover-preview").get_json()
    assert preview["unpaid"] == [{"id": e["id"], "name": "كهرباء مصر", "due": 1000, "currency": "EGP"}]
    y, m = (month["year"] + 1, 1) if month["month"] == 12 else (month["year"], month["month"] + 1)
    d2 = api.post("/api/budgets", {"year": y, "month": m, "copy_from_previous": True,
                                   "carry_unpaid_ids": [e["id"]]}).get_json()
    carried = next(x for x in d2["expenses"] if x["prev_expense_id"] == e["id"])
    assert (carried["carried_in"], carried["currency"]) == (1000, "EGP")
