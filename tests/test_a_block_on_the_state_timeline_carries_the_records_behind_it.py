"""Click a block on a machine's timeline and the records behind it answer for it.

The bar of running / idle / down / setup was a picture a person could only look
at. This file pins the read behind the panel that opens beside it -
`GET /analysis/timeline/block/{equipment_code}?start=&end=` and
`services.state_block.dossier` - block by block, and it ends with the one walk
the product's own answer key asks for: link 1 of the chain, from a four-minute
setup to the word the floor gave it, the person who gave it, and the order
either side.

Every test is keyed on a fact the fixture *declares* - the minute the
changeover began, the person who named it, the order that was booked before it,
the maintenance order that was in progress - because a dossier that drew the
wrong window would still return a plausible number of things.

Three claims here are easy to break and none of them shows on a screenshot:

- **Nothing is computed twice.** The tags are the SPC panel's own read, the
  maintenance orders are the SPC panel's own list. The tests assert the
  identity rather than the arithmetic.
- **Derived is said out loud.** No column joins a stop to an order, and no
  column says which *person* named one. Both are read off other records, and
  each answer carries the sentence that says so.
- **Unknown is a valid answer, zero is not.** A stop nobody has named says
  nobody has named it; a stretch nobody was watching says nobody was looking,
  which is a different sentence from a stop with no reason.
"""

from datetime import timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import (
    ConnectionStateName,
    EquipmentConnection,
    EquipmentState,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
    OperationStatus,
    ProductionLog,
    ProductionSource,
    TagValue,
)
from fsmes.services import (
    Invalid,
    masterdata,
    quality,
    reasons,
    spc_point,
    state_block,
    workorders,
)
from fsmes.services import (
    equipment as equipment_service,
)

#: The two orders of link 1 of the chain, in the words the lab's own answer key
#: uses: `labs/multiplant/bottling/line.json` -> `_chain` -> link 1, *"the
#: changeover brings order 4712 and the filler runs it faster"*.
BEFORE_ORDER = "4711"
AFTER_ORDER = "4712"

#: How long the changeover was. Four minutes, the chain's own figure.
SETUP_MINUTES = 4

#: The machine's temperature either side of the changeover, in °C. A step, and
#: the panel's job is only to draw it with a line where the block starts.
TEMP_BEFORE = 62.0
TEMP_AFTER = 58.5

#: The filler's cycle time either side of it, in milliseconds - link 1's own
#: second record, about 33 ms faster on the new product. It is in the fixture
#: and it is deliberately *not* expected on a small chart: the kernel classes
#: `CycleTimeMs` as a structural tag, the plant's fabric rather than its
#: process, and both panels that draw analogs leave those out. The step is
#: readable on the Analysis tab's tag chart, which is where that link is
#: scored.
CYCLE_BEFORE = 1180.0
CYCLE_AFTER = 1147.0

#: How often the fixture's tags arrive. Ten seconds, which the panel buckets
#: against this plant's configured analog cadence rather than against itself.
SAMPLE_SECONDS = 10


def _book(session, unit, order_code, *, at, good):
    """One quantity this machine booked against an order, at a stated moment."""
    order = workorders.get(session, order_code)
    session.add(ProductionLog(
        work_order_id=order.id, operation_id=order.operations[0].id,
        equipment_id=unit.id, good_qty=good, scrap_qty=0.0,
        source=ProductionSource.OPC, ts=at))


def _order(session, code, *, started_at, completed_at=None):
    """An order released to the floor, with its mixing step's own stamps.

    Written on the operation rather than through `start_operation`, which can
    only ever start a step *now*: the point of this fixture is that each fact
    carries the minute it happened.
    """
    workorders.create(session, code=code, material_code="FG-COLA", quantity=1000)
    order = workorders.release(session, code)
    operation = order.operations[0]
    operation.started_at = started_at
    operation.completed_at = completed_at
    operation.status = OperationStatus.DONE if completed_at else OperationStatus.RUNNING
    session.flush()
    return order


