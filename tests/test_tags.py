"""The read side of the tag fabric: every tag a machine publishes, the alarm
word decoded, the equipment tree with cost centers, and WIP along a line.

The fabric made each machine publish a dozen tags and the product read two.
These tests hold the new surface to the fabric's own honesty rules: a tag
the manifest does not describe is still shown, a stale tag says so, an
unnamed alarm bit is still an alarm, and negative WIP is reported.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import Equipment, EquipmentLevel, TagValue
from fsmes.services import masterdata, tags, workorders

MANIFEST = {
    "MIX01": {
        "tags": ["State", "GoodCount", "ScrapCount", "Temperature", "TemperatureSP", "AlarmWord"],
        "meta": {
            "State": {"kind": "state", "writable": False},
            "GoodCount": {"kind": "counter", "writable": False},
            "ScrapCount": {"kind": "counter", "writable": False},
            "Temperature": {"kind": "pv", "unit": "°C", "nominal": 60.0, "follows": "TemperatureSP", "writable": False},
            "TemperatureSP": {"kind": "sp", "unit": "°C", "min": 40.0, "max": 80.0, "drives": "Temperature",
                              "writable": True},
            "AlarmWord": {"kind": "alarm", "bits": {"0": "fault", "3": "planned stop"}, "writable": False},
        },
        "analog": "Temperature",
    }
}


def _unit(session, code="MIX01") -> Equipment:
    return session.scalar(select(Equipment).where(Equipment.code == code))


def _write(session, unit, name, value, age_seconds=0.0):
    session.add(TagValue(equipment_id=unit.id, tag=f"{unit.code}.{name}", value_num=value,
                         ts=utcnow() - timedelta(seconds=age_seconds)))
    session.flush()


def test_decode_alarm_names_known_bits_and_keeps_unknown_ones():
    bits = {"0": "fault", "3": "planned stop"}
    assert tags.decode_alarm(0, bits) == []
    assert tags.decode_alarm(1, bits) == ["fault"]
    assert tags.decode_alarm(0b1001, bits) == ["fault", "planned stop"]
    # A set bit the manifest never named is still an alarm, not a silence.
    assert tags.decode_alarm(0b100, bits) == ["bit 2"]
    assert tags.decode_alarm(None, bits) == []


def test_snapshot_carries_every_tag_with_manifest_metadata_and_age(session):
    unit = _unit(session)
    _write(session, unit, "Temperature", 61.5)
    _write(session, unit, "TemperatureSP", 60.0)
    _write(session, unit, "GoodCount", 120)
    _write(session, unit, "AlarmWord", 0b1000)

    snap = tags.snapshot(session, unit, cat=MANIFEST)

    by = {t["tag"]: t for t in snap["tags"]}
    assert snap["source"] == "manifest"
    assert set(by) == set(MANIFEST["MIX01"]["tags"])
    assert by["Temperature"]["value"] == 61.5 and by["Temperature"]["unit"] == "°C"
    assert by["Temperature"]["primary"] is True and by["Temperature"]["follows"] == "TemperatureSP"
    assert by["TemperatureSP"]["writable"] is True and by["TemperatureSP"]["max"] == 80.0
    assert by["AlarmWord"]["active"] == ["planned stop"]
    assert by["Temperature"]["stale"] is False and by["Temperature"]["age_seconds"] < 5
    # Never written: value unknown, and honestly so - not stale, not zero.
    assert by["State"]["value"] is None and by["State"]["stale"] is None
    # State first, then the alarm, then process values, then counters.
    kinds = [t["kind"] for t in snap["tags"]]
    assert kinds.index("state") < kinds.index("alarm") < kinds.index("pv") < kinds.index("counter")


def test_a_tag_that_stopped_arriving_is_marked_stale(session):
    unit = _unit(session)
    _write(session, unit, "Temperature", 59.0, age_seconds=tags.STALE_AFTER_SECONDS + 30)
    snap = tags.snapshot(session, unit, cat=MANIFEST)
    temp = next(t for t in snap["tags"] if t["tag"] == "Temperature")
    assert temp["stale"] is True and temp["age_seconds"] > tags.STALE_AFTER_SECONDS


def test_a_state_that_last_changed_long_ago_is_live_while_the_machine_talks(session):
    """State, alarms, setpoints and counters are written when they change.
    On a machine still streaming its process values they are current; on a
    machine that has gone quiet, everything is stale together."""
    unit = _unit(session)
    _write(session, unit, "State", 1, age_seconds=3600)
    _write(session, unit, "TemperatureSP", 60.0, age_seconds=3600)
    _write(session, unit, "Temperature", 61.0, age_seconds=2)
    by = {t["tag"]: t for t in tags.snapshot(session, unit, cat=MANIFEST)["tags"]}
    assert by["State"]["stale"] is False and by["TemperatureSP"]["stale"] is False
    assert by["Temperature"]["stale"] is False

    quiet = _unit(session, session.scalar(select(Equipment.code).where(
        Equipment.level == EquipmentLevel.WORK_UNIT, Equipment.code != "MIX01")))
    _write(session, quiet, "State", 1, age_seconds=3600)
    _write(session, quiet, "Temperature", 61.0, age_seconds=3600)
    snap = tags.snapshot(session, quiet, cat={quiet.code: MANIFEST["MIX01"]})
    assert all(t["stale"] for t in snap["tags"] if t["value"] is not None)
    assert snap["quiet_for_seconds"] > tags.STALE_AFTER_SECONDS


def test_a_machine_no_manifest_describes_is_read_from_history(session):
    """A real plant with no generator still gets a tag list - from what the
    machine actually wrote."""
    unit = _unit(session)
    _write(session, unit, "Pressure", 4.2)
    _write(session, unit, "State", 1)
    snap = tags.snapshot(session, unit, cat={})
    assert snap["source"] == "history"
    assert {t["tag"] for t in snap["tags"]} == {"Pressure", "State"}
    assert all(t["kind"] is None for t in snap["tags"])


def test_alarms_across_machines(session):
    unit = _unit(session)
    _write(session, unit, "AlarmWord", 1)
    other = session.scalar(select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT,
                                                    Equipment.code != "MIX01"))
    out = {a["equipment"]: a for a in tags.alarms(session, [unit, other], cat=MANIFEST)}
    assert out["MIX01"]["active"] == ["fault"] and out["MIX01"]["word"] == 1
    assert out[other.code]["active"] == [] and out[other.code]["word"] is None


def test_head_says_where_the_machine_sits(session):
    unit = _unit(session)
    h = tags.head(session, unit)
    assert h["code"] == "MIX01" and h["level"] == "work_unit"
    assert [p["level"] for p in h["path"]][-1] == "work_center"
    assert h["line"]["code"] == h["path"][-1]["code"]
    assert h["state"] in {"unknown", "idle", "running", "down", "setup"}


def test_tree_walks_every_depth_and_resolves_cost_centers(session):
    line = masterdata.get_equipment(session, tags.head(session, _unit(session))["line"]["code"])
    line.cost_center = "CC-100"
    session.flush()
    roots = tags.tree(session)
    assert roots, "the demo plant has a root"

    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n["children"])

    nodes = {n["code"]: n for n in walk(roots)}
    assert nodes["MIX01"]["level"] == "work_unit" and "state" in nodes["MIX01"]
    assert nodes["MIX01"]["cost_center"] == "CC-100", "a machine inherits its line's cost center"
    assert nodes[line.code]["level"] == "work_center"


def test_line_wip_sums_the_orders_on_the_floor(session, supervisor):
    line = masterdata.get_equipment(session, tags.head(session, _unit(session))["line"]["code"])
    created = supervisor.post("/workorders", json={"code": "WO-WIP-1", "material": "FG-COLA", "quantity": 100})
    assert created.status_code == 201
    assert supervisor.post("/workorders/WO-WIP-1/release").status_code == 200
    session.flush()
    wo = workorders.get(session, "WO-WIP-1")
    first = sorted(wo.operations, key=lambda o: o.seq)[0]
    assert supervisor.post(f"/workorders/WO-WIP-1/operations/{first.seq}/start").status_code == 200
    r = supervisor.post("/execution/report", json={"equipment": first.equipment.code, "order": "WO-WIP-1",
                                                   "seq": first.seq, "good": 30, "scrap": 2})
    assert r.status_code in (200, 201), r.text
    session.flush()

    w = tags.line_wip(session, line)
    assert [o["order"] for o in w["orders"]] == ["WO-WIP-1"]
    stations = {s["code"]: s for s in w["stations"]}
    assert stations[first.equipment.code]["wip_qty"] == 100 - 30 - 2
    assert w["consistent"] is True
    assert w["wip_total"] == sum(s["wip_qty"] for s in w["stations"])
    assert w["stations"][0]["seq"] <= w["stations"][-1]["seq"]


@pytest.fixture()
def supervisor(sign_in):
    return sign_in("SUP-WIP", role="supervisor")


def test_equipment_endpoints_serve_the_surface(session, supervisor):
    unit = _unit(session)
    _write(session, unit, "Temperature", 58.0)
    assert supervisor.get("/equipment/tree").json()["roots"]
    head = supervisor.get("/equipment/MIX01").json()
    assert head["code"] == "MIX01"
    snap = supervisor.get("/equipment/MIX01/tags").json()
    assert any(t["tag"] == "Temperature" for t in snap["tags"])
    assert isinstance(supervisor.get("/equipment/alarms").json(), list)
    browse = supervisor.get("/equipment/tags").json()
    assert any(r["equipment"] == "MIX01" and r["tag"] == "Temperature" for r in browse["rows"])
    assert supervisor.get("/equipment/MIX01/alarms").json()["equipment"] == "MIX01"
    assert supervisor.get("/equipment/NOPE/tags").status_code == 404
    wip = supervisor.get(f"/line/wip?line={head['line']['code']}").json()
    assert wip["line"] == head["line"]["code"] and "stations" in wip


def test_browse_is_the_plant_flat_with_a_health_line_per_machine(session):
    unit = _unit(session)
    other = session.scalar(select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT,
                                                    Equipment.code != "MIX01"))
    _write(session, unit, "Temperature", 61.0)
    _write(session, unit, "AlarmWord", 1)
    _write(session, other, "Pressure", 2.0, age_seconds=tags.STALE_AFTER_SECONDS + 10)

    out = tags.browse(session, [unit, other], cat=MANIFEST)

    by = {m["code"]: m for m in out["machines"]}
    assert by["MIX01"]["alarms"] == ["fault"] and by["MIX01"]["tags"] == 6
    assert by[other.code]["quiet_for_seconds"] > tags.STALE_AFTER_SECONDS and by[other.code]["stale"] == 1
    assert {(r["equipment"], r["tag"]) for r in out["rows"]} >= {("MIX01", "Temperature"), (other.code, "Pressure")}
    assert all("stale" in r and "writable" in r for r in out["rows"])
