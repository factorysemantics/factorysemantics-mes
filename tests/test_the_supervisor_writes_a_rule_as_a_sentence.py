"""The supervisor's screen: a rule is a sentence, and the shift is one line.

Two things are proved here, and they are the two a maintenance supervisor with
three hundred people would judge the page on.

The first is that nothing drifts. A rule is written by filling in four blanks;
the sentence under the blanks, the sentence in the list, the sentence the CLI
prints, the sentence in the audit row and the sentence `explain` quotes are all
one string, rendered once, by `dispatch.says`. A page with its own copy of the
words would be a page that says "whoever is nearest" about a rule the
dispatcher reads as "whoever has least on", and a supervisor who catches that
once stops believing the screen.

The second is that waiting work says why it is waiting - in the dispatcher's
own three reasons, plus the fourth that is not a dispatch failure at all: an
order somebody already has, that needs the machine stopped, on a machine that
has been running all shift. On the lab on 2026-10-09 that order was a chiller
clean, the electrician had it from 18:20, and nothing on any screen said so.
"""

from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from fsmes.domain import (
    AuditLog,
    DispatchRule,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
    UnassignedReason,
)
from fsmes.services import calendar, dispatch, equipment, masterdata

#: A Thursday inside the day shift, on the plant's own clock (naive UTC), so
#: every test here agrees about which shift is running.
NOON = datetime(2026, 10, 8, 12, 0)
#: The shift NOON falls in, asked for by name. The route reads the shift
#: running *now* when nobody names one, which is right for a screen and no use
#: to a test whose orders were raised on a Thursday in October.
SHIFT = "/maintenance/shift?shift=2026-10-08/DAY"


@pytest.fixture()
def plant(session):
    """Two shifts, three trades, a crew of four, and nothing written yet."""
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
    }
    for code, (name, skill, home) in crew.items():
        masterdata.create_person(session, code=code, name=name, role="operator",
                                 home_equipment=home)
        dispatch.grant_skill(session, person_code=code, skill_code=skill)
        dispatch.set_roster(session, person_code=code, shift_code="DAY")
    session.flush()
    return session


def _order(session, equipment_code="MIX01", *, skill=None, priority=None,
           raised_at=None, code=None, summary=None, needs_stop=False):
    machine = masterdata.get_equipment(session, equipment_code)
    order = MaintenanceOrder(
        code=code or f"CM-{equipment_code}-{skill or 'ANY'}",
        equipment_id=machine.id, kind=MaintenanceKind.CORRECTIVE,
        summary=summary or f"something on {equipment_code}", skill_code=skill,
        priority=priority, needs_stop=needs_stop,
        raised_at=raised_at or NOON - timedelta(hours=1))
    session.add(order)
    session.flush()
    return order


# ------------------------------------------------- writing one as a sentence


def test_a_supervisor_fills_in_blanks_and_the_answer_is_the_dispatchers_own_sentence(
        plant, admin):
    """The whole point. Four blanks in, one sentence back, and the sentence is
    rendered by the dispatcher rather than by whatever sent the blanks."""
    written = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC", "strategy": "nearest"})

    assert written.status_code == 201, written.text
    out = written.json()
    assert out["says"] == ("Work on MIX01 needing ELEC goes to somebody on this "
                           "shift, whoever is nearest the machine.")
    rule = plant.scalar(select(DispatchRule).where(DispatchRule.code == out["code"]))
    assert dispatch.says(rule) == out["says"]


def test_the_preview_under_the_blanks_writes_nothing_and_takes_no_code(plant, admin):
    """What the page shows while a supervisor is still choosing. The words come
    from the server so the preview and the saved rule cannot disagree."""
    preview = admin.post("/maintenance/rules?dry_run=1", json={
        "skill": "MECH", "priority_at_least": 2, "strategy": "round_robin"})

    assert preview.status_code == 200, preview.text
    out = preview.json()
    assert out["dry_run"] is True
    assert out["says"] == ("Work anywhere in the plant needing MECH, priority 2 "
                           "or worse, goes to somebody on this shift, taking turns.")
    assert plant.scalar(select(DispatchRule)) is None

    saved = admin.post("/maintenance/rules", json={
        "skill": "MECH", "priority_at_least": 2, "strategy": "round_robin"}).json()
    assert saved["says"] == out["says"]
    assert saved["code"] == out["code"]


