"""A thousand machines and two thousand measurements, and the screens that
have to stay usable in front of them.

Scott, at the 108-station lab plant on 2026-09-04: "floor machines needs to
paginate and filter", "same for work orders", and on the Quality screen
"measurements card definitely needs filtering, there are way too many tags.
specifications needs filtering."

The screens already had filter bars. What they did not have was a server
doing the filtering: the Machines card sliced a copy of every machine in the
plant that it re-fetched twice a second, the specifications card and the tab
strip came out of one fetch of every specification the plant has. That is
invisible on a two-machine demo and it is the whole cost of the screen on a
real one, so this file pins the property rather than the pixels:

- every one of those lists pages and filters on the server, and says its
  total;
- the tiles above a filtered list keep counting the plant, not the page;
- and the work a page costs does not grow with the plant. That last one is
  measured by counting the statements the database is asked to run, because
  it is the only way to tell "returns twenty-four rows" apart from "reads a
  thousand rows and returns twenty-four".
"""

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, insert, select

from fsmes.db import utcnow
from fsmes.domain import (
    CheckResult,
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    Material,
    MaterialType,
    QualityCheck,
    QualitySpec,
    Routing,
    RoutingOperation,
)
from fsmes.services import workorders

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

MACHINES = 1000
LINES = 10
CHECKS = 2000
MATERIALS = 20
CHARACTERISTICS = 12          # per material: 240 specifications in the plant
ORDERS = 60                   # more than the floor's order card ever lists

# The states machines are put in, in rotation. Two in five run, which is a
# believable plant and, more to the point, is not all of them - a filter that
# matches everything proves nothing.
STATES = [EquipmentStateName.RUNNING, EquipmentStateName.RUNNING,
          EquipmentStateName.IDLE, EquipmentStateName.DOWN, EquipmentStateName.SETUP]