@pytest.fixture()
def shift(session):
    """A shift on the mixer with link 1's shape in it, each fact timestamped.

    Reading down the clock, where `at` is now:

    * `at - 3 h`    the machine starts running order 4711;
    * `at - 70 min` five minutes down that nobody has named - the stop the
      panel has to report as unnamed rather than as nothing;
    * `at - 50 min` the changeover: four minutes of setup, named *Changeover*
      by ANNA afterwards, through the write the panel's own control posts to;
    * `at - 46 min` running again, on order 4712 from a minute later;
    * `at - 30 min` ten minutes down for a chiller clean - a maintenance order
      in progress across it, named in the stop's own reason, which is what a
      planned stop looks like in these records;
    * `at - 20 min` running, still open.

    Tags across the whole of it; two brix readings five minutes after the
    changeover, one of them out of specification.
    """
    unit = masterdata.get_equipment(session, "MIX01")
    at = utcnow()
    unnamed_from = at - timedelta(minutes=70)
    unnamed_to = at - timedelta(minutes=65)
    setup_from = at - timedelta(minutes=50)
    setup_to = setup_from + timedelta(minutes=SETUP_MINUTES)
    down_from = at - timedelta(minutes=30)
    down_to = down_from + timedelta(minutes=10)

    # The plant's own vocabulary, so the code on the interval has a word.
    reasons.define(session, code="changeover", name="Changeover",
                   description="Product change on the machine.", actor="ADMIN")
    reasons.approve(session, "changeover", 1, actor="ADMIN")
    session.flush()

    _order(session, BEFORE_ORDER, started_at=at - timedelta(hours=3),
           completed_at=setup_from - timedelta(seconds=30))
    _order(session, AFTER_ORDER, started_at=setup_to + timedelta(minutes=1))
    _book(session, unit, BEFORE_ORDER, at=setup_from - timedelta(minutes=10), good=500.0)
    _book(session, unit, AFTER_ORDER, at=setup_to + timedelta(minutes=2), good=20.0)

    for state, started, ended, reason in (
            (EquipmentStateName.RUNNING, at - timedelta(hours=3), unnamed_from, None),
            (EquipmentStateName.DOWN, unnamed_from, unnamed_to, None),
            (EquipmentStateName.RUNNING, unnamed_to, setup_from, None),
            (EquipmentStateName.SETUP, setup_from, setup_to, None),
            (EquipmentStateName.RUNNING, setup_to, down_from, None),
            (EquipmentStateName.DOWN, down_from, down_to,
             "planned maintenance MO-CHILL"),
            (EquipmentStateName.RUNNING, down_to, None, None)):
        session.add(EquipmentState(equipment_id=unit.id, state=state, reason=reason,
                                   started_at=started, ended_at=ended))
    session.flush()

    # Somebody names the changeover afterwards, over the window they chose -
    # the write the panel's control posts to, not a second way to do it.
    equipment_service.name_stops(
        session, equipment_code="MIX01", start=setup_from, end=setup_to,
        reason_code="changeover", reason="Changeover", actor="ANNA")

    session.add(MaintenanceOrder(
        code="MO-CHILL", equipment_id=unit.id, kind=MaintenanceKind.PREVENTIVE,
        status=MaintenanceStatus.IN_PROGRESS, summary="Clean the chiller condenser",
        raised_at=at - timedelta(hours=6), started_at=down_from - timedelta(minutes=2),
        performed_by="NILS"))

    moment = setup_from - timedelta(minutes=15)
    while moment <= setup_to + timedelta(minutes=5):
        before = moment < setup_from
        session.add(TagValue(equipment_id=unit.id, tag="MIX01.Temperature", ts=moment,
                             value_num=TEMP_BEFORE if before else TEMP_AFTER))
        session.add(TagValue(equipment_id=unit.id, tag="MIX01.CycleTimeMs", ts=moment,
                             value_num=CYCLE_BEFORE if before else CYCLE_AFTER))
        moment += timedelta(seconds=SAMPLE_SECONDS)

    for value in (14.0, 11.0):
        check, _nc, _signals = quality.record_check(
            session, material_code="FG-COLA", characteristic="brix", value=value,
            equipment_code="MIX01", actor="OP-DAY")
        check.ts = setup_to + timedelta(minutes=5)
    session.flush()
    return {"unit": unit, "now": at, "setup": (setup_from, setup_to),
            "unnamed": (unnamed_from, unnamed_to), "down": (down_from, down_to),
            "running": (setup_to, down_from)}


