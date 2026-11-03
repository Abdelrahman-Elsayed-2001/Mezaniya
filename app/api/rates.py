"""Exchange rates: the user's own rates per period, and fetching online on demand."""
from __future__ import annotations

from flask import jsonify

from app.db import get_db, transaction
from app.db.repo import currencies, rates, settings
from app.i18n import current_lang
from app.services import fx_online

from . import bp, uid
from .errors import invalid
from .budgets import check_currency
from .validation import Field, FieldError, body, count, currency_code, parse

MAX_RATE = 1_000_000


def rate_value(value, f: Field) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError
    x = float(value)
    if not 0 < x <= MAX_RATE:
        raise FieldError("api.err.rate_range")
    return round(x, 10)


RATE = {
    # "1 from_currency = rate to_currency"; one side must be the base currency, either way round
    "from_currency": Field(currency_code, required=True),
    "to_currency": Field(currency_code, required=True),
    "from_year": Field(count, required=True, max_value=2200),
    "from_month": Field(count, required=True, max_value=12),
    "to_year": Field(count, nullable=True, max_value=2200),
    "to_month": Field(count, nullable=True, max_value=12),
    "rate": Field(rate_value, required=True),
}


def _rates_json(conn, user_id: int) -> dict:
    base_currency = settings.get_settings(conn, user_id)["currency"]
    return {"base": base_currency, "rates": rates.list_rates(conn, user_id, base_currency),
            "currencies": currencies.options(conn, user_id, current_lang())}


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
    """The user's rate for a period: from a month, optionally up to a month (inclusive). Entered
    in whichever direction the user knows ("1 SAR = 13.2 EGP" or "1 EGP = 0.0755 SAR") and kept
    as the other currency's value in the base currency."""
    values = parse(RATE, body(), partial=False)
    conn, user_id = get_db(), uid()
    base_currency = settings.get_settings(conn, user_id)["currency"]
    check_currency(conn, user_id, values["from_currency"], "from_currency")
    check_currency(conn, user_id, values["to_currency"], "to_currency")
    pair = (values["from_currency"], values["to_currency"])
    if pair[0] == pair[1] or base_currency not in pair:
        raise invalid("api.err.rate_needs_base", "to_currency", base=base_currency)
    if pair[1] == base_currency:
        code, rate = pair[0], values["rate"]
    else:
        code, rate = pair[1], round(1 / values["rate"], 10)
    start = _period(values["from_year"], values["from_month"], "from_month")
    end = _period(values.get("to_year"), values.get("to_month"), "to_month")
    if end is not None and end < start:
        raise invalid("api.err.period_order", "to_month")
    with transaction(conn):
        rates.set_manual(conn, user_id, base_currency, code, start, rate, end)
    return jsonify(_rates_json(conn, user_id))


@bp.delete("/rates/<int:rate_id>")
def delete_rate(rate_id: int):
    conn, user_id = get_db(), uid()
    rates.delete_rate(conn, user_id, rate_id)
    return jsonify(_rates_json(conn, user_id))


@bp.post("/rates/fetch")
def fetch_rates():
    """The "fetch now" button: rates for every month that uses another currency (needs the
    fetch_rates setting on). Rates the user entered are kept."""
    conn, user_id = get_db(), uid()
    if not settings.get_settings(conn, user_id)["fetch_rates"]:
        raise invalid("api.err.fetch_rates_off", "fetch_rates")
    written = fx_online.refresh_all(conn, user_id)
    return jsonify({**_rates_json(conn, user_id), "fetched": written})


@bp.post("/rates/test")
def test_rates():
    """The "test the source" button: today's rate of each of the user's currencies, fetched now
    and shown, not saved. Works whether or not automatic fetching is on (the user asked)."""
    conn, user_id = get_db(), uid()
    base_currency = settings.get_settings(conn, user_id)["currency"]
    others = [c for c in currencies.codes(conn, user_id) if c != base_currency]
    if not others:
        raise invalid("api.err.no_other_currency")
    return jsonify({"base": base_currency, "results": [fx_online.probe(base_currency, c) for c in others]})
