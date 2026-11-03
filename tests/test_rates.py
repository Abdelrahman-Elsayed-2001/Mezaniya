"""Exchange rates per period: manual rates, online rates, and which one wins."""
import pytest

from app.db.repo import rates
from tests.api_helpers import Api, make_app


@pytest.fixture
def api(tmp_path):
    return Api(make_app(tmp_path), "alice", currencies=("EGP", "USD"))          # base SAR


def table(api):
    return [(r["from_ym"], r["rate"], r["source"]) for r in api.get("/api/rates").get_json()["rates"]
            if r["currency"] == "EGP"]


def put(api, **values):
    return api.put("/api/rates", {"from_currency": "EGP", "to_currency": "SAR", **values})


def test_open_and_closed_periods(api):
    assert put(api, from_year=2026, from_month=1, rate=0.08).status_code == 200
    assert table(api) == [(202601, 0.08, "manual")]
    # a closed period in the middle: the old rate comes back after it
    put(api, from_year=2026, from_month=3, to_year=2026, to_month=6, rate=0.075)
    assert table(api) == [(202601, 0.08, "manual"), (202603, 0.075, "manual"), (202607, 0.08, "manual")]
    # an open period from May replaces what came after it
    put(api, from_year=2026, from_month=5, rate=0.07)
    assert table(api) == [(202601, 0.08, "manual"), (202603, 0.075, "manual"), (202605, 0.07, "manual")]
    rid = api.get("/api/rates").get_json()["rates"][1]["id"]
    api.delete(f"/api/rates/{rid}")
    assert table(api) == [(202601, 0.08, "manual"), (202605, 0.07, "manual")]


def test_rate_is_validated(api):
    assert put(api, from_year=2026, from_month=1, rate=0).status_code == 422
    assert put(api, from_year=2026, from_month=13, rate=1).status_code == 422
    assert put(api, from_year=2026, from_month=5, to_year=2026, to_month=4, rate=1).status_code == 422
    assert put(api, from_year=2026, from_month=5, to_year=2026, rate=1).status_code == 422
    assert put(api, from_year=2026, from_month=1, rate=1, from_currency="SAR").status_code == 422


def test_manual_rates_win_over_online(tmp_path):
    from app.db import connect
    from app.db.connection import migrate
    from app.services import admin
    from datetime import date
    c = connect(tmp_path / "r.db")
    migrate(c)
    uid = admin.create_user(c, "bob", "secret1", "ar", date(2026, 1, 1))
    assert rates.set_online(c, uid, "SAR", "EGP", 202601, 0.081)
    assert rates.set_online(c, uid, "SAR", "EGP", 202602, 0.079)
    rates.set_manual(c, uid, "SAR", "EGP", 202603, 0.08, to_ym=202604)
    assert not rates.set_online(c, uid, "SAR", "EGP", 202604, 0.07)          # inside the manual period
    assert rates.set_online(c, uid, "SAR", "EGP", 202605, 0.07)               # after it
    assert rates.rate_for(c, uid, "SAR", "EGP", 2026, 4).rate == 0.08
    assert rates.rate_for(c, uid, "SAR", "EGP", 2026, 5) == rates.Rate(0.07, "online", 202605)
    assert rates.rate_for(c, uid, "SAR", "EGP", 2025, 12) is None
    assert rates.rate_for(c, uid, "EGP", "EGP", 2026, 5) is None              # rows are per base currency
    c.close()


def test_rate_in_either_direction(api):
    # "1 SAR = 13.25 EGP" is kept as 1 EGP in SAR
    assert api.put("/api/rates", {"from_currency": "SAR", "to_currency": "EGP", "from_year": 2026, "from_month": 1,
                                  "rate": 13.25}).status_code == 200
    assert table(api) == [(202601, round(1 / 13.25, 10), "manual")]
    # neither side is the base currency, or both the same
    for pair in (("EGP", "USD"), ("SAR", "SAR")):
        r = api.put("/api/rates", {"from_currency": pair[0], "to_currency": pair[1], "from_year": 2026,
                                   "from_month": 1, "rate": 2})
        assert r.status_code == 422
    # a currency the user doesn't work with
    r = api.put("/api/rates", {"from_currency": "EUR", "to_currency": "SAR", "from_year": 2026, "from_month": 1, "rate": 4})
    assert r.status_code == 422 and r.get_json()["field"] == "from_currency"


def test_the_users_own_currency_list(api):
    cur = api.get("/api/currencies").get_json()
    assert [c["code"] for c in cur["currencies"]] == ["SAR", "EGP", "USD"] and cur["base"] == "SAR"
    assert "EUR" in [c["code"] for c in cur["builtin"]] and "EGP" not in [c["code"] for c in cur["builtin"]]
    assert api.post("/api/currencies", {"code": "EGP"}).status_code == 422                  # already there
    assert api.post("/api/currencies", {"code": "CN1"}).status_code == 422                  # not 3 letters
    assert api.post("/api/currencies", {"code": "CNY"}).status_code == 422                  # needs a name
    cur = api.post("/api/currencies", {"code": "cny", "name": "يوان", "symbol": "¥"}).get_json()
    cny = next(c for c in cur["currencies"] if c["code"] == "CNY")
    assert (cny["name"], cny["symbol"], cny["builtin"]) == ("يوان", "¥", False)
    # a new currency works like any other: an item, a rate, and it can't be removed while used
    b = api.budget_id()
    item = api.post(f"/api/budgets/{b}/incomes", {"source": "x", "amount": 100, "currency": "CNY"}).get_json()["item"]
    assert api.put("/api/rates", {"from_currency": "CNY", "to_currency": "SAR", "from_year": 2026, "from_month": 1,
                                  "rate": 0.52}).status_code == 200
    assert api.delete("/api/currencies/CNY").status_code == 422                             # in use
    assert api.delete("/api/currencies/SAR").status_code == 422                             # the base
    api.delete(f"/api/budgets/{b}/incomes/{item['id']}")
    cur = api.delete("/api/currencies/CNY").get_json()
    assert "CNY" not in [c["code"] for c in cur["currencies"]]
    assert all(r["currency"] != "CNY" for r in api.get("/api/rates").get_json()["rates"])  # its rates went too
    # items may only use the user's currencies
    r = api.post(f"/api/budgets/{b}/incomes", {"source": "y", "amount": 1, "currency": "EUR"})
    assert r.status_code == 422


def test_a_new_user_has_only_the_base_currency(tmp_path):
    api = Api(make_app(tmp_path), "bob")
    assert [c["code"] for c in api.get("/api/currencies").get_json()["currencies"]] == ["SAR"]
    assert api.post("/api/rates/test").status_code == 422                                   # nothing to test
