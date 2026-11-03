"""Exchange rates per user and month (table exchange_rates, see 0001 for the rules).

A rate converts 1 unit of `currency` into the base currency. It holds from its month until the
next row of the same currency. The user sets rates for a period (from a month, optionally to a
month); those rows are 'manual' and win over anything fetched online.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import base


def ym(year: int, month: int) -> int:
    return year * 100 + month


def next_ym(value: int) -> int:
    year, month = divmod(value, 100)
    return ym(year + 1, 1) if month == 12 else ym(year, month + 1)


@dataclass(frozen=True)
class Rate:
    rate: float
    source: str          # 'manual' | 'online'
    from_ym: int


def list_rates(conn: sqlite3.Connection, user_id: int, base_currency: str, currency: str | None = None) -> list[dict]:
    where = {"base": base_currency, **({"currency": currency} if currency else {})}
    return base.list_rows(conn, user_id, "exchange_rates", **where)


def rate_for(conn: sqlite3.Connection, user_id: int, base_currency: str, currency: str, year: int,
             month: int) -> Rate | None:
    """The rate in force in that month: the latest row starting at or before it."""
    row = conn.execute(
        "SELECT rate, source, from_ym FROM exchange_rates WHERE user_id = ? AND base = ? AND currency = ? "
        "AND from_ym <= ? ORDER BY from_ym DESC LIMIT 1",
        (user_id, base_currency, currency, ym(year, month))).fetchone()
    return Rate(row["rate"], row["source"], row["from_ym"]) if row else None


def _row_at(conn, user_id: int, base_currency: str, currency: str, at: int):
    return conn.execute("SELECT * FROM exchange_rates WHERE user_id = ? AND base = ? AND currency = ? AND from_ym = ?",
                        (user_id, base_currency, currency, at)).fetchone()


def _put(conn, user_id: int, base_currency: str, currency: str, at: int, rate: float, source: str) -> None:
    conn.execute(
        "INSERT INTO exchange_rates (user_id, base, currency, from_ym, rate, source) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id, base, currency, from_ym) DO UPDATE SET rate = excluded.rate, "
        "source = excluded.source, updated_at = CURRENT_TIMESTAMP",
        (user_id, base_currency, currency, at, rate, source))


def set_manual(conn: sqlite3.Connection, user_id: int, base_currency: str, currency: str, from_ym: int,
               rate: float, to_ym: int | None = None) -> None:
    """The user's rate from `from_ym`, up to and including `to_ym` (open-ended without it).
    Rows inside the period are replaced; after a closed period the rate that was in force
    there before is restored, so later months don't change."""
    if currency == base_currency:
        raise ValueError("a currency has no rate against itself")
    if to_ym is not None and to_ym < from_ym:
        raise ValueError("the period ends before it starts")
    after = next_ym(to_ym) if to_ym is not None else None
    restore = None
    if after is not None and _row_at(conn, user_id, base_currency, currency, after) is None:
        y, m = divmod(after, 100)
        restore = rate_for(conn, user_id, base_currency, currency, y, m)
    if to_ym is None:
        conn.execute("DELETE FROM exchange_rates WHERE user_id = ? AND base = ? AND currency = ? AND from_ym > ?",
                     (user_id, base_currency, currency, from_ym))
    else:
        conn.execute("DELETE FROM exchange_rates WHERE user_id = ? AND base = ? AND currency = ? "
                     "AND from_ym > ? AND from_ym <= ?", (user_id, base_currency, currency, from_ym, to_ym))
    _put(conn, user_id, base_currency, currency, from_ym, rate, "manual")
    if restore is not None:
        _put(conn, user_id, base_currency, currency, after, restore.rate, restore.source)


def set_online(conn: sqlite3.Connection, user_id: int, base_currency: str, currency: str, at: int,
               rate: float) -> bool:
    """A fetched rate for one month. Skipped (False) when the month is covered by a manual rate:
    the user's rates always win."""
    y, m = divmod(at, 100)
    current = rate_for(conn, user_id, base_currency, currency, y, m)
    if current is not None and current.source == "manual":
        return False
    _put(conn, user_id, base_currency, currency, at, rate, "online")
    return True


def delete_rate(conn: sqlite3.Connection, user_id: int, rate_id: int) -> None:
    """Remove one row: its months fall back to the row before it."""
    base.delete(conn, user_id, "exchange_rates", rate_id)
