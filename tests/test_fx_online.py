"""Rates fetched online: off until the user turns it on, manual rates win, failures don't stall pages."""
from datetime import date

import pytest

from app.services import fx_online
from tests.api_helpers import Api, make_app


@pytest.fixture
def calls(monkeypatch):
    """Stands in for the network: records the URLs asked for and answers 1 EGP = 0.075 SAR."""
    log = {"urls": [], "fail": False}

    def fake(url):
        log["urls"].append(url)
        if log["fail"]:
            raise OSError("offline")
        cur = url.rsplit("/", 1)[1].removesuffix(".json")
        return {"date": "x", cur: {"sar": 0.075, "usd": 0.02}}
    monkeypatch.setattr(fx_online, "_get_json", fake)
    fx_online._failed.clear()
    return log


@pytest.fixture
def api(tmp_path):
    return Api(make_app(tmp_path), "alice", currencies=("EGP", "USD"))          # base SAR


def month(api, year, m):
    b = api.post("/api/budgets", {"year": year, "month": m}).get_json()["budget"]["id"]
    api.post(f"/api/budgets/{b}/incomes", {"source": "إيجار", "amount": 1000, "currency": "EGP"})
    return b


def egp_rows(api):
    return [(r["from_ym"], r["rate"], r["source"]) for r in api.get("/api/rates").get_json()["rates"]]


def test_off_by_default_sends_nothing(api, calls):
    assert api.get("/api/settings").get_json()["fetch_rates"] is False
    b = month(api, 2025, 1)
    d = api.get(f"/api/budgets/{b}").get_json()
    assert calls["urls"] == [] and d["missing_rates"] == ["EGP"]
    assert api.post("/api/rates/fetch").status_code == 422


def test_ended_month_uses_its_last_day_once(api, calls):
    b = month(api, 2025, 1)
    api.patch("/api/settings", {"fetch_rates": True})
    d = api.get(f"/api/budgets/{b}").get_json()
    assert calls["urls"] == [fx_online.URLS[0].format(day="2025-01-31", cur="egp")]
    assert d["missing_rates"] == [] and egp_rows(api) == [(202501, 0.075, "online")]
    assert d["incomes"][0]["fx"]["amount_base"] == 75
    api.get(f"/api/budgets/{b}")
    assert len(calls["urls"]) == 1                                  # fetched after the month ended: done


def test_months_without_data_are_skipped(api, calls):
    api.patch("/api/settings", {"fetch_rates": True})
    today = date.today()
    future = (today.year + 1, today.month)
    for y, m in ((2024, 1), future):                                # before the first file / not yet
        api.get(f"/api/budgets/{month(api, y, m)}")
    assert calls["urls"] == []


def test_current_month_uses_today(api, calls):
    api.patch("/api/settings", {"fetch_rates": True})
    today = date.today()
    b = api.budget_id()                                             # the month the user starts in: now
    api.post(f"/api/budgets/{b}/incomes", {"source": "إيجار", "amount": 1000, "currency": "EGP"})
    api.get(f"/api/budgets/{b}")
    api.get(f"/api/budgets/{b}")
    assert calls["urls"] == [fx_online.URLS[0].format(day=today.isoformat(), cur="egp")]   # once a day


def test_manual_rate_wins(api, calls):
    b = month(api, 2025, 1)
    api.put("/api/rates", {"from_currency": "EGP", "to_currency": "SAR", "from_year": 2024, "from_month": 6, "rate": 0.08})
    api.patch("/api/settings", {"fetch_rates": True})
    d = api.get(f"/api/budgets/{b}").get_json()
    assert calls["urls"] == [] and d["incomes"][0]["fx"]["rate"] == 0.08


def test_failure_falls_back_and_is_not_retried_on_every_page(api, calls):
    api.patch("/api/settings", {"fetch_rates": True})
    b0 = month(api, 2024, 12)
    api.get(f"/api/budgets/{b0}")                                    # an online rate for Dec 2024
    calls["urls"].clear()
    calls["fail"] = True
    b = month(api, 2025, 1)
    d = api.get(f"/api/budgets/{b}").get_json()
    assert len(calls["urls"]) == 2                                   # both sources tried
    fx = d["incomes"][0]["fx"]
    assert fx["from_ym"] == 202412 and fx["estimated"] is True     # last month's rate, marked as an estimate
    api.get(f"/api/budgets/{b}")
    assert len(calls["urls"]) == 2                                   # remembered: no waiting again
    calls["fail"] = False
    r = api.post("/api/rates/fetch").get_json()                      # "fetch now" tries again
    assert r["fetched"] == 1 and (202501, 0.075, "online") in egp_rows(api)


def test_current_month_falls_back_to_yesterday_when_todays_file_is_not_out(api, calls, monkeypatch):
    today = date.today()
    real = fx_online._get_json

    def no_today(url):
        if today.isoformat() in url:
            calls["urls"].append(url)
            raise OSError("404")
        return real(url)
    monkeypatch.setattr(fx_online, "_get_json", no_today)
    api.patch("/api/settings", {"fetch_rates": True})
    b = api.budget_id()
    d = api.post(f"/api/budgets/{b}/incomes", {"source": "x", "amount": 1000, "currency": "EGP"}).get_json()["dashboard"]
    assert d["missing_rates"] == [] and d["incomes"][0]["fx"]["rate"] == 0.075


def test_the_test_button_fetches_without_saving(api, calls):
    r = api.post("/api/rates/test").get_json()                       # works even with fetching off
    assert r["base"] == "SAR" and [x["currency"] for x in r["results"]] == ["EGP", "USD"]
    egp = r["results"][0]
    assert egp["ok"] and egp["rate"] == 0.075 and egp["inverse"] == pytest.approx(1 / 0.075)
    assert api.get("/api/rates").get_json()["rates"] == []          # nothing saved
    calls["fail"] = True
    assert api.post("/api/rates/test").get_json()["results"][0] == {"currency": "EGP", "ok": False}
