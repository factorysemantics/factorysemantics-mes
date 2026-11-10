"""Every list a supervisor would take to Excel exports as an xlsx file.

The quality checks went first (#225). These are the rest: work orders,
non-conformances, maintenance work, the audit trail, lots, OEE by station and
the stops behind the downtime pareto. Each file is the list's own rows under
the list's own filters, without its paging; a bold, frozen header row; times
as date cells on the plant's clock; the capability that reads the list and no
other; and a range with nothing in it is a header and no rows, never an error.

Each file is read back as a zip with the standard library only, as the checks'
own tests do: if these can find every row, so can Excel.
"""

import io
import re
import zipfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from tests.test_a_supervisor_exports_the_quality_checks_of_a_date_range_as_xlsx import (
    as_datetime,
    read_sheet,
)

from fsmes.api import export
from fsmes.api.routers.execution import LOT_EXPORT
from fsmes.db import utcnow
from fsmes.domain import AuditLog, Equipment, EquipmentState, EquipmentStateName, NonConformance
from fsmes.services import execution, maintenance, quality, workorders

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def header_is_bold_and_frozen(body: bytes) -> bool:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    first = re.search(r'<row r="1"[^>]*>(.*?)</row>', sheet, re.S).group(1)
    cells = re.findall(r"<c [^>]*>", first)
    return bool(cells) and all('s="1"' in c for c in cells) and 'state="frozen"' in sheet


def filename(response) -> str:
    return re.search(r'filename="([^"]+)"', response.headers["content-disposition"]).group(1)


def exported(client, path: str) -> tuple[list, list[list]]:
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == XLSX
    assert header_is_bold_and_frozen(r.content)
    rows = read_sheet(r.content)
    assert r.headers["x-total-count"] == str(len(rows) - 1)
    return rows[0], rows[1:]


@pytest.fixture()
def chicago(monkeypatch):
    """A plant five hours behind Greenwich in September, on the live settings."""
    from fsmes.config import get_settings

    monkeypatch.setattr(get_settings(), "plant_timezone", "America/Chicago")


# ------------------------------------------------------------ the one step


def test_the_shared_step_writes_a_zone_in_a_time_header_and_none_as_an_empty_cell(chicago):
    columns = (export.Column("Name", "name"), export.Column("At", "at", when=True),
               export.Column("Count", lambda r: r["n"]))
    r = export.spreadsheet("things", "Things", columns,
                           [{"name": "a", "at": datetime(2026, 9, 2, 3, 0), "n": None}],
                           range_="2026-09-01")
    assert 'filename="things-2026-09-01.xlsx"' in r.headers["content-disposition"]
    header, *rows = read_sheet(r.body)
    assert header == ["Name", "At (America/Chicago)", "Count"]
    assert rows[0][0] == "a"
    assert as_datetime(rows[0][1]) == datetime(2026, 9, 1, 22, 0)
    assert rows[0][2] is None


def test_a_range_in_a_filename_is_its_two_days_or_all():
    assert export.span("2026-09-01", "2026-10-31") == "2026-09-01-to-2026-10-31"
    assert export.span("2026-09-01T06:00:00", None) == "2026-09-01"
    assert export.span(None, None) == "all"


# ------------------------------------------------------------- work orders


def _order(session, code, due, material="FG-COLA"):
    wo = workorders.create(session, code=code, material_code=material, quantity=100, due_date=due)
    session.flush()
    return wo