def test_nothing_has_to_be_named_because_the_code_is_made_from_the_blanks(plant, admin):
    """A supervisor filling in blanks has not been asked to invent an
    identifier, and a rule nobody named is called what it does."""
    out = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC", "strategy": "nearest"}).json()

    assert out["code"] == "MIX01-ELEC-NEAR"
    assert out["name"] == out["says"]


def test_the_same_sentence_written_twice_gets_a_code_of_its_own(plant, admin):
    """Two rules that read alike are two rules; neither overwrites the other."""
    first = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()
    second = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()

    assert first["code"] == "MIX01-ELEC-LEAST"
    assert second["code"] == "MIX01-ELEC-LEAST-2"


def test_a_rule_naming_a_machine_this_plant_does_not_have_is_refused(plant, admin):
    """404 at the door rather than a rule that quietly matches nothing."""
    refused = admin.post("/maintenance/rules", json={"equipment": "NOPE01"})

    assert refused.status_code == 404
    assert "NOPE01" in refused.text


def test_an_empty_sentence_is_the_rule_a_plant_falls_back_to_anyway(plant, admin):
    """Every blank left out means "anything", which is a rule a plant might
    really want and the one it runs on with nothing written."""
    out = admin.post("/maintenance/rules", json={}).json()

    assert out["says"] == dispatch.says(dispatch.default_rule())
    assert out["code"] == "ANY-ANY-LEAST"


# ------------------------------------------------------ changing one, and off


def test_clearing_the_trade_blank_stops_the_rule_being_about_electricians(
        plant, admin):
    """A blank sent as null is cleared, and the rule's name follows its
    sentence - so the rule never reads as the rule it used to be."""
    code = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()["code"]

    changed = admin.patch(f"/maintenance/rules/{code}", json={"skill": None})

    assert changed.status_code == 200, changed.text
    out = changed.json()
    assert out["skill"] is None
    assert out["says"] == ("Work on MIX01, whatever the skill, goes to somebody "
                           "on this shift, whoever has least on.")
    assert out["name"] == out["says"]


def test_a_blank_left_out_of_the_body_is_left_alone(plant, admin):
    """`exclude_unset`, not a whole-row write: a screen changing the strategy
    must not clear the trade it never sent."""
    code = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()["code"]

    out = admin.patch(f"/maintenance/rules/{code}",
                           json={"strategy": "nearest"}).json()

    assert out["skill"] == "ELEC"
    assert out["equipment"] == "MIX01"
    assert out["strategy"] == "nearest"


def test_switching_a_rule_off_takes_it_out_of_the_order_the_rules_are_tried_in(
        plant, admin):
    written = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()["code"]

    admin.patch(f"/maintenance/rules/{written}", json={"active": False})

    listed = admin.get("/maintenance/rules").json()
    assert listed["total"] == 1
    assert written not in listed["tried_in_order"]
    # One rule, switched off, is a plant running on the house default - which
    # is what `rules()` says and what the screen has to say with it.
    assert listed["tried_in_order"] == [dispatch.DEFAULT_RULE_CODE]


def test_the_arrows_move_a_rule_up_without_leaving_two_on_one_number(plant, admin):
    """One call, because the order is the thing being changed. Every rule is
    renumbered in tens afterwards, so the arrows keep working."""
    for skill in ("ELEC", "MECH", "GEN"):
        admin.post("/maintenance/rules", json={"skill": skill})
    order = admin.get("/maintenance/rules").json()["tried_in_order"]
    assert order == ["ANY-ELEC-LEAST", "ANY-MECH-LEAST", "ANY-GEN-LEAST"]

    moved = admin.patch("/maintenance/rules/ANY-GEN-LEAST", json={"move": "up"})

    assert moved.status_code == 200, moved.text
    after = admin.get("/maintenance/rules").json()
    assert after["tried_in_order"] == ["ANY-ELEC-LEAST", "ANY-GEN-LEAST",
                                       "ANY-MECH-LEAST"]
    sequences = [r["sequence"] for r in after["rules"]]
    assert sequences == [10, 20, 30]


