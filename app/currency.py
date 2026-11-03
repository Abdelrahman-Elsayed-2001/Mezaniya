"""Built-in currencies, by ISO 4217 code, and how a currency is shown.

Built-ins are named in strings.json "currency.<CODE>.symbol" / ".name" and are offered when a
user adds a currency (Settings → Currencies). The `currencies` table in 0001 starts with the same
list (a test keeps them equal); a user may add any other three-letter code with their own name.
Which currencies a user works with is app/db/repo/currencies.py.
"""
from __future__ import annotations

from app.i18n import has, t

# code → decimal places, in the order shown to the user
CURRENCIES: dict[str, int] = {
    "SAR": 2, "EGP": 2, "AED": 2, "KWD": 3, "QAR": 2, "BHD": 3, "OMR": 3, "JOD": 3,
    "USD": 2, "EUR": 2, "GBP": 2, "TRY": 2, "MAD": 2,
}
DEFAULT = "SAR"


def symbol(code: str, lang: str | None = None) -> str:
    return t(f"currency.{code}.symbol", lang) if has(f"currency.{code}.symbol") else code


def name(code: str, lang: str | None = None) -> str:
    return t(f"currency.{code}.name", lang) if has(f"currency.{code}.name") else code


def info(code: str, lang: str | None = None, *, own_name: str | None = None, own_symbol: str | None = None,
         decimals: int | None = None) -> dict:
    """A currency for the page; own_name / own_symbol are a user's own for a code that isn't built in."""
    return {"code": code, "symbol": own_symbol or symbol(code, lang), "name": own_name or name(code, lang),
            "decimals": decimals if decimals is not None else CURRENCIES.get(code, 2),
            "builtin": code in CURRENCIES}


def options(lang: str | None = None) -> list[dict]:
    return [info(code, lang) for code in CURRENCIES]
