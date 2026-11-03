"""Each user's own currency list (table user_currencies, see 0001).

The base currency is always in the list. Items, templates, balances and rates may only use a
currency from it. Built-in codes (app/currency.CURRENCIES) are named in strings.json; any other
three-letter code can be added with the user's own name and symbol.
"""
from __future__ import annotations

import re
import sqlite3

from app import currency as catalog
from .base import NotFound

CODE = re.compile(r"^[A-Z]{3}$")
# where a currency can be in use; a used currency can't be removed
USED_IN = ("incomes", "expenses", "goals", "recurring_templates", "budget_balances")


class CurrencyError(ValueError):
    """`key` is a strings.json key under api.err."""
    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key, self.params = key, params


def _rows(conn, user_id: int):
    return conn.execute("SELECT uc.code, uc.name, uc.symbol, c.decimals FROM user_currencies uc "
                        "JOIN currencies c ON c.code = uc.code WHERE uc.user_id = ? "
                        "ORDER BY uc.sort_order, uc.code", (user_id,)).fetchall()


def codes(conn: sqlite3.Connection, user_id: int) -> list[str]:
    return [r["code"] for r in _rows(conn, user_id)]


def _info(row, lang: str | None) -> dict:
    return catalog.info(row["code"], lang, own_name=row["name"], own_symbol=row["symbol"], decimals=row["decimals"])


def options(conn: sqlite3.Connection, user_id: int, lang: str | None = None) -> list[dict]:
    """The user's currencies for the page: code, symbol, name, decimals, builtin."""
    return [_info(r, lang) for r in _rows(conn, user_id)]


def info(conn: sqlite3.Connection, user_id: int, code: str, lang: str | None = None) -> dict:
    row = conn.execute("SELECT uc.code, uc.name, uc.symbol, c.decimals FROM user_currencies uc "
                       "JOIN currencies c ON c.code = uc.code WHERE uc.user_id = ? AND uc.code = ?",
                       (user_id, code)).fetchone()
    return _info(row, lang) if row else catalog.info(code, lang)


def add(conn: sqlite3.Connection, user_id: int, code: str, name: str | None = None,
        symbol: str | None = None) -> None:
    code = (code or "").strip().upper()
    if not CODE.match(code):
        raise CurrencyError("currency_code")
    name = (name or "").strip() or None
    symbol = (symbol or "").strip() or None
    if code not in catalog.CURRENCIES and not name:
        raise CurrencyError("currency_name_required")
    if conn.execute("SELECT 1 FROM user_currencies WHERE user_id = ? AND code = ?", (user_id, code)).fetchone():
        raise CurrencyError("currency_exists", code=code)
    if conn.execute("SELECT 1 FROM currencies WHERE code = ?", (code,)).fetchone() is None:
        order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM currencies").fetchone()[0]
        conn.execute("INSERT INTO currencies (code, decimals, sort_order) VALUES (?, ?, ?)",
                     (code, catalog.CURRENCIES.get(code, 2), order))
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM user_currencies WHERE user_id = ?",
                         (user_id,)).fetchone()[0]
    conn.execute("INSERT INTO user_currencies (user_id, code, name, symbol, sort_order) VALUES (?, ?, ?, ?, ?)",
                 (user_id, code, name, symbol, order))


def ensure(conn: sqlite3.Connection, user_id: int, code: str) -> None:
    """Add a built-in currency to the list if it isn't there (the base currency, the demo)."""
    if code not in codes(conn, user_id):
        add(conn, user_id, code)


def used(conn: sqlite3.Connection, user_id: int, code: str) -> bool:
    return any(conn.execute(f"SELECT 1 FROM {table} WHERE user_id = ? AND currency = ? LIMIT 1",
                            (user_id, code)).fetchone() for table in USED_IN)


def remove(conn: sqlite3.Connection, user_id: int, code: str, base_currency: str) -> None:
    """Take a currency off the list. Not the base currency, and not one any item, recurring item
    or balance still uses. Its exchange rates go with it."""
    if code == base_currency:
        raise CurrencyError("currency_is_base")
    if code not in codes(conn, user_id):
        raise NotFound("currency", code)
    if used(conn, user_id, code):
        raise CurrencyError("currency_in_use", code=code)
    conn.execute("DELETE FROM exchange_rates WHERE user_id = ? AND currency = ?", (user_id, code))
    conn.execute("DELETE FROM user_currencies WHERE user_id = ? AND code = ?", (user_id, code))