def test_the_rule_already_tried_first_cannot_be_moved_up(plant, admin):
    admin.post("/maintenance/rules", json={"skill": "ELEC"})

    refused = admin.patch("/maintenance/rules/ANY-ELEC-LEAST", json={"move": "up"})

    assert refused.status_code == 409
    assert "already tried first" in refused.text


def test_a_patch_that_names_nothing_is_told_what_a_rules_blanks_are_called(plant, admin):
    admin.post("/maintenance/rules", json={"skill": "ELEC"})

    refused = admin.patch("/maintenance/rules/ANY-ELEC-LEAST", json={})

    assert refused.status_code == 400
    assert "strategy" in refused.text


# ------------------------------------------------------------ removing one


def test_a_rule_that_never_handed_anything_out_can_be_removed(plant, admin):
    admin.post("/maintenance/rules", json={"skill": "GEN"})

    removed = admin.delete("/maintenance/rules/ANY-GEN-LEAST")

    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] == "ANY-GEN-LEAST"
    assert plant.scalar(select(DispatchRule)) is None


def test_a_rule_that_handed_work_out_is_switched_off_rather_than_deleted(
        plant, admin):
    """Every order it sent still names it in `assigned_by`. An audit trail
    pointing at a rule nobody can look up has stopped being one."""
    code = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()["code"]
    order = _order(plant, skill="ELEC")
    dispatch.dispatch(plant, NOON)
    assert order.assigned_by == code

    refused = admin.delete(f"/maintenance/rules/{code}")

    assert refused.status_code == 409
    assert "handed out 1 order" in refused.text
    assert "Switch the rule off instead" in refused.text
    assert plant.scalar(select(DispatchRule).where(DispatchRule.code == code)) is not None


def test_the_house_default_is_not_a_row_and_says_so_when_somebody_edits_it(plant, admin):
    refused = admin.patch("/maintenance/rules/DEFAULT", json={"active": False})

    assert refused.status_code == 404
    assert "not a row" in refused.text


# ------------------------------------------------------------ who may do this


def test_an_operator_may_read_the_rules_and_may_not_write_one(plant, client):
    """Read is open to anybody who can sign in; authoring is `maintenance.plan`."""
    assert client.get("/maintenance/rules").status_code == 200
    assert client.get("/maintenance/shift").status_code == 200

    assert client.post("/maintenance/rules", json={"skill": "ELEC"}).status_code == 403
    assert client.patch("/maintenance/rules/X", json={"active": False}).status_code == 403
    assert client.delete("/maintenance/rules/X").status_code == 403


def test_nobody_signed_in_reads_nothing(anon):
    assert anon.get("/maintenance/shift").status_code == 401
    assert anon.post("/maintenance/rules", json={}).status_code == 401


# ------------------------------------------------ the sentence, before and after


def test_every_change_leaves_the_sentence_before_and_the_sentence_after(
        plant, admin):
    """What a supervisor wants to read six weeks later is not that `skill_code`
    went to null. It is that the rule stopped being about electricians."""
    code = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC"}).json()["code"]
    admin.patch(f"/maintenance/rules/{code}", json={"skill": None})

    rows = list(plant.scalars(
        select(AuditLog).where(AuditLog.entity_id == code).order_by(AuditLog.id)))
    actions = [r.action for r in rows]
    assert actions == ["maintenance.rule_written", "maintenance.rule_changed"]
    changed = rows[-1]
    assert "needing ELEC" in changed.before["says"]
    assert "whatever the skill" in changed.after["says"]


