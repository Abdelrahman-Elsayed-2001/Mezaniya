"""The user's own currency list: GET / add / remove (Settings → Currencies)."""
from __future__ import annotations

from flask import jsonify

from app import currency
from app.db import get_db, transaction
from app.db.repo import currencies, settings
from app.i18n import current_lang, languages

from . import bp, uid
from .errors import invalid
from .validation import Field, body, currency_code, optional_text, parse

NEW_CURRENCY = {
    "code": Field(currency_code, required=True),
    "name": Field(optional_text, nullable=True, max_len=40),      # needed for a code that isn't built in
    "symbol": Field(optional_text, nullable=True, max_len=6),
}


def _json(conn, user_id: int) -> dict:
    lang = current_lang()
    mine = currencies.options(conn, user_id, lang)
    have = {c["code"] for c in mine}
    return {"base": settings.get_settings(conn, user_id)["currency"], "currencies": mine,
            # every ISO currency not in the list yet, with its name in every language to search by
            "builtin": [{**c, "search": " ".join([c["code"]] + [currency.name(c["code"], code)
                                                                   for code in languages()]).lower()}
                        for c in currency.options(lang) if c["code"] not in have],
            "in_use": [c["code"] for c in mine if currencies.used(conn, user_id, c["code"])]}


def _raise(e: currencies.CurrencyError, field: str):
    raise invalid(f"api.err.{e.key}", field, **e.params)


@bp.get("/currencies")
def list_currencies():
    return jsonify(_json(get_db(), uid()))


@bp.post("/currencies")
def add_currency():
    values = parse(NEW_CURRENCY, body(), partial=False)
    conn, user_id = get_db(), uid()
    try:
        with transaction(conn):
            currencies.add(conn, user_id, values["code"], values.get("name"), values.get("symbol"))
    except currencies.CurrencyError as e:
        _raise(e, "name" if e.key == "currency_name_required" else "code")
    return jsonify(_json(conn, user_id)), 201


@bp.delete("/currencies/<code>")
def remove_currency(code: str):
    conn, user_id = get_db(), uid()
    try:
        with transaction(conn):
            currencies.remove(conn, user_id, code.upper(), settings.get_settings(conn, user_id)["currency"])
    except currencies.CurrencyError as e:
        _raise(e, "code")
    return jsonify(_json(conn, user_id))
