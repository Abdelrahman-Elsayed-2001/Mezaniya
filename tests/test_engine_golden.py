"""Golden test: the engine must reproduce the values Excel cached in mezaniya.xlsx.

Values that intentionally differ (agreed design changes) are listed in
CHANGED_BY_DESIGN with the Excel value, the new value and the reason.
"""
from decimal import Decimal

import pytest

from app.engine import compute
from tests.sheet_data import sheet_input

TOL = Decimal("0.001")


@pytest.fixture(scope="module")
def d():
    return compute(sheet_input())


def approx(actual, expected):
    assert abs(Decimal(actual) - Decimal(str(expected))) <= TOL, f"{actual} != {expected}"


# cell -> (getter, excel cached value)
EXCEL_NUMBERS = {
    "J5": (lambda d: d.summary.income, 5600),
    "J6": (lambda d: d.summary.expenses, 5558.333333333333),
    "J7": (lambda d: d.summary.debt, 2250),
    "J8": (lambda d: d.summary.luxury, 2983.3333333333335),
    "J9": (lambda d: d.summary.essential, 270),
    "J10": (lambda d: d.summary.net, 41.66666666666697),
    "K6": (lambda d: d.summary.expenses_pct, 0.99255952380952372),
    "K7": (lambda d: d.summary.debt_pct, 0.4017857142857143),
    "K8": (lambda d: d.summary.luxury_pct, 0.53273809523809523),
    "K9": (lambda d: d.summary.essential_pct, 0.048214285714285716),
    "K10": (lambda d: d.summary.net_pct, 0.0074404761904762447),
    "J15": (lambda d: d.suggestions.emergency, 0),
    "J16": (lambda d: d.suggestions.saving, 0),
    "J17": (lambda d: d.suggestions.safe_spend, 0),
    "J18": (lambda d: d.suggestions.safe_daily, 0),
    "J19": (lambda d: d.suggestions.safe_weekly, 0),
    "J20": (lambda d: d.suggestions.return_to_savings, 41.66666666666697),
    "K20": (lambda d: d.suggestions.return_to_savings_pct, 0.0074404761904762447),
    "J22": (lambda d: d.emergency.current, 250),
    "J23": (lambda d: d.emergency.from_expenses, 0),
    "J24": (lambda d: d.emergency.after, 250),
    "J25": (lambda d: d.emergency.minimum, 260),
    "J26": (lambda d: d.emergency.reference, 9450),
    "J27": (lambda d: d.emergency.remaining, 10),
    "C50": (lambda d: d.payments.cash_total, 3875),
    "D50": (lambda d: d.payments.installment_total, 6350),
    "F50": (lambda d: sum(l.effective for l in d.expenses), 5558.333333333333),
    "F51": (lambda d: d.payments.paid, 0),
    "F52": (lambda d: d.payments.unpaid, 5558.333333333333),
    "F53": (lambda d: d.payments.cash_now, 5600),
    "B57": (lambda d: d.savings.from_expenses, 0),
    "B58": (lambda d: d.savings.withdrawal, 100),
    "B59": (lambda d: d.savings.total, -10),
    "B75": (lambda d: d.goals.total.target, 41020),
    "C75": (lambda d: d.goals.total.explicit_priority, 0.1),
    "E75": (lambda d: d.goals.total.manual, 10),
    "H75": (lambda d: d.goals.total.monthly, 0),
    "Backend!D15": (lambda d: d.debug.available, 0),
    "Backend!D28": (lambda d: d.debug.base, 0),
    "Backend!D29": (lambda d: d.debug.emergency_gap_before, 10),
    "Backend!D30": (lambda d: d.debug.emergency_target, 0),
    "Backend!D32": (lambda d: d.debug.saving_rate, 0.2),
    "Backend!D33": (lambda d: d.debug.saving_target, 0),
    "Backend!D50": (lambda d: d.debug.goals_monthly_pool, 0),
    "Backend!D51": (lambda d: d.debug.goals_pool, 0),
    "Backend!D24": (lambda d: d.charts.progress[0].available, 250),
    "Backend!E24": (lambda d: d.charts.progress[0].remaining, 10),
}

