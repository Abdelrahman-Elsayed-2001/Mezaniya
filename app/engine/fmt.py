"""Number formatting that mirrors the Excel TEXT() formats used in the sheet."""
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .models import ZERO


def safe_div(a: Decimal, b: Decimal) -> Decimal:
    """a / b, or 0 when b is 0 (Excel IFERROR(a/b, 0))."""
    return a / b if b else ZERO


def ceil_int(x: Decimal) -> int:
    """Excel ROUNDUP(x, 0) for non-negative x."""
    return int(x.to_integral_value(rounding=ROUND_CEILING))


def num(x: Decimal) -> str:
    """TEXT(x, "#,##0"): half away from zero, thousands separator."""
    return f"{int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP)):,}"


def pct1(x: Decimal) -> str:
    """TEXT(x, "0.0%")."""
    return f"{(x * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def pct0(x: Decimal) -> str:
    """TEXT(x, "0%")."""
    return f"{(x * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"
