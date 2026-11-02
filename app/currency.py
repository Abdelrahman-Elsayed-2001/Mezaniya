"""The currencies a user can pick, by ISO 4217 code.

The base currency (user setting 'currency') is a code from here; its symbol and name in each
language are strings.json "currency.<CODE>.symbol" / ".name". The `currencies` table in 0001
holds the same list (a test keeps them equal) so other tables can reference a code.
"""
from __future__ import annotations

from app.i18n import t

# code → decimal places, in the order shown to the user
CURRENCIES: dict[str, int] = {
    "SAR": 2, "EGP": 2, "AED": 2, "KWD": 3, "QAR": 2, "BHD": 3, "OMR": 3, "JOD": 3,
    "USD": 2, "EUR": 2, "GBP": 2, "TRY": 2, "MAD": 2,
}
DEFAULT = "SAR"


def symbol(code: str, lang: str | None = None) -> str:
    return t(f"currency.{code}.symbol", lang)


def name(code: str, lang: str | None = None) -> str:
    return t(f"currency.{code}.name", lang)


def info(code: str, lang: str | None = None) -> dict:
    return {"code": code, "symbol": symbol(code, lang), "name": name(code, lang), "decimals": CURRENCIES[code]}


def options(lang: str | None = None) -> list[dict]:
    return [info(code, lang) for code in CURRENCIES]
