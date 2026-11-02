"""Exchange rates: the user's own rates per period (rates fetched online are added later)."""
from __future__ import annotations

from flask import jsonify

from app import currency
from app.db import get_db, transaction
from app.db.repo import rates, settings
from app.i18n import current_lang

from . import bp, uid
from .errors import invalid
from .validation import Field, FieldError, body, choice, count, parse

MAX_RATE = 1_000_000


def rate_value(value, f: Field) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError
    x = float(value)
    if not 0 < x <= MAX_RATE:
        raise FieldError("api.err.rate_range")
    return round(x, 8)


RATE = {
    "currency": Field(choice, required=True, choices=tuple(currency.CURRENCIES)),
    "from_year": Field(count, required=True, max_value=2200),
    "from_month": Field(count, required=True, max_value=12),
    "to_year": Field(count, nullable=True, max_value=2200),
    "to_month": Field(count, nullable=True, max_value=12),
    "rate": Field(rate_value, required=True),
}


def _rates_json(conn, user_id: int) -> dict:
    base_currency = settings.get_settings(conn, user_id)["currency"]
    return {"base": base_currency, "rates": rates.list_rates(conn, user_id, base_currency),
            "currencies": currency.options(current_lang())}


def _period(year: int | None, month: int | None, field: str) -> int | None:
    if year is None and month is None:
        return None
    if year is None or month is None or year < 2000 or month < 1:
        raise invalid("api.err.period", field)
    return rates.ym(year, month)


@bp.get("/rates")
def list_rates():
    return jsonify(_rates_json(get_db(), uid()))


@bp.put("/rates")
def set_rate():
    """The user's rate for a period: from a month, optionally up to a month (inclusive)."""
    values = parse(RATE, body(), partial=False)
    conn, user_id = get_db(), uid()
    base_currency = settings.get_settings(conn, user_id)["currency"]
    if values["currency"] == base_currency:
        raise invalid("api.err.rate_base_currency", "currency")
    start = _period(values["from_year"], values["from_month"], "from_month")
    end = _period(values.get("to_year"), values.get("to_month"), "to_month")
    if end is not None and end < start:
        raise invalid("api.err.period_order", "to_month")
    with transaction(conn):
        rates.set_manual(conn, user_id, base_currency, values["currency"], start, values["rate"], end)
    return jsonify(_rates_json(conn, user_id))


@bp.delete("/rates/<int:rate_id>")
def delete_rate(rate_id: int):
    conn, user_id = get_db(), uid()
    rates.delete_rate(conn, user_id, rate_id)
    return jsonify(_rates_json(conn, user_id))
