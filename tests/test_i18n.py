"""strings.json is the only home of display text: both languages complete, every key the code
uses exists, and no Arabic is left in code files."""
import re
from pathlib import Path

import pytest

from app import i18n
from app.api.budgets import ITEM_SPECS, NEW_BUDGET
from app.api.catalog import SETTINGS, TEMPLATE
from app.config import BASE_DIR
from app.engine import DEFAULT_TYPE_KEYS
from app.services.seed_user import DEFAULT_GOAL_CATEGORY_KEYS
from tests.api_helpers import Api, make_app

CODE_FILES = [p for p in (BASE_DIR / "app").rglob("*") if p.suffix in (".py", ".js", ".html") and "vendor" not in p.parts]
CODE_FILES += [BASE_DIR / name for name in ("run.py", "manage.py", "server_manager.py")]
PLACEHOLDER = re.compile(r"\{(\w+)\}")
NAMESPACES = ("app", "lang", "common", "count", "auth", "nav", "dashboard", "status", "summary", "suggestions",
              "warnings", "incomes", "emergency", "savings", "expenses", "goals", "payments", "charts", "details",
              "wizard", "rollover", "field", "api", "issue", "settings", "role", "seed", "demo", "admin",
              "manager", "cli", "log")
KEY_LITERAL = re.compile(r"""["']((?:%s)\.[a-z0-9_.]*[a-z0-9])["']""" % "|".join(NAMESPACES))
# lines that hand a key to something that translates it (not Flask endpoints, file names or JS state)
TRANSLATING_CALL = re.compile(r"\b(t|tr|plural|invalid|error|json_error|AdminError|say|Issue|FieldError|_invalid|_raise)\(")