@pytest.fixture()
def out(session, shift):
    """The dossier behind the changeover block, as the panel asks for it."""
    start, end = shift["setup"]
    return state_block.dossier(session, "MIX01", start, end)


# ------------------------------------------------------ what it was, how long


def test_the_block_says_what_it_was_how_long_and_out_of_how_many_records(out, shift):
    start, end = shift["setup"]
    block = out["block"]
    assert block["state"] == "setup"
    assert (block["started_at"], block["ended_at"]) == (start, end)
    assert block["seconds"] == SETUP_MINUTES * 60.0
    assert block["records_total"] == 1 and block["records_shown"] == 1
    assert block["open"] is False
    assert block["runs_back_before_window"] is False
    assert block["runs_on_after_window"] is False
    # One record and one state reads as nothing: the note is for the reader who
    # clicked something that is not one record.
    assert block["note"] is None
    assert block["seconds_by_state"] == {"setup": SETUP_MINUTES * 60.0}
    assert out["equipment"] == {"code": "MIX01", "name": "Mixer 01"}
    assert out["window"]["seconds"] == SETUP_MINUTES * 60.0


def test_a_block_folded_from_several_records_says_so_rather_than_naming_one(
        session, shift):
    """`_merge_short` folds records too brief to draw into the block before
    them, so what a reader clicks can be a stretch of time holding several
    states. A panel that answered about one of them would be about something
    other than what was clicked."""
    unnamed_from, unnamed_to = shift["unnamed"]
    out = state_block.dossier(session, "MIX01", unnamed_from - timedelta(minutes=2),
                              unnamed_to + timedelta(minutes=2))
    block = out["block"]
    assert block["records_total"] == 3
    assert sorted(block["seconds_by_state"]) == ["down", "running"]
    assert block["seconds_by_state"]["down"] == 300.0
    assert "3 records folded together" in block["note"]
    assert "a stretch of time rather than one record" in block["note"]


# ----------------------------------------------------------- why, and who said so


def test_the_reason_the_floor_gave_it_arrives_with_the_person_who_named_it(out):
    why = out["why"]
    assert why["reason"] == "Changeover"
    assert why["reason_code"] == "changeover"
    # The plant's word for the code, out of the vocabulary in force.
    assert why["reason_label"] == "Changeover"
    # Null is this MES itself: somebody here chose the label from this plant's
    # own list, which is the whole reason the person has to come from elsewhere.
    assert why["reason_source"] is None
    assert why["records_with_a_reason"] == 1 and why["records_total"] == 1

    named = why["labelled_by"]
    assert len(named) == 1
    assert named[0]["actor"] == "ANNA"
    assert named[0]["action"] == "equipment.stop_labelled"
    assert named[0]["reason_code"] == "changeover"
    assert named[0]["intervals"] == 1
    assert "overlaps the block" in named[0]["matched_by"]
    assert why["says"].startswith("Changeover — named by ANNA at ")
    assert "not on the interval" in why["derived"]


def test_a_stop_nobody_has_named_says_so_and_offers_the_name_for_that_stop_only(
        session, shift):
    """One act of labelling named the changeover and nothing else. The stop an
    hour earlier is still unnamed, and *unnamed* is the answer rather than a
    blank where a reason would go."""
    out = state_block.dossier(session, "MIX01", *shift["unnamed"])
    why = out["why"]
    assert why["reason"] is None and why["reason_code"] is None
    assert why["labelled_by"] == []
    assert why["says"].startswith("nobody has said why yet")
    # And the control would land on exactly this block's one record.
    assert why["labelling"]["would_name"] == 1
    assert why["labelling"]["capability"] == "equipment.state"


