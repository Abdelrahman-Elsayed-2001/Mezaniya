"""The exact inputs currently in mezaniya.xlsx (Dashboard sheet)."""
from app.engine import DEFAULT_TYPE_KEYS, BudgetInput, Expense, ExpenseType, Goal, Income
from app.i18n import t

# the default expense types as an Arabic-speaking user gets them (the workbook's names)
TYPES = tuple(ExpenseType(t(f"seed.type.{key}", "ar"), role) for key, role in DEFAULT_TYPE_KEYS)


def sheet_input(**overrides) -> BudgetInput:
    data = dict(
        year=2026, month=11,   # a 30-day month, so per-day numbers match the sheet's fixed /30
        types=TYPES,
        incomes=(
            Income("راتب", 5000),
            Income("حوافز", 500),
            Income("سحب من المدخرات", 100, is_savings_withdrawal=True),
        ),
        expenses=(
            Expense("بلايستيشن", "كمالي", cash=0, inst_balance=700, inst_count=3),
            Expense("اقساط تابي وتمارا", "دين/أقساط", cash=800, inst_balance=3250, inst_count=5),
            Expense("انترنت", "أساسي", cash=120),
            Expense("دين", "دين/أقساط", cash=0, inst_balance=2400, inst_count=3),
            Expense("اشتراكات AI", "أساسي", cash=150),
            Expense("اشتراكات المنصات الاونلاين", "كمالي", cash=50),
            Expense("خروجة البر", "كمالي", cash=200),
            Expense("ادخار عام", "ادخار"),
            Expense("صندوق الطوارئ", "طوارئ"),
            Expense("مساعدة صديق", "العطاء والمساندة", cash=0),
            Expense("تحويل لحسابي المصري", "تحويل خارجي", cash=0),
            Expense("اسهم", "الاستثمار", cash=55),
            Expense("test", "كمالي", cash=2500),
        ),
        goals=(
            Goal("بطيخ", target=20, priority=0),
            Goal("بطاطس", target=1000, priority=0),
            Goal("جوال", target=4000, priority="0.1"),
            Goal("هوبالالا", target=1000, manual_amount=10),
            Goal("سيارة", target=35000, priority=0),
        ),
        emergency_current=250,
        emergency_min=260,
        savings_current=90,
    )
    data.update(overrides)
    return BudgetInput(**data)
