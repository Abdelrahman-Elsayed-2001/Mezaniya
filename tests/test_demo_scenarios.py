"""0.3.0 scenarios on the demo user: every number with other currencies must equal the same month
written by hand in the base currency, and the demo must show each 0.3.0 feature."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.db import connect
from app.db.repo import base, budgets, rates
from app.services import demo
from tests.api_helpers import Api, make_app

TODAY = date(2026, 10, 20)
MONTHS = ((2026, 8), (2026, 9), (2026, 10))
EGP = dict(zip(MONTHS, demo.EGP_RATES))
MONEY_COLS = {"incomes": ("amount",), "goals": ("target_amount", "manual_allocation"),
              "expenses": ("cash_amount", "inst_balance", "carried_in", "deferred_amount")}


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def conn(app):
    c = connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture
def api(app, conn):
    demo.create_demo_user(conn, "ar", TODAY)
    a = Api(app)
    a.post("/auth/login", {"username": demo.DEMO_USERNAME, "password": demo.DEMO_PASSWORD})
    return a


def months(api):
    return {(m["year"], m["month"]): api.get(f"/api/budgets/{m['id']}").get_json()
            for m in api.get("/api/budgets").get_json()}


def numbers(d):
    """What the dashboard computes (not the raw rows)."""
    return {"summary": {k: v for k, v in d["summary"].items() if k != "status"}, "status": d["summary"]["status"]["key"],
            "savings": d["savings"], "emergency": d["emergency"], "payments": d["payments"],
            "goals_total": d["goals_total"], "warnings": d["warnings_count"],
            "goals": [(g["id"], g.get("allocated"), g.get("remaining")) for g in d["goals"]]}


def to_base(x, rate):
    return None if x is None else float((Decimal(str(x)) * Decimal(str(rate))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def test_demo_shows_every_030_feature(api):
    ms = months(api)
    assert list(ms) == list(MONTHS)
    for (y, m), d in ms.items():
        assert d["missing_rates"] == [] and d["currency"]["code"] == "SAR"
        assert d["budget"]["notes"]                                                    # monthly notes
        assert {(b["kind"], b["currency"]) for b in d["balances"]} == {("savings", "USD"), ("emergency", "EGP")}
        assert {i["currency"] for i in d["incomes"]} == {None, "USD", "EGP"}
        assert any(e["currency"] == "EGP" for e in d["expenses"])
        assert any(g["currency"] == "USD" for g in d["goals"])
        egp = next(i for i in d["incomes"] if i["currency"] == "EGP")["fx"]
        assert (egp["rate"], egp["source"], egp["estimated"]) == (EGP[(y, m)], "manual", False)
    support = [next(e for e in d["expenses"] if e["currency"] == "EGP") for d in ms.values()]
    assert [(s["status"], s["deferred_amount"], s["carried_in"]) for s in support] == [
        ("paid", 0, 0), ("deferred", 500, 0), ("paid", 0, 500)]                         # carried in EGP
    assert support[1]["fx"]["deferred_amount_base"] == pytest.approx(500 * EGP[MONTHS[1]])   # shown in SAR too
    assert support[2]["fx"]["carried_in_base"] == pytest.approx(500 * EGP[MONTHS[2]])
    assert api.get("/api/settings").get_json()["fetch_rates"] is False                # nothing goes online


def test_every_month_equals_the_same_month_written_in_the_base_currency(api, conn):
    """Differential check of all formulas: convert each foreign row by hand, drop the foreign
    balances into the base balances, and the dashboard must not change."""
    before = {k: numbers(d) for k, d in months(api).items()}
    uid = demo_uid(conn)
    for b in budgets.list_budgets(conn, uid):
        rate = {"USD": demo.USD_RATE, "EGP": EGP[(b["year"], b["month"])]}
        for table, cols in MONEY_COLS.items():
            for row in base.list_rows(conn, uid, table, budget_id=b["id"]):
                if row["currency"]:
                    r = rate[row["currency"]]
                    base.update(conn, uid, table, row["id"], {"currency": None, **{c: to_base(row[c], r) for c in cols}})
        extra = {"savings": 0.0, "emergency": 0.0}
        for bal in budgets.list_balances(conn, uid, b["id"]):
            extra[bal["kind"]] += to_base(bal["amount"], rate[bal["currency"]])
            budgets.set_balance(conn, uid, b["id"], bal["kind"], bal["currency"], 0)
        budgets.update_budget(conn, uid, b["id"], {"savings_current": b["savings_current"] + extra["savings"],
                                                   "emergency_current": b["emergency_current"] + extra["emergency"]})
    after = {k: numbers(d) for k, d in months(api).items()}
    for k in MONTHS:
        assert after[k] == before[k], k


def demo_uid(conn):
    return conn.execute("SELECT id FROM users WHERE username = ?", (demo.DEMO_USERNAME,)).fetchone()[0]


def test_income_total_by_hand(api):
    for (y, m), d in months(api).items():
        bonus = 750 if (y, m) == MONTHS[0] else 0
        assert d["summary"]["income"] == pytest.approx(9000 + bonus + 400 * demo.USD_RATE + 6000 * EGP[(y, m)])


def test_a_new_month_keeps_currencies_and_does_not_double_count_balances(api, conn):
    third = budgets.list_budgets(conn, demo_uid(conn))[-1]
    wizard = api.post("/api/budgets/rollover-preview", {}).get_json()
    assert wizard["mode"] == "copy"
    d = api.post("/api/budgets", {"year": 2026, "month": 11, "copy_from_previous": True}).get_json()
    assert d["budget"]["savings_current"] in (third["savings_current"], third["savings_current"] + 500)
    assert {(b["kind"], b["currency"], b["amount"]) for b in d["balances"]} == {
        ("savings", "USD", 1000), ("emergency", "EGP", 20000)}
    egp = next(i for i in d["incomes"] if i["currency"] == "EGP")["fx"]
    assert egp["rate"] == EGP[MONTHS[-1]] and egp["from_ym"] == 202610                # last rate holds on
    assert d["budget"]["notes"] in (None, "")                                         # notes aren't copied


def test_a_missing_rate_counts_zero_and_says_so(api, conn):
    uid = demo_uid(conn)
    for r in rates.list_rates(conn, uid, "SAR", "USD"):
        rates.delete_rate(conn, uid, r["id"])
    d = months(api)[MONTHS[0]]
    assert d["missing_rates"] == ["USD"]
    usd = next(i for i in d["incomes"] if i["currency"] == "USD")["fx"]
    assert usd["missing"] is True
    assert d["summary"]["income"] == pytest.approx(9000 + 750 + 6000 * EGP[MONTHS[0]])


def test_manual_rate_change_moves_only_its_months(api):
    before = months(api)
    api.put("/api/rates", {"from_currency": "EGP", "to_currency": "SAR", "from_year": 2026, "from_month": 9, "to_year": 2026, "to_month": 9,
                           "rate": 0.1})
    after = months(api)
    assert after[MONTHS[0]]["summary"] == before[MONTHS[0]]["summary"]
    assert after[MONTHS[2]]["summary"] == before[MONTHS[2]]["summary"]
    assert after[MONTHS[1]]["summary"]["income"] == pytest.approx(before[MONTHS[1]]["summary"]["income"]
                                                                 + 6000 * (0.1 - EGP[MONTHS[1]]))


@pytest.mark.parametrize("lang", ["ar", "en"])
def test_demo_pages_render_in_both_languages(app, conn, lang):
    demo.create_demo_user(conn, lang, TODAY)
    a = Api(app)
    a.post("/auth/login", {"username": demo.DEMO_USERNAME, "password": demo.DEMO_PASSWORD})
    a.call("GET", f"/lang/{lang}")
    for path in ("/", "/settings"):
        r = a.get(path)
        assert r.status_code == 200
    d = a.get(f"/api/budgets/{a.get('/api/budgets').get_json()[0]['id']}").get_json()
    assert d["budget"]["notes"] == __import__("app.i18n", fromlist=["t"]).t("demo.month_note_1", lang)
