"""The dispatcher: due work reaches a free person who holds the trade.

Every test here is one sentence a maintenance supervisor would recognise, and
the ones that matter most are the three refusals - nobody on shift with the
skill, everybody busy, no rule covers it - because an MES that silently leaves
an order at `due` with no reason on it is an MES whose backlog nobody reads.
"""

from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from fsmes.domain import (
    LEVEL_TRAINEE,
    DispatchStrategy,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
    UnassignedReason,
)
from fsmes.services import Forbidden, calendar, dispatch, maintenance, masterdata

#: A Thursday inside the day shift, on the plant's own clock (the suite pins
#: the plant to UTC), so every test here agrees about which shift is running.
NOON = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def plant(session):
    """A plant with two shifts, three trades and a crew of five."""
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(6, 0), ends=time(18, 0), days="1111111")
    calendar.create_pattern(session, code="NIGHT", name="Night shift",
                            starts=time(18, 0), ends=time(6, 0), days="1111111")
    for code, name in (("ELEC", "Electrician"), ("MECH", "Mechanic"),
                       ("GEN", "General maintenance")):
        dispatch.create_skill(session, code=code, name=name)

    crew = {
        "SPARKY": ("Eve Sparks", "ELEC", "MIX01"),
        "SPARKY2": ("Ash Volt", "ELEC", "PACK01"),
        "SPANNER": ("Mo Spanner", "MECH", "MIX01"),
        "HANDY": ("Jo Hands", "GEN", None),
        "NIGHTY": ("Nia Tenn", "ELEC", None),
    }
    for code, (name, skill, home) in crew.items():
        masterdata.create_person(session, code=code, name=name,
                                 role="operator", home_equipment=home)
        dispatch.grant_skill(session, person_code=code, skill_code=skill)
        dispatch.set_roster(session, person_code=code,
                            shift_code="NIGHT" if code == "NIGHTY" else "DAY")
    session.flush()
    return session


def _order(session, equipment_code="MIX01", *, skill=None, priority=None,
           raised_at=None, code=None):
    equipment = masterdata.get_equipment(session, equipment_code)
    order = MaintenanceOrder(
        code=code or f"CM-{equipment_code}-{skill or 'ANY'}-{priority or 0}",
        equipment_id=equipment.id, kind=MaintenanceKind.CORRECTIVE,
        summary=f"something on {equipment_code}", skill_code=skill,
        priority=priority, raised_at=raised_at or NOON - timedelta(hours=1))
    session.add(order)
    session.flush()
    return order


def test_a_plant_with_no_rules_still_sends_an_electrician_to_electrical_work(plant):
    """The house default is a rule, so an unconfigured plant dispatches."""
    order = _order(plant, skill="ELEC")

    report = dispatch.dispatch(plant, NOON)

    assert report["assigned"] == 1
    assert order.status is MaintenanceStatus.ASSIGNED
    assert order.assigned_to in {"SPARKY", "SPARKY2"}
    assert order.assigned_by == dispatch.DEFAULT_RULE_CODE
    assert order.assigned_at == NOON
    assert order.unassigned_reason is None


def test_the_rule_a_supervisor_wrote_is_tried_before_the_house_default(plant):
    dispatch.create_rule(plant, code="MIX-ELEC", name="Mixer electrical work",
                         equipment_code="MIX01", skill_code="ELEC",
                         strategy=DispatchStrategy.NEAREST.value, sequence=10)
    order = _order(plant, "MIX01", skill="ELEC")

    dispatch.dispatch(plant, NOON)

    assert order.assigned_by == "MIX-ELEC"
    # NEAREST: SPARKY is homed on the mixer itself, SPARKY2 on the oven.
    assert order.assigned_to == "SPARKY"


def test_nobody_on_the_night_shift_holds_the_mechanical_trade(plant):
    """One of the three refusals, and the one a supervisor acts on tonight."""
    order = _order(plant, skill="MECH")

    report = dispatch.dispatch(plant, NOON.replace(hour=22))

    assert order.status is MaintenanceStatus.DUE
    assert order.assigned_to is None
    assert order.unassigned_reason == UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value
    assert report["unassigned_by_reason"] == {
        UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value: 1}


def test_the_second_electrical_job_waits_when_both_electricians_are_out(plant):
    first = _order(plant, skill="ELEC", code="CM-E1")
    second = _order(plant, skill="ELEC", code="CM-E2")
    third = _order(plant, skill="ELEC", code="CM-E3")

    dispatch.dispatch(plant, NOON)

    assert {first.assigned_to, second.assigned_to} == {"SPARKY", "SPARKY2"}
    assert third.status is MaintenanceStatus.DUE
    assert third.unassigned_reason == UnassignedReason.ALL_BUSY.value


