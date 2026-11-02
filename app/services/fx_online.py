"""Exchange rates fetched online, only when the user turned it on (setting 'fetch_rates').

Source: the free fawazahmed0/exchange-api (daily rates by date, 200+ currencies, no key),
from jsDelivr with Cloudflare Pages as the fallback. Dated files start in March 2024, so older
months need a rate entered by hand. A request carries only a currency code and a date.

Rules (with app/db/repo/rates.py):
- One 'online' row per currency per month, written at that month (from_ym).
- A month that has ended uses its last day's rate, fetched once after it ended; the current
  month uses today's rate, refreshed at most once a day; months after today get nothing.
- Months covered by the user's own ('manual') rate are never touched.
- Failures (offline, timeouts) are remembered for a while so an offline PC doesn't wait on
  every page load; the month then uses the latest earlier rate, shown as an estimate.
"""
from __future__ import annotations

import calendar
import json
import logging
import sqlite3
import time
import urllib.request
from datetime import date

from app.db.repo import base, rates, settings

log = logging.getLogger(__name__)

URLS = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/{cur}.json",
    "https://{day}.currency-api.pages.dev/v1/currencies/{cur}.json",
)
FIRST_DAY = date(2024, 3, 2)          # the first dated file
TIMEOUT = 4                           # seconds per request
RETRY_AFTER = 15 * 60                 # seconds to wait after a failure before trying again
_failed: dict[tuple[str, str, str], float] = {}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mezaniya"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:      # noqa: S310 (fixed https hosts)
        return json.loads(res.read().decode("utf-8"))


def fetch_rate(base_currency: str, currency: str, day: date) -> float | None:
    """1 `currency` in `base_currency` on `day`, or None when it can't be had."""
    key = (base_currency, currency, day.isoformat())
    if time.monotonic() - _failed.get(key, -RETRY_AFTER) < RETRY_AFTER:
        return None
    cur, want = currency.lower(), base_currency.lower()
    for template in URLS:
        url = template.format(day=day.isoformat(), cur=cur)
        try:
            value = _get_json(url)[cur][want]
            if isinstance(value, (int, float)) and value > 0:
                _failed.pop(key, None)
                return float(value)
        except Exception as e:  # noqa: BLE001 (network, HTTP, JSON: all mean "not now")
            log.info("rate fetch failed: %s (%s)", url, e)
    _failed[key] = time.monotonic()
    return None


def _day_for(year: int, month: int, today: date) -> date | None:
    """Which day's rate a month uses: its last day once it has ended, today while it runs."""
    if (year, month) > (today.year, today.month):
        return None
    if (year, month) == (today.year, today.month):
        day = today
    else:
        day = date(year, month, calendar.monthrange(year, month)[1])
    return day if day >= FIRST_DAY else None


def _needs_fetch(conn, user_id: int, base_currency: str, currency: str, year: int, month: int,
                 day: date, today: date) -> bool:
    current = rates.rate_for(conn, user_id, base_currency, currency, year, month)
    if current is not None and current.source == "manual":
        return False                                    # the user's rate wins
    row = conn.execute("SELECT updated_at FROM exchange_rates WHERE user_id = ? AND base = ? AND currency = ? "
                       "AND from_ym = ? AND source = 'online'",
                       (user_id, base_currency, currency, rates.ym(year, month))).fetchone()
    if row is None:
        return True
    fetched_on = date.fromisoformat(row["updated_at"][:10])
    if day == today:
        return fetched_on < today                       # the running month: once a day
    return fetched_on <= day                            # an ended month: once, after it ended


def refresh_month(conn: sqlite3.Connection, user_id: int, budget: dict, today: date | None = None,
                  force: bool = False) -> int:
    """Fetch what this month is missing, if the user turned fetching on. Returns rates written."""
    user_settings = settings.get_settings(conn, user_id)
    if not user_settings.get("fetch_rates"):
        return 0
    today = today or date.today()
    day = _day_for(budget["year"], budget["month"], today)
    if day is None:
        return 0
    base_currency = user_settings["currency"]
    codes = {row[0] for table in ("incomes", "expenses", "goals", "budget_balances") for row in conn.execute(
        f"SELECT DISTINCT currency FROM {table} WHERE user_id = ? AND budget_id = ? AND currency IS NOT NULL",
        (user_id, budget["id"]))} - {base_currency}
    written = 0
    for code in sorted(codes):
        if not _needs_fetch(conn, user_id, base_currency, code, budget["year"], budget["month"],
                                          day, today):
            continue
        value = fetch_rate(base_currency, code, day)
        if value is not None and rates.set_online(conn, user_id, base_currency, code,
                                                  rates.ym(budget["year"], budget["month"]), round(value, 8)):
            written += 1
    return written


def refresh_all(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> int:
    """The "fetch now" button: whatever any month is missing, trying failed requests again."""
    _failed.clear()
    return sum(refresh_month(conn, user_id, b, today) for b in base.list_rows(conn, user_id, "budgets"))
