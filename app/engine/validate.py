"""Input validation (the sheet's Data Validation rules + basic sanity), shared by the API.

Issues carry a translation key and parameters; the field path uses the API's field
names (e.g. "expenses[3].deferred_amount") so a message can name the field in any language.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .calc import compute, expense_line
from .fmt import num
from .models import ZERO, BudgetInput, PaymentStatus, Role


@dataclass(frozen=True)
class Issue:
    field: str                  # e.g. "expenses[3].deferred_amount"
    key: str                    # translation key
    params: dict = field(default_factory=dict)
    blocking: bool = True       # False: allowed to save, shown as a warning (state can drift, e.g. savings drop)


NON_NEGATIVE = "api.err.non_negative"


def validate(inp: BudgetInput) -> list[Issue]:
    issues: list[Issue] = []

    def neg(path: str, value):
        if value is not None and value < 0:
            issues.append(Issue(path, NON_NEGATIVE))

    for f in ("emergency_current", "emergency_min", "savings_current"):
        neg(f, getattr(inp, f))

    for i, inc in enumerate(inp.incomes):
        neg(f"incomes[{i}].amount", inc.amount)

    type_names = {t.name for t in inp.types}
    for i, e in enumerate(inp.expenses):
        p = f"expenses[{i}]"
        if e.type_name not in type_names:
            issues.append(Issue(f"{p}.type_id", "issue.type_unknown"))
        neg(f"{p}.cash_amount", e.cash)
        neg(f"{p}.inst_balance", e.inst_balance)
        neg(f"{p}.inst_count", e.inst_count)
        neg(f"{p}.carried_in", e.carried_in)
        if e.status == PaymentStatus.DEFERRED:
            due = expense_line(e, Role.OTHER).due
            if not ZERO < e.deferred_amount <= due:
                issues.append(Issue(f"{p}.deferred_amount", "issue.deferred_range", {"max": num(due)}))

    for i, g in enumerate(inp.goals):
        p = f"goals[{i}]"
        neg(f"{p}.target_amount", g.target)
        neg(f"{p}.priority", g.priority)
        neg(f"{p}.manual_allocation", g.manual_amount)
        if g.priority is not None and g.manual_amount is not None:
            issues.append(Issue(f"{p}.manual_allocation", "api.err.priority_and_manual"))

    if not issues:
        total = compute(inp).goals.total
        if total.over_requested > 0:
            issues.append(Issue("goals", "issue.over_requested", {"amount": num(total.over_requested)},
                                blocking=False))
    return issues
