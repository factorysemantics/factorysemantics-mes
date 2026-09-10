"""Every remaining list at plant scale, and the query that fell over there.

The mega-factory audit (labs/megafactory/full/surfaces.py) against a
108-station plant a day old: the Floor summary took 58 seconds and exhausted
the connection pool, the retention line 27 seconds, and Master data,
Maintenance, Triggers, Adjustments, Certificates, Instructions, Gauges and
Tags each drew the whole plant with no filter and no page. What this file
pins is that the sums are done in the database, that every list that grows
with the plant or with time filters and pages, and that the counts say what
they cover.
"""

import random
import re
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import Equipment, EquipmentState, EquipmentStateName, ProductionLog, TagValue
from fsmes.services import equipment as equipment_service
from fsmes.services import masterdata, retention, workorders

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _machines(session) -> list[Equipment]:
    return equipment_service.work_units(session)


# ------------------------------------------------------------- the OEE sums


def _reference(session, ids, start, end):
    """The sum the old code did, over ORM rows, kept as the oracle."""
    out: dict = {}
    for s in session.scalars(select(EquipmentState).where(EquipmentState.equipment_id.in_(ids))):
        lo, hi = max(s.started_at, start), min(s.ended_at or end, end)
        if hi > lo:
            out.setdefault(s.equipment_id, {}).setdefault(s.state.value, 0.0)
            out[s.equipment_id][s.state.value] += (hi - lo).total_seconds()
    return out


def test_state_seconds_summed_in_the_database_match_the_python_sum(session):
    """Thousands of short intervals per machine, clipped both ends, open
    intervals included: the grouped query agrees with the reference to the
    millisecond, and never leaves the database."""
    rng = random.Random(7)
    now = utcnow()
    machines = _machines(session)
    for m in machines:
        t = now - timedelta(hours=9)
        while t < now - timedelta(seconds=5):
            length = timedelta(seconds=rng.uniform(0.5, 400))
            state = rng.choice(list(EquipmentStateName))
            session.add(EquipmentState(equipment_id=m.id, state=state, started_at=t, ended_at=t + length))
            t += length
        session.add(EquipmentState(equipment_id=m.id, state=EquipmentStateName.RUNNING, started_at=t))
    session.flush()

    start, end = now - timedelta(hours=8), now
    ids = [m.id for m in machines]
    fast = equipment_service.state_seconds(session, ids, start, end)
    slow = _reference(session, ids, start, end)
    assert set(fast) == set(slow) and len(fast) == len(machines)
    for eid in ids:
        for state in slow[eid]:
            # SQLite sums the intervals as julian-day floats, so thousands of
            # them drift a few milliseconds from the Python reference over an
            # eight-hour window. Fifty milliseconds over eight hours is the
            # honest tolerance; five was a coin flip.
            assert fast[eid][state] == pytest.approx(slow[eid][state], abs=0.05), (eid, state)
        assert sum(fast[eid].values()) == pytest.approx(8 * 3600, abs=0.01), "the window is fully accounted for"


def test_one_machines_oee_is_the_plant_answer_restricted_to_it(session):
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=now - timedelta(minutes=60), ended_at=now - timedelta(minutes=30)))
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               started_at=now - timedelta(minutes=30)))
    wo = workorders.create(session, code="WO-MANY", material_code="FG-COLA", quantity=20)
    session.add(ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=18, scrap_qty=2,
                              ts=now - timedelta(minutes=40)))
    session.flush()

    alone = equipment_service.oee(session, equipment_code="MIX01", hours=1.0)
    together = equipment_service.oee_many(session, _machines(session), hours=1.0)
    assert together["MIX01"] == alone
    assert alone["availability"] == pytest.approx(0.5, rel=0.02)
    assert alone["downtime_seconds"] == pytest.approx(1800, abs=2)
    # A machine never observed says unknown, in the batch as alone.
    assert together["PACK01"]["oee"] is None and together["PACK01"]["window_hours"] == 0


