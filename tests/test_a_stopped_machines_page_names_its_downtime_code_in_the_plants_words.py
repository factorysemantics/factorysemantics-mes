"""A machine that stops on a downtime code says why in the plant's own words.

The OPC agent records what the machine said ("FaultCode 41") and, when the tag
map turns that number into one of the plant's codes, the code too. The page a
person opens on that machine used to print only the first. These tests hold
that the machine's head now carries the plant's word for the code beside the
raw value, and that it never invents one: an unmapped code keeps only what the
machine said, and a running machine has neither.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from fsmes.domain import Equipment, EquipmentStateName
from fsmes.services import equipment, reasons, tags

PRESS = "MIX01"  # the seeded test machine; it stands in for an invented press


def _unit(session) -> Equipment:
    return session.scalar(select(Equipment).where(Equipment.code == PRESS))


def _vocabulary(session) -> None:
    reasons.define(session, code="tool_a_crash", name="Tool A crash",
                   description="Tool A crashed and is looked at before it runs again.",
                   actor="ADMIN")
    reasons.approve(session, "tool_a_crash", 1, actor="ADMIN")
    session.flush()


def test_a_stop_the_tag_map_named_reads_in_the_plants_word_beside_the_machines_own(session):
    _vocabulary(session)
    equipment.set_state(session, equipment_code=PRESS, state=EquipmentStateName.DOWN,
                        reason="FaultCode 41", reason_code="tool_a_crash", actor="opc-agent")
    session.flush()

    head = tags.head(session, _unit(session))

    assert head["state"] == "down"
    assert head["reason"] == "FaultCode 41"
    assert head["reason_code"] == "tool_a_crash"
    assert head["reason_name"] == "Tool A crash"


def test_a_code_the_map_does_not_know_keeps_only_what_the_machine_said(session):
    _vocabulary(session)
    equipment.set_state(session, equipment_code=PRESS, state=EquipmentStateName.DOWN,
                        reason="FaultCode 999", reason_code=None, actor="opc-agent")
    session.flush()

    head = tags.head(session, _unit(session))

    assert head["reason"] == "FaultCode 999"
    assert head["reason_code"] is None
    assert head["reason_name"] is None


def test_a_running_machine_has_no_reason_and_no_word_for_one(session):
    equipment.set_state(session, equipment_code=PRESS, state=EquipmentStateName.RUNNING,
                        actor="opc-agent")
    session.flush()

    head = tags.head(session, _unit(session))

    assert head["state"] == "running"
    assert head["reason"] is None and head["reason_code"] is None
    assert head["reason_name"] is None


@pytest.fixture()
def supervisor(sign_in):
    return sign_in("SUP-WORDS", role="supervisor")


def test_the_machine_endpoint_serves_the_plants_word_for_the_stop(session, supervisor):
    _vocabulary(session)
    equipment.set_state(session, equipment_code=PRESS, state=EquipmentStateName.DOWN,
                        reason="FaultCode 41", reason_code="tool_a_crash", actor="opc-agent")
    session.flush()

    head = supervisor.get(f"/equipment/{PRESS}").json()

    assert (head["reason"], head["reason_name"]) == ("FaultCode 41", "Tool A crash")
