"""Every user-visible text lives in strings.json, Arabic and English side by side:

    {"section": {"key": {"ar": "...", "en": "..."}}}         → t("section.key")
    {"count": {"months": {"ar": {"one": .., "few": ..}, "en": {...}}}}   → plural by `n`

Placeholders are `{name}`. Python, the Jinja templates and the browser all read the same file.
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

STRINGS_PATH = Path(__file__).with_name("strings.json")
COOKIE = "mezaniya_lang"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_PLURAL_FORMS = {"zero", "one", "two", "few", "many", "other"}

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load() -> tuple[dict, dict[str, dict[str, object]]]:
    raw = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    meta = raw.pop("_meta")
    catalogs: dict[str, dict[str, object]] = {lang: {} for lang in meta["languages"]}

    def walk(node: dict, prefix: str) -> None:
        for name, value in node.items():
            key = f"{prefix}{name}"
            if isinstance(value, dict) and set(value) == set(meta["languages"]):
                for lang in meta["languages"]:
                    catalogs[lang][key] = value[lang]
            elif isinstance(value, dict):
                walk(value, f"{key}.")
            else:
                raise ValueError(f"strings.json: {key} must have one entry per language")

    walk(raw, "")
    return meta, catalogs


def languages() -> list[str]:
    return list(_load()[0]["languages"])


def default_language() -> str:
    env = os.environ.get("MEZANIYA_LANGUAGE", "").strip().lower()
    return env if env in languages() else _load()[0]["default"]


def normalize(lang: str | None) -> str:
    lang = (lang or "").strip().lower()[:2]
    return lang if lang in languages() else default_language()


def direction(lang: str) -> str:
    return _load()[0]["dir"][normalize(lang)]


def language_name(lang: str) -> str:
    return _load()[0]["names"][normalize(lang)]


def catalog(lang: str, prefixes: tuple[str, ...] | None = None) -> dict[str, object]:
    entries = _load()[1][normalize(lang)]
    if prefixes is None:
        return dict(entries)
    return {k: v for k, v in entries.items() if k.startswith(prefixes)}


def has(key: str) -> bool:
    return all(key in c for c in _load()[1].values())


def plural_form(lang: str, n: int) -> str:
    """CLDR plural categories (Arabic has six, English two)."""
    n = abs(int(n))
    if normalize(lang) == "ar":
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
    return "one" if n == 1 else "other"


def fill(template: str, params: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), template)


def t(key: str, lang: str | None = None, **params) -> str:
    lang = normalize(lang)
    value = _load()[1][lang].get(key)
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
            "other_lang": next(code for code in languages() if code != lang),
            "i18n_payload": {"lang": lang, "dir": direction(lang),
                             "strings": catalog(lang, prefixes=WEB_PREFIXES)},
        }


# sections the browser needs (manager / cli / log stay server-side)
WEB_PREFIXES = ("app.", "lang.", "common.", "count.", "auth.", "nav.", "dashboard.", "status.", "summary.",
                "suggestions.", "warnings.", "incomes.", "emergency.", "savings.", "expenses.", "goals.",
                "payments.", "charts.", "details.", "wizard.", "settings.", "role.")
