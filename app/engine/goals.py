"""Distribution of savings over goals (Backend!C37:J51), with the agreed changes:

* a goal without a positive target is ignored entirely;
* a manual amount is converted to a percentage of the available savings and then
  behaves like an explicit priority (allocation and monthly suggestion), except that
  the amount is a hard ceiling for its allocation;
* leftovers are redistributed repeatedly (water-filling) until the money runs out
  or every prioritised goal is full, instead of Excel's single second round.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .fmt import ceil_int, safe_div
from .models import ZERO, Goal

EPS = Decimal("0.000001")
ONE = Decimal(1)


@dataclass(frozen=True)
class GoalLine:
    goal: Goal
    valid: bool                  # has a positive target
    mode: str                    # "manual" | "priority" | "auto" | "ignored"
    priority: Decimal            # effective share of the available savings (D39)
    requested: Decimal           # amount the priority / manual entry asks for
    allocated: Decimal           # actual allocation after water-filling (Dashboard D)
    remaining: Decimal           # F
    progress: Decimal | None     # G
    monthly: Decimal             # H
    months_state: str            # I: "" (ignored goal) | "covered" | "count" | "unknown"
    months: int | None           # with months_state == "count"


@dataclass(frozen=True)
class GoalsTotal:
    target: Decimal              # B75
    explicit_priority: Decimal   # C75 (manual + typed priorities; > 1 means over-requested)
    manual: Decimal              # E75
    allocated: Decimal           # D76
    remaining: Decimal           # F75
    progress: Decimal            # G75
    monthly: Decimal             # H75
    requested: Decimal           # Σ explicit requests
    over_requested: Decimal      # how much the explicit requests exceed the available savings
    status_key: str              # I75: "" | "total_all_covered" | "total_some_missing" (goals.<key>)


@dataclass(frozen=True)
class GoalsResult:
    lines: tuple[GoalLine, ...]
    total: GoalsTotal
    pool: Decimal                # savings available for allocation
    rounds: int                  # water-filling iterations used


def waterfill(pool: Decimal, weights: list[Decimal], caps: list[Decimal]) -> tuple[list[Decimal], int]:
    """Distribute pool × min(1, Σw) proportionally to weights, capped per item,
    re-spreading whatever a capped item could not take until nothing is left."""
    alloc = [ZERO] * len(weights)
    total_w = sum(weights, ZERO)
    money = pool * min(ONE, total_w)
    rounds = 0
    while money > EPS and rounds <= len(weights):
        active = [i for i, w in enumerate(weights) if w > 0 and caps[i] - alloc[i] > EPS]
        if not active:
            break
        w_active = sum((weights[i] for i in active), ZERO)
        given = ZERO
        for i in active:
            x = min(caps[i] - alloc[i], money * weights[i] / w_active)
            alloc[i] += x
            given += x
        money -= given
        rounds += 1
    return alloc, rounds


def allocate_goals(goals: tuple[Goal, ...], savings_total: Decimal, monthly_pool: Decimal) -> GoalsResult:
    pool = max(ZERO, savings_total)

    valid = [g.target is not None and g.target > 0 for g in goals]
    modes: list[str] = []
    explicit: list[Decimal | None] = []
    for g, ok in zip(goals, valid):
        if not ok:
            modes.append("ignored"); explicit.append(None)
        elif g.manual_amount is not None:
            modes.append("manual"); explicit.append(safe_div(g.manual_amount, pool))
        elif g.priority is not None:
            modes.append("priority"); explicit.append(g.priority)
        else:
            modes.append("auto"); explicit.append(None)

    explicit_sum = sum((p for p in explicit if p is not None), ZERO)
    auto_count = modes.count("auto")
    auto_share = safe_div(max(ZERO, ONE - explicit_sum), Decimal(auto_count))
    eff = [
        ZERO if m == "ignored" else (auto_share if m == "auto" else p)
        for m, p in zip(modes, explicit)
    ]

    # a manual amount is a hard ceiling: redistribution never pushes a goal past it
    caps = [
        ZERO if not ok else (min(g.target, g.manual_amount) if m == "manual" else g.target)
        for g, ok, m in zip(goals, valid, modes)
    ]
    alloc, rounds = waterfill(pool, eff, caps)

    remaining = [
        max(ZERO, g.target - a) if g.target is not None else ZERO
        for g, a in zip(goals, alloc)
    ]

    # monthly suggestion: by priority among goals that still need money, else equal split
    wm = [e if r > 0 else ZERO for e, r in zip(eff, remaining)]
    wm_sum = sum(wm, ZERO)
    needing = sum(1 for r in remaining if r > 0)
    monthly = []
    for w, r in zip(wm, remaining):
        if wm_sum > 0:
            share = w / wm_sum
        else:
            share = safe_div(ONE if r > 0 else ZERO, Decimal(needing))
        monthly.append(min(r, monthly_pool * share))

    def requested_for(g: Goal, m: str, e: Decimal) -> Decimal:
        if m == "manual":
            return g.manual_amount
        if m in ("priority", "auto"):
            return e * pool
        return ZERO

    lines = []
    for g, ok, m, e, a, r, mo in zip(goals, valid, modes, eff, alloc, remaining, monthly):
        months = None
        if not ok:
            months_state = ""
        elif r == 0:
            months_state = "covered"
        elif mo > 0:
            months_state, months = "count", ceil_int(r / mo)
        else:
            months_state = "unknown"
        lines.append(GoalLine(
            goal=g, valid=ok, mode=m, priority=e, requested=requested_for(g, m, e),
            allocated=a, remaining=r,
            progress=safe_div(a, g.target) if ok else None,
            monthly=mo, months_state=months_state, months=months,
        ))

    target_sum = sum((g.target for g in goals if g.target is not None), ZERO)
    remaining_sum = sum(remaining, ZERO)
    requested_sum = sum((l.requested for l in lines if l.mode in ("manual", "priority")), ZERO)
    total = GoalsTotal(
        target=target_sum,
        explicit_priority=explicit_sum,
        manual=sum((g.manual_amount for g, ok in zip(goals, valid) if ok and g.manual_amount is not None), ZERO),
        allocated=sum(alloc, ZERO),
        remaining=remaining_sum,
        progress=safe_div(target_sum - remaining_sum, target_sum),
        monthly=sum(monthly, ZERO),
        requested=requested_sum,
        over_requested=max(ZERO, requested_sum - pool),
        status_key="" if target_sum == 0 else (
            "total_all_covered" if remaining_sum == 0 else "total_some_missing"),
    )
    return GoalsResult(lines=tuple(lines), total=total, pool=pool, rounds=rounds)