def test_production_before_a_machine_was_first_seen_is_not_counted(session):
    """The window clamps to first sight per machine; a booking from before
    that (an agent that counted before it recorded a state) stays out, in
    the batch exactly as it did one machine at a time."""
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    wo = workorders.create(session, code="WO-EARLY", material_code="FG-COLA", quantity=20)
    session.add(ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=5, scrap_qty=0,
                              ts=now - timedelta(minutes=50)))
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=now - timedelta(minutes=20)))
    session.add(ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=7, scrap_qty=0,
                              ts=now - timedelta(minutes=10)))
    session.flush()
    result = equipment_service.oee_many(session, _machines(session), hours=1.0)["MIX01"]
    assert result["good_qty"] == 7 and result["window_hours"] == pytest.approx(20 / 60, abs=0.005)


def test_the_floor_summary_and_the_machine_page_cannot_disagree(client, session):
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=now - timedelta(minutes=30)))
    session.flush()
    summary = client.get("/dashboard/summary?oee_hours=1").json()
    tile = next(m for m in summary["machines"] if m["code"] == "MIX01")
    page = client.get("/equipment/MIX01/oee?hours=1").json()
    assert tile["oee"]["availability"] == pytest.approx(page["availability"], abs=0.002)
    assert tile["state"] == "running"


def test_the_analysis_screen_sums_the_same_way(session):
    from fsmes.services import analysis

    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    for i in range(300):
        session.add(EquipmentState(
            equipment_id=mixer.id, state=EquipmentStateName.DOWN if i % 3 else EquipmentStateName.RUNNING,
            started_at=now - timedelta(minutes=60) + timedelta(seconds=i * 10),
            ended_at=now - timedelta(minutes=60) + timedelta(seconds=(i + 1) * 10)))
    session.flush()
    line = session.scalar(select(Equipment).where(Equipment.id == mixer.parent_id))
    station = next(s for s in analysis.oee_breakdown(session, line_code=line.code, hours=1)["stations"]
                   if s["code"] == "MIX01")
    assert station["seconds_by_state"]["down"] == pytest.approx(2000, abs=1)
    assert station["seconds_by_state"]["running"] == pytest.approx(1000, abs=1)


# ------------------------------------------------------------- retention


def test_the_retention_line_is_read_from_the_ends_of_the_table_and_stays_exact(session):
    unit = masterdata.get_equipment(session, "MIX01")
    for age_days in (30, 20, 10, 1, 0):
        session.add(TagValue(equipment_id=unit.id, tag="MIX01.Temperature", value_num=60.0,
                             ts=utcnow() - timedelta(days=age_days)))
    session.flush()
    before = retention.report(session, keep_days=14)
    assert before["tag_values"] == 5
    assert (utcnow() - before["oldest"]).days == 30 and (utcnow() - before["newest"]).days == 0
    retention.prune_tag_values(session, keep_days=14)
    after = retention.report(session, keep_days=14)
    assert after["tag_values"] == 3 == session.scalar(select(TagValue.id).order_by(TagValue.id.desc())) - \
        session.scalar(select(TagValue.id).order_by(TagValue.id)) + 1
    assert (utcnow() - after["oldest"]).days == 10
    assert retention.report(session, keep_days=14)["tag_values"] == 3


# ------------------------------------------------------------- the envelopes


def test_maintenance_orders_are_paged_and_filtered_on_the_server(admin, session):
    from fsmes.services import maintenance

    for i in range(6):
        maintenance.raise_corrective(session, equipment_code="MIX01" if i % 2 else "PACK01",
                                     summary=f"Seal {i} weeping", reason="walk-round", actor="T")
    session.flush()
    page = admin.get("/maintenance/orders?limit=2").json()
    assert {"items", "total", "has_more", "limit", "offset"} <= set(page)
    assert page["total"] == 6 and len(page["items"]) == 2 and page["has_more"]
    # "Open" is the server's answer, not open picked out of the newest N.
    open_ = admin.get("/maintenance/orders?status=due&status=in_progress&equipment=MIX01").json()
    assert open_["total"] == 3 and all(o["equipment"] == "MIX01" for o in open_["items"])
    assert admin.get("/maintenance/orders?status=done").json()["total"] == 0
    assert admin.get("/maintenance/orders?q=Seal 4").json()["total"] == 1
    assert admin.get("/maintenance/orders?kind=preventive").json()["total"] == 0
    assert admin.get("/maintenance/orders?limit=0").status_code == 422


