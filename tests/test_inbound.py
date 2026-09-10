"""Events that reach the MES from another system: the contract and the writer.

The rule under all of these is that being told something is not the same as
having seen it. A supplied label goes on a stop this MES observed and never
creates one; a supplied count is booked and never guessed into an order; a
supplied verdict is kept beside this MES's own rather than replacing it. And
every one of them says which system supplied it, on the row with the number.
"""

from datetime import UTC, datetime, timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import (
    CheckResult,
    EquipmentStateName,
    Gauge,
    InboundKind,
    ProductionLog,
    ProductionSource,
    QualityCheck,
)
from fsmes.integrations.inbound.contract import DowntimeLabel, ManualCount, QualityResult
from fsmes.services import analysis, equipment, execution, inbound, workorders

SOURCE = "replay:incumbent-mes"


def label(**kwargs) -> DowntimeLabel:
    base = dict(source=SOURCE, source_kind="replay", external_key="stop-1",
                recorded_at=datetime(2026, 9, 10, 17, 0), equipment="MIX01",
                started_at=datetime(2026, 9, 10, 9, 12), ended_at=datetime(2026, 9, 10, 9, 31),
                reason="blade change")
    return DowntimeLabel(**{**base, **kwargs})


def count(**kwargs) -> ManualCount:
    base = dict(source=SOURCE, source_kind="replay", external_key="entry-1",
                recorded_at=datetime(2026, 9, 10, 17, 0), equipment="MIX01", good=3.0, scrap=1.0)
    return ManualCount(**{**base, **kwargs})


def result(**kwargs) -> QualityResult:
    base = dict(source=SOURCE, source_kind="replay", external_key="check-1",
                recorded_at=datetime(2026, 9, 10, 17, 0), characteristic="brix", value=10.0)
    return QualityResult(**{**base, **kwargs})


@pytest.fixture()
def released_order(session):
    workorders.create(session, code="WO-1", material_code="FG-COLA", quantity=4, actor="test")
    workorders.release(session, "WO-1", "test")
    return session


@pytest.fixture()
def observed_stop(session):
    """A stop this MES watched and could not explain — the usual starting point."""
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.DOWN, actor="opc-agent")
    state = equipment.current_states(session)[0]
    state.started_at = datetime(2026, 9, 10, 9, 10)
    state.ended_at = datetime(2026, 9, 10, 9, 35)
    session.flush()
    return state


# ------------------------------------------------------------------ contract


def test_an_event_that_cannot_say_who_supplied_it_is_not_accepted():
    with pytest.raises(ValueError, match="source"):
        label(source="   ")


def test_an_event_with_no_key_of_its_own_is_not_accepted():
    # Without the supplier's key there is no way to tell the second delivery
    # from the first, so there is no way to be idempotent.
    with pytest.raises(ValueError, match="external_key"):
        label(external_key="")


def test_a_blank_reason_is_not_a_label():
    with pytest.raises(ValueError, match="unlabelled"):
        label(reason="  ")


def test_a_reading_attached_to_neither_a_machine_nor_an_order_is_not_accepted():
    with pytest.raises(ValueError, match="nothing to attach"):
        result()


def test_a_time_zone_is_removed_but_the_moment_is_not_moved():
    supplied = label(started_at=datetime(2026, 9, 10, 9, 12, tzinfo=UTC))
    assert supplied.started_at == datetime(2026, 9, 10, 9, 12)


def test_a_count_that_does_not_say_when_the_units_were_made_uses_the_suppliers_own_clock():
    # Never this MES's clock: a file read at five o'clock is not evidence
    # that anything happened at five o'clock.
    supplied = count()
    assert supplied.made_at == supplied.recorded_at


# ------------------------------------------------------------- downtime labels


def test_a_supplied_label_names_a_stop_this_mes_observed(session, observed_stop):
    outcome = inbound.record_downtime_label(session, label())

    assert outcome.applied and not outcome.duplicate
    assert observed_stop.reason == "blade change"
    assert observed_stop.reason_source == SOURCE
    assert "blade change" in outcome.detail


def test_a_label_for_a_stop_this_mes_never_saw_is_refused_and_invents_nothing(session):
    before = len(equipment.current_states(session))
    with pytest.raises(inbound.Refused, match="observed no unlabelled stop"):
        inbound.record_downtime_label(session, label())
    assert len(equipment.current_states(session)) == before


def test_a_supplied_label_never_overwrites_one_given_here(session, observed_stop):
    observed_stop.reason = "jam, said so here"
    session.flush()
    with pytest.raises(inbound.Refused):
        inbound.record_downtime_label(session, label())
    assert observed_stop.reason == "jam, said so here"
    assert observed_stop.reason_source is None


def test_the_same_label_delivered_twice_changes_nothing(session, observed_stop):
    first = inbound.record_downtime_label(session, label())
    second = inbound.record_downtime_label(session, label())

    assert first.applied and not second.applied
    assert second.duplicate
    assert observed_stop.reason == "blade change"


def test_the_pareto_says_which_system_named_each_stop(session, observed_stop):
    inbound.record_downtime_label(session, label())
    pareto = analysis.downtime_pareto(session, hours=100000)
    named = next(b for b in pareto["reasons"] if b["reason"] == "blade change")
    assert list(named["labelled_by"]) == [SOURCE]


# --------------------------------------------------------------------- counts


def test_a_supplied_count_books_against_an_open_operation_and_says_where_it_came_from(
        session, released_order):
    outcome = inbound.record_manual_count(session, count(order="WO-1", good=2.0, scrap=0.0))

    assert outcome.entity_id == "WO-1"
    rows = session.query(ProductionLog).all()
    assert [(row.source, row.source_system) for row in rows] == [(ProductionSource.EXTERNAL, SOURCE)]


