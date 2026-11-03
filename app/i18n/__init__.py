"""Every user-visible text lives in the language files app/i18n/languages/<code>.json, one per
language, found automatically (a new <code>.json is a new language):

    {"_meta": {"name": "English", "dir": "ltr", "plural": "en", "order": 2},
     "section": {"key": "..."},                                    → t("section.key")
     "count": {"months": {"one": "{n} month", "other": "{n} months"}}}   → plural by `n`

Placeholders are `{name}`. Python, the Jinja templates and the browser all read the same files.
A key missing from a language shows in its reference language, else the default language.

Once use_database() is called (the web app and the server manager do), texts are read from
languages.db in the data folder instead: the files are copied into it, and the server manager
edits texts and adds languages there (see store.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import closing
from functools import lru_cache
from pathlib import Path

LANGUAGES_DIR = Path(__file__).with_name("languages")
DEFAULT_LANGUAGE = "ar"
COOKIE = "mezaniya_lang"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_PLURAL_FORMS = {"zero", "one", "two", "few", "many", "other"}
_CODE = re.compile(r"^[a-z]{2,3}$")

log = logging.getLogger(__name__)


def _plural_ar(n: int) -> str:
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    if 3 <= n % 100 <= 10:
        return "few"
    if 11 <= n % 100 <= 99:
        return "many"
    return "other"


def _plural_ru(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


# CLDR plural rules a language file can name in _meta "plural" (whole numbers only)
PLURAL_RULES = {
    "ar": _plural_ar,                                      # zero/one/two/few/many/other
    "en": lambda n: "one" if n == 1 else "other",          # also German, Spanish, Italian, Turkish…
    "fr": lambda n: "one" if n in (0, 1) else "other",     # also Portuguese (Brazil), Hindi…
    "ru": _plural_ru,                                      # also Ukrainian; one/few/many
    "none": lambda n: "other",                             # Japanese, Chinese, Indonesian…
}


def _is_plural(value: object) -> bool:
    return (isinstance(value, dict) and value and set(value) <= _PLURAL_FORMS
            and all(isinstance(v, str) for v in value.values()))


def _flatten(node: dict, prefix: str, out: dict[str, object], source: str) -> None:
    for name, value in node.items():
        key = f"{prefix}{name}"
        if isinstance(value, str) or _is_plural(value):
            out[key] = value
        elif isinstance(value, dict):
            _flatten(value, f"{key}.", out, source)
        else:
            raise ValueError(f"{source}: {key} must be a text, plural forms or a section")


@lru_cache(maxsize=1)
def _files() -> dict[str, tuple[dict, dict[str, object]]]:
    """The language files shipped with the program: code → (_meta, flat texts)."""
    found = {}
    for path in sorted(LANGUAGES_DIR.glob("*.json")):
        code = path.stem.lower()
        if not _CODE.match(code):
            log.warning("ignoring language file %s (name must be a 2-3 letter code)", path.name)
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        meta = raw.pop("_meta")
        if meta.get("dir") not in ("rtl", "ltr") or meta.get("plural") not in PLURAL_RULES:
            raise ValueError(f"{path.name}: _meta needs dir rtl/ltr and plural one of {sorted(PLURAL_RULES)}")
        entries: dict[str, object] = {}
        _flatten(raw, "", entries, path.name)
        found[code] = (meta, entries)
    if DEFAULT_LANGUAGE not in found:
        raise ValueError(f"{LANGUAGES_DIR}: the default language file {DEFAULT_LANGUAGE}.json is missing")
    return found


def _from_files() -> tuple[dict[str, dict], dict[str, dict[str, object]]]:
    rows = sorted(_files().items(), key=lambda item: (item[1][0].get("order", 99), item[0]))
    metas = {code: {"name": meta["name"], "dir": meta["dir"], "plural": meta["plural"],
                    "order": meta.get("order", 99), "reference": None, "system": True, "enabled": True}
             for code, (meta, _) in rows}
    return metas, {code: entries for code, (_, entries) in rows}


# languages.db, once use_database() names it; until then (and if it can't be used) the files
_db_path: Path | None = None
_state: dict = {"loaded": None, "revision": None, "checked": 0.0}
CHECK_SECONDS = 2.0


def use_database(path: str | Path | None) -> None:
    """Read languages from `path` (created and synced with the language files), or the files if None."""
    global _db_path
    _db_path = None
    _state.update(loaded=None, revision=None, checked=0.0)
    if path is None:
        return
    from . import store
    try:
        with closing(store.open_db(path)) as conn:
            store.sync(conn, _files(), DEFAULT_LANGUAGE)
        _db_path = Path(path)
    except Exception:                                  # the files still work without the database
        log.exception("languages database %s unusable, using the language files", path)


def reload() -> None:
    """Forget what was read, so the next text comes from the database again."""
    _state.update(loaded=None, checked=0.0)


def refresh() -> None:
    """Reload if the server manager changed something (checked at most every CHECK_SECONDS)."""
    if _db_path is None or time.monotonic() - _state["checked"] < CHECK_SECONDS:
        return
    from . import store
    try:
        current = store.read_revision(_db_path)
    except Exception:
        log.exception("languages database %s unreadable", _db_path)
        return
    _state["checked"] = time.monotonic()
    if current != _state["revision"]:
        _state["loaded"] = None


def _load() -> tuple[dict[str, dict], dict[str, dict[str, object]]]:
    loaded = _state["loaded"]
    if loaded is not None:
        return loaded
    if _db_path is None:
        loaded = _from_files()
    else:
        from . import store
        try:
            with closing(store.open_db(_db_path)) as conn:
                _state["revision"] = store.revision(conn)
                loaded = store.load(conn)
        except Exception:
            log.exception("languages database %s unreadable, using the language files", _db_path)
            loaded = _from_files()
    _state["loaded"] = loaded
    return loaded


def _chain(lang: str) -> list[str]:
    """Where a language's texts come from: itself, its reference language, the default language."""
    metas = _load()[0]
    chain = [lang]
    reference = metas[lang].get("reference")
    if reference in metas and reference not in chain:
        chain.append(reference)
    if DEFAULT_LANGUAGE not in chain:
        chain.append(DEFAULT_LANGUAGE)
    return chain


