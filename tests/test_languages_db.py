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


# ---------- the language switch

def _add_french(path, enabled=True):
    with store.open_db(path) as conn:
        conn.execute("INSERT INTO language_info (id, name, dir, plural_rule, reference_id, sort_order, enabled) "
                     "VALUES ('fr', 'Français', 'ltr', 'fr', 'en', 3, ?)", (int(enabled),))
        store.set_custom(conn, "fr", "app.title", "Mon budget")


def test_two_languages_show_one_button(tmp_path):
    try:
        client = make_app(tmp_path).test_client()
        page = client.get("/login").get_data(as_text=True)
        assert 'class="btn btn-small btn-ghost lang-switch"' in page and ">English</a>" in page
        assert "lang-menu" not in page
        assert '"plural": "ar"' in page
        client.set_cookie(i18n.COOKIE, "en")
        english = client.get("/login").get_data(as_text=True)
        assert ">العربية</a>" in english and '"plural": "en"' in english
    finally:
        i18n.use_database(None)


def test_more_languages_show_a_menu(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "CHECK_SECONDS", 0)
    try:
        client = make_app(tmp_path).test_client()
        _add_french(tmp_path / "languages.db")
        client.set_cookie(i18n.COOKIE, "fr")
        page = client.get("/login").get_data(as_text=True)
        assert '<html lang="fr" dir="ltr">' in page
        assert "<details class=\"lang-menu\">" in page and "🌐 Français" in page
        assert 'lang="ar">العربية</a>' in page and 'lang="en">English</a>' in page
        assert "Mon budget" in page and '"plural": "fr"' in page
    finally:
        i18n.use_database(None)


def test_disabled_language_is_not_offered(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "CHECK_SECONDS", 0)
    try:
        client = make_app(tmp_path).test_client()
        _add_french(tmp_path / "languages.db", enabled=False)
        client.set_cookie(i18n.COOKIE, "fr")
        page = client.get("/login").get_data(as_text=True)
        assert "lang-menu" not in page and "Français" not in page and ">English</a>" in page
    finally:
        i18n.use_database(None)


def test_browser_plural_rules_match_the_server():
    import shutil
    import subprocess
    from app.config import BASE_DIR
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = (BASE_DIR / "app" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    rules = source[source.index("const PLURAL_RULES"):source.index("function pluralForm")]
    numbers = list(range(0, 230)) + [1001, 1011, 1021, 1102]
    script = rules + f"console.log(JSON.stringify(Object.fromEntries(Object.entries(PLURAL_RULES).map("\
        f"([k, f]) => [k, {numbers}.map(f)]))));"
    out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert set(out) == set(i18n.PLURAL_RULES)
    for name, rule in i18n.PLURAL_RULES.items():
        assert out[name] == [rule(n) for n in numbers], name