def test_a_running_block_has_no_stop_to_explain_and_nothing_to_name(session, shift):
    out = state_block.dossier(session, "MIX01", *shift["running"])
    assert out["block"]["state"] == "running"
    assert out["why"]["says"] == (
        "the machine was running across this block, so there is no stop to explain")
    assert out["why"]["labelling"]["would_name"] == 0
    assert out["why"]["labelling"]["note"] == (
        "the machine was running, and a running observation is never labelled")
    assert out["maintenance"]["planned"] is None


def test_who_may_name_a_stop_is_answered_whether_or_not_the_reader_may(
        session, shift):
    """`refusal-names-who-can`: a panel that hid the control and said nothing
    would be the product keeping a rule it had not told anybody about."""
    start, end = shift["setup"]
    refused = state_block.dossier(session, "MIX01", start, end, may_label=False)
    assert refused["why"]["labelling"]["may"] is False
    assert "can" in refused["why"]["labelling"]["who_may"]
    assert refused["why"]["labelling"]["what"] == (
        "Set a machine's state and label its downtime")
    # Null is "the read was not told who is asking" - a CLI, a test, an agent -
    # and is not the same answer as "no".
    assert state_block.dossier(
        session, "MIX01", start, end)["why"]["labelling"]["may"] is None


# ---------------------------------------------------------- what it belonged to


def test_the_order_either_side_of_a_changeover_is_read_off_the_records_and_says_so(
        out):
    orders = out["orders"]
    assert orders["before"]["order"] == BEFORE_ORDER
    assert orders["after"]["order"] == AFTER_ORDER
    assert orders["before"]["material"] == "FG-COLA"
    assert orders["before"]["booked"]["good_qty"] == 500.0
    assert orders["before"]["operation"]["order"] == BEFORE_ORDER
    assert orders["after"]["operation"]["status"] == "running"
    assert orders["before"]["operation_names_another_order"] is None
    # Nothing booked inside the four minutes, and no step was open across them:
    # that is what a changeover looks like from the records.
    assert orders["during"] == [] and orders["during_total"] == 0
    assert orders["booked_during"] == [] and orders["booked_during_total"] == 0
    assert orders["searched_hours"] == state_block.ORDER_HOURS
    assert "derived from what was running, not a recorded link" in orders["derived"]


def test_where_the_booking_and_the_step_name_different_orders_both_are_reported(
        session, shift):
    """A changeover is exactly where the two kinds of record disagree, and
    picking one silently would make the panel's cleanest sentence its least
    trustworthy one."""
    start, end = shift["setup"]
    _order(session, "4713", started_at=start - timedelta(minutes=20),
           completed_at=start - timedelta(seconds=10))
    out = state_block.dossier(session, "MIX01", start, end)
    before = out["orders"]["before"]
    assert before["order"] == BEFORE_ORDER          # what the machine booked
    assert before["operation"]["order"] == "4713"   # what a step record says
    assert before["operation_names_another_order"] == "4713"


def test_an_order_still_open_across_the_block_is_not_reported_as_a_disagreement(
        session, shift):
    """The chiller stop, and the reason this check exists: the last operation
    record to COMPLETE before that stop is 4711, while the machine was booking
    4712 - and 4712's own step is open across the stop, right there under *open
    across it*. Two records naming one job are not a conflict, and saying they
    disagree would teach a reader to distrust the sentence that matters.

    Found 2026-10-11 in a browser, reading the panel the fixture below draws."""
    out = state_block.dossier(session, "MIX01", *shift["down"])
    before = out["orders"]["before"]
    assert before["order"] == AFTER_ORDER                  # what it was booking
    assert before["operation"]["order"] == BEFORE_ORDER    # the step that finished
    assert [row["order"] for row in out["orders"]["during"]] == [AFTER_ORDER]
    assert before["operation_names_another_order"] is None
    # And the words only claim the two agree where they do.
    assert before["from"] == "a quantity this machine booked before the block"
    assert out["orders"]["after"] is None


# ------------------------------------------------ planned work, or a breakdown