def test_work_orders_export_every_order_the_list_shows_for_its_due_range(client, session):
    for day in (2, 10, 20):
        _order(session, f"WO-X-{day}", datetime(2026, 9, day, 12))
    _order(session, "WO-X-OUT", datetime(2026, 12, 1, 12))
    query = "due_after=2026-09-01T00:00:00&due_before=2026-09-30T23:59:59"
    listed = client.get(f"/workorders?{query}&limit=500").json()

    r = client.get(f"/workorders/export.xlsx?{query}")
    assert filename(r) == "work-orders-due-2026-09-01-to-2026-09-30.xlsx"
    header, rows = exported(client, f"/workorders/export.xlsx?{query}")
    assert header[:3] == ["Order", "Material", "Status"]
    assert len(rows) == listed["total"] == 3
    # Oldest due first; the list's own is soonest-due-last.
    assert [row[0] for row in rows] == ["WO-X-2", "WO-X-10", "WO-X-20"]
    assert as_datetime(rows[0][header.index("Due (UTC)")]) == datetime(2026, 9, 2, 12)


def test_work_orders_export_honours_the_status_filter(client, session):
    _order(session, "WO-X-REL", datetime(2026, 9, 3, 12))
    workorders.release(session, "WO-X-REL")
    _order(session, "WO-X-NEW", datetime(2026, 9, 4, 12))
    listed = client.get("/workorders?status=released&limit=500").json()
    _header, rows = exported(client, "/workorders/export.xlsx?status=released")
    assert len(rows) == listed["total"]
    assert {row[2] for row in rows} == {"released"}
    assert "WO-X-REL" in {row[0] for row in rows}
    assert "WO-X-NEW" not in {row[0] for row in rows}


def test_work_orders_with_nothing_due_in_the_range_are_a_header_and_no_rows(client):
    header, rows = exported(client, "/workorders/export.xlsx?due_after=1999-01-01T00:00:00"
                                    "&due_before=1999-01-31T00:00:00")
    assert header[0] == "Order"
    assert rows == []


def test_work_orders_export_is_for_whoever_may_read_the_list(anon, sign_in):
    assert anon.get("/workorders/export.xlsx").status_code == 401
    viewer = sign_in("VIEW", role="viewer")
    assert viewer.get("/workorders").status_code == 200
    assert viewer.get("/workorders/export.xlsx").status_code == 200


# --------------------------------------------------------- non-conformances


def _nc(session, description, raised):
    nc = quality.open_nc(session, description=description, severity="minor", actor="ines")
    nc.created_at = raised
    session.flush()
    return nc


def test_non_conformances_export_the_ones_raised_in_the_range_oldest_first(client, session):
    inside = [_nc(session, "cap loose", datetime(2026, 9, 2, 8)),
              _nc(session, "label skew", datetime(2026, 9, 30, 23, 59))]
    _nc(session, "before", datetime(2026, 8, 31, 23, 59))
    _nc(session, "after", datetime(2026, 10, 1, 0, 0))
    query = "since=2026-09-01&until=2026-09-30"
    listed = client.get(f"/quality/nonconformances?{query}&limit=500").json()

    r = client.get(f"/quality/nonconformances/export.xlsx?{query}")
    assert filename(r) == "non-conformances-2026-09-01-to-2026-09-30.xlsx"
    header, rows = exported(client, f"/quality/nonconformances/export.xlsx?{query}")
    assert header[:2] == ["Code", "Raised (UTC)"]
    assert len(rows) == listed["total"] == 2
    assert [row[0] for row in rows] == [nc.code for nc in inside]
    assert rows[0][header.index("Raised by")] == "ines"
    assert rows[0][header.index("Order")] == "no order"


def test_non_conformances_export_honours_the_status_filter(client, session):
    _nc(session, "open one", datetime(2026, 9, 5, 8))
    listed = client.get("/quality/nonconformances?status=open&limit=500").json()
    header, rows = exported(client, "/quality/nonconformances/export.xlsx?status=open")
    assert len(rows) == listed["total"] >= 1
    assert {row[header.index("Status")] for row in rows} == {"open"}