def test_the_sentence_on_the_screen_is_the_sentence_the_command_line_prints(
        plant, admin, monkeypatch):
    """One rendering, read by everything. `fsmes maintenance rules` prints
    `dispatch.says`, and so does the route the page reads."""
    from typer.testing import CliRunner

    from fsmes import cli

    said = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC", "strategy": "nearest"}).json()["says"]

    from contextlib import contextmanager

    @contextmanager
    def _same(*_args, **_kwargs):
        yield plant

    monkeypatch.setattr("fsmes.db.session_scope", _same)
    printed = CliRunner().invoke(cli.app, ["maintenance", "rules"])

    assert printed.exit_code == 0, printed.output
    assert said in printed.output


# ----------------------------------------------------------- the shift, in one line


def test_the_shift_reads_as_one_line_a_supervisor_could_say_out_loud(plant, admin):
    _order(plant, skill="ELEC", code="CM-E1")
    _order(plant, skill="MECH", code="CM-M1")
    _order(plant, skill="MECH", code="CM-M2")
    dispatch.dispatch(plant, NOON)

    out = admin.get(SHIFT).json()

    assert out["shift"]["code"] == "DAY"
    assert "3 orders came due" in out["sentence"]
    assert "the rules handed out 2" in out["sentence"]
    assert "1 is waiting" in out["sentence"]
    assert "everybody with the trade is out" in out["sentence"]
    assert out["counts"] == {"came_due": 3, "by_rules": 2, "by_hand": 0,
                             "in_progress": 0, "done": 0, "carried": 0,
                             "waiting": 1}


def test_a_shift_that_raised_nothing_says_so_rather_than_reading_as_empty(
        plant, admin):
    out = admin.get(SHIFT).json()

    assert "no orders came due" in out["sentence"]
    assert "nothing is waiting" in out["sentence"]
    assert out["total"] == 0


def test_the_shift_read_names_the_person_rather_than_their_code(plant, admin):
    """The defect this page was written to fix. On the lab on 2026-10-09 the
    Work tab said "started 6:20:26 PM by MT-04"; a supervisor reads Marco
    Silva. Both travel, and the screen decides which one is big."""
    _order(plant, skill="MECH", code="CM-M1")
    dispatch.dispatch(plant, NOON)

    row = next(o for o in admin.get(SHIFT).json()["orders"]
               if o["code"] == "CM-M1")

    assert row["assigned_to"] == "SPANNER"
    assert row["assigned_to_name"] == "Mo Spanner"


def test_each_order_says_which_rule_sent_it_and_what_that_rule_says(plant, admin):
    """Nothing on the Work tab said which rule handed a job out. A row that
    carries the rule's own sentence can be read without looking it up."""
    code = admin.post("/maintenance/rules", json={
        "equipment": "MIX01", "skill": "ELEC", "strategy": "nearest"}).json()["code"]
    _order(plant, skill="ELEC", code="CM-E1")
    dispatch.dispatch(plant, NOON)

    row = next(o for o in admin.get(SHIFT).json()["orders"]
               if o["code"] == "CM-E1")

    assert row["by_rule"] == code
    assert row["by_rule_says"] == ("Work on MIX01 needing ELEC goes to somebody "
                                   "on this shift, whoever is nearest the machine.")


def test_work_a_supervisor_gave_out_by_hand_is_counted_apart_from_the_rules(
        plant, admin):
    """"The rules handed out five" has to be about the rules. A supervisor's own
    name in `assigned_by` is not a rule."""
    _order(plant, skill="ELEC", code="CM-E1")
    dispatch.assign(plant, "CM-E1", "SPARKY", actor="SUPER")

    out = admin.get(SHIFT).json()

    assert out["counts"]["by_rules"] == 0
    assert out["counts"]["by_hand"] == 1
    assert "you gave out 1 by hand" in out["sentence"]
    row = next(o for o in out["orders"] if o["code"] == "CM-E1")
    assert row["by_rule"] == "SUPER"
    assert row["by_rule_says"] is None


# -------------------------------------------------------- waiting, and why