def test_a_planned_stop_reads_as_planned_work_by_a_named_person_for_this_order(
        session, shift):
    """The question the handoff asks: does a planned down block read as the job
    it was, or as a breakdown with a note attached?"""
    out = state_block.dossier(session, "MIX01", *shift["down"])
    maintenance = out["maintenance"]
    assert maintenance["planned"] is True
    assert maintenance["in_progress"] == ["MO-CHILL"]
    assert maintenance["named_in_the_reason"] == ["MO-CHILL"]
    assert maintenance["never_started"] == []
    says = maintenance["says"]
    assert says.startswith("planned work: this stop's own reason names MO-CHILL")
    assert "preventive maintenance order that is in_progress" in says
    assert "done by NILS" in says
    assert says.endswith("This is the job, not a breakdown")


def test_the_maintenance_orders_are_the_other_panels_own_list_and_not_a_second_read(
        session, shift):
    """Identity, not arithmetic: the panel beside the control chart and the
    panel beside the timeline must not be able to list one order two ways."""
    start, end = shift["down"]
    out = state_block.dossier(session, "MIX01", start, end)
    unit = shift["unit"]
    same = spc_point.maintenance_orders(session, unit, start, end)
    assert out["maintenance"]["orders"] == same["orders"]
    assert out["maintenance"]["total"] == same["total"]


def test_an_order_raised_and_never_started_is_not_what_stopped_the_machine(
        session, shift):
    """A stop explained by an order nobody started would explain every stop."""
    session.add(MaintenanceOrder(
        code="MO-WAIT", equipment_id=shift["unit"].id, kind=MaintenanceKind.PREVENTIVE,
        status=MaintenanceStatus.DUE, summary="Belt check, waiting for a stop",
        raised_at=shift["now"] - timedelta(hours=8)))
    session.flush()
    out = state_block.dossier(session, "MIX01", *shift["unnamed"])
    assert out["maintenance"]["planned"] is False
    assert out["maintenance"]["never_started"] == ["MO-WAIT"]
    assert out["maintenance"]["says"].startswith("not this work: MO-WAIT")


def test_an_order_that_only_touches_the_window_is_not_said_to_have_been_the_stop(
        session, shift):
    """MO-CHILL is open across this part of the shift and was started half an
    hour after this stop ended. Touching the window is not an explanation."""
    out = state_block.dossier(session, "MIX01", *shift["unnamed"])
    assert [o["code"] for o in out["maintenance"]["orders"]] == ["MO-CHILL"]
    assert out["maintenance"]["in_progress"] == []
    assert out["maintenance"]["planned"] is False
    assert out["maintenance"]["says"].startswith(
        "maintenance orders touch this window but none was in progress")


def test_corrective_work_across_a_stop_is_the_repair_of_it_and_not_planned_work(
        session, shift):
    """A corrective order is raised *after* something broke. An order being
    worked across a stop is therefore not proof that anybody planned the stop -
    it is usually proof of the opposite, and the panel has to say which."""
    session.add(MaintenanceOrder(
        code="MO-JAM", equipment_id=shift["unit"].id, kind=MaintenanceKind.CORRECTIVE,
        status=MaintenanceStatus.IN_PROGRESS, summary="Clear the jam",
        raised_at=shift["unnamed"][0],
        started_at=shift["unnamed"][0] + timedelta(minutes=1), performed_by="MARCO"))
    session.flush()
    out = state_block.dossier(session, "MIX01", *shift["unnamed"])
    maintenance = out["maintenance"]
    assert maintenance["in_progress"] == ["MO-JAM"]       # reported, as what it is
    assert maintenance["planned"] is False
    says = maintenance["says"]
    assert says.startswith("MO-JAM, a corrective order that is in_progress, was in "
                           "progress on this machine across this block (Clear the jam)")
    assert says.endswith("corrective work is raised after something broke, so this "
                         "reads as the repair of this stop and not as planned work")


