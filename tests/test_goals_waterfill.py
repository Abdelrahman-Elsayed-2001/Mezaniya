from decimal import Decimal

from app.engine import Goal
from app.engine.goals import allocate_goals, waterfill

D = Decimal


def amounts(result):
    return [round(l.allocated, 6) for l in result.lines]


def test_auto_goals_split_equally():
    r = allocate_goals((Goal("a", 100), Goal("b", 100), Goal("c", 100)), D(150), D(0))
    assert amounts(r) == [50, 50, 50]
    assert r.total.over_requested == 0


def test_leftover_redistributed_until_money_runs_out():
    # Excel's single second round would leave 35 unallocated here (165 of 200).
    goals = (Goal("a", 10, priority="0.5"), Goal("b", 60, priority="0.25"), Goal("c", 1000, priority="0.25"))
    r = allocate_goals(goals, D(200), D(0))
    assert amounts(r) == [10, 60, 130]
    assert r.total.allocated == 200
    assert r.rounds == 3


def test_stops_when_every_goal_is_full():
    r = allocate_goals((Goal("a", 30), Goal("b", 20)), D(500), D(0))
    assert amounts(r) == [30, 20]
    assert r.total.remaining == 0
    assert r.total.status_key == "total_all_covered"


def test_manual_amount_becomes_priority():
    # available 200: manual 100 → 50%; the auto goal takes the other 50%
    r = allocate_goals((Goal("manual", 1000, manual_amount=100), Goal("auto", 1000)), D(200), D(0))
    manual, auto = r.lines
    assert manual.mode == "manual" and manual.priority == D("0.5")
    assert manual.requested == 100 and manual.allocated == 100
    assert auto.priority == D("0.5") and auto.allocated == 100


def test_manual_amount_is_a_hard_cap():
    # B fills at 30; its leftover must not push A past the 100 typed manually
    r = allocate_goals((Goal("A", 1000, manual_amount=100), Goal("B", 30, priority="0.5")), D(200), D(0))
    assert amounts(r) == [100, 30]
    assert r.total.allocated == 130 and r.lines[0].remaining == 900


def test_redistribution_tops_manual_up_to_its_cap_only():
    # over 100% → round 1 normalises A below its amount; later rounds refill it to exactly 100
    goals = (Goal("A", 1000, manual_amount=100), Goal("B", 20, priority="0.8"), Goal("C", 1000, priority="0.2"))
    r = allocate_goals(goals, D(200), D(0))
    assert amounts(r) == [100, 20, 80]


def test_manual_above_target_is_capped_at_target():
    r = allocate_goals((Goal("A", 50, manual_amount=80), Goal("B", 1000)), D(200), D(0))
    assert amounts(r)[0] == 50


def test_priority_becomes_amount():
    r = allocate_goals((Goal("p", 1000, priority="0.5"),), D(200), D(0))
    assert r.lines[0].requested == 100 and r.lines[0].allocated == 100
    assert r.total.allocated == 100           # the other 50% stays unallocated on purpose


def test_goal_without_target_is_ignored():
    r = allocate_goals((Goal("no target", None, priority="0.5"), Goal("zero", 0, priority="0.3"),
                        Goal("auto", 1000)), D(200), D(0))
    ignored, zero, auto = r.lines
    assert ignored.mode == zero.mode == "ignored"
    assert ignored.allocated == zero.allocated == 0
    assert ignored.months_state == zero.months_state == "" and ignored.progress is None
    assert auto.priority == 1 and auto.allocated == 200     # their priorities aren't subtracted
    assert r.total.explicit_priority == 0


def test_priorities_over_100_percent_are_normalised_and_flagged():
    r = allocate_goals((Goal("a", 1000, priority="0.8"), Goal("b", 1000, priority="0.8")), D(200), D(0))
    assert amounts(r) == [100, 100]
    assert r.total.explicit_priority == D("1.6")
    assert r.total.over_requested == 120


def test_manual_with_no_available_savings_is_over_requested():
    r = allocate_goals((Goal("m", 1000, manual_amount=10),), D(-10), D(0))
    assert r.pool == 0 and r.lines[0].allocated == 0
    assert r.total.over_requested == 10


def test_monthly_follows_priorities_including_manual_goals():
    r = allocate_goals((Goal("manual", 1000, manual_amount=50), Goal("p", 1000, priority="0.25")),
                       D(200), D(300))
    # weights 0.25 / 0.25 → equal monthly share
    assert [l.monthly for l in r.lines] == [150, 150]
    assert [(l.months_state, l.months) for l in r.lines] == [("count", 7)] * 2   # 950 / 150 → 6.33 → 7


def test_monthly_skips_full_goals_and_caps_at_remaining():
    r = allocate_goals((Goal("full", 50, priority="0.5"), Goal("big", 1000, priority="0.5"),
                        Goal("small", 120, priority=0)), D(100), D(400))
    full, big, small = r.lines
    assert full.remaining == 0 and full.monthly == 0 and full.months_state == "covered"
    assert big.allocated == 50 and big.monthly == 400       # only goal with weight and remaining
    assert small.monthly == 0 and small.months_state == "unknown"


def test_monthly_equal_split_when_no_weights():
    r = allocate_goals((Goal("a", 100, priority=0), Goal("b", 100, priority=0)), D(0), D(50))
    assert [l.monthly for l in r.lines] == [25, 25]


def test_waterfill_terminates_with_zero_weights():
    alloc, rounds = waterfill(D(100), [D(0), D(0)], [D(10), D(10)])
    assert alloc == [0, 0] and rounds == 0