def test_the_non_conformance_range_is_a_day_on_the_plants_clock(client, session, chicago):
    # 03:00 UTC on the 2nd is still the 1st in Chicago.
    nc = _nc(session, "late on the first", datetime(2026, 9, 2, 3, 0))
    header, rows = exported(client, "/quality/nonconformances/export.xlsx"
                                    "?since=2026-09-01&until=2026-09-01")
    assert [row[0] for row in rows] == [nc.code]
    assert header[1] == "Raised (America/Chicago)"
    assert as_datetime(rows[0][1]) == datetime(2026, 9, 1, 22, 0)


def test_non_conformances_with_none_in_the_range_are_a_header_and_no_rows(client):
    header, rows = exported(client, "/quality/nonconformances/export.xlsx?since=1999-01-01&until=1999-01-31")
    assert header[0] == "Code"
    assert rows == []


def test_non_conformances_export_is_for_whoever_may_read_the_list(anon, sign_in):
    assert anon.get("/quality/nonconformances/export.xlsx").status_code == 401
    viewer = sign_in("VIEW", role="viewer")
    assert viewer.get("/quality/nonconformances").status_code == 200
    assert viewer.get("/quality/nonconformances/export.xlsx").status_code == 200


# -------------------------------------------------------- maintenance work


def _job(session, summary, completed=None):
    job = maintenance.raise_corrective(session, equipment_code="MIX01", summary=summary, actor="test")
    session.flush()
    if completed:
        maintenance.complete(session, job.code, findings="done", downtime_minutes=12, actor="test")
        job.completed_at = completed
        session.flush()
    return job


def test_maintenance_exports_the_jobs_completed_in_the_range(client, session):
    inside = [_job(session, "seal", completed=datetime(2026, 9, 3, 9)),
              _job(session, "belt", completed=datetime(2026, 9, 28, 15))]
    _job(session, "earlier", completed=datetime(2026, 8, 20, 9))
    _job(session, "not done")
    query = "status=done&since=2026-09-01&until=2026-09-30"
    listed = client.get(f"/maintenance/orders?{query}&limit=500").json()

    r = client.get(f"/maintenance/orders/export.xlsx?{query}")
    assert filename(r) == "maintenance-work-2026-09-01-to-2026-09-30.xlsx"
    header, rows = exported(client, f"/maintenance/orders/export.xlsx?{query}")
    assert len(rows) == listed["total"] == 2
    assert [row[0] for row in rows] == [job.code for job in inside]
    assert rows[0][header.index("Down (min)")] == 12
    assert rows[0][header.index("Plan")] == "no plan"


def test_a_job_not_started_has_an_empty_started_and_never_a_made_up_one(client, session):
    job = _job(session, "not yet")
    header, rows = exported(client, f"/maintenance/orders/export.xlsx?q={job.code}")
    assert [row[0] for row in rows] == [job.code]
    assert rows[0][header.index("Started (UTC)")] is None
    assert rows[0][header.index("Completed (UTC)")] is None


def test_maintenance_with_nothing_in_the_range_is_a_header_and_no_rows(client):
    header, rows = exported(client, "/maintenance/orders/export.xlsx?since=1999-01-01&until=1999-01-31")
    assert header[0] == "Order"
    assert rows == []


def test_maintenance_export_is_for_whoever_may_read_the_list(anon, sign_in):
    assert anon.get("/maintenance/orders/export.xlsx").status_code == 401
    viewer = sign_in("VIEW", role="viewer")
    assert viewer.get("/maintenance/orders").status_code == 200
    assert viewer.get("/maintenance/orders/export.xlsx").status_code == 200


# --------------------------------------------------------- the audit trail


def _audit(session, actor, action, ts):
    row = AuditLog(actor=actor, action=action, entity_type="test", entity_id="1", ts=ts,
                   after={"n": 1})
    session.add(row)
    session.flush()
    return row


