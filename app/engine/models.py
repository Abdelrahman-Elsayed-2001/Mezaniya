"""Input models for the budget engine.

All money and rate fields are normalised to Decimal on construction, so callers
may pass int / str / Decimal (floats are converted through str to avoid binary noise).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)


def dec(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def dec_or_none(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return dec(value)


class Role(StrEnum):
    ESSENTIAL = "essential"
    LUXURY = "luxury"
    DEBT = "debt"
    SAVINGS = "savings"
    EMERGENCY = "emergency"
    OTHER = "other"


class PaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"
    DEFERRED = "deferred"


class _Normalised:
    """Converts annotated Decimal fields after __init__ (works with frozen dataclasses)."""

    _decimal_fields: tuple[str, ...] = ()
    _optional_decimal_fields: tuple[str, ...] = ()

    def __post_init__(self):
        for name in self._decimal_fields:
            object.__setattr__(self, name, dec(getattr(self, name)))
        for name in self._optional_decimal_fields:
            object.__setattr__(self, name, dec_or_none(getattr(self, name)))


@dataclass(frozen=True)
class Settings(_Normalised):
    excellent_rate: Decimal = Decimal("0.20")             # Backend!D4
    good_rate: Decimal = Decimal("0.10")                  # D5
    max_debt_rate: Decimal = Decimal("0.33")              # D6
    max_luxury_rate: Decimal = Decimal("0.30")            # D7
    emergency_cut_rate: Decimal = Decimal("0.30")         # D8
    saving_rate_em_incomplete: Decimal = Decimal("0.20")  # D11
    saving_rate_em_complete: Decimal = Decimal("0.50")    # D12
    emergency_ref_months: Decimal = Decimal(3)            # J26
    emergency_ref_factor: Decimal = Decimal("1.25")       # J26

    _decimal_fields = (
        "excellent_rate", "good_rate", "max_debt_rate", "max_luxury_rate",
        "emergency_cut_rate", "saving_rate_em_incomplete", "saving_rate_em_complete",
        "emergency_ref_months", "emergency_ref_factor",
    )


@dataclass(frozen=True)
class ExpenseType:
    name: str
    role: Role = Role.OTHER


# The types every user starts with, in display order. Names are translations ("seed.type.<key>").
DEFAULT_TYPE_KEYS: tuple[tuple[str, Role], ...] = (
    ("emergency", Role.EMERGENCY),
    ("savings", Role.SAVINGS),
    ("luxury", Role.LUXURY),
    ("debt", Role.DEBT),
    ("essential", Role.ESSENTIAL),
    ("giving", Role.OTHER),
    ("transfer", Role.OTHER),
    ("investment", Role.OTHER),
)


@dataclass(frozen=True)
class Income(_Normalised):
    source: str
    amount: Decimal = ZERO
    is_savings_withdrawal: bool = False
    needs_amount: bool = False
    id: int | None = None

    _decimal_fields = ("amount",)


@dataclass(frozen=True)
class Expense(_Normalised):
    name: str
    type_name: str
    cash: Decimal = ZERO
    inst_balance: Decimal = ZERO      # remaining installment balance (the sheet's "installment total")
    inst_count: int = 0               # remaining number of payments
    carried_in: Decimal = ZERO        # carried over from the previous month
    status: PaymentStatus = PaymentStatus.UNPAID
    deferred_amount: Decimal = ZERO   # only meaningful when status == DEFERRED
    paid_at: date | None = None
    needs_amount: bool = False        # recurring item with a variable price not entered yet
    id: int | None = None

    _decimal_fields = ("cash", "inst_balance", "carried_in", "deferred_amount")

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "inst_count", int(self.inst_count or 0))
        object.__setattr__(self, "status", PaymentStatus(self.status))


@dataclass(frozen=True)
class Goal(_Normalised):
    name: str
    target: Decimal | None = None          # target amount (None = empty → the goal is ignored)
    priority: Decimal | None = None        # priority as a fraction (0.5 = 50%); None = automatic
    manual_amount: Decimal | None = None   # manual amount; becomes a share of the available savings
    category: str | None = None
    id: int | None = None

    _optional_decimal_fields = ("target", "priority", "manual_amount")


@dataclass(frozen=True)
class BudgetInput(_Normalised):
    year: int
    month: int
    settings: Settings = field(default_factory=Settings)
    types: tuple[ExpenseType, ...] = ()
    incomes: tuple[Income, ...] = ()
    expenses: tuple[Expense, ...] = ()
    goals: tuple[Goal, ...] = ()
    emergency_current: Decimal = ZERO   # Dashboard!J22
    emergency_min: Decimal = ZERO       # J25
    savings_current: Decimal = ZERO     # B56

    _decimal_fields = ("emergency_current", "emergency_min", "savings_current")

    def __post_init__(self):
        super().__post_init__()
        for name in ("types", "incomes", "expenses", "goals"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not 1 <= self.month <= 12:
            raise ValueError(f"month must be 1..12, got {self.month}")


__all__ = [
    "ZERO", "dec", "dec_or_none", "Role", "PaymentStatus", "Settings", "ExpenseType",
    "DEFAULT_TYPE_KEYS", "Income", "Expense", "Goal", "BudgetInput",
]