def test_a_rule_that_covers_nothing_leaves_the_order_saying_no_rule(plant):
    """A plant that has written rules does not get the house default on top.

    Which is the point: an order none of the supervisor's own rules covers is
    `no_rule` and says so, rather than being quietly swept up by a rule nobody
    wrote. The default is for a plant that has configured nothing.
    """
    dispatch.create_rule(plant, code="PACK-ONLY", name="Packer work only",
                         equipment_code="PACK01", sequence=10)
    order = _order(plant, "MIX01", skill="GEN")

    report = dispatch.dispatch(plant, NOON)

    assert order.status is MaintenanceStatus.DUE
    assert order.unassigned_reason == UnassignedReason.NO_RULE.value
    assert report["unassigned_by_reason"] == {UnassignedReason.NO_RULE.value: 1}


def test_safety_work_is_handed_out_before_a_routine_job_raised_earlier(plant):
    routine = _order(plant, skill="MECH", priority=3, code="CM-ROUTINE",
                     raised_at=NOON - timedelta(hours=5))
    safety = _order(plant, skill="MECH", priority=1, code="CM-SAFETY",
                    raised_at=NOON - timedelta(minutes=1))

    dispatch.dispatch(plant, NOON)

    # One mechanic, two jobs: the safety job gets him.
    assert safety.assigned_to == "SPANNER"
    assert routine.status is MaintenanceStatus.DUE
    assert routine.unassigned_reason == UnassignedReason.ALL_BUSY.value


def test_within_one_priority_the_oldest_job_is_handed_out_first(plant):
    old = _order(plant, skill="MECH", priority=2, code="CM-OLD",
                 raised_at=NOON - timedelta(hours=6))
    new = _order(plant, skill="MECH", priority=2, code="CM-NEW",
                 raised_at=NOON - timedelta(minutes=5))

    dispatch.dispatch(plant, NOON)

    assert old.assigned_to == "SPANNER"
    assert new.assigned_to is None


def test_a_trainee_is_on_the_roster_and_is_not_sent_on_their_own(plant):
    masterdata.create_person(plant, code="LEARNER", name="Sam Learning")
    dispatch.grant_skill(plant, person_code="LEARNER", skill_code="MECH",
                         level=LEVEL_TRAINEE)
    dispatch.set_roster(plant, person_code="LEARNER", shift_code="DAY")
    busy = _order(plant, skill="MECH", code="CM-BUSY")
    waiting = _order(plant, skill="MECH", code="CM-WAITING")

    dispatch.dispatch(plant, NOON)

    assert busy.assigned_to == "SPANNER"
    assert waiting.status is MaintenanceStatus.DUE
    assert waiting.unassigned_reason == UnassignedReason.ALL_BUSY.value
    explained = dispatch.explain(plant, "CM-WAITING", now=NOON)
    skipped = {row["person"]: row["verdict"] for row in explained["considered"]}
    assert "trainee" in skipped["LEARNER"]


def test_somebody_marked_absent_for_the_day_is_not_sent_anywhere(plant):
    dispatch.set_roster(plant, person_code="SPANNER", shift_code="DAY",
                        day=NOON.date(), available=False, reason="training course")
    order = _order(plant, skill="MECH")

    dispatch.dispatch(plant, NOON)

    assert order.status is MaintenanceStatus.DUE
    assert order.unassigned_reason == UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value
    explained = dispatch.explain(plant, order.code, now=NOON)
    verdicts = {row["person"]: row["verdict"] for row in explained["considered"]}
    assert verdicts["SPANNER"] == "not available (training course)"


def test_the_absence_is_for_that_day_only_and_the_roster_is_not_rewritten(plant):
    dispatch.set_roster(plant, person_code="SPANNER", shift_code="DAY",
                        day=NOON.date(), available=False, reason="training course")
    order = _order(plant, skill="MECH")

    dispatch.dispatch(plant, NOON + timedelta(days=1))

    assert order.assigned_to == "SPANNER"


def test_taking_turns_spreads_the_work_rather_than_piling_it_on_one_person(plant):
    dispatch.create_rule(plant, code="GEN-TURNS", name="General work, taking turns",
                         skill_code="GEN", strategy=DispatchStrategy.ROUND_ROBIN.value,
                         sequence=10)
    dispatch.grant_skill(plant, person_code="SPARKY", skill_code="GEN")
    first = _order(plant, skill="GEN", code="CM-G1")
    dispatch.dispatch(plant, NOON)
    maintenance.complete(plant, first.code, actor=first.assigned_to)
    second = _order(plant, skill="GEN", code="CM-G2")

    dispatch.dispatch(plant, NOON + timedelta(minutes=5))

    assert first.assigned_to != second.assigned_to


