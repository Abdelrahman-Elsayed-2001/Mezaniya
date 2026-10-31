"""Overall status (Dashboard!J11) and warnings (I32:K42).

The engine is language-neutral: it returns translation keys ("status.<key>",
"warnings.<key>.title/details") and already formatted numbers as parameters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .fmt import num, pct0, pct1
from .models import Settings


@dataclass(frozen=True)
class Status:
    key: str     # status.<key>
    level: str   # green | yellow | orange | red


@dataclass(frozen=True)
class Warning:
    key: str
    active: bool
    params: dict = field(default_factory=dict)   # formatted values for warnings.<key>.details


@dataclass(frozen=True)
class RuleInputs:
    net: Decimal
    withdrawal: Decimal
    debt_pct: Decimal
    luxury_pct: Decimal
    net_pct: Decimal
    em_remaining: Decimal
    savings_available_before_withdrawal: Decimal   # B56 + B57
    over_requested: Decimal
    return_to_savings: Decimal
    needs_amount_count: int


def overall_status(r: RuleInputs, s: Settings) -> Status:
    if r.net < 0:
        return Status("deficit", "red")
    if r.net - r.withdrawal < 0:
        return Status("squeezed", "orange")
    high_debt = r.debt_pct > s.max_debt_rate
    high_luxury = r.luxury_pct > s.max_luxury_rate
    if high_debt and high_luxury:
        return Status("high_debt_luxury", "orange")
    if high_debt:
        return Status("high_debt", "orange")
    if high_luxury:
        return Status("high_luxury", "orange")
    if r.em_remaining > 0:
        return Status("complete_emergency", "yellow")
    if r.net_pct >= s.excellent_rate:
        return Status("excellent", "green")
    if r.net_pct >= s.good_rate:
        return Status("good", "yellow")
    return Status("acceptable", "orange")


def warnings(r: RuleInputs, s: Settings) -> tuple[Warning, ...]:
    squeezed = r.net >= 0 and r.net - r.withdrawal < 0
    over_withdrawn = r.withdrawal > r.savings_available_before_withdrawal
    high_debt = r.debt_pct > s.max_debt_rate
    high_luxury = r.luxury_pct > s.max_luxury_rate
    low_surplus = r.net >= 0 and r.net_pct < s.good_rate

    def w(key: str, active: bool, **params) -> Warning:
        return Warning(key, active, params if active else {})

    return (
        w("deficit", r.net < 0, amount=num(-r.net)),
        w("squeezed", squeezed, amount=num(r.withdrawal - r.net)),
        w("over_withdrawn", over_withdrawn, withdrawn=num(r.withdrawal),
          available=num(r.savings_available_before_withdrawal)),
        w("high_debt", high_debt, pct=pct1(r.debt_pct), limit=pct0(s.max_debt_rate)),
        w("high_luxury", high_luxury, pct=pct1(r.luxury_pct), limit=pct0(s.max_luxury_rate)),
        w("emergency_short", r.em_remaining > 0, amount=num(r.em_remaining)),
        w("low_surplus", low_surplus, pct=pct1(r.net_pct), min=pct0(s.good_rate)),
        w("over_allocated", r.over_requested > 0, amount=num(r.over_requested)),
        w("return_to_savings", r.return_to_savings > 0, amount=num(r.return_to_savings)),
        w("needs_amount", r.needs_amount_count > 0, n=r.needs_amount_count),
    )


def warnings_summary(items: tuple[Warning, ...]) -> tuple[int, str]:
    """(active count, warnings.<key> of the summary line)."""
    count = sum(1 for w in items if w.active)
    return count, ("summary_none" if count == 0 else "summary_some")
