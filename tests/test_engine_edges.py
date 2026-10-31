from datetime import date
from decimal import Decimal

from app.engine import BudgetInput, Expense, Goal, Income, PaymentStatus, Settings, compute, validate
from tests.sheet_data import TYPES, sheet_input

D = Decimal


def healthy(**kw) -> BudgetInput:
    data = dict(
        year=2026, month=2, types=TYPES,
        incomes=(Income("راتب", 10000),),
        expenses=(
            Expense("إيجار", "أساسي", cash=3000),
            Expense("قسط", "دين/أقساط", inst_balance=3000, inst_count=3),
            Expense("خروجات", "كمالي", cash=1000),
        ),
        emergency_current=0, emergency_min=3000,
    )
    data.update(kw)
    return BudgetInput(**data)


def test_distribution_with_incomplete_emergency_and_february():
    d = compute(healthy())
    assert d.summary.net == 5000
    assert d.debug.base == 5000
    assert d.suggestions.emergency == 1500           # min(gap 3000, 5000 × 30%)
    assert d.suggestions.saving == 1000              # 5000 × 20%
    assert d.suggestions.safe_spend == 2500
    assert d.suggestions.days_in_month == 28
    assert round(d.suggestions.safe_daily, 4) == D("89.2857")
    assert d.suggestions.safe_weekly == 625
    assert d.emergency.months == 2                   # ceil(3000 / 1500)
    assert d.emergency.reference == 15000            # 3 × (3000 + 1000) × 1.25
    assert d.summary.status.key == "complete_emergency"


def test_complete_emergency_raises_saving_rate():
    d = compute(healthy(emergency_current=3000))
    assert d.suggestions.emergency == 0
    assert d.suggestions.saving == 2500              # 50%
    assert d.emergency.months == 0 and d.emergency.complete
    assert d.summary.status.key == "excellent"
    assert d.warnings_count == 0 and d.warnings_key == "summary_none"


def test_leap_february_and_31_day_month():
    assert compute(healthy(year=2028)).suggestions.days_in_month == 29
    assert compute(healthy(month=12)).suggestions.days_in_month == 31


def test_reference_factor_and_months_come_from_settings():
    s = Settings(emergency_ref_months=6, emergency_ref_factor=1)
    assert compute(healthy(settings=s)).emergency.reference == 24000


def test_expense_table_contributions_reduce_suggestions():
    d = compute(healthy(expenses=healthy().expenses + (
        Expense("طوارئ", "طوارئ", cash=500), Expense("ادخار", "ادخار", cash=400))))
    assert d.debug.base == 5000                     # net 4100 + 500 + 400
    assert d.suggestions.emergency == 1000          # target 1500 − 500 already in the table
    assert d.suggestions.saving == 600              # target 1000 − 400
    assert d.emergency.after == 500
    assert d.savings.total == 400


def test_status_cascade():
    deficit = compute(healthy(incomes=(Income("راتب", 4000),)))
    assert deficit.summary.status.level == "red"
    w = {x.key: x for x in deficit.warnings}
    assert w["deficit"].active and w["deficit"].params == {"amount": "1,000"}
    high_debt = compute(healthy(expenses=(Expense("قسط", "دين/أقساط", cash=4000),), emergency_current=3000))
    assert high_debt.summary.status.key == "high_debt"
    low = compute(healthy(expenses=(Expense("x", "أساسي", cash=9500),), emergency_current=3000))
    assert low.summary.status.key == "acceptable"
    good = compute(healthy(expenses=(Expense("x", "أساسي", cash=8500),), emergency_current=3000))
    assert good.summary.status.key == "good"


def test_zero_income_does_not_crash():
    d = compute(BudgetInput(year=2026, month=1))
    assert d.summary.net == 0 and d.summary.net_pct == 0
    assert d.summary.status.key == "acceptable"
    assert d.savings.status_key == "ok"
    assert d.goals.total.status_key == ""


def test_installment_without_count_is_one_time():
    d = compute(healthy(expenses=(Expense("x", "أساسي", cash=100, inst_balance=900, inst_count=0),)))
    assert d.expenses[0].base == 1000


def test_partial_deferral():
    # installment 500, paid 400, 100 carried to next month
    e = Expense("قسط", "دين/أقساط", inst_balance=1500, inst_count=3,
                status=PaymentStatus.DEFERRED, deferred_amount=100, paid_at=date(2026, 2, 5))
    d = compute(healthy(expenses=(e,)))
    line = d.expenses[0]
    assert (line.due, line.deferred, line.effective, line.paid_part) == (500, 100, 400, 400)
    assert d.summary.expenses == 400 and d.summary.debt == 400
    assert d.payments.deferred == 100 and d.payments.unpaid == 0 and d.payments.deferred_count == 1


def test_next_month_carried_in_adds_to_due():
    e = Expense("قسط", "دين/أقساط", inst_balance=1000, inst_count=2, carried_in=100)
    d = compute(healthy(expenses=(e,)))
    assert d.expenses[0].due == 600 and d.payments.carried_in_total == 100


def test_full_deferral_counts_nothing_this_month():
    e = Expense("كهرباء", "أساسي", cash=300, status="deferred", deferred_amount=300)
    d = compute(healthy(expenses=(e,)))
    assert d.expenses[0].effective == 0 and d.expenses[0].paid_part == 0


def test_daily_spend_chart():
    exp = (
        Expense("a", "أساسي", cash=100, status="paid", paid_at=date(2026, 2, 3)),
        Expense("b", "أساسي", cash=50, status="paid", paid_at=date(2026, 2, 3)),
        Expense("c", "أساسي", cash=70, status="paid"),                           # no date
        Expense("d", "أساسي", cash=30, status="paid", paid_at=date(2026, 3, 1)),  # outside
        Expense("e", "أساسي", cash=40, status="deferred", deferred_amount=10, paid_at=date(2026, 2, 28)),
        Expense("f", "أساسي", cash=999),                                           # unpaid
    )
    daily = compute(healthy(expenses=exp)).charts.daily
    assert len(daily.by_day) == 28
    assert daily.by_day[2] == 150 and daily.by_day[27] == 30
    assert daily.no_date == 70 and daily.outside_month == 30


def test_needs_amount_warning():
    d = compute(healthy(expenses=(Expense("كهرباء", "أساسي", needs_amount=True),)))
    w = {x.key: x for x in d.warnings}
    assert w["needs_amount"].active and w["needs_amount"].params == {"n": 1}


def test_unknown_type_counts_as_other_and_appears_in_chart():
    d = compute(healthy(expenses=(Expense("x", "نوع محذوف", cash=100),)))
    assert d.summary.expenses == 100 and d.summary.essential == 0
    assert any(s.label == "نوع محذوف" and s.amount == 100 for s in d.charts.by_type)


def test_validation_rules():
    inp = healthy(
        expenses=(
            Expense("x", "مش موجود", cash=10),
            Expense("y", "أساسي", cash=100, status="deferred", deferred_amount=150),
            Expense("z", "أساسي", cash=-5),
        ),
        goals=(Goal("g", 100, priority="0.5", manual_amount=10),),
    )
    fields = {i.field for i in validate(inp)}
    assert fields == {"expenses[0].type_id", "expenses[1].deferred_amount", "expenses[2].cash_amount",
                      "goals[0].manual_allocation"}


def test_over_requested_goals_is_a_non_blocking_issue():
    issues = validate(sheet_input())
    assert [(i.field, i.blocking) for i in issues] == [("goals", False)]
    assert validate(healthy()) == []