def test_a_supplied_count_with_no_order_open_here_is_kept_as_unassigned_production(session):
    # The system that took the count had the order; this one does not. The
    # units were still made, so they are kept rather than refused.
    outcome = inbound.record_manual_count(session, count())

    assert outcome.applied
    listing = execution.unassigned_production(session)
    assert listing["good_total"] == 3.0
    assert listing["items"][0]["source_system"] == SOURCE
    assert listing["items"][0]["source"] == "external"


def test_a_supplied_count_is_stamped_with_the_suppliers_time_not_ours(session):
    made = datetime(2026, 9, 10, 9, 40)
    inbound.record_manual_count(session, count(when=made))
    assert session.query(ProductionLog).one().ts == made


def test_a_count_naming_an_order_this_mes_cannot_open_is_kept_against_the_machine(session):
    # WO-2 exists here but was never released, so nothing books against it.
    workorders.create(session, code="WO-2", material_code="FG-COLA", quantity=4, actor="test")
    outcome = inbound.record_manual_count(session, count(order="WO-2"))

    assert outcome.entity_id == "MIX01"
    assert "WO-2" in outcome.detail
    assert execution.unassigned_production(session)["good_total"] == 3.0


def test_a_count_for_a_machine_this_mes_does_not_have_is_refused(session):
    with pytest.raises(inbound.Refused, match="NOPE"):
        inbound.record_manual_count(session, count(equipment="NOPE"))


def test_the_same_count_delivered_twice_books_once(session, released_order):
    inbound.record_manual_count(session, count(order="WO-1", good=2.0, scrap=0.0))
    second = inbound.record_manual_count(session, count(order="WO-1", good=2.0, scrap=0.0))

    assert second.duplicate
    assert session.query(ProductionLog).count() == 1


def test_two_different_events_may_share_a_key_because_suppliers_number_them_apart(
        session, observed_stop):
    # An incumbent whose downtime rows and count rows both start at 1 is
    # ordinary; treating those as the same event would drop half the data.
    inbound.record_downtime_label(session, label(external_key="1"))
    outcome = inbound.record_manual_count(session, count(external_key="1"))

    assert outcome.applied and not outcome.duplicate
    assert outcome.kind is InboundKind.MANUAL_COUNT


# ------------------------------------------------------------------- quality


def test_a_supplied_reading_is_judged_by_this_mess_own_spec(session, released_order):
    outcome = inbound.record_quality_result(session, result(order="WO-1", value=10.0))

    check = session.get(QualityCheck, int(outcome.entity_id))
    assert check.result is CheckResult.PASS
    assert check.source_system == SOURCE


def test_both_verdicts_are_kept_when_the_two_systems_disagree(session, released_order):
    # 12.0 is outside this MES's 9.5-11.5 spec; the supplier called it a pass.
    outcome = inbound.record_quality_result(session, result(order="WO-1", value=12.0, passed=True))

    check = session.get(QualityCheck, int(outcome.entity_id))
    assert check.result is CheckResult.FAIL
    assert check.supplied_result is CheckResult.PASS
    assert "calls it fail" in outcome.detail


def test_a_reading_with_no_spec_here_is_refused_rather_than_measured_against_a_guess(
        session, released_order):
    with pytest.raises(inbound.Refused, match="no quality spec"):
        inbound.record_quality_result(session, result(order="WO-1", characteristic="viscosity"))


def test_a_reading_naming_only_a_machine_with_nothing_running_is_refused(session):
    with pytest.raises(inbound.Refused, match="is not guessed"):
        inbound.record_quality_result(session, result(equipment="MIX01"))


def test_a_reading_naming_only_a_machine_finds_the_order_running_there(session, released_order):
    execution.report(session, equipment_code="MIX01", good=1, source=ProductionSource.OPC)
    outcome = inbound.record_quality_result(session, result(equipment="MIX01"))
    assert outcome.applied


def test_an_instrument_this_mes_does_not_know_is_reported_not_invented(session, released_order):
    outcome = inbound.record_quality_result(session, result(order="WO-1", gauge="BRIX-99"))

    assert session.query(Gauge).filter(Gauge.code == "BRIX-99").one_or_none() is None
    assert session.get(QualityCheck, int(outcome.entity_id)).gauge_id is None
    assert "untraceable here" in outcome.detail


def test_an_inspector_is_recorded_as_the_opaque_id_the_supplier_sent(session, released_order):
    outcome = inbound.record_quality_result(session, result(order="WO-1", inspector="B-4471"))

    assert session.get(QualityCheck, int(outcome.entity_id)).checked_by == "B-4471"


def test_the_window_a_label_covers_is_not_stretched_to_now(session):
    # A stop that ended long before the labelled window is not touched.
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.DOWN, actor="opc-agent")
    state = equipment.current_states(session)[0]
    state.started_at = datetime(2026, 9, 9, 3, 0)
    state.ended_at = datetime(2026, 9, 9, 3, 20)
    session.flush()

    with pytest.raises(inbound.Refused):
        inbound.record_downtime_label(session, label())
    assert state.reason is None


def test_an_open_stop_is_labelled_when_the_supplier_says_the_stop_is_still_open(session):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.DOWN, actor="opc-agent")
    state = equipment.current_states(session)[0]
    state.started_at = utcnow() - timedelta(minutes=5)
    session.flush()

    inbound.record_downtime_label(session, label(started_at=state.started_at, ended_at=None))
    assert state.reason == "blade change"
