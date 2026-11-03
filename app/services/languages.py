"""Languages and texts as the server manager edits them (in languages.db, see app.i18n.store).

Every change bumps the revision, so a running server shows it within seconds. Refusals are
admin.AdminError with a manager.lang_err.* key, like the manager's other actions.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from app import i18n
from app.db.connection import transaction
from app.i18n import store
from app.services import xlsx
from app.services.admin import AdminError

CODE = re.compile(r"^[a-z]{2,3}$")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# the forms each plural rule can pick; "zero" may stay empty (Arabic then uses "other")
PLURAL_FORMS = {
    "ar": ("zero", "one", "two", "few", "many", "other"),
    "en": ("one", "other"),
    "fr": ("one", "other"),
    "ru": ("one", "few", "many", "other"),
    "none": ("other",),
}
OPTIONAL_FORMS = {"zero"}


def _decode(plural: int, text: str | None) -> object:
    if text is None:
        return None
    return json.loads(text) if plural else text


def languages(conn: sqlite3.Connection) -> list[dict]:
    """Every language with how many of the program's texts it has (own texts, not borrowed)."""
    total = conn.execute("SELECT COUNT(*) FROM language_text WHERE language_id = ? "
                         "AND COALESCE(custom_text, system_text) IS NOT NULL", (i18n.DEFAULT_LANGUAGE,)).fetchone()[0]
    rows = conn.execute(
        "SELECT i.*, (SELECT COUNT(*) FROM language_text t WHERE t.language_id = i.id "
        "  AND COALESCE(t.custom_text, t.system_text) IS NOT NULL AND t.key IN "
        "  (SELECT key FROM language_text WHERE language_id = ?)) AS translated, "
        "(SELECT COUNT(*) FROM language_text t WHERE t.language_id = i.id AND t.custom_text IS NOT NULL) AS edited "
        "FROM language_info i ORDER BY i.sort_order, i.id", (i18n.DEFAULT_LANGUAGE,)).fetchall()
    return [{"code": r["id"], "name": r["name"], "dir": r["dir"], "plural": r["plural_rule"],
             "reference": r["reference_id"], "system": bool(r["is_system"]), "enabled": bool(r["enabled"]),
             "translated": r["translated"], "edited": r["edited"], "total": total} for r in rows]


def get_language(conn: sqlite3.Connection, code: str) -> dict:
    for lang in languages(conn):
        if lang["code"] == code:
            return lang
    raise AdminError("manager.lang_err.unknown", code=code)


def texts(conn: sqlite3.Connection, code: str) -> dict[str, dict]:
    """The language's own texts by key: value (dict for plural forms), original, edited."""
    out = {}
    for r in conn.execute("SELECT key, plural, system_text, custom_text FROM language_text "
                          "WHERE language_id = ?", (code,)):
        own = r["custom_text"] if r["custom_text"] is not None else r["system_text"]
        out[r["key"]] = {"value": _decode(r["plural"], own), "original": _decode(r["plural"], r["system_text"]),
                         "edited": r["custom_text"] is not None}
    return out


def keys(conn: sqlite3.Connection) -> list[tuple[str, bool]]:
    """Every text the program uses (the default language's keys) and whether it has plural forms."""
    return [(r[0], bool(r[1])) for r in conn.execute(
        "SELECT key, plural FROM language_text WHERE language_id = ? ORDER BY key", (i18n.DEFAULT_LANGUAGE,))]


def rows(conn: sqlite3.Connection, reference: str, target: str, search: str = "",
         untranslated_only: bool = False) -> list[dict]:
    """The table of the texts tab: key, the reference language's text, the target's own text."""
    ref_texts = i18n.catalog(reference) if reference in i18n.language_info() else {}
    own = texts(conn, target)
    needle = search.strip().casefold()
    out = []
    for key, plural in keys(conn):
        mine = own.get(key)
        value = mine["value"] if mine else None
        if untranslated_only and value is not None:
            continue
        row = {"key": key, "plural": plural, "reference": ref_texts.get(key), "value": value,
               "original": mine["original"] if mine else None, "edited": bool(mine and mine["edited"])}
        if needle and needle not in " ".join([key, as_line(row["reference"]), as_line(value)]).casefold():
            continue
        out.append(row)
    return out