def test_the_audit_trail_exports_every_row_of_the_range_not_the_screens_last_few(supervisor, session):
    start = datetime(2026, 9, 1, 6)
    for n in range(650):
        _audit(session, "marek", "test.write", start + timedelta(minutes=n))
    _audit(session, "marek", "test.write", datetime(2026, 8, 31, 23))
    query = "since=2026-09-01&until=2026-09-30"
    # The screen is capped at 500; the file is not.
    assert len(supervisor.get(f"/ops/activity?{query}&limit=5000").json()["entries"]) == 500

    r = supervisor.get(f"/ops/activity/export.xlsx?{query}")
    assert filename(r) == "audit-trail-2026-09-01-to-2026-09-30.xlsx"
    header, rows = exported(supervisor, f"/ops/activity/export.xlsx?{query}")
    assert header[:4] == ["Time (UTC)", "Who", "On behalf of", "Did"]
    assert len(rows) == 650
    assert as_datetime(rows[0][0]) == start
    assert rows[0][header.index("After")] == '{"n": 1}'
    # The demo plant has shifts; a write at 06:00 falls in one, and says which.
    assert rows[0][header.index("Shift")]


def test_the_audit_trail_export_honours_who_and_what(supervisor, session):
    _audit(session, "marek", "order.release", datetime(2026, 9, 2, 8))
    _audit(session, "ines", "order.release", datetime(2026, 9, 2, 9))
    _audit(session, "marek", "check.record", datetime(2026, 9, 2, 10))
    query = "actor=marek&action=order&since=2026-09-02&until=2026-09-02"
    listed = supervisor.get(f"/ops/activity?{query}").json()["entries"]
    _header, rows = exported(supervisor, f"/ops/activity/export.xlsx?{query}")
    assert len(rows) == len(listed) == 1
    assert rows[0][1:4] == ["marek", None, "order.release"]


def test_the_audit_trail_with_nothing_in_the_range_is_a_header_and_no_rows(supervisor):
    header, rows = exported(supervisor, "/ops/activity/export.xlsx?since=1999-01-01&until=1999-01-31")
    assert header[0] == "Time (UTC)"
    assert rows == []


def test_the_audit_trail_export_needs_audit_read_as_the_list_does(anon, sign_in, supervisor):
    assert anon.get("/ops/activity/export.xlsx").status_code == 401
    operator = sign_in("OPER", role="operator")
    assert operator.get("/ops/activity").status_code == 403
    assert operator.get("/ops/activity/export.xlsx").status_code == 403
    assert supervisor.get("/ops/activity/export.xlsx").status_code == 200


# --------------------------------------------------------------------- lots


def test_lots_export_every_lot_the_list_shows_under_its_filters(client, session):
    for n in range(3):
        execution.create_lot(session, code=f"LOT-X-{n}", material_code="FG-COLA", quantity=10 + n)
    session.flush()
    listed = client.get("/execution/lots?q=LOT-X-&limit=500").json()

    r = client.get("/execution/lots/export.xlsx?q=LOT-X-")
    assert filename(r) == "lots-all.xlsx"
    header, rows = exported(client, "/execution/lots/export.xlsx?q=LOT-X-")
    assert header == [c.header for c in LOT_EXPORT]
    assert len(rows) == listed["total"] == 3
    assert [row[0] for row in rows] == [item["code"] for item in listed["items"]]
    assert rows[0][header.index("Made by order")] == "not made here"


def test_every_lot_is_in_the_file_past_one_page(client, session):
    from fsmes.api import paging

    for n in range(paging.MAX_LIMIT + 7):
        execution.create_lot(session, code=f"LOT-P-{n:04d}", material_code="FG-COLA", quantity=1)
    session.flush()
    _header, rows = exported(client, "/execution/lots/export.xlsx?q=LOT-P-")
    assert len(rows) == paging.MAX_LIMIT + 7
    assert len({row[0] for row in rows}) == len(rows)


def test_lots_matching_nothing_are_a_header_and_no_rows(client):
    header, rows = exported(client, "/execution/lots/export.xlsx?q=NO-SUCH-LOT")
    assert header[0] == "Lot"
    assert rows == []