@contextmanager
def statements(session):
    """Count the statements the database is actually asked to run."""
    counted: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        counted.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield counted
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.fixture()
def megaplant(session):
    """A thousand machines on ten lines, 240 characteristics, two thousand
    measurements over thirty days, and sixty orders."""
    site = session.scalar(select(Equipment).where(Equipment.level == EquipmentLevel.SITE))
    assert site is not None, "the demo plant seeds a site"

    session.execute(insert(Equipment), [
        {"code": f"SCALE{n:02d}LINE", "name": f"Scale line {n}",
         "level": EquipmentLevel.WORK_CENTER, "parent_id": site.id}
        for n in range(LINES)])
    lines = {eq.code: eq.id for eq in session.scalars(
        select(Equipment).where(Equipment.code.like("SCALE%LINE")))}
    assert len(lines) == LINES

    session.execute(insert(Equipment), [
        {"code": f"SCALE{i:04d}", "name": f"Scale machine {i}",
         "level": EquipmentLevel.WORK_UNIT, "ideal_cycle_seconds": 1.0,
         "parent_id": lines[f"SCALE{i % LINES:02d}LINE"]}
        for i in range(MACHINES)])
    machines = list(session.scalars(
        select(Equipment)
        .where(Equipment.code.like("SCALE0%"), Equipment.level == EquipmentLevel.WORK_UNIT)
        .order_by(Equipment.code)))
    assert len(machines) == MACHINES

    now = utcnow()
    session.execute(insert(EquipmentState), [
        {"equipment_id": eq.id, "state": STATES[i % len(STATES)],
         "started_at": now - timedelta(hours=2), "ended_at": None}
        for i, eq in enumerate(machines)])

    session.execute(insert(Material), [
        {"code": f"SCALE-MAT-{m:02d}", "name": f"Scale material {m}",
         "type": MaterialType.FINISHED, "uom": "ea"}
        for m in range(MATERIALS)])
    materials = list(session.scalars(
        select(Material).where(Material.code.like("SCALE-MAT-%")).order_by(Material.code)))
    session.execute(insert(QualitySpec), [
        {"material_id": mat.id, "characteristic": f"scale_char_{c:02d}",
         "unit": "mm", "min_value": 0.0, "max_value": 10.0}
        for mat in materials for c in range(CHARACTERISTICS)])
    specs = list(session.scalars(
        select(QualitySpec).where(QualitySpec.characteristic.like("scale_char_%"))
        .order_by(QualitySpec.id)))
    assert len(specs) == MATERIALS * CHARACTERISTICS

    # Every material needs a route before an order can be built against it.
    # One step each, on the machine that shares its number.
    session.execute(insert(Routing), [
        {"code": f"SCALE-RT-{m:02d}", "name": f"Scale route {m}", "material_id": mat.id}
        for m, mat in enumerate(materials)])
    routings = list(session.scalars(
        select(Routing).where(Routing.code.like("SCALE-RT-%")).order_by(Routing.code)))
    session.execute(insert(RoutingOperation), [
        {"routing_id": rt.id, "seq": 10, "name": "Scale step",
         "equipment_id": machines[m].id, "run_seconds_per_unit": 1.0}
        for m, rt in enumerate(routings)])
    session.flush()

    orders = [workorders.create(session, code=f"SCALE-WO-{n:03d}",
                                material_code=materials[n % MATERIALS].code, quantity=100)
              for n in range(ORDERS)]
    for order in orders[:ORDERS // 2]:
        workorders.release(session, order.code, actor="scale-fixture")

    # Every fifth check fails, so "failures only" is a real subset and not
    # the whole list. Spread a day apart each, newest last.
    session.execute(insert(QualityCheck), [
        {"spec_id": specs[i % len(specs)].id,
         "work_order_id": orders[i % ORDERS].id if i % 3 == 0 else None,
         "value": 5.0 if i % 5 else 99.0,
         "result": CheckResult.PASS if i % 5 else CheckResult.FAIL,
         "checked_by": "SCALE",
         "ts": now - timedelta(days=30) + timedelta(minutes=i * 21)}
        for i in range(CHECKS)])
    session.flush()
    return {"machines": machines, "specs": specs, "materials": materials,
            "orders": orders, "now": now}


# ----------------------------------------------------------------- the floor


def test_the_floor_asks_for_a_page_of_machines_and_is_told_how_many_there_are(client, megaplant, session):
    whole = session.scalar(select(func.count()).select_from(Equipment)
                           .where(Equipment.level == EquipmentLevel.WORK_UNIT))
    page = client.get("/dashboard/summary?machine_limit=24").json()
    assert len(page["machines"]) == 24
    envelope = page["machines_page"]
    assert envelope["total"] == whole == envelope["scope_total"]
    assert envelope["offset"] == 0 and envelope["has_more"] is True
    assert envelope["filtered"] is False

    second = client.get("/dashboard/summary?machine_limit=24&machine_offset=24").json()
    assert [m["code"] for m in second["machines"]] != [m["code"] for m in page["machines"]]
    assert second["machines_page"]["offset"] == 24

    last = client.get(f"/dashboard/summary?machine_limit=24&machine_offset={whole - 5}").json()
    assert len(last["machines"]) == 5 and last["machines_page"]["has_more"] is False


def test_a_page_of_the_floor_does_not_cost_a_query_per_machine_in_the_plant(client, megaplant, session):
    """The property the screens were failing, stated as a number.

    Before this, one refresh of a thousand-machine floor asked the database
    for each machine's process value one machine at a time - a thousand
    statements, twice a second, for every screen watching. Twenty-four cards
    should cost about twenty-four of those, however large the plant is.
    """
    with statements(session) as paged:
        client.get("/dashboard/summary?machine_limit=24")
    with statements(session) as everything:
        client.get("/dashboard/summary")

    assert len(paged) < 60, f"a page of 24 took {len(paged)} statements"
    # And the unpaged answer, which is still what an agent tool asks for,
    # is the thing that scales with the plant - proof the count above is
    # measuring what it claims to.
    assert len(everything) > MACHINES


def test_a_filtered_floor_never_makes_the_plant_look_smaller(client, megaplant, session):
    whole = client.get("/dashboard/summary?machine_limit=1").json()
    running = whole["plant"]["machines_running"]
    total = whole["plant"]["machines_total"]
    assert 0 < running < total, "a plant where every machine runs proves nothing"

    down = client.get("/dashboard/summary?machine_limit=50&machine_state=down").json()
    assert all(m["state"] == "down" for m in down["machines"])
    assert 0 < down["machines_page"]["total"] < total
    assert down["machines_page"]["filtered"] is True
    assert down["machines_page"]["scope_total"] == total
    # The tiles are about the plant, not about what the grid is showing.
    assert down["plant"]["machines_running"] == running
    assert down["plant"]["machines_total"] == total
    assert down["plant"]["oee"] == whole["plant"]["oee"]


def test_a_machine_that_never_reported_a_state_is_unknown_and_findable(client, megaplant):
    """Unknown is an answer, not a gap: the seeded demo machines have no open
    state here, and the filter can name them."""
    unknown = client.get("/dashboard/summary?machine_limit=50&machine_state=unknown").json()
    assert unknown["machines_page"]["total"] >= 1
    assert all(m["state"] == "unknown" for m in unknown["machines"])


def test_the_floor_filters_by_name_and_scopes_to_a_line(client, megaplant):
    found = client.get("/dashboard/summary?machine_limit=50&machine_q=SCALE0007").json()
    assert [m["code"] for m in found["machines"]] == ["SCALE0007"]
    assert found["machines_page"]["total"] == 1

    line = client.get("/dashboard/summary?line=SCALE03LINE&machine_limit=10").json()
    assert line["machines_page"]["scope_total"] == MACHINES // LINES
    # A line is a scope, so the tiles follow it.
    assert line["plant"]["machines_total"] == MACHINES // LINES
    assert all(m["line"]["code"] == "SCALE03LINE" for m in line["machines"])


def test_the_floor_refuses_a_page_bigger_than_any_other_list_allows(client, megaplant):
    assert client.get("/dashboard/summary?machine_limit=501").status_code == 422
    assert client.get("/dashboard/summary?machine_limit=0").status_code == 422
    assert client.get("/dashboard/summary?machine_offset=-1").status_code == 422


def test_the_active_orders_tile_counts_the_plant_not_the_card(client, megaplant, session):
    """It counted the twenty-five orders listed below it. A plant with sixty
    released orders was told it had twenty-five."""
    summary = client.get("/dashboard/summary?machine_limit=1").json()
    assert len(summary["orders"]) == 25
    assert summary["plant"]["active_orders"] == ORDERS // 2
    assert summary["plant"]["active_orders"] == client.get(
        "/workorders/summary").json()["by_status"]["released"]


# ----------------------------------------------------------------- the orders


def test_the_order_list_pages_and_filters_at_scale(client, megaplant):
    page = client.get("/workorders?limit=50").json()
    assert len(page["items"]) == 50 and page["total"] >= ORDERS and page["has_more"]
    released = client.get("/workorders?status=released&limit=5").json()
    assert released["total"] == ORDERS // 2
    assert all(o["status"] == "released" for o in released["items"])
    one_material = client.get("/workorders?material=SCALE-MAT-03&limit=500").json()
    assert 0 < one_material["total"] < page["total"]
    assert all(o["material"] == "SCALE-MAT-03" for o in one_material["items"])


def test_the_floor_order_box_finds_a_material_as_well_as_an_order(client, megaplant):
    """The box has said "Order or material code" since it was written."""
    by_order = client.get("/workorders?q=SCALE-WO-007").json()
    assert [o["code"] for o in by_order["items"]] == ["SCALE-WO-007"]
    by_material = client.get("/workorders?q=SCALE-MAT-05&limit=500").json()
    assert by_material["total"] > 0
    assert all(o["material"] == "SCALE-MAT-05" for o in by_material["items"])


# ---------------------------------------------------------------- the quality


def test_the_specification_list_pages_and_says_how_many_there_are(client, megaplant):
    page = client.get("/quality/specs?limit=25").json()
    assert {"items", "total", "limit", "offset", "has_more"} <= set(page)
    assert len(page["items"]) == 25 and page["has_more"]
    assert page["total"] >= MATERIALS * CHARACTERISTICS

    one = client.get("/quality/specs?material=SCALE-MAT-04&limit=500").json()
    assert one["total"] == CHARACTERISTICS
    assert all(s["material"] == "SCALE-MAT-04" for s in one["items"])
    named = client.get("/quality/specs?characteristic=scale_char_03&limit=500").json()
    assert named["total"] == MATERIALS
    searched = client.get("/quality/specs?q=scale_char_1&limit=500").json()
    assert 0 < searched["total"] < page["total"]


def test_the_filter_selects_are_built_without_reading_every_specification(client, megaplant, session):
    """Filling "any material" used to mean fetching the whole table."""
    with statements(session) as asked:
        facets = client.get("/quality/specs/facets").json()
    assert len(asked) < 20, f"the facets took {len(asked)} statements"

    assert facets["specs_total"] >= MATERIALS * CHARACTERISTICS
    assert facets["materials_total"] >= MATERIALS
    assert {"code", "specs"} <= set(facets["materials"][0])
    scale = next(m for m in facets["materials"] if m["code"] == "SCALE-MAT-00")
    assert scale["specs"] == CHARACTERISTICS
    # Narrowed to one material, the characteristics are that material's.
    narrowed = client.get("/quality/specs/facets?material=SCALE-MAT-00").json()
    assert narrowed["characteristics_total"] == CHARACTERISTICS


def test_the_inspection_history_pages_filters_and_answers_a_date_range(client, megaplant):
    page = client.get("/quality/checks?limit=50").json()
    assert len(page["items"]) == 50 and page["total"] >= CHECKS and page["has_more"]

    failures = client.get("/quality/checks?result=fail&limit=1").json()
    assert 0 < failures["total"] < page["total"]

    one = client.get("/quality/checks?material=SCALE-MAT-00&characteristic=scale_char_00&limit=500").json()
    assert 0 < one["total"] < page["total"]
    assert all(c["material"] == "SCALE-MAT-00" and c["characteristic"] == "scale_char_00"
               for c in one["items"])

    start = (megaplant["now"] - timedelta(days=30)).replace(microsecond=0)
    window = client.get(
        f"/quality/checks?since={start.isoformat()}&until={(start + timedelta(days=1)).isoformat()}"
        "&limit=500").json()
    assert 0 < window["total"] < page["total"]
    assert all(c["ts"] <= (start + timedelta(days=1)).isoformat() for c in window["items"])

    against = client.get("/quality/checks?order=SCALE-WO-000&limit=500").json()
    assert against["total"] > 0
    assert all(c["order"] == "SCALE-WO-000" for c in against["items"])


def test_a_measurement_does_not_claim_a_station_it_never_recorded(client, megaplant):
    """Scott asked for a station filter. A check records the material, the
    characteristic, the inspector, the gauge and the order — not the machine
    it was taken at, and guessing one from the order's route would name a
    station nobody stood at. The endpoint refuses the parameter rather than
    pretending, and the docstring says why."""
    from fsmes.api.routers import quality as quality_router

    assert "station" not in QualityCheck.__table__.c
    assert "station" in quality_router.list_checks.__doc__
    # An unknown filter is ignored by FastAPI rather than silently narrowing,
    # so the answer is the unfiltered one - not an empty list that would read
    # as "no measurements at that station".
    assert client.get("/quality/checks?station=SCALE0001&limit=1").json()["total"] \
        == client.get("/quality/checks?limit=1").json()["total"]


# ------------------------------------------------------------- the screens


def test_no_screen_asks_for_a_list_without_saying_how_much_of_it_it_wants():
    """The source ratchet. Every one of the four cards names a limit, so a
    plant ten times this size cannot quietly turn a screen into a download."""
    app_js = (WEB / "app.js").read_text(encoding="utf-8")
    quality_js = (WEB / "quality.js").read_text(encoding="utf-8")
    masterdata_js = (WEB / "masterdata.js").read_text(encoding="utf-8")

    assert "machine_limit: String(MACHINE_PAGE)" in app_js
    assert "machine_offset" in app_js
    # The floor no longer downloads the equipment tree to label its cards.
    assert "/equipment/tree" not in app_js
    assert "/masterdata/equipment?level=work_center" in app_js

    for js, name in ((app_js, "app.js"), (quality_js, "quality.js"), (masterdata_js, "masterdata.js")):
        assert 'api("/quality/specs")' not in js, name

    for source in ("limit: String(SPEC_PAGE)", "limit: String(HISTORY_PAGE)",
                   "limit: String(NC_PAGE)", "limit: String(CHAR_PAGE)"):
        assert source in quality_js, source