def test_maintenance_plans_filter_by_machine_trigger_and_name(admin):
    for code, machine in (("PM-A", "MIX01"), ("PM-B", "PACK01")):
        made = admin.post("/maintenance/plans", json={
            "code": code, "name": f"{code} seals", "equipment": machine, "trigger": "runtime_hours",
            "interval": 100, "expected_minutes": 30})
        assert made.status_code == 201, made.text
    assert len(admin.get("/maintenance/plans").json()) == 2
    assert [p["plan"] for p in admin.get("/maintenance/plans?equipment=PACK01").json()] == ["PM-B"]
    assert len(admin.get("/maintenance/plans?trigger=produced_qty").json()) == 0
    assert [p["plan"] for p in admin.get("/maintenance/plans?q=PM-A").json()] == ["PM-A"]


def test_adjustments_certificates_and_firings_use_the_envelope(admin):
    for path in ("/adjustments", "/coa", "/triggers/firings?hours=24"):
        page = admin.get(path).json()
        assert {"items", "total", "has_more"} <= set(page), path
        assert page["items"] == [] and page["total"] == 0
    assert admin.get("/adjustments?limit=501").status_code == 422


def test_master_data_lists_answer_a_search(client):
    assert [e["code"] for e in client.get("/masterdata/equipment?q=MIX").json()] == ["MIX01"]
    assert client.get("/masterdata/equipment?q=nothing-called-this").json() == []
    finished = client.get("/masterdata/materials?type=finished").json()
    assert finished and all(m["type"] == "finished" for m in finished)
    assert [m["code"] for m in client.get("/masterdata/materials?q=FG-CO").json()] == ["FG-COLA"]
    people = client.get("/masterdata/personnel?q=SCOTT").json()
    assert [p["code"] for p in people] == ["SCOTT"]
    assert all(p["role"] == "operator" for p in client.get("/masterdata/personnel?role=operator").json())
    routings = client.get("/masterdata/routings?material=FG-COLA").json()
    assert routings and all(r["material"] == "FG-COLA" for r in routings)
    assert client.get("/masterdata/routings?q=no-such-routing").json() == []


def test_a_search_finds_a_code_typed_in_the_wrong_case(client):
    """The same answer on both supported databases. SQLite's LIKE ignores
    case for ASCII and PostgreSQL's does not, so a search box that matched on
    a laptop would quietly stop matching on a plant."""
    assert [e["code"] for e in client.get("/masterdata/equipment?q=mix01").json()] == ["MIX01"]
    assert [m["code"] for m in client.get("/masterdata/materials?q=fg-cola").json()] == ["FG-COLA"]


def test_specifications_search_and_stay_ordered(client, session):
    from fsmes.domain import Material, QualitySpec

    material = session.scalar(select(Material).where(Material.code == "FG-COLA"))
    for name in ("zz_scale_last", "aa_scale_first"):
        session.add(QualitySpec(material=material, characteristic=name, unit="x", min_value=1.0, max_value=2.0))
    session.flush()
    rows = [r["characteristic"] for r in client.get("/quality/specs?material=FG-COLA").json()]
    assert rows == sorted(rows) and rows[0] == "aa_scale_first" and rows[-1] == "zz_scale_last"
    assert [r["characteristic"] for r in client.get("/quality/specs?q=zz_scale").json()] == ["zz_scale_last"]


def test_a_filtered_gauge_register_keeps_the_plants_verdict(admin):
    for code in ("G-A", "G-B"):
        made = admin.post("/quality/gauges", json={"code": code, "name": f"{code} caliper", "kind": "caliper",
                                                   "interval_days": 365, "resolution": 0.01, "location": "bench"})
        assert made.status_code == 201, made.text
    whole = admin.get("/quality/gauges").json()
    part = admin.get("/quality/gauges?q=G-A").json()
    assert [g["code"] for g in part["gauges"]] == ["G-A"]
    assert part["gauges_total"] == 2 and part["overdue"] == whole["overdue"] and part["verdict"] == whole["verdict"]
    assert len(admin.get("/quality/gauges?overdue=true").json()["gauges"]) == whole["overdue"]


