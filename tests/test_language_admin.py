"""What the server manager's languages tab does to languages.db."""
import pytest

from app import i18n
from app.i18n import store
from app.services import languages as langs
from app.services.admin import AdminError


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "languages.db"
    i18n.use_database(path)
    db = store.open_db(path)
    yield db
    db.close()
    i18n.use_database(None)


def err(fn, *args):
    with pytest.raises(AdminError) as e:
        fn(*args)
    return e.value.key


def test_list_counts_translated_texts(conn):
    rows = {lang["code"]: lang for lang in langs.languages(conn)}
    assert set(rows) == {"ar", "en"}
    assert rows["ar"]["system"] and rows["ar"]["enabled"]
    assert rows["en"]["translated"] == rows["en"]["total"] == rows["ar"]["total"] > 900


def test_add_translate_enable_delete(conn):
    assert err(langs.add_language, conn, "French", "Français", "ltr", "fr", "en") == "manager.lang_err.code"
    assert err(langs.add_language, conn, "en", "X", "ltr", "en", None) == "manager.lang_err.exists"
    assert err(langs.add_language, conn, "fr", " ", "ltr", "fr", "en") == "manager.lang_err.name"
    assert err(langs.add_language, conn, "fr", "Français", "ltr", "fr", "fr") == "manager.lang_err.reference_self"
    langs.add_language(conn, "FR", "Français", "ltr", "fr", "en")
    i18n.reload()
    assert "fr" not in i18n.languages() and "fr" in i18n.language_info()      # off until enabled

    fr = langs.get_language(conn, "fr")
    assert fr["translated"] == 0 and not fr["system"]
    table = langs.rows(conn, "en", "fr", untranslated_only=True)
    assert len(table) == fr["total"] and table[0]["value"] is None

    langs.save_text(conn, "fr", "app.title", "Mon budget mensuel")
    langs.save_text(conn, "fr", "count.months", {"one": "{n} mois", "other": "{n} mois"})
    langs.set_enabled(conn, "fr", True)
    i18n.reload()
    assert i18n.languages() == ["ar", "en", "fr"]
    assert i18n.t("app.title", "fr") == "Mon budget mensuel"
    assert i18n.t("nav.new_month", "fr") == i18n.t("nav.new_month", "en")   # untranslated → reference
    assert i18n.plural("count.months", 0, "fr") == "0 mois"
    assert langs.get_language(conn, "fr")["translated"] == 2
    assert [r["key"] for r in langs.rows(conn, "en", "fr", search="mensuel")] == ["app.title"]

    langs.delete_language(conn, "fr")
    i18n.reload()
    assert "fr" not in i18n.language_info()
    assert conn.execute("SELECT COUNT(*) FROM language_text WHERE language_id = 'fr'").fetchone()[0] == 0


def test_placeholders_and_plural_forms_are_checked(conn):
    key = next(k for k, p in langs.keys(conn) if not p and "{name}" in i18n.t(k, "en", name="{name}"))
    default = i18n.catalog("ar")[key]
    expected = langs.placeholders(default)
    assert err(langs.save_text, conn, "en", key, "no placeholders") == "manager.lang_err.placeholders_missing"
    assert err(langs.save_text, conn, "en", key, " ".join("{%s}" % n for n in expected) + " {oops}") \
        == "manager.lang_err.placeholders_unknown"
    assert err(langs.save_text, conn, "ar", "count.months", {"one": "شهر", "other": "{n} شهر"}) \
        == "manager.lang_err.plural_missing"                                   # two/few/many missing
    assert err(langs.save_text, conn, "en", "count.months", {"one": "{n} {x}", "other": "{n}"}) \
        == "manager.lang_err.placeholders_unknown"
    assert err(langs.save_text, conn, "en", "no.such.key", "x") == "manager.lang_err.unknown_key"


def test_edit_and_restore_a_file_language(conn):
    original = i18n.t("nav.month", "en")
    langs.save_text(conn, "en", "nav.month", "Mois")
    i18n.reload()
    assert i18n.t("nav.month", "en") == "Mois"
    row = next(r for r in langs.rows(conn, "ar", "en") if r["key"] == "nav.month")
    assert row["edited"] and row["original"] == original and row["value"] == "Mois"
    assert langs.get_language(conn, "en")["edited"] == 1
    langs.save_text(conn, "en", "nav.month", "")                          # restore
    i18n.reload()
    assert i18n.t("nav.month", "en") == original


def test_file_languages_are_protected(conn):
    assert err(langs.delete_language, conn, "en") == "manager.lang_err.system"
    assert err(langs.set_enabled, conn, "ar", False) == "manager.lang_err.default"
    langs.update_language(conn, "en", "Anglais", "rtl", "none", "ar")          # only the reference changes
    en = langs.get_language(conn, "en")
    assert (en["name"], en["dir"], en["plural"], en["reference"]) == ("English", "ltr", "en", "ar")
    langs.set_enabled(conn, "en", False)
    i18n.reload()
    assert i18n.languages() == ["ar"]
