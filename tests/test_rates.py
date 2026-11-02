"""Exchange rates per period: manual rates, online rates, and which one wins."""
import pytest

from app.db.repo import rates
from tests.api_helpers import Api, make_app


@pytest.fixture
def api(tmp_path):
    return Api(make_app(tmp_path), "alice")          # base SAR


def table(api):
    return [(r["from_ym"], r["rate"], r["source"]) for r in api.get("/api/rates").get_json()["rates"]
            if r["currency"] == "EGP"]


def put(api, **values):
    return api.put("/api/rates", {"currency": "EGP", **values})


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
    assert api.put("/api/rates", {"currency": "SAR", "from_year": 2026, "from_month": 1, "rate": 1}).status_code == 422


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