def test_a_planned_job_this_plant_says_needs_no_stop_is_not_what_stopped_the_line(
        session, shift):
    """Found 2026-10-11 on a replayed shift: a preventive order with `needs_stop`
    false was worked for seven minutes while the machine ran. Offered as the
    reason for a stop it would read as planned work, which is the one mistake
    this panel exists to avoid. #158 put the fact in the data; use it."""
    session.add(MaintenanceOrder(
        code="MO-GREASE", equipment_id=shift["unit"].id,
        kind=MaintenanceKind.PREVENTIVE, status=MaintenanceStatus.IN_PROGRESS,
        summary="Grease the bearings", needs_stop=False,
        raised_at=shift["now"] - timedelta(hours=4),
        started_at=shift["unnamed"][0] - timedelta(minutes=1), performed_by="AMIRA"))
    session.flush()
    out = state_block.dossier(session, "MIX01", *shift["unnamed"])
    maintenance = out["maintenance"]
    assert maintenance["in_progress"] == ["MO-GREASE"]
    assert maintenance["planned"] is False
    says = maintenance["says"]
    assert says.startswith("MO-GREASE, a preventive order that is in_progress, was in "
                           "progress on this machine across this block "
                           "(Grease the bearings), but this plant says that job does "
                           "not need the line stopped")
    assert says.endswith("so it is not what stopped it, and the stop's own reason does "
                         "not name it")


def test_a_reason_naming_an_order_worked_later_does_not_claim_the_order_was_unstarted(
        session, shift):
    """The down block's reason names MO-CHILL. If that order's own record puts
    the work an hour after the block, the honest sentence is that the record
    does not place the work here - not that the order was never started, which
    would be false of a job somebody did."""
    order = session.query(MaintenanceOrder).filter_by(code="MO-CHILL").one()
    order.started_at = shift["down"][1] + timedelta(hours=1)
    session.flush()
    out = state_block.dossier(session, "MIX01", *shift["down"])
    maintenance = out["maintenance"]
    assert maintenance["named_in_the_reason"] == ["MO-CHILL"]
    assert maintenance["in_progress"] == []
    assert maintenance["never_started"] == []
    assert maintenance["planned"] is False
    assert maintenance["says"] == (
        "this stop's own reason names maintenance order MO-CHILL, and that order's own "
        "record places its work outside this block — so the reason says planned work "
        "and the order record does not put that work here")


def test_a_stretch_with_no_record_of_any_kind_is_neither_running_nor_a_breakdown(
        session, shift):
    """The hardest sentence in the panel to get right. A window this MES holds
    nothing about must not read as a machine that was running, and must not
    read as a stop nobody explained either."""
    start = shift["now"] - timedelta(hours=7)
    out = state_block.dossier(session, "MIX01", start, start + timedelta(minutes=10))
    assert out["block"]["state"] is None
    assert out["why"]["says"].startswith(
        "this MES holds no state record across this stretch")
    assert out["why"]["labelling"]["note"] == (
        "there is no stop here to name: this MES holds no state record across this "
        "stretch at all")
    assert out["maintenance"]["orders"] == []
    assert out["maintenance"]["says"] == (
        "this MES holds no state record across this stretch, so there is no stop "
        "here to place against a maintenance order")
    assert out["coverage"] == 0.0


# --------------------------------------------- what the machine was doing either side


def test_the_tags_either_side_are_the_spc_panels_own_read_through_the_same_bucketing(
        session, out, shift):
    start, end = shift["setup"]
    unit = shift["unit"]
    same = spc_point.analog_trends(
        session, unit,
        start - timedelta(minutes=spc_point.BEFORE_MINUTES),
        end + timedelta(minutes=spc_point.AFTER_MINUTES),
        [{"t": start, "label": "the block starts"},
         {"t": end, "label": "the block ends"}])
    assert out["tags"] == same
    drawn = [t for t in out["tags"]["trends"] if t["tag"] == "Temperature"]
    assert len(drawn) == 1
    # The line where the block starts is on the chart, which is the whole
    # reason a reader can see a step rather than be told about one.
    assert [m["label"] for m in drawn[0]["markers"]] == [
        "the block starts", "the block ends"]
    assert drawn[0]["markers"][0]["t"] == start
    # And the tag fabric is not drawn as the process: `CycleTimeMs` is in this
    # window and is not one of the small charts, on this panel or on the one
    # beside the control chart.
    assert [t["tag"] for t in out["tags"]["trends"]] == ["Temperature"]