def test_the_instruction_catalogue_answers_a_search(admin):
    made = admin.post("/documents", json={"code": "WI-SCALE", "title": "How to page a list", "body": "Read it."})
    assert made.status_code == 201, made.text
    assert [d["code"] for d in admin.get("/documents?q=page a list").json()] == ["WI-SCALE"]
    assert admin.get("/documents?q=WI-SCALE&in_force=true").json() == []
    assert [d["code"] for d in admin.get("/documents?in_force=false").json()] == ["WI-SCALE"]


def test_the_agent_tools_say_how_much_of_a_paged_list_they_show(admin, monkeypatch):
    from fsmes import mcp_server

    monkeypatch.setattr(mcp_server, "_clients", {"testplant": admin})
    monkeypatch.setattr(mcp_server, "_registry", lambda: {"testplant": {"api_port": 0, "label": "test"}})
    for tool, key in ((mcp_server.maintenance_work, "work"), (mcp_server.adjustments, "adjustments"),
                      (mcp_server.certificates, "certificates"), (mcp_server.trigger_firings, "firings")):
        out = tool("testplant")
        assert out[key] == [] and out["total"] == 0 and out["shown"] == 0 and out["has_more"] is False, tool


# ------------------------------------------------------------- the screens


@pytest.mark.parametrize("html,controls", [
    ("masterdata.html", ["eq-q", "eq-filter-level", "eq-pager", "mat-q", "mat-filter-type", "mat-pager",
                         "spec-q", "spec-filter-material", "spec-pager", "person-q", "person-filter-role",
                         "person-pager"]),
    ("admin.html", ["u-q", "u-role", "u-pager", "user-count", "r-q", "r-material", "r-pager", "routing-count"]),
    ("maintenance.html", ["plan-q", "plan-filter-machine", "plan-filter-trigger", "plan-pager",
                          "history-q", "history-machine", "history-kind", "history-pager"]),
    ("triggers.html", ["tr-q", "tr-status", "tr-machine", "tr-pager", "f-machine", "f-ok", "f-pager", "firing-count"]),
    ("adjustments.html", ["q-status", "q-machine", "q-pager"]),
    ("coa.html", ["list-q", "list-material", "list-pager"]),
    ("instructions.html", ["doc-q", "doc-state", "doc-pager", "catalogue-count"]),
    ("gauges.html", ["g-q", "g-state", "g-pager", "register-count"]),
    ("tags.html", ["q", "machine", "kind", "tag-pager"]),
])
def test_every_plant_sized_list_has_a_filter_and_a_pager(html, controls):
    text = _read(html)
    for control in controls:
        assert f'id="{control}"' in text, f"{html} lacks #{control}"


def test_every_pager_is_the_shared_one_and_every_count_says_what_it_covers():
    common = _read("common.js")
    assert "FS.clientPage = " in common and "FS.countText = " in common
    assert "matching" in common and "in the plant" in common
    for name in ("masterdata", "maintenance", "triggers", "adjustments", "coa", "gauges", "tags"):
        assert "FS.pager(" in _read(f"{name}.js"), name
    for name in ("admin", "instructions"):
        assert "window.FS.pager(" in _read(f"{name}.js"), name
    # Server-paged screens read the envelope; none slices a bare list any more.
    assert "status=due&status=in_progress" in _read("maintenance.js")
    assert 'params.set("q"' in _read("maintenance.js")
    assert "page.items" in _read("adjustments.js") and "page.items" in _read("coa.js")
    assert "/triggers/firings?${params}" in _read("triggers.js")
    assert "of the last" not in _read("maintenance.js")
    # The KPI tiles keep counting the plant, never the page.
    triggers = _read("triggers.js")
    assert "fired.total" in triggers and "triggers.filter((t) => t.status" in triggers


def test_the_machine_page_says_how_many_alarm_changes_it_did_not_draw():
    js = _read("machine.js")
    assert re.search(r"newest \$\{changes\.length\} of \$\{all", js)
    assert ".then((p) => p.items)" in js


def test_the_spc_picker_is_grouped_by_material():
    assert 'document.createElement("optgroup")' in _read("spc.js")
