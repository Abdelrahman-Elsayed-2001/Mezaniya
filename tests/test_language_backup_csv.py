"""languages.db in backups (restored only when asked), CSV export/import, and a third language
translated from a CSV end to end."""
import csv
import sqlite3

import pytest

import manage
from app import i18n
from app.config import Config
from app.i18n import store
from app.services import backup, xlsx
from app.services import languages as langs
from app.services.admin import AdminError
from tests.api_helpers import Api, make_app


def custom(path, lang, key):
    c = sqlite3.connect(path)
    try:
        row = c.execute("SELECT custom_text FROM language_text WHERE language_id = ? AND key = ?",
                        (lang, key)).fetchone()
        return row[0] if row else None
    finally:
        c.close()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(Config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(Config, "LANGUAGES_DB", None)
    app = make_app(tmp_path)
    Api(app, "alice")
    conn = store.open_db(tmp_path / "languages.db")
    yield tmp_path, conn
    conn.close()
    i18n.use_database(None)


def test_backup_keeps_languages_and_restore_asks(setup, capsys):
    tmp_path, conn = setup
    live = tmp_path / "languages.db"
    langs.save_text(conn, "en", "nav.month", "Before")
    snap = backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, keep=5)
    companion = backup.languages_backup(snap)
    assert companion and companion.name == "languages-" + snap.name[len("mezaniya-"):]
    langs.save_text(conn, "en", "nav.month", "After")
    conn.close()

    assert manage.main(["restore", str(snap), "--yes", "--no-languages"]) == 0      # data only
    assert custom(live, "en", "nav.month") == "After"
    assert "--languages" in capsys.readouterr().out
    assert manage.main(["restore", str(snap), "--yes", "--languages"]) == 0
    assert custom(live, "en", "nav.month") == "Before"
    # every restore first saved the current files, languages included
    safeties = [p for p in backup.list_backups(Config.BACKUP_DIR) if p != snap]
    assert len(safeties) == 2 and all(backup.languages_backup(p) for p in safeties)


def test_restore_without_a_languages_copy(setup):
    tmp_path, conn = setup
    snap = backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, keep=5)
    backup.languages_backup(snap).unlink()
    with pytest.raises(ValueError):
        backup.restore(Config.DB_PATH, snap, Config.BACKUP_DIR, keep=5, with_languages=True)
    assert manage.main(["restore", str(snap), "--yes"]) == 0


def test_prune_removes_the_languages_copy_too(setup):
    from datetime import datetime
    for day in (1, 2, 3):
        backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, keep=2, now=datetime(2026, 11, day))
    left = sorted(p.name for p in Config.BACKUP_DIR.iterdir())
    assert left == ["languages-20261102-000000.db", "languages-20261103-000000.db",
                    "mezaniya-20261102-000000.db", "mezaniya-20261103-000000.db"]


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_third_language_through_csv(setup, monkeypatch):
    tmp_path, conn = setup
    monkeypatch.setattr(i18n, "CHECK_SECONDS", 0)
    langs.add_language(conn, "fr", "Français", "ltr", "fr", "en")
    out = tmp_path / "fr.csv"
    rows = langs.export_texts(conn, out, "fr", "en")
    table = read_csv(out)
    assert table[0] == ["key", "form", "en", "fr"]
    assert len(table) == rows + 1
    months = [r for r in table if r[0] == "count.months"]
    assert [r[1] for r in months] == ["one", "other"] and months[0][2] == "{n} month" and months[0][3] == ""

    # translate a few rows the way Excel would hand them back (semicolons, CRLF, edited cells)
    wanted = {("auth.login.subtitle", ""): "Connectez-vous pour continuer", ("auth.username", ""): "Nom d'utilisateur",
              ("count.months", "one"): "{n} mois", ("count.months", "other"): "{n} mois",
              ("nav.month", ""): "Mois {oops}"}
    for r in table[1:]:
        r[3] = wanted.get((r[0], r[1]), "")
    table.append(["no.such.key", "", "", "x"])
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f, delimiter=";", lineterminator="\r\n").writerows(table)
    result = langs.import_texts(conn, out, "fr")
    assert result["saved"] == 3 and result["unchanged"] == 0
    assert sorted(k for k, _ in result["errors"]) == ["nav.month", "no.such.key"]
    assert all(isinstance(e, AdminError) for _, e in result["errors"])
    again = langs.import_texts(conn, out, "fr")
    assert again["unchanged"] == 3, again              # the same file again

    with pytest.raises(AdminError) as e:
        langs.import_texts(conn, out, "ar")                                    # no "ar" column
    assert e.value.key == "manager.lang_err.csv_column"

    langs.set_enabled(conn, "fr", True)
    client = make_app(tmp_path).test_client()
    client.set_cookie(i18n.COOKIE, "fr")
    page = client.get("/login").get_data(as_text=True)
    assert '<html lang="fr" dir="ltr">' in page and "Connectez-vous pour continuer" in page and "Nom d&#39;utilisateur" in page
    assert "lang-menu" in page and i18n.plural("count.months", 0, "fr") == "0 mois"
    assert '"plural": "fr"' in page