def test_the_record_either_side_of_the_block_is_reported_and_null_is_not_stillness(
        out, shift):
    either = out["either_side"]
    assert either["before"]["state"] == "running"
    assert either["before"]["seconds_from_the_block"] == 0.0
    assert either["after"]["state"] == "running"
    assert either["after"]["seconds_from_the_block"] == 0.0
    assert either["after"]["open"] is False
    assert "not the machine standing still" in either["note"]


def test_before_the_first_record_this_mes_holds_there_is_no_side_to_report(
        session, shift):
    """Null here is this MES holding nothing, and the panel says that rather
    than drawing a machine that stood still before it was installed."""
    out = state_block.dossier(session, "MIX01",
                              shift["now"] - timedelta(hours=5),
                              shift["now"] - timedelta(hours=4))
    assert out["either_side"]["before"] is None
    assert out["block"]["state"] is None
    assert "no state record across that stretch" in out["block"]["note"]


# ------------------------------------------------------- what quality saw after it


def test_what_quality_saw_after_the_block_carries_the_charts_own_verdict(out):
    q = out["quality"]
    assert q["total"] == 2 and q["shown"] == 2
    assert q["minutes"] == state_block.QUALITY_AFTER_MINUTES
    assert q["failed"] == 1
    first = q["readings"][0]
    assert first["characteristic"] == "brix"
    assert first["material"] == "FG-COLA"
    assert first["value"] == 14.0
    assert first["result"] == "fail"
    assert first["above_spec"] is True and first["below_spec"] is False
    assert (first["lower_spec"], first["upper_spec"]) == (9.5, 11.5)
    assert first["checked_by"] == "OP-DAY"
    assert first["seconds_after_the_block"] == 300.0
    # Two readings cannot make a control chart, so no rule fired - and an empty
    # list is "no rule fired", never "no rules were checked".
    assert first["signals"] == []
    assert q["with_a_signal"] == 0
    assert "a reading taken by a person with a gauge records none" in q["scope"]


# --------------------------------------------------------------------- coverage


def test_the_block_states_the_watched_share_of_its_own_stretch(out):
    assert out["coverage"] == 1.0
    assert out["ledger"]["equipment"] == "MIX01"
    assert "how much of the block's own stretch" in out["coverage_note"]


def test_a_stretch_nobody_was_watching_is_not_a_stop_with_no_reason(session, shift):
    """Decision 0030: a disconnection is drawn as its own hatched block, so a
    click can land on one. The answer then is *nobody was looking*, which is a
    different sentence from a stop nobody has named."""
    unit = shift["unit"]
    start = shift["now"] - timedelta(hours=4)
    end = start + timedelta(minutes=10)
    session.add(EquipmentConnection(
        equipment_id=unit.id, state=ConnectionStateName.DISCONNECTED,
        started_at=start, detected_at=start + timedelta(seconds=30), ended_at=end,
        reason="the agent stopped"))
    session.flush()
    out = state_block.dossier(session, "MIX01", start, end)
    assert out["block"]["state"] == "disconnected"
    assert out["block"]["records_total"] == 1
    assert out["why"]["says"].startswith("this MES lost sight of the machine")
    assert "nobody was looking" in out["why"]["says"]
    assert "a disconnection is not a stop anybody can name" in (
        out["why"]["labelling"]["note"])
    assert out["coverage"] == 0.0


def test_every_list_in_the_panel_states_its_own_total(out):
    """House rule 3, on each block that is a list."""
    assert out["block"]["records_shown"] <= out["block"]["records_total"]
    assert out["quality"]["shown"] <= out["quality"]["total"]
    assert out["tags"]["shown"] <= out["tags"]["total"]
    assert out["orders"]["during_total"] == len(out["orders"]["during"])
    assert len(out["maintenance"]["orders"]) <= out["maintenance"]["total"]


def test_a_block_that_ends_before_it_starts_is_refused_rather_than_drawn(
        session, shift):
    start, _end = shift["setup"]
    with pytest.raises(Invalid):
        state_block.dossier(session, "MIX01", start, start)
    with pytest.raises(Invalid):
        state_block.dossier(session, "MIX01", start, start - timedelta(minutes=1))


# ------------------------------------------------------------------- the route