def test_an_order_nobody_could_take_waits_under_the_dispatchers_own_reason(
        plant, admin):
    _order(plant, skill="GEN", code="CM-G1")
    _order(plant, skill="GEN", code="CM-G2")
    dispatch.dispatch(plant, NOON)

    out = admin.get(SHIFT).json()

    assert out["waiting_total"] == 1
    group = out["waiting"][0]
    assert group["reason"] == UnassignedReason.ALL_BUSY.value
    assert group["label"] == "everybody with the trade is out on a job"
    assert group["total"] == 1
    assert [o["code"] for o in group["orders"]] == ["CM-G2"]


def test_an_order_no_rule_covers_waits_under_the_reason_a_supervisor_can_fix(
        plant, admin):
    """Written rules mean a plant has said what it wants, so work none of them
    covers is genuinely `no_rule` rather than swept up by a default."""
    admin.post("/maintenance/rules", json={"skill": "ELEC"})
    _order(plant, skill="MECH", code="CM-M1")
    dispatch.dispatch(plant, NOON)

    group = admin.get(SHIFT).json()["waiting"][0]

    assert group["reason"] == UnassignedReason.NO_RULE.value
    assert "Write a rule for it" in group["why"]


def test_an_order_raised_since_the_last_dispatch_pass_says_that_rather_than_nothing(
        plant, admin):
    """Null `unassigned_reason` is not "nobody could take it". A screen shows
    them differently or it invents a refusal that never happened."""
    _order(plant, skill="ELEC", code="CM-E1")

    group = admin.get(SHIFT).json()["waiting"][0]

    assert group["reason"] == "not_dispatched_yet"
    assert group["label"] == "the dispatcher has not seen it"


def test_the_chiller_clean_reads_as_waiting_for_a_stop_while_the_line_runs(
        plant, admin):
    """The fourth bucket, and the one that is not a dispatch failure at all.

    On the lab on 2026-10-09 the electrician had the chiller clean from 18:20.
    The job needs the machine stopped; the line had been making bottles all
    shift. `unassigned_reason` was null and every screen read the order as
    handed out and fine.
    """
    equipment.set_state(plant, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING)
    _order(plant, skill="ELEC", code="CM-CHILL", needs_stop=True,
           summary="Clean the chiller condenser")
    dispatch.dispatch(plant, NOON)

    out = admin.get(SHIFT).json()

    group = out["waiting"][0]
    assert group["reason"] == "needs_a_stop"
    assert group["label"] == "needs the line stopped"
    assert [o["code"] for o in group["orders"]] == ["CM-CHILL"]
    assert group["orders"][0]["assigned_to_name"] in {"Eve Sparks", "Ash Volt"}
    assert group["orders"][0]["machine_state"] == "running"
    assert "needs a stop and the line has not stopped" in out["sentence"]


def test_the_walk_on_a_job_somebody_has_says_what_it_is_waiting_for(plant, admin):
    """*Why?* on a stop job read wrongly until the live proof on 2026-10-09.

    The walk is a hypothetical - which rule would match and who would be
    picked if the job were handed out this minute - so under a heading that
    said *needs the line stopped* the chiller clean ended "would go to nobody
    - all_busy", the answer to a question nobody asked. The walk now says
    what the order is waiting for first, in the same clause the group
    heading uses, and keeps the hypothetical underneath it.
    """
    equipment.set_state(plant, equipment_code="MIX01",
                        state=EquipmentStateName.RUNNING)
    order = _order(plant, skill="ELEC", code="CM-CHILL", needs_stop=True,
                   summary="Clean the chiller condenser")
    dispatch.dispatch(plant, NOON)

    walk = admin.get(f"/maintenance/dispatch/{order.code}/explain").json()

    assert walk["held_by"], "the dispatcher did hand it out; it is the stop it waits for"
    assert walk["waiting_clause"] == "needs a stop and the line has not stopped"
    assert walk["would_now"], "and the hypothetical is still there, underneath"


