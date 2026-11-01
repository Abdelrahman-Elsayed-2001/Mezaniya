"""Seeded types and categories follow the viewer's language until the user renames them."""
import pytest

from app import i18n
from app.db.repo import Conflict, types
from app.engine import DEFAULT_TYPE_KEYS
from app.services.seed_user import DEFAULT_GOAL_CATEGORY_KEYS
from tests.api_helpers import Api, make_app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def names(api, url="/api/expense-types"):
    return [t["name"] for t in api.get(url).get_json()]


def with_lang(api, lang):
    api.client.set_cookie(i18n.COOKIE, lang)
    return api


def test_seeded_names_follow_the_language(app):
    api = Api(app, "alice")                      # registered in Arabic
    assert "أساسي" in names(with_lang(api, "ar"))
    assert "Essential" in names(with_lang(api, "en"))
    assert "Investment" in names(api, "/api/goal-categories")


def test_renamed_type_keeps_its_name_and_renaming_back_follows_the_language_again(app):
    api = with_lang(Api(app, "alice"), "en")
    tid = api.type_id("Essential")
    r = api.patch(f"/api/expense-types/{tid}", {"name": "Bills"}).get_json()
    assert (r["name"], r["custom_name"]) == ("Bills", True)
    assert "Bills" in names(with_lang(api, "ar"))
    r = api.patch(f"/api/expense-types/{tid}", {"name": "أساسي"}).get_json()
    assert (r["name"], r["custom_name"]) == ("أساسي", False)
    assert "Essential" in names(with_lang(api, "en"))


def test_names_must_differ_in_every_language(conn, alice):
    with pytest.raises(Conflict):
        types.create_expense_type(conn, alice, {"name": "essential "})     # the English name of a seeded type
    with pytest.raises(Conflict):
        types.create_goal_category(conn, alice, {"name": "Luxury"})
    mine = types.create_expense_type(conn, alice, {"name": "Pets"})
    with pytest.raises(Conflict):
        types.update_expense_type(conn, alice, mine["id"], {"name": "Savings"})
    luxury = next(t for t in types.list_expense_types(conn, alice) if t["name_key"] == "luxury")
    with pytest.raises(Conflict):
        types.update_expense_type(conn, alice, luxury["id"], {"name": "pets"})


def test_name_key_is_not_writable(conn, alice):
    with pytest.raises(ValueError):
        types.create_expense_type(conn, alice, {"name": "x", "name_key": "savings"})


def test_every_seed_key_has_a_translation():
    for lang in i18n.languages():
        catalog = i18n.catalog(lang)
        assert all(f"seed.type.{k}" in catalog for k, _ in DEFAULT_TYPE_KEYS)
        assert all(f"seed.category.{k}" in catalog for k in DEFAULT_GOAL_CATEGORY_KEYS)