def test_running_the_dispatcher_twice_changes_nothing_the_second_time(plant):
    _order(plant, skill="ELEC")

    first = dispatch.dispatch(plant, NOON)
    second = dispatch.dispatch(plant, NOON)

    assert first["assigned"] == 1
    assert second["assigned"] == 0
    assert second["considered"] == 0


def test_the_rules_never_take_back_an_order_a_supervisor_handed_out(plant):
    order = _order(plant, skill="ELEC")
    dispatch.assign(plant, order.code, "HANDY", actor="SUPER")

    dispatch.dispatch(plant, NOON)

    assert order.assigned_to == "HANDY"
    assert order.assigned_by == "SUPER"


def test_the_order_explains_itself_in_the_words_of_the_rule(plant):
    dispatch.create_rule(plant, code="MIX-ELEC", name="Mixer electrical work",
                         supervisor_code="SUPER", equipment_code="MIX01",
                         skill_code="ELEC", priority_at_least=2, sequence=10)
    order = _order(plant, "MIX01", skill="ELEC", priority=1)
    dispatch.dispatch(plant, NOON)

    explained = dispatch.explain(plant, order.code, now=NOON)

    assert explained["rule"] == "MIX-ELEC"
    assert explained["rule_says"] == (
        "Work on MIX01 needing ELEC, priority 2 or worse, goes to somebody "
        "on this shift, whoever has least on.")
    assert explained["held_by"] == order.assigned_to
    chosen = [row for row in explained["considered"] if row["chosen"]]
    assert len(chosen) == 1
    assert "least loaded" in chosen[0]["verdict"]


def test_an_assigned_order_is_started_by_the_person_it_was_given_to(plant):
    order = _order(plant, skill="MECH")
    dispatch.dispatch(plant, NOON)
    assert order.assigned_to == "SPANNER"

    maintenance.start(plant, order.code, actor="SPANNER")

    assert order.status is MaintenanceStatus.IN_PROGRESS


def test_somebody_elses_order_is_refused_and_the_refusal_says_who_can(plant):
    order = _order(plant, skill="MECH")
    dispatch.dispatch(plant, NOON)

    with pytest.raises(Forbidden) as refused:
        maintenance.start(plant, order.code, actor="HANDY")

    assert "assigned to SPANNER" in str(refused.value)
    assert "maintenance.plan" in str(refused.value)


def test_a_supervisor_may_start_an_order_that_was_given_to_somebody_else(plant):
    order = _order(plant, skill="MECH")
    dispatch.dispatch(plant, NOON)

    maintenance.start(plant, order.code, actor="SUPER",
                      capabilities={"maintenance.plan"})

    assert order.status is MaintenanceStatus.IN_PROGRESS


def test_nobody_holds_two_jobs_at_once(plant):
    for n in range(6):
        _order(plant, skill="ELEC", code=f"CM-E{n}")

    dispatch.dispatch(plant, NOON)

    held = [o.assigned_to for o in plant.scalars(select(MaintenanceOrder))
            if o.assigned_to is not None]
    assert len(held) == len(set(held))


def test_a_plant_that_has_told_this_mes_no_shifts_has_nobody_rostered(session):
    """Not an empty crew: a finding about the calendar, said out loud."""
    dispatch.create_skill(session, code="ELEC", name="Electrician")
    masterdata.create_person(session, code="SPARKY", name="Eve Sparks")
    dispatch.grant_skill(session, person_code="SPARKY", skill_code="ELEC")
    order = _order(session, skill="ELEC")

    dispatch.dispatch(session, NOON)

    assert order.unassigned_reason == UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value
    on_shift = dispatch.roster(session, now=NOON)
    assert on_shift["shift"] is None
    assert on_shift["total"] == 0
    assert "shift" in on_shift["why_empty"]


def test_the_roster_says_who_is_on_shift_with_what_they_can_do_and_what_they_have(plant):
    _order(plant, skill="MECH")
    dispatch.dispatch(plant, NOON)

    on_shift = dispatch.roster(plant, now=NOON)

    assert on_shift["total"] == 4          # four on days, one on nights
    assert on_shift["available"] == 4
    spanner = next(p for p in on_shift["people"] if p["person"] == "SPANNER")
    assert spanner["skills"] == [{"skill": "MECH", "level": 2}]
    assert spanner["open_orders"] == 1
    assert spanner["home"] == "MIX01"


def test_an_order_raised_from_a_plan_carries_the_plans_trade_and_priority(plant):
    plan = maintenance.create_plan(
        plant, code="PM-MIX-ELEC", name="Mixer motor check", equipment_code="MIX01",
        trigger="calendar_days", interval=1.0, skill_code="ELEC", priority=2)
    assert plan.skill_code == "ELEC"

    raised = maintenance.raise_due(plant)

    assert [o.skill_code for o in raised] == ["ELEC"]
    assert [o.priority for o in raised] == [2]