def test_lots_export_is_for_whoever_may_read_the_list(anon, sign_in):
    assert anon.get("/execution/lots/export.xlsx").status_code == 401
    viewer = sign_in("VIEW", role="viewer")
    assert viewer.get("/execution/lots").status_code == 200
    assert viewer.get("/execution/lots/export.xlsx").status_code == 200


# ------------------------------------------------- OEE and the downtime stops


def _down(session, code, *, minutes_ago, minutes, reason=None):
    unit = session.scalar(select(Equipment).where(Equipment.code == code))
    now = utcnow()
    session.add(EquipmentState(equipment_id=unit.id, state=EquipmentStateName.DOWN, reason=reason,
                               started_at=now - timedelta(minutes=minutes_ago),
                               ended_at=now - timedelta(minutes=minutes_ago - minutes)))
    session.flush()


def test_oee_exports_one_row_per_station_the_panel_shows(client, session):
    panel = client.get("/analysis/oee?hours=8").json()
    r = client.get("/analysis/oee/export.xlsx?hours=8")
    assert re.fullmatch(r"oee-by-station-[A-Z0-9_-]+-\d{4}-\d\d-\d\d-\d{4}-to-\d{4}-\d\d-\d\d-\d{4}\.xlsx",
                        filename(r)), filename(r)
    header, rows = exported(client, "/analysis/oee/export.xlsx?hours=8")
    assert header[:3] == ["Station", "Name", "OEE"]
    assert [row[0] for row in rows] == [s["code"] for s in panel["stations"]]
    # A station the MES cannot rate is an empty cell, never a zero.
    for row, station in zip(rows, panel["stations"], strict=True):
        if station["oee"] is None:
            assert row[2] is None


def test_the_downtime_stops_add_up_to_the_paretos_total(client, session):
    _down(session, "MIX01", minutes_ago=30, minutes=10)
    _down(session, "PACK01", minutes_ago=20, minutes=2, reason="blade change")
    pareto = client.get("/analysis/downtime?hours=8").json()
    r = client.get("/analysis/downtime/export.xlsx?hours=8")
    assert filename(r).startswith("downtime-")
    header, rows = exported(client, "/analysis/downtime/export.xlsx?hours=8")
    assert header[:3] == ["Machine", "Started (UTC)", "Ended (UTC)"]
    seconds = header.index("Seconds in window")
    assert sum(row[seconds] for row in rows) == pytest.approx(pareto["total_seconds"], abs=1)
    by_machine = {row[0]: row for row in rows}
    assert by_machine["MIX01"][header.index("Reason")] == "unlabelled"
    assert by_machine["MIX01"][header.index("Named by")] == "nobody"
    assert by_machine["PACK01"][header.index("Reason")] == "blade change"


def test_a_window_with_no_stops_is_a_header_and_no_rows(client, session):
    for state in session.scalars(select(EquipmentState)):
        if state.state == EquipmentStateName.DOWN:
            session.delete(state)
    session.flush()
    header, rows = exported(client, "/analysis/downtime/export.xlsx?hours=8")
    assert header[0] == "Machine"
    assert rows == []


def test_the_analysis_exports_are_for_whoever_may_read_the_analysis(anon, sign_in):
    viewer = sign_in("VIEW", role="viewer")
    for path in ("/analysis/oee", "/analysis/downtime"):
        assert anon.get(f"{path}/export.xlsx?hours=8").status_code == 401
        assert viewer.get(f"{path}?hours=8").status_code == 200
        assert viewer.get(f"{path}/export.xlsx?hours=8").status_code == 200


def test_no_export_changes_the_list_it_was_made_from(client, session):
    _nc(session, "unchanged", datetime(2026, 9, 2, 8))
    before = client.get("/quality/nonconformances?limit=500").json()
    client.get("/quality/nonconformances/export.xlsx")
    assert client.get("/quality/nonconformances?limit=500").json() == before
    assert session.scalar(select(NonConformance).where(NonConformance.description == "unchanged"))