def literal_keys(path: Path) -> set[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if TRANSLATING_CALL.search(line) and "url_for(" not in line:
            keys.update(KEY_LITERAL.findall(line))
    return keys


def entries(lang):
    return i18n.catalog(lang)


def test_both_languages_have_every_key():
    assert set(entries("ar")) == set(entries("en"))
    assert len(entries("ar")) > 300


@pytest.mark.parametrize("key", sorted(i18n.catalog("ar")))
def test_placeholders_match_between_languages(key):
    ar, en = i18n.catalog("ar")[key], i18n.catalog("en")[key]
    if isinstance(ar, dict):                                  # plural forms
        assert {"one", "two", "few", "many", "other"} <= set(ar) and {"one", "other"} <= set(en)
        ar_ph = set().union(*(PLACEHOLDER.findall(v) for v in ar.values()))
        en_ph = set().union(*(PLACEHOLDER.findall(v) for v in en.values()))
        assert ar_ph <= {"n"} and en_ph <= {"n"}
    else:
        assert set(PLACEHOLDER.findall(ar)) == set(PLACEHOLDER.findall(en)), key
        assert ar.strip() and en.strip(), key


def test_every_literal_key_in_code_exists():
    found = {path.name: literal_keys(path) for path in CODE_FILES}
    assert sum(len(k) for k in found.values()) > 250                  # the scan really sees the keys
    missing = sorted(f"{name}: {key}" for name, keys in found.items() for key in keys if not i18n.has(key))
    assert missing == []


def test_dynamic_key_families_exist():
    keys = [f"status.{k}" for k in ("deficit", "squeezed", "high_debt_luxury", "high_debt", "high_luxury",
                                    "complete_emergency", "excellent", "good", "acceptable")]
    for w in ("deficit", "squeezed", "over_withdrawn", "high_debt", "high_luxury", "emergency_short",
              "low_surplus", "over_allocated", "return_to_savings", "needs_amount"):
        keys += [f"warnings.{w}.title", f"warnings.{w}.details"]
    keys += [f"savings.status.{k}" for k in ("over_withdrawn", "over_allocated", "all_covered", "ok")]
    keys += ["goals.total_all_covered", "goals.total_some_missing", "warnings.summary_none", "warnings.summary_some"]
    keys += [f"charts.progress_{k}" for k in ("emergency", "goals")]
    keys += [f"rollover.err.{k}" for k in ("duplicate", "no_previous", "not_after_latest", "invalid")]
    keys += [f"api.conflict.{k}" for k in ("duplicate", "system_type", "system_role", "in_use", "has_followers",
                                           "last_budget", "integrity")]
    fields = set(NEW_BUDGET) | set(SETTINGS) | set(TEMPLATE) | {f for spec in ITEM_SPECS.values() for f in spec}
    keys += [f"field.{f}" for f in fields]
    keys += [f"role.{r}" for r in ("essential", "luxury", "debt", "savings", "emergency", "other")]
    keys += [f"settings.tab_{k}" for k in ("criteria", "types", "categories", "templates", "account")]
    keys += [f"settings.{k}" for k in ("excellent_rate", "good_rate", "max_debt_rate", "max_luxury_rate",
                                       "emergency_cut_rate", "saving_rate_em_incomplete", "saving_rate_em_complete")]
    keys += [f"settings.templates_kind_{k}" for k in ("income", "expense")]
    keys += [f"wizard.origin_{k}" for k in ("template", "installment", "carry")]
    keys += [f"seed.type.{k}" for k, _ in DEFAULT_TYPE_KEYS] + [f"seed.category.{k}" for k in DEFAULT_GOAL_CATEGORY_KEYS]
    keys += [f"manager.col_{k}" for k in ("username", "state", "last_seen", "last_login", "created", "months")]
    assert [k for k in keys if not i18n.has(k)] == []


def test_no_arabic_left_in_code_files():
    arabic = re.compile(r"[؀-ۿ]")
    offenders = [str(p.relative_to(BASE_DIR)) for p in CODE_FILES if arabic.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_plural_forms():
    assert [i18n.plural("count.months", n, "ar") for n in (1, 2, 3, 10, 11, 100)] == [
        "شهر واحد", "شهران", "3 أشهر", "10 أشهر", "11 شهرًا", "100 شهر"]
    assert [i18n.plural("count.months", n, "en") for n in (1, 2)] == ["1 month", "2 months"]
    assert i18n.t("missing.key") == "missing.key"


# ---------- English end to end

@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def english(app, username=None):
    api = Api(app)
    api.client.set_cookie(i18n.COOKIE, "en")
    if username:
        r = api.post("/auth/register", {"username": username, "password": "secret1", "password_confirm": "secret1"})
        assert r.status_code == 201
    return api


def test_language_switch_sets_cookie_and_direction(app):
    client = app.test_client()
    page = client.get("/login").get_data(as_text=True)
    assert 'dir="rtl"' in page and 'lang="ar"' in page and "English" in page
    r = client.get("/lang/en?next=/login")
    assert r.status_code == 302 and r.headers["Location"] == "/login"
    assert i18n.COOKIE in r.headers["Set-Cookie"]
    page = client.get("/login").get_data(as_text=True)
    assert 'dir="ltr"' in page and "Log in to continue" in page and "العربية" in page
    assert client.get("/lang/en?next=//evil.com").headers["Location"] == "/"


def test_english_user_gets_english_data_and_messages(app):
    api = english(app, "sara")
    types = [t["name"] for t in api.get("/api/expense-types").get_json()]
    assert "Essential" in types and "Debt/installments" in types
    assert api.get("/api/settings").get_json()["currency"] == "SAR"
    d = api.get(f"/api/budgets/{api.budget_id()}").get_json()
    assert d["summary"]["status"]["text"] == "🟠 Acceptable — the surplus is small"
    assert d["warnings_text"] == "⚠️ Review the items below"
    bad = api.post(f"/api/budgets/{api.budget_id()}/expenses", {"name": "x", "type_id": 1, "cash_amount": -1})
    assert bad.get_json()["message"] == "“Cash amount” cannot be negative"
    login = english(app).post("/auth/login", {"username": "sara", "password": "nope"})
    assert login.get_json()["message"] == "Incorrect username or password"


def test_arabic_messages_by_default(app):
    api = Api(app, "huda")
    bad = api.post(f"/api/budgets/{api.budget_id()}/expenses", {"name": "x", "type_id": 1, "cash_amount": -1})
    assert bad.get_json()["message"] == "يجب ألا تكون قيمة «المبلغ النقدي» سالبة"