def as_line(value: object) -> str:
    """A text on one line for the table (plural forms joined)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " | ".join(f"{form}: {text}" for form, text in value.items())
    return " ".join(str(value).split())


def placeholders(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(PLACEHOLDER.findall(v) for v in value.values())) if value else set()
    return set(PLACEHOLDER.findall(value or ""))


def _default_text(conn: sqlite3.Connection, key: str) -> tuple[bool, object]:
    row = conn.execute("SELECT plural, COALESCE(custom_text, system_text) FROM language_text "
                       "WHERE language_id = ? AND key = ?", (i18n.DEFAULT_LANGUAGE, key)).fetchone()
    if row is None:
        raise AdminError("manager.lang_err.unknown_key", text=key)
    return bool(row[0]), _decode(row[0], row[1])


def save_text(conn: sqlite3.Connection, code: str, key: str, value: object) -> None:
    """Store a translation; empty (or all forms empty) restores the original / leaves it untranslated."""
    lang = get_language(conn, code)
    plural, default = _default_text(conn, key)
    if plural:
        forms = PLURAL_FORMS[lang["plural"]]
        value = {f: str(value.get(f, "")).strip() for f in forms} if isinstance(value, dict) else {}
        value = {f: v for f, v in value.items() if v}
        if value:
            missing = [f for f in forms if f not in OPTIONAL_FORMS and f not in value]
            if missing:
                raise AdminError("manager.lang_err.plural_missing",
                                 forms=", ".join(i18n.t(f"manager.plural_form_{f}", code) for f in missing))
            unknown = placeholders(value) - {"n"}
            if unknown:
                raise AdminError("manager.lang_err.placeholders_unknown", names=_names(unknown))
    else:
        value = str(value or "").strip()
        if value:
            expected, given = placeholders(default), placeholders(value)
            if expected - given:
                raise AdminError("manager.lang_err.placeholders_missing", names=_names(expected - given))
            if given - expected:
                raise AdminError("manager.lang_err.placeholders_unknown", names=_names(given - expected))
    store.set_custom(conn, code, key, value or None)


def _names(names: set[str]) -> str:
    return "  ".join("{" + n + "}" for n in sorted(names))


def _check_info(conn, name: str, direction: str, plural: str, reference: str | None, code: str) -> str:
    name = (name or "").strip()
    if not name:
        raise AdminError("manager.lang_err.name")
    if direction not in ("rtl", "ltr") or plural not in PLURAL_FORMS:
        raise AdminError("manager.lang_err.invalid")
    if reference is not None:
        if reference == code:
            raise AdminError("manager.lang_err.reference_self")
        get_language(conn, reference)
    return name


def add_language(conn: sqlite3.Connection, code: str, name: str, direction: str, plural: str,
                 reference: str | None) -> str:
    """A new language, off until it is enabled; untranslated texts show in `reference`."""
    code = (code or "").strip().lower()
    if not CODE.match(code):
        raise AdminError("manager.lang_err.code")
    if conn.execute("SELECT 1 FROM language_info WHERE id = ?", (code,)).fetchone():
        raise AdminError("manager.lang_err.exists", code=code)
    name = _check_info(conn, name, direction, plural, reference, code)
    with transaction(conn):
        order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM language_info").fetchone()[0]
        conn.execute("INSERT INTO language_info (id, name, dir, plural_rule, reference_id, sort_order, is_system, "
                     "enabled) VALUES (?, ?, ?, ?, ?, ?, 0, 0)", (code, name, direction, plural, reference, order))
        store.bump(conn)
    return code


def update_language(conn: sqlite3.Connection, code: str, name: str, direction: str, plural: str,
                    reference: str | None) -> None:
    """Change a language's details. A language from a file keeps the file's name, direction and rule."""
    lang = get_language(conn, code)
    if lang["system"]:
        name, direction, plural = lang["name"], lang["dir"], lang["plural"]
    name = _check_info(conn, name, direction, plural, reference, code)
    with transaction(conn):
        conn.execute("UPDATE language_info SET name = ?, dir = ?, plural_rule = ?, reference_id = ? WHERE id = ?",
                     (name, direction, plural, reference, code))
        store.bump(conn)


def set_enabled(conn: sqlite3.Connection, code: str, enabled: bool) -> None:
    get_language(conn, code)
    if code == i18n.DEFAULT_LANGUAGE and not enabled:
        raise AdminError("manager.lang_err.default")
    with transaction(conn):
        conn.execute("UPDATE language_info SET enabled = ? WHERE id = ?", (int(enabled), code))
        store.bump(conn)


def delete_language(conn: sqlite3.Connection, code: str) -> None:
    """Remove a language added in the manager with all its texts (file languages stay)."""
    lang = get_language(conn, code)
    if lang["system"]:
        raise AdminError("manager.lang_err.system")
    with transaction(conn):
        conn.execute("UPDATE language_info SET reference_id = NULL WHERE reference_id = ?", (code,))
        conn.execute("DELETE FROM language_text WHERE language_id = ?", (code,))
        conn.execute("DELETE FROM language_info WHERE id = ?", (code,))
        store.bump(conn)


# ---------- export / import (to translate in Excel or another tool, then bring back)

CSV_KEY, CSV_FORM = "key", "form"
EXCEL_ERROR = re.compile(r"^#(NAME\?|VALUE!|REF!|DIV/0!|N/A|NUM!|NULL!|SPILL!|CALC!)$")


def export_rows(conn: sqlite3.Connection, target: str, reference: str | None) -> list[list[str]]:
    """Header key, form, <reference>, <target>, then one row per text and one per plural form."""
    lang = get_language(conn, target)
    ref = get_language(conn, reference) if reference and reference != target else None
    ref_texts = i18n.catalog(ref["code"]) if ref else {}
    own = texts(conn, target)
    rows = [[CSV_KEY, CSV_FORM] + ([ref["code"]] if ref else []) + [lang["code"]]]
    for key, plural in keys(conn):
        value = own[key]["value"] if key in own else None
        reference_value = ref_texts.get(key)
        forms = PLURAL_FORMS[lang["plural"]] if plural else ("",)
        for form in forms:
            def cell(v):
                if plural:
                    return v.get(form, "") if isinstance(v, dict) else ""
                return v or ""
            rows.append([key, form] + ([cell(reference_value)] if ref else []) + [cell(value)])
    return rows


def export_texts(conn: sqlite3.Connection, path: str | Path, target: str, reference: str | None) -> int:
    """.xlsx (every cell is text, so Excel never turns "+ New" into a formula) or, for any other
    extension, CSV in UTF-8 with a BOM so Excel shows Arabic. Returns the number of text rows."""
    rows = export_rows(conn, target, reference)
    if Path(path).suffix.lower() == ".xlsx":
        rtl = get_language(conn, target)["dir"] == "rtl"
        xlsx.write(path, rows, widths=[34, 8] + [60] * (len(rows[0]) - 2), rtl=rtl)
    else:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
    return len(rows) - 1


def _read_rows(path: str | Path) -> list[list[str]]:
    raw = Path(path).read_bytes()
    if raw[:2] == b"PK":                                 # .xlsx is a zip
        try:
            return xlsx.read(path)
        except (zipfile.BadZipFile, KeyError, ValueError, ElementTree.ParseError):
            raise AdminError("manager.lang_err.csv_read") from None
    for encoding in ("utf-8-sig", "cp1256"):           # Excel's "CSV UTF-8", or plain "CSV" on Arabic Windows
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise AdminError("manager.lang_err.csv_read")
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def import_texts(conn: sqlite3.Connection, path: str | Path, target: str) -> dict:
    """Save the target column's texts. Empty cells are skipped (nothing is cleared); each text is
    checked as in the editor. Returns saved / unchanged counts and the refused keys with their
    AdminError (message(lang) words it)."""
    lang = get_language(conn, target)
    rows = _read_rows(path)
    if not rows:
        raise AdminError("manager.lang_err.csv_column", code=target)
    header = [h.strip().lower() for h in rows[0]]
    if CSV_KEY not in header or lang["code"] not in header:
        raise AdminError("manager.lang_err.csv_column", code=lang["code"])
    k, col = header.index(CSV_KEY), header.index(lang["code"])
    f = header.index(CSV_FORM) if CSV_FORM in header else None
    known = dict(keys(conn))
    own = texts(conn, target)
    wanted: dict[str, object] = {}
    errors: list[tuple[str, str]] = []
    for row in rows[1:]:
        if len(row) <= max(k, col):
            continue
        key, value = row[k].strip(), row[col].replace("\r\n", "\n").strip()
        if not key or not value:
            continue
        if key not in known:
            errors.append((key, AdminError("manager.lang_err.unknown_key", text=key)))
            continue
        if EXCEL_ERROR.match(value):                     # a cell Excel took for a formula
            errors.append((key, AdminError("manager.lang_err.excel_error", text=value)))
            continue
        if known[key]:
            form = row[f].strip() if f is not None and len(row) > f else ""
            if form in PLURAL_FORMS[lang["plural"]]:
                wanted.setdefault(key, {})[form] = value
        else:
            wanted[key] = value
    saved = unchanged = 0
    for key, value in wanted.items():
        current = own[key]["value"] if key in own else None
        if known[key] and isinstance(current, dict):
            value = {**current, **value}                 # forms left empty keep what they had
        if value == current:
            unchanged += 1
            continue
        try:
            save_text(conn, target, key, value)
            saved += 1
        except AdminError as e:
            errors.append((key, e))
    return {"saved": saved, "unchanged": unchanged, "errors": errors}