def _ask(client, shift, which="setup", **params):
    start, end = shift[which]
    return client.get("/analysis/timeline/block/MIX01",
                      params={"start": start.isoformat(), "end": end.isoformat(),
                              **params})


def test_the_route_answers_for_one_block_and_asks_who_is_asking(anon, client, shift):
    assert _ask(anon, shift).status_code == 401
    response = _ask(client, shift)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["block"]["state"] == "setup"
    assert body["why"]["reason"] == "Changeover"
    assert body["orders"]["before"]["order"] == BEFORE_ORDER
    assert body["orders"]["after"]["order"] == AFTER_ORDER
    assert body["coverage"] == 1.0


def test_the_route_says_whether_the_reader_may_name_the_stop_and_who_may_if_not(
        client, sign_in, shift):
    """The operator holds `equipment.state`; a viewer does not, and the answer
    that refuses names who can rather than leaving a dead control on screen."""
    mine = _ask(client, shift).json()["why"]["labelling"]
    assert mine["may"] is True

    viewer = sign_in("LOOKER", role="viewer")
    refused = _ask(viewer, shift).json()["why"]["labelling"]
    assert refused["may"] is False
    assert "can" in refused["who_may"]
    assert refused["capability"] == "equipment.state"


def test_the_route_takes_the_minutes_either_side_and_refuses_a_backwards_window(
        client, shift):
    body = _ask(client, shift, before_minutes=30, after_minutes=5,
                quality_minutes=15).json()
    assert body["window"]["before_minutes"] == 30
    assert body["window"]["after_minutes"] == 5
    assert body["quality"]["minutes"] == 15

    start, _end = shift["setup"]
    backwards = client.get("/analysis/timeline/block/MIX01",
                           params={"start": start.isoformat(), "end": start.isoformat()})
    assert backwards.status_code == 400
    assert "later than" in backwards.json()["detail"]


def test_a_machine_this_plant_does_not_have_is_a_not_found_and_not_an_empty_panel(
        client, shift):
    start, end = shift["setup"]
    response = client.get("/analysis/timeline/block/NOPE01",
                          params={"start": start.isoformat(), "end": end.isoformat()})
    assert response.status_code == 404


# ------------------------------------------------- link 1 of the chain, walked


def test_link_one_of_the_chain_walks_from_the_block_to_its_word_its_person_and_both_orders(
        out, shift):
    """The walk the product's own answer key says a person cannot make today.

    `labs/multiplant/bottling/line.json` -> `_chain` -> link 1: *"On the order
    book and the state timeline: a four-minute setup at 4:40, labelled
    Changeover by the floor, and from the moment it ends the filler's cycle
    time sits about 33 ms shorter than it did all morning - the same machine, a
    faster product."*

    One click, one read, and every clause of that sentence is answered off
    records: the four minutes, the word, the person, the order handed over, and
    the step in the tag with a line where the block starts. Nothing here is a
    model's opinion and nothing is a second arithmetic.
    """
    start, end = shift["setup"]

    # A four-minute setup.
    assert out["block"]["state"] == "setup"
    assert out["block"]["seconds"] == 240.0
    assert (end - start) == timedelta(minutes=4)

    # Labelled Changeover, by the floor - and the floor has a name.
    assert out["why"]["reason"] == "Changeover"
    assert out["why"]["labelled_by"][0]["actor"] == "ANNA"

    # The hand-over: 4711 before it, 4712 after.
    assert out["orders"]["before"]["order"] == "4711"
    assert out["orders"]["after"]["order"] == "4712"

    # And the step in the machine's own tag, with the block marked on it. Not
    # the chain's `CycleTimeMs`: the kernel classes that one as the plant's
    # fabric and neither analog panel draws it, so what this panel shows is
    # the process tags either side and the line where the block begins.
    tag = next(t for t in out["tags"]["trends"] if t["tag"] == "Temperature")
    drawn = [p["mean"] for p in tag["points"] if p["mean"] is not None]
    assert drawn[0] == TEMP_BEFORE and drawn[-1] == TEMP_AFTER
    assert tag["markers"][0]["label"] == "the block starts"
    assert tag["markers"][0]["t"] == start
