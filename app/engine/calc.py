"""compute(BudgetInput) -> Dashboard: every derived number of the workbook.

Cell references in comments point at the original mezaniya.xlsx.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .fmt import ceil_int, safe_div
from .goals import GoalsResult, allocate_goals
from .models import ZERO, BudgetInput, Expense, PaymentStatus, Role
from .rules import RuleInputs, Status, Warning, overall_status, warnings, warnings_summary


@dataclass(frozen=True)
class ExpenseLine:
    expense: Expense
    role: Role
    base: Decimal         # F: cash + installment share
    due: Decimal          # base + carried_in
    deferred: Decimal
    effective: Decimal    # counted in this month's expenses
    paid_part: Decimal


@dataclass(frozen=True)
class Summary:
    income: Decimal       # J5
    withdrawal: Decimal   # B58
    expenses: Decimal     # J6
    debt: Decimal         # J7
    luxury: Decimal       # J8
    essential: Decimal    # J9
    net: Decimal          # J10
    expenses_pct: Decimal
    debt_pct: Decimal
    luxury_pct: Decimal
    essential_pct: Decimal
    net_pct: Decimal
    status: Status        # J11


@dataclass(frozen=True)
class Suggestions:
    emergency: Decimal          # J15 / Backend D16
    saving: Decimal             # J16 / D17
    safe_spend: Decimal         # J17 / D18
    safe_daily: Decimal         # J18
    safe_weekly: Decimal        # J19
    return_to_savings: Decimal  # J20 / D22
    emergency_pct: Decimal      # K15 (of income)
    saving_pct: Decimal
    safe_spend_pct: Decimal
    return_to_savings_pct: Decimal
    days_in_month: int


@dataclass(frozen=True)
class Emergency:
    current: Decimal            # J22
    from_expenses: Decimal      # J23
    after: Decimal              # J24
    minimum: Decimal            # J25
    reference: Decimal          # J26
    remaining: Decimal          # J27
    complete: bool              # J28
    months: int | None          # J29 (None → "-")


@dataclass(frozen=True)
class Savings:
    current: Decimal            # B56
    from_expenses: Decimal      # B57
    withdrawal: Decimal         # B58
    total: Decimal              # B59
    allocated: Decimal          # B60
    unallocated: Decimal        # B61
    status_key: str             # B62 → savings.status.<key>
    status_ok: bool


@dataclass(frozen=True)
class Payments:
    cash_total: Decimal         # C50
    installment_total: Decimal  # D50
    carried_in_total: Decimal
    paid: Decimal               # F51
    deferred: Decimal
    unpaid: Decimal             # F52
    cash_now: Decimal           # F53
    paid_count: int             # G50
    deferred_count: int


@dataclass(frozen=True)
class Debug:
    """Backend!C27:E34 (how the suggestions are calculated)."""
    available: Decimal          # D15
    base: Decimal               # D28
    emergency_gap_before: Decimal   # D29
    emergency_target: Decimal   # D30
    saving_rate: Decimal        # D32
    saving_target: Decimal      # D33
    goals_pool: Decimal         # D51
    goals_monthly_pool: Decimal # D50
    goals_rounds: int


@dataclass(frozen=True)
class ChartSlice:
    label: str          # expense type name ("" for the surplus slice)
    amount: Decimal
    pct: Decimal
    key: str = "type"   # "type" | "surplus"


@dataclass(frozen=True)
class ProgressBar:
    key: str            # "emergency" | "goals"
    available: Decimal
    remaining: Decimal


@dataclass(frozen=True)
class DailySpend:
    by_day: tuple[Decimal, ...]   # index 0 → day 1
    no_date: Decimal
    outside_month: Decimal


@dataclass(frozen=True)
class Charts:
    by_type: tuple[ChartSlice, ...]
    progress: tuple[ProgressBar, ...]
    daily: DailySpend


@dataclass(frozen=True)
class Dashboard:
    expenses: tuple[ExpenseLine, ...]
    summary: Summary
    suggestions: Suggestions
    emergency: Emergency
    savings: Savings
    goals: GoalsResult
    payments: Payments
    warnings: tuple[Warning, ...]
    warnings_count: int
    warnings_key: str           # warnings.<key> of the summary line
    charts: Charts
    debug: Debug


def expense_line(e: Expense, role: Role) -> ExpenseLine:
    installment = e.inst_balance / e.inst_count if e.inst_count > 0 else e.inst_balance
    base = e.cash + installment
    due = base + e.carried_in
    if e.status == PaymentStatus.DEFERRED:
        deferred = min(max(ZERO, e.deferred_amount), due)
        paid_part = due - deferred
    else:
        deferred = ZERO
        paid_part = due if e.status == PaymentStatus.PAID else ZERO
    return ExpenseLine(e, role, base, due, deferred, due - deferred, paid_part)


def compute(inp: BudgetInput) -> Dashboard:
    s = inp.settings
    roles = {t.name: t.role for t in inp.types}
    lines = tuple(expense_line(e, roles.get(e.type_name, Role.OTHER)) for e in inp.expenses)

    def by_role(role: Role) -> Decimal:
        return sum((l.effective for l in lines if l.role == role), ZERO)

    # ---- summary (I4:K11)
    income = sum((i.amount for i in inp.incomes), ZERO)
    withdrawal = sum((i.amount for i in inp.incomes if i.is_savings_withdrawal), ZERO)
    expenses = sum((l.effective for l in lines), ZERO)
    debt, luxury, essential = by_role(Role.DEBT), by_role(Role.LUXURY), by_role(Role.ESSENTIAL)
    net = income - expenses

    def pct(x: Decimal) -> Decimal:
        return safe_div(x, income)

    # ---- emergency inputs (J22:J27)
    em_from_expenses = by_role(Role.EMERGENCY)
    em_after = inp.emergency_current + em_from_expenses
    em_remaining = max(ZERO, inp.emergency_min - em_after)

    # ---- monthly distribution of the net (Backend C15:D34)
    sav_from_expenses = by_role(Role.SAVINGS)
    available = max(ZERO, net - withdrawal)
    base = max(ZERO, net - withdrawal + em_from_expenses + sav_from_expenses)
    em_gap_before = max(ZERO, inp.emergency_min - inp.emergency_current)
    em_target = min(em_gap_before, base * s.emergency_cut_rate)
    suggest_emergency = min(max(ZERO, em_target - em_from_expenses), available)
    saving_rate = s.saving_rate_em_complete if em_gap_before == 0 else s.saving_rate_em_incomplete
    saving_target = base * saving_rate
    suggest_saving = min(max(ZERO, saving_target - sav_from_expenses), available - suggest_emergency)
    safe_spend = available - suggest_emergency - suggest_saving
    days = calendar.monthrange(inp.year, inp.month)[1]
    return_to_savings = min(withdrawal, max(ZERO, net))

    em_denominator = suggest_emergency + em_from_expenses
    if em_remaining == 0:
        em_months: int | None = 0
    elif em_denominator > 0:
        em_months = ceil_int(em_gap_before / em_denominator)
    else:
        em_months = None

    # ---- savings & goals (A55:B62, A64:I76)
    sav_total = inp.savings_current + sav_from_expenses - withdrawal
    monthly_pool = suggest_saving + sav_from_expenses
    goals = allocate_goals(inp.goals, sav_total, monthly_pool)
    allocated = goals.total.allocated
    savings_before_withdrawal = inp.savings_current + sav_from_expenses
    if withdrawal > savings_before_withdrawal:
        sav_status, sav_ok = "over_withdrawn", False
    elif goals.total.over_requested > 0:
        sav_status, sav_ok = "over_allocated", False
    elif goals.total.target > 0 and goals.total.remaining == 0:
        sav_status, sav_ok = "all_covered", True
    else:
        sav_status, sav_ok = "ok", True

    # ---- status & warnings
    rule_inputs = RuleInputs(
        net=net, withdrawal=withdrawal, debt_pct=pct(debt), luxury_pct=pct(luxury),
        net_pct=pct(net), em_remaining=em_remaining,
        savings_available_before_withdrawal=savings_before_withdrawal,
        over_requested=goals.total.over_requested, return_to_savings=return_to_savings,
        needs_amount_count=sum(1 for e in inp.expenses if e.needs_amount)
        + sum(1 for i in inp.incomes if i.needs_amount),
    )
    warns = warnings(rule_inputs, s)
    warn_count, warn_key = warnings_summary(warns)

    # ---- payments (A50:G53)
    paid = sum((l.paid_part for l in lines), ZERO)
    payments = Payments(
        cash_total=sum((e.cash for e in inp.expenses), ZERO),
        installment_total=sum((e.inst_balance for e in inp.expenses), ZERO),
        carried_in_total=sum((e.carried_in for e in inp.expenses), ZERO),
        paid=paid,
        deferred=sum((l.deferred for l in lines), ZERO),
        unpaid=expenses - paid,
        cash_now=income - paid,
        paid_count=sum(1 for e in inp.expenses if e.status == PaymentStatus.PAID),
        deferred_count=sum(1 for e in inp.expenses if e.status == PaymentStatus.DEFERRED),
    )

    # ---- charts
    by_type_amounts: dict[str, Decimal] = {t.name: ZERO for t in inp.types}
    for l in lines:
        by_type_amounts[l.expense.type_name] = by_type_amounts.get(l.expense.type_name, ZERO) + l.effective
    surplus = max(ZERO, net)
    by_type = tuple(ChartSlice(k, v, pct(v)) for k, v in by_type_amounts.items()) + (
        ChartSlice("", surplus, pct(surplus), key="surplus"),)

    by_day = defaultdict(lambda: ZERO)
    no_date = outside = ZERO
    for l in lines:
        if l.paid_part <= 0:
            continue
        d = l.expense.paid_at
        if d is None:
            no_date += l.paid_part
        elif d.year == inp.year and d.month == inp.month:
            by_day[d.day] += l.paid_part
        else:
            outside += l.paid_part
    daily = DailySpend(tuple(by_day[d] for d in range(1, days + 1)), no_date, outside)

    progress = (
        ProgressBar("emergency", min(em_after, inp.emergency_min), em_remaining),
        ProgressBar("goals", allocated, goals.total.remaining),
    )

    return Dashboard(
        expenses=lines,
        summary=Summary(
            income=income, withdrawal=withdrawal, expenses=expenses, debt=debt, luxury=luxury,
            essential=essential, net=net, expenses_pct=pct(expenses), debt_pct=pct(debt),
            luxury_pct=pct(luxury), essential_pct=pct(essential), net_pct=pct(net),
            status=overall_status(rule_inputs, s),
        ),
        suggestions=Suggestions(
            emergency=suggest_emergency, saving=suggest_saving, safe_spend=safe_spend,
            safe_daily=safe_spend / days, safe_weekly=safe_spend / days * 7,
            return_to_savings=return_to_savings, emergency_pct=pct(suggest_emergency),
            saving_pct=pct(suggest_saving), safe_spend_pct=pct(safe_spend),
            return_to_savings_pct=pct(return_to_savings), days_in_month=days,
        ),
        emergency=Emergency(
            current=inp.emergency_current, from_expenses=em_from_expenses, after=em_after,
            minimum=inp.emergency_min,
            reference=s.emergency_ref_months * (essential + debt) * s.emergency_ref_factor,
            remaining=em_remaining, complete=em_remaining == 0,
            months=em_months,
        ),
        savings=Savings(
            current=inp.savings_current, from_expenses=sav_from_expenses, withdrawal=withdrawal,
            total=sav_total, allocated=allocated, unallocated=sav_total - allocated,
            status_key=sav_status, status_ok=sav_ok,
        ),
        goals=goals,
        payments=payments,
        warnings=warns,
        warnings_count=warn_count,
        warnings_key=warn_key,
        charts=Charts(by_type=by_type, progress=progress, daily=daily),
        debug=Debug(
            available=available, base=base, emergency_gap_before=em_gap_before,
            emergency_target=em_target, saving_rate=saving_rate, saving_target=saving_target,
            goals_pool=goals.pool, goals_monthly_pool=monthly_pool, goals_rounds=goals.rounds,
        ),
    )