def test_csv_from_plain_excel_csv_on_arabic_windows(setup):
    tmp_path, conn = setup
    out = tmp_path / "ar.csv"
    out.write_bytes("key,ar\r\nnav.month,الشهر المختار\r\n".encode("cp1256"))
    assert langs.import_texts(conn, out, "ar")["saved"] == 1
    i18n.reload()
    assert i18n.t("nav.month", "ar") == "الشهر المختار"


def test_excel_file_round_trip(setup, monkeypatch):
    """The export most people will use: .xlsx with text cells, translated and saved back by Excel
    (shared strings, a cell it took for a formula, an untouched row, plural forms)."""
    import zipfile
    tmp_path, conn = setup
    monkeypatch.setattr(i18n, "CHECK_SECONDS", 0)
    langs.add_language(conn, "fil", "Filipino", "ltr", "en", "en")
    out = tmp_path / "fil.xlsx"
    count = langs.export_texts(conn, out, "fil", "en")
    table = xlsx.read(out)
    assert table[0] == ["key", "form", "en", "fil"] and len(table) == count + 1
    by_key = {(r[0], r[1]): r for r in table}
    assert by_key[("dashboard.add_board", "")][2] == "+ New dashboard"      # kept as text, not a formula
    with zipfile.ZipFile(out) as z:
        assert "<f>" not in z.read("xl/worksheets/sheet1.xml").decode()

    # Excel saves with shared strings; one cell became a formula showing #NAME?
    wanted = {("dashboard.add_board", ""): "+ Bagong dashboard", ("nav.month", ""): "Buwan",
              ("count.months", "one"): "{n} buwan", ("count.months", "other"): "{n} buwan"}
    strings, rows_xml = [], []
    for r, row in enumerate(table, start=1):
        if r > 1:
            row = row[:3] + [wanted.get((row[0], row[1]), "")]
        cells = []
        for c, value in enumerate(row):
            ref = "ABCD"[c] + str(r)
            if r > 1 and c == 3 and row[0] == "expenses.apply_templates":
                cells.append(f'<c r="{ref}" t="e"><f>+ Paulit-ulit</f><v>#NAME?</v></c>')
            elif value:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        rows_xml.append(f'<row r="{r}">{"".join(cells)}</row>')
    from xml.sax.saxutils import escape
    saved = tmp_path / "fil-excel.xlsx"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(saved, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = (f'<worksheet xmlns="{xlsx.MAIN}"><sheetData>{"".join(rows_xml)}</sheetData></worksheet>').encode()
            dst.writestr(name, data)
        dst.writestr("xl/sharedStrings.xml", f'<sst xmlns="{xlsx.MAIN}">' + "".join(
            f"<si><r><t>{escape(s[:3])}</t></r><r><t>{escape(s[3:])}</t></r></si>" for s in strings) + "</sst>")
    result = langs.import_texts(conn, saved, "fil")
    assert result["saved"] == 3 and result["errors"] == []      # the #NAME? cell is just empty
    langs.set_enabled(conn, "fil", True)
    i18n.reload()
    assert i18n.t("dashboard.add_board", "fil") == "+ Bagong dashboard"
    assert i18n.t("expenses.apply_templates", "fil") == "+ Recurring items"      # falls back to English
    assert i18n.plural("count.months", 5, "fil") == "5 buwan"


def test_formula_errors_saved_into_a_csv_are_refused(setup):
    tmp_path, conn = setup
    out = tmp_path / "en.csv"
    out.write_text("key,en\nexpenses.apply_templates,#NAME?\nnav.month,Month!\n", encoding="utf-8-sig")
    result = langs.import_texts(conn, out, "en")
    assert result["saved"] == 1
    assert [(k, e.key) for k, e in result["errors"]] == [("expenses.apply_templates", "manager.lang_err.excel_error")]


def test_not_a_spreadsheet(setup):
    tmp_path, conn = setup
    out = tmp_path / "broken.xlsx"
    out.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(AdminError) as e:
        langs.import_texts(conn, out, "en")
    assert e.value.key == "manager.lang_err.csv_read"


def test_languages_db_format(tmp_path):
    from app.db.connection import IncompatibleDatabase
    path = tmp_path / "languages.db"
    conn = store.open_db(path)
    assert store.generation(conn) == store.GENERATION
    conn.execute(f"PRAGMA user_version = {store.GENERATION + 1}")          # made by a newer version
    conn.close()
    with pytest.raises(IncompatibleDatabase) as e:
        store.open_db(path)
    assert (e.value.generation, e.value.expected) == (store.GENERATION + 1, store.GENERATION)
    try:
        i18n.use_database(path)                                              # the app still runs, on the files
        assert i18n.t("nav.month", "en") == i18n._files()["en"][1]["nav.month"]
    finally:
        i18n.use_database(None)

    unstamped = tmp_path / "old.db"                                          # before the format was stamped
    raw = sqlite3.connect(unstamped)
    raw.executescript(store.SCHEMA)
    raw.close()
    assert store.generation(store.open_db(unstamped)) == store.GENERATION


def test_restore_refuses_languages_from_a_newer_version(setup):
    tmp_path, conn = setup
    snap = backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, keep=5)
    companion = sqlite3.connect(backup.languages_backup(snap))
    companion.execute(f"PRAGMA user_version = {store.GENERATION + 1}")
    companion.close()
    assert manage.main(["restore", str(snap), "--yes", "--languages"]) == 1
    assert manage.main(["restore", str(snap), "--yes", "--no-languages"]) == 0