def test_the_walk_on_work_in_hand_says_it_is_waiting_for_nothing(plant, admin):
    """The other half: a stop job on a stopped machine is simply being done,
    and a walk that named something it was waiting for would be inventing it."""
    equipment.set_state(plant, equipment_code="MIX01", state=EquipmentStateName.DOWN)
    order = _order(plant, skill="ELEC", code="CM-CHILL", needs_stop=True)
    dispatch.dispatch(plant, NOON)

    walk = admin.get(f"/maintenance/dispatch/{order.code}/explain").json()

    assert walk["waiting_clause"] is None


def test_the_same_job_is_not_waiting_once_the_machine_is_down(plant, admin):
    """The other half. A stop job on a stopped machine is work in hand."""
    equipment.set_state(plant, equipment_code="MIX01", state=EquipmentStateName.DOWN)
    _order(plant, skill="ELEC", code="CM-CHILL", needs_stop=True)
    dispatch.dispatch(plant, NOON)

    out = admin.get(SHIFT).json()

    assert out["waiting_total"] == 0
    assert "nothing is waiting" in out["sentence"]


def test_a_plant_with_no_shift_pattern_is_told_there_is_no_shift(session, admin):
    """Not an empty shift, which would read as a quiet night (house rule 3)."""
    out = admin.get("/maintenance/shift").json()

    assert out["shift"] is None
    assert out["sentence"] is None
    assert out["why_empty"]
    assert out["orders"] == [] and out["total"] == 0


def test_the_shift_can_be_asked_for_by_name(plant, admin):
    out = admin.get("/maintenance/shift?shift=2026-10-08/DAY").json()

    assert out["shift"]["key"] == "2026-10-08/DAY"


def test_work_still_open_from_an_earlier_shift_is_shown_but_not_counted_as_new(
        plant, admin):
    """Two questions, one read: what this shift raised, and what is stuck now.
    A job stuck since yesterday is stuck now, and it did not come due today."""
    _order(plant, skill="GEN", code="CM-OLD",
           raised_at=NOON - timedelta(days=2))

    out = admin.get(SHIFT).json()

    assert out["counts"]["came_due"] == 0
    assert out["total"] == 1
    row = out["orders"][0]
    assert row["code"] == "CM-OLD" and row["this_shift"] is False
    assert out["waiting_total"] == 1


# ------------------------------------------------------------ the vocabulary


def test_the_screen_is_handed_the_words_the_blanks_may_be_filled_with(plant, admin):
    """So a page offers exactly the vocabulary this dispatcher understands and
    keeps no copy of its own."""
    out = admin.get("/maintenance/rules").json()["vocabulary"]

    assert [s["code"] for s in out["skills"]] == ["ELEC", "GEN", "MECH"]
    assert {s["value"] for s in out["strategies"]} == {
        "least_loaded", "nearest", "round_robin"}
    said = {s["value"]: s["says"] for s in out["strategies"]}
    assert said["nearest"] == "whoever is nearest the machine"
    assert [p["name"] for p in out["priorities"]] == [
        "safety", "production-critical", "routine"]
    assert any(e["code"] == "MIX01" for e in out["equipment"])


def test_the_strategy_words_the_page_offers_are_the_ones_says_uses(plant):
    """Read from one place. A page whose dropdown and whose preview drift apart
    is the thing this whole section exists to prevent."""
    for strategy in dispatch.vocabulary(plant)["strategies"]:
        rule = DispatchRule(code="X", name="x", strategy=strategy["value"],
                            sequence=1, active=True)
        assert strategy["says"] in dispatch.says(rule)


def test_an_order_that_is_assigned_is_in_this_shifts_work(plant, admin):
    """#158 left the Work list fetching only `due` and `in_progress`, so an
    assigned order was counted in the KPI and never shown anywhere."""
    _order(plant, skill="MECH", code="CM-M1")
    dispatch.dispatch(plant, NOON)

    codes = [o["code"] for o in admin.get(SHIFT).json()["orders"]]

    assert codes == ["CM-M1"]
    assert plant.scalar(select(MaintenanceOrder.status).where(
        MaintenanceOrder.code == "CM-M1")) is MaintenanceStatus.ASSIGNED