# cell -> (getter, excel value, new value, reason)
CHANGED_BY_DESIGN = {
    "B60": (lambda d: d.savings.allocated, 10, 0,
            "manual amount is a % of available savings; available is 0 so nothing can be allocated"),
    "B61": (lambda d: d.savings.unallocated, -20, -10, "follows B60"),
    "D76": (lambda d: d.goals.total.allocated, 10, 0, "follows B60"),
    "F68": (lambda d: d.goals.lines[3].remaining, 990, 1000, "follows B60"),
    "G68": (lambda d: d.goals.lines[3].progress, 0.01, 0, "follows B60"),
    "F75": (lambda d: d.goals.total.remaining, 41010, 41020, "follows B60"),
    "G75": (lambda d: d.goals.total.progress, 0.00024378352023403217, 0, "follows B60"),
    "Backend!D25": (lambda d: d.charts.progress[1].available, 10, 0, "follows B60"),
    "Backend!E25": (lambda d: d.charts.progress[1].remaining, 0, 41020,
                    "bug fix: chart 'remaining' used D75 (suggested) instead of F75"),
}


@pytest.mark.parametrize("cell", EXCEL_NUMBERS)
def test_matches_excel(d, cell):
    getter, expected = EXCEL_NUMBERS[cell]
    approx(getter(d), expected)


@pytest.mark.parametrize("cell", CHANGED_BY_DESIGN)
def test_changed_by_design(d, cell):
    getter, _excel, new, _reason = CHANGED_BY_DESIGN[cell]
    approx(getter(d), new)


def test_states(d):
    assert (d.summary.status.key, d.summary.status.level) == ("squeezed", "orange")   # J11
    assert d.emergency.complete is False                    # J28
    assert d.emergency.months is None                       # J29 "-"
    assert d.savings.status_key == "over_withdrawn"         # B62
    assert d.goals.total.status_key == "total_some_missing" # I75
    assert d.payments.paid_count == 0                       # G50
    assert [l.months_state for l in d.goals.lines] == ["unknown"] * 5      # I65:I69 "-"


def test_texts_in_both_languages(d):
    """The workbook's sentences, now in fusha Arabic, and their English versions."""
    from app.api.serialize import warning_json
    from app.i18n import t
    assert t(f"status.{d.summary.status.key}", "ar") == "🟠 ضائقة — السحب من المدخرات غطّى العجز"
    assert t(f"status.{d.summary.status.key}", "en") == "🟠 Tight — the savings withdrawal covered the shortfall"
    squeezed = next(w for w in d.warnings if w.key == "squeezed")
    assert warning_json(squeezed, "ar", "ر.س")["details"] == "غطّى السحب عجزًا قدره 58 ر.س"
    assert warning_json(squeezed, "en", "SAR")["details"] == "The withdrawal covered a shortfall of 58 SAR"


def test_monthly_installment_amounts(d):
    expected = [233.33333333333334, 1450, 120, 800, 150, 50, 200, 0, 0, 0, 0, 55, 2500]  # F15:F27
    for line, value in zip(d.expenses, expected):
        approx(line.base, value)


def test_warnings(d):
    w = {x.key: x for x in d.warnings}
    expected = {   # I33:K41 — the numbers the sheet's TEXT() formulas produced
        "deficit": (False, {}),
        "squeezed": (True, {"amount": "58"}),
        "over_withdrawn": (True, {"withdrawn": "100", "available": "90"}),
        "high_debt": (True, {"pct": "40.2%", "limit": "33%"}),
        "high_luxury": (True, {"pct": "53.3%", "limit": "30%"}),
        "emergency_short": (True, {"amount": "10"}),
        "low_surplus": (True, {"pct": "0.7%", "min": "10%"}),
        "over_allocated": (True, {"amount": "10"}),   # Excel: 20 (see B61 above)
        "return_to_savings": (True, {"amount": "42"}),
        "needs_amount": (False, {}),                  # new
    }
    assert {k: (v.active, v.params) for k, v in w.items()} == expected
    assert d.warnings_count == 8                       # J42
    assert d.warnings_key == "summary_some"            # K42


def test_by_type_chart(d):
    got = {(s.key, s.label): s.amount for s in d.charts.by_type}
    expected = {   # Backend!G5:H13 (type names as an Arabic-speaking user's defaults)
        ("type", "طوارئ"): 0, ("type", "ادخار"): 0, ("type", "كمالي"): 2983.3333333333335,
        ("type", "دين/أقساط"): 2250, ("type", "أساسي"): 270, ("type", "العطاء والمساندة"): 0,
        ("type", "تحويل خارجي"): 0, ("type", "الاستثمار"): 55, ("surplus", ""): 41.66666666666697,
    }
    assert list(got) == list(expected)
    for k, v in expected.items():
        approx(got[k], v)
