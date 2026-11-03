"""languages.db: filled from the language files, edits survive a new version, a running server
picks up the server manager's changes, and missing texts come from the reference language."""
import json

import pytest

from app import i18n
from app.i18n import store
from tests.api_helpers import make_app

AR = {"_meta": {"name": "Arabic", "dir": "rtl", "plural": "ar", "order": 1},
      "app": {"title": "AR title", "old": "AR old"},
      "count": {"months": {"one": "month", "two": "2 months", "few": "{n} months-few", "many": "{n} months-many",
                           "other": "{n} months"}}}
EN = {"_meta": {"name": "English", "dir": "ltr", "plural": "en", "order": 2},
      "app": {"title": "EN title", "old": "EN old"},
      "count": {"months": {"one": "{n} month", "other": "{n} months"}}}


@pytest.fixture
def files(monkeypatch, tmp_path):
    folder = tmp_path / "languages"
    folder.mkdir()

    def write(**langs):
        for path in folder.glob("*.json"):
            path.unlink()
        for code, data in langs.items():
            (folder / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        i18n._files.cache_clear()

    monkeypatch.setattr(i18n, "LANGUAGES_DIR", folder)
    write(ar=AR, en=EN)
    yield write
    i18n._files.cache_clear()
    i18n.use_database(None)


def test_files_are_copied_once(files, tmp_path):
    path = tmp_path / "languages.db"
    i18n.use_database(path)
    with store.open_db(path) as conn:
        rows = conn.execute("SELECT language_id, key, plural FROM language_text ORDER BY 1, 2").fetchall()
        assert [tuple(r) for r in rows] == [("ar", "app.old", 0), ("ar", "app.title", 0), ("ar", "count.months", 1),
                                            ("en", "app.old", 0), ("en", "app.title", 0), ("en", "count.months", 1)]
        first = store.revision(conn)
        assert store.sync(conn, i18n._files(), "ar") is False           # same files → nothing to do
        assert store.revision(conn) == first
    assert i18n.t("app.title", "en") == "EN title"
    assert i18n.plural("count.months", 3, "ar") == "3 months-few"
    assert i18n.language_info()["ar"]["system"] is True


def test_edits_survive_a_new_version(files, tmp_path):
    path = tmp_path / "languages.db"
    i18n.use_database(path)
    with store.open_db(path) as conn:
        store.set_custom(conn, "en", "app.title", "My title")
        store.set_custom(conn, "en", "count.months", {"one": "{n} mo", "other": "{n} mos"})
    i18n.reload()
    assert i18n.t("app.title", "en") == "My title" and i18n.plural("count.months", 2, "en") == "2 mos"

    # a new version: app.title changed, app.old gone, app.new added
    new_ar = {**AR, "app": {"title": "AR title 2", "new": "AR new"}}
    new_en = {**EN, "app": {"title": "EN title 2", "new": "EN new"}}
    files(ar=new_ar, en=new_en)
    i18n.use_database(path)
    assert i18n.t("app.title", "en") == "My title"                     # the edit wins
    assert i18n.t("app.title", "ar") == "AR title 2"
    assert i18n.t("app.new", "en") == "EN new"
    assert not i18n.has("app.old")
    with store.open_db(path) as conn:
        row = conn.execute("SELECT system_text, custom_text FROM language_text "
                           "WHERE language_id = 'en' AND key = 'app.title'").fetchone()
        assert tuple(row) == ("EN title 2", "My title")
        store.set_custom(conn, "en", "app.title", None)                # restore the original
    i18n.reload()
    assert i18n.t("app.title", "en") == "EN title 2"


def test_added_language_falls_back_to_its_reference(files, tmp_path):
    path = tmp_path / "languages.db"
    i18n.use_database(path)
    with store.open_db(path) as conn:
        conn.execute("INSERT INTO language_info (id, name, dir, plural_rule, reference_id, sort_order) "
                     "VALUES ('fr', 'Français', 'ltr', 'fr', 'en', 3)")
        store.set_custom(conn, "fr", "count.months", {"one": "{n} mois", "other": "{n} mois"})
    i18n.reload()
    assert i18n.languages() == ["ar", "en", "fr"]
    assert i18n.t("app.title", "fr") == "EN title"                     # from English, not Arabic
    assert i18n.plural("count.months", 0, "fr") == "0 mois"
    assert i18n.catalog("fr")["app.title"] == "EN title"
    with store.open_db(path) as conn:
        conn.execute("UPDATE language_info SET enabled = 0 WHERE id = 'fr'")
        store.bump(conn)
    i18n.reload()
    assert i18n.languages() == ["ar", "en"] and i18n.normalize("fr") == "ar"
    assert "fr" in i18n.language_info()


def test_a_removed_language_file_keeps_its_texts(files, tmp_path):
    path = tmp_path / "languages.db"
    i18n.use_database(path)
    files(ar=AR)
    i18n.use_database(path)
    info = i18n.language_info()["en"]
    assert info["system"] is False and i18n.t("app.title", "en") == "EN title"


def test_running_server_picks_up_manager_edits(files, tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "CHECK_SECONDS", 0)
    app = make_app(tmp_path)
    client = app.test_client()
    assert (tmp_path / "languages.db").exists()
    assert i18n.t("app.title", "en") == "EN title"
    with store.open_db(tmp_path / "languages.db") as conn:          # the server manager's edit
        store.set_custom(conn, "en", "app.title", "Edited")
    client.get("/healthz")
    assert i18n.t("app.title", "en") == "Edited"


def test_unusable_database_falls_back_to_the_files(files, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    i18n.use_database(blocker / "languages.db")
    assert i18n.t("app.title", "en") == "EN title"


def test_real_language_files_load_into_the_database(tmp_path):
    try:
        i18n.use_database(tmp_path / "languages.db")
        with store.open_db(tmp_path / "languages.db") as conn:
            db_texts = store.load(conn)[1]
        assert db_texts == {code: entries for code, (_, entries) in i18n._files().items()}
    finally:
        i18n.use_database(None)