def languages() -> list[str]:
    """The languages people can pick (enabled), in order."""
    return [code for code, meta in _load()[0].items() if meta["enabled"]]


def language_info() -> dict[str, dict]:
    """Every language, enabled or not: name, dir, plural, order, reference, system, enabled."""
    return {code: dict(meta) for code, meta in _load()[0].items()}


def default_language() -> str:
    env = os.environ.get("MEZANIYA_LANGUAGE", "").strip().lower()
    return env if env in languages() else DEFAULT_LANGUAGE


def normalize(lang: str | None) -> str:
    lang = (lang or "").strip().lower()
    return lang if lang in languages() else default_language()


def direction(lang: str) -> str:
    return _load()[0][normalize(lang)]["dir"]


def language_name(lang: str) -> str:
    return _load()[0][normalize(lang)]["name"]


def catalog(lang: str, prefixes: tuple[str, ...] | None = None) -> dict[str, object]:
    """The language's texts, with any key it lacks taken from the default language."""
    catalogs = _load()[1]
    entries: dict[str, object] = {}
    for code in reversed(_chain(normalize(lang))):
        entries.update(catalogs[code])
    if prefixes is None:
        return entries
    return {k: v for k, v in entries.items() if k.startswith(prefixes)}


def has(key: str) -> bool:
    """Whether the program's texts define `key` (in the default language)."""
    return key in _load()[1][DEFAULT_LANGUAGE]


def plural_rule(lang: str) -> str:
    """The language's plural rule, a key of PLURAL_RULES (api.js implements the same ones)."""
    return _load()[0][normalize(lang)]["plural"]


def plural_form(lang: str, n: int) -> str:
    """CLDR plural category of `n` under the language's rule."""
    return PLURAL_RULES[plural_rule(lang)](abs(int(n)))


def fill(template: str, params: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), template)


def t(key: str, lang: str | None = None, **params) -> str:
    lang = normalize(lang)
    catalogs = _load()[1]
    value = next((catalogs[code][key] for code in _chain(lang) if key in catalogs[code]), None)
    if value is None:
        log.warning("missing translation %s [%s]", key, lang)
        return key
    if isinstance(value, dict):                     # plural forms, chosen by n
        forms = value
        value = forms.get(plural_form(lang, params.get("n", 0))) or forms["other"]
    return fill(value, params)


def plural(key: str, n: int, lang: str | None = None) -> str:
    return t(key, lang, n=n)


# ---------- Flask

def current_lang() -> str:
    """Language of the current request: the cookie, else the server default."""
    from flask import has_request_context, request
    if not has_request_context():
        return default_language()
    return normalize(request.cookies.get(COOKIE))


def init_app(app) -> None:
    from flask import Blueprint, make_response, redirect, request

    from app.auth.routes import safe_next

    bp = Blueprint("i18n", __name__)
    use_database(Path(app.config.get("LANGUAGES_DB") or Path(app.config["DB_PATH"]).parent / "languages.db"))

    @app.before_request
    def pick_up_edits():
        refresh()

    @bp.get("/lang/<code>")
    def set_language(code: str):
        lang = normalize(code)
        target = request.args.get("next") or "/"
        response = make_response(redirect(safe_next(target)))
        response.set_cookie(COOKIE, lang, max_age=365 * 24 * 3600, samesite="Lax", httponly=False)
        return response

    app.register_blueprint(bp)

    @app.context_processor
    def inject():
        lang = current_lang()
        return {
            "t": lambda key, **params: t(key, lang, **params),
            "lang": lang,
            "dir": direction(lang),
            "lang_name": language_name(lang),
            # the language switch: a button with two languages, a menu with more
            "other_languages": [{"code": code, "name": language_name(code)} for code in languages() if code != lang],
            "i18n_payload": {"lang": lang, "dir": direction(lang), "plural": plural_rule(lang),
                             "strings": catalog(lang, prefixes=WEB_PREFIXES)},
        }


# sections the browser needs (manager / cli / log stay server-side)
WEB_PREFIXES = ("app.", "common.", "count.", "auth.", "nav.", "dashboard.", "status.", "summary.",
                "suggestions.", "warnings.", "incomes.", "emergency.", "savings.", "expenses.", "goals.",
                "payments.", "charts.", "details.", "wizard.", "settings.", "role.", "fx.", "month_notes.")
