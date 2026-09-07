"""Write-back as a recommendation queue: three guards, one human, a verified outcome.

Nothing writes to a PLC without a person saying yes. These tests hold each
guard on its own (manifest, API, agent), the lifecycle, the agent's write
loop against a fake OPC node, and the verification that says whether the
process actually followed.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import AdjustmentStatus, Equipment, RecommendedAdjustment, TagValue
from fsmes.integrations.opc import agent as opc_agent
from fsmes.services import adjustments

CAT = {
    "MIX01": {
        "tags": ["State", "Temperature", "TemperatureSP", "Pressure"],
        "meta": {
            "Temperature": {"kind": "pv", "unit": "°C", "follows": "TemperatureSP", "writable": False},
            "TemperatureSP": {"kind": "sp", "unit": "°C", "min": 40.0, "max": 80.0, "drives": "Temperature",
                              "lag_s": 20.0, "writable": True},
            "Pressure": {"kind": "pv", "unit": "bar", "writable": False},
        },
        "analog": "Temperature",
    }
}


def _write(session, code, name, value, age=0.0):
    unit = session.scalar(select(Equipment).where(Equipment.code == code))
    session.add(TagValue(equipment_id=unit.id, tag=f"{code}.{name}", value_num=value,
                         ts=utcnow() - timedelta(seconds=age)))
    session.flush()


def test_guard_one_the_manifest_decides_what_may_be_written(session):
    with pytest.raises(Exception, match="not declared writable"):
        adjustments.propose(session, equipment_code="MIX01", tag="Temperature", value=60, rationale="x", cat=CAT)
    with pytest.raises(Exception, match="not in the tag manifest"):
        adjustments.propose(session, equipment_code="MIX01", tag="Nope", value=60, rationale="x", cat=CAT)
    with pytest.raises(Exception, match="outside the declared bounds"):
        adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=95, rationale="x", cat=CAT)
    with pytest.raises(Exception, match="rationale"):
        adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=60, rationale="  ", cat=CAT)


def test_a_proposal_snapshots_the_bounds_and_the_current_value_and_waits_for_a_person(session):
    _write(session, "MIX01", "TemperatureSP", 65.0)
    rec = adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=60.0,
                              rationale="fill-weight defects rise with washer temperature",
                              evidence={"checks": 40, "correlation": 0.7}, actor="AGENT", cat=CAT)
    assert rec.status is AdjustmentStatus.PROPOSED and rec.current_value == 65.0
    assert (rec.minimum, rec.maximum, rec.drives) == (40.0, 80.0, "Temperature")
    assert rec.verify_after_seconds == 60.0, "three time constants of the tag's own lag"
    assert adjustments.approved_writes(session) == [], "nothing is written until a person says yes"
    with pytest.raises(Exception, match="already open"):
        adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=58.0, rationale="x", cat=CAT)


def test_guard_two_approval_rechecks_the_snapshotted_bounds(session):
    rec = adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=60.0,
                              rationale="cool it", cat=CAT)
    rec.maximum = 55.0  # the bounds shrank after the proposal (an edited manifest, say)
    session.flush()
    with pytest.raises(Exception, match="at approval"):
        adjustments.approve(session, rec.code, actor="eng")
    rec.maximum = 80.0
    adjustments.approve(session, rec.code, note="agreed", actor="eng")
    assert rec.status is AdjustmentStatus.APPROVED and rec.decided_by == "eng"
    [pending] = adjustments.approved_writes(session)
    assert pending["code"] == rec.code and pending["value"] == 60.0 and pending["minimum"] == 40.0


class FakeNode:
    def __init__(self, value):
        self.value = value
        self.writes = []

    async def write_value(self, value):
        self.writes.append(value)
        self.value = value

    async def read_value(self):
        return self.value


def test_guard_three_the_agent_refuses_out_of_bounds_and_writes_the_rest(session, scope):
    rec = adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=60.0,
                              rationale="cool it", cat=CAT)
    adjustments.approve(session, rec.code, actor="eng")
    session.flush()
    sp, pv = FakeNode(65.0), FakeNode(64.0)
    nodes = {("MIX01", "TemperatureSP"): sp, ("MIX01", "Temperature"): pv}
    manifest = {"MIX01": {"TemperatureSP": {"min": 40.0, "max": 80.0}}}

    written = asyncio.run(opc_agent.write_approved_adjustments(nodes, manifest, scope))
    assert written == [rec.code] and sp.writes == [60.0]
    session.refresh(rec)
    assert rec.status is AdjustmentStatus.WRITTEN and rec.written_value == 60.0

    # The agent's own copy of the manifest is the last word: a narrower one refuses.
    rec2 = RecommendedAdjustment(code="ADJ-X", equipment_code="MIX01", tag="TemperatureSP", drives="Temperature",
                                 proposed_value=78.0, minimum=40.0, maximum=80.0, rationale="hot",
                                 proposed_by="t", status=AdjustmentStatus.APPROVED)
    session.add(rec2)
    session.flush()
    narrow = {"MIX01": {"TemperatureSP": {"min": 40.0, "max": 70.0}}}
    assert asyncio.run(opc_agent.write_approved_adjustments(nodes, narrow, scope)) == []
    session.refresh(rec2)
    assert rec2.status is AdjustmentStatus.FAILED and "bounds" in rec2.verification["error"]
    assert sp.writes == [60.0], "nothing else reached the node"


def test_verification_says_whether_the_process_followed(session, scope):
    rec = adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=60.0,
                              rationale="cool it", verify_after_seconds=0, cat=CAT)
    rec.current_value = 70.0
    adjustments.approve(session, rec.code, actor="eng")
    adjustments.mark_written(session, rec.code, 60.0)
    rec.written_at = utcnow() - timedelta(seconds=1)
    session.flush()
    [due] = adjustments.due_for_verification(session)
    assert due["code"] == rec.code and due["drives"] == "Temperature"
    adjustments.verify(session, rec.code, pv_before=70.0, pv_after=63.0)
    assert rec.status is AdjustmentStatus.VERIFIED and rec.verification["followed"] is True

    rec2 = adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=50.0,
                               rationale="cooler", verify_after_seconds=0, cat=CAT)
    rec2.current_value = 60.0
    adjustments.approve(session, rec2.code, actor="eng")
    adjustments.mark_written(session, rec2.code, 50.0)
    adjustments.verify(session, rec2.code, pv_before=60.0, pv_after=59.5)
    assert rec2.status is AdjustmentStatus.FAILED and rec2.verification["followed"] is False


def test_the_api_gates_proposal_and_approval_separately(session, sign_in, monkeypatch):
    monkeypatch.setattr(adjustments.tags, "catalog", lambda: CAT)
    op = sign_in("OP-ADJ", role="operator")
    body = {"equipment": "MIX01", "tag": "TemperatureSP", "value": 60, "rationale": "cool it"}
    assert op.post("/adjustments", json=body).status_code == 403
    eng = sign_in("ENG-ADJ", role="supervisor")
    r = eng.post("/adjustments", json=body)
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert eng.post(f"/adjustments/{code}/approve", json={}).status_code == 403, "approval is an admin's"
    admin = sign_in("ADM-ADJ", role="admin")
    assert admin.post(f"/adjustments/{code}/approve", json={"note": "ok"}).json()["status"] == "approved"
    approved = admin.get("/adjustments?status=approved").json()
    assert approved["items"][0]["code"] == code and approved["total"] == 1
    assert admin.get(f"/adjustments/{code}").json()["decision_note"] == "ok"
    assert admin.post("/adjustments", json={**body, "value": 999}).status_code == 400
