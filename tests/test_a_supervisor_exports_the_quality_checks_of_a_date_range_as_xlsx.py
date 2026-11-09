"""A supervisor exports two months of quality checks as an xlsx file.

The first card the pretend supervisor left (2026-11-08): Inspection History
had no export, and the date range it did have sat behind a "When" toggle the
walker read as a sort. The file is the list's own query without its paging,
so every row the table would show for that range is a row in the file, under
one header row, on the plant's own clock.

Each file here is read back as a zip with the standard library only, the way
any reader of the format has to: if these tests can find every row, so can
Excel and LibreOffice (both were opened by hand on the PR).
"""

import io
import re
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree

import pytest

from fsmes import xlsx
from fsmes.api.routers.quality import EXPORT_COLUMNS
from fsmes.domain import QualityCheck
from fsmes.services import quality

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EXPORT = "/quality/checks/export.xlsx"


def read_sheet(body: bytes) -> list[list]:
    """Every row of the one sheet, as Python values: str, float, or None."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"} <= names
        sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for number, row in enumerate(sheet.find("m:sheetData", NS), start=1):
        assert row.get("r") == str(number)
        cells: dict[int, object] = {}
        for cell in row:
            letters = re.match(r"[A-Z]+", cell.get("r")).group()
            index = 0
            for ch in letters:
                index = index * 26 + ord(ch) - 64
            if cell.get("t") == "inlineStr":
                value = cell.find("m:is/m:t", NS).text or ""
            else:
                value = float(cell.find("m:v", NS).text)
            cells[index - 1] = value
        rows.append(cells)
    # Short rows are padded to the header: a trailing empty cell is not written.
    width = max((max(c) + 1 for c in rows if c), default=0)
    return [[c.get(i) for i in range(width)] for c in rows]


def as_datetime(serial: float) -> datetime:
    """A date cell back to the second, which is what a spreadsheet shows."""
    return datetime(1899, 12, 30) + timedelta(seconds=round(serial * 86400))


def check_at(session, ts: datetime, value: float = 10.2, **kw) -> QualityCheck:
    check, _nc, _signals = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=value,
        actor=kw.pop("actor", "ines"), **kw)
    check.ts = ts
    session.flush()
    return check


@pytest.fixture()
def chicago(monkeypatch):
    """A plant five hours behind Greenwich in September (daylight time).

    Set on the live settings rather than through the environment: a fresh
    settings object carries a fresh signing secret, and the signed-in client
    would be signed out by it.
    """
    from fsmes.config import get_settings

    monkeypatch.setattr(get_settings(), "plant_timezone", "America/Chicago")


def test_the_file_has_one_header_row_then_one_row_per_check_in_the_range(client, session):
    inside = [check_at(session, datetime(2026, 9, 2, 8, 0)),
              check_at(session, datetime(2026, 10, 15, 14, 30), value=13.0),
              check_at(session, datetime(2026, 10, 31, 23, 59, 59))]
    check_at(session, datetime(2026, 8, 31, 23, 59, 59))
    check_at(session, datetime(2026, 11, 1, 0, 0, 0))

    r = client.get(f"{EXPORT}?since=2026-09-01&until=2026-10-31")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert 'filename="quality-checks-2026-09-01-to-2026-10-31.xlsx"' in r.headers["content-disposition"]
    assert r.headers["x-total-count"] == "3"

    rows = read_sheet(r.content)
    assert rows[0][0] == "When (UTC)"
    assert tuple(rows[0][1:]) == EXPORT_COLUMNS[1:]
    assert len(rows) == 1 + len(inside)
    # Oldest first, as somebody reading two months down a page expects.
    assert [as_datetime(row[0]) for row in rows[1:]] == [c.ts for c in inside]


def test_the_file_holds_exactly_the_rows_the_list_shows_for_the_same_filters(client, session):
    for day in range(1, 29):
        check_at(session, datetime(2026, 9, day, 10), value=13.0 if day % 4 == 0 else 10.2)
    query = "since=2026-09-05&until=2026-09-20&result=fail"
    listed = client.get(f"/quality/checks?{query}&limit=500").json()
    rows = read_sheet(client.get(f"{EXPORT}?{query}").content)[1:]
    assert listed["total"] == len(rows) == 4
    assert {r[3] for r in rows} == {13.0}
    assert {r[7] for r in rows} == {"fail"}


def test_a_row_carries_the_spec_limits_who_the_order_the_machine_and_the_hold(client, session):
    from fsmes.services import workorders

    wo = workorders.create(session, code="WO-EXPORT-1", material_code="FG-COLA", quantity=10, actor="test")
    session.flush()
    check = check_at(session, datetime(2026, 9, 3, 9, 0), value=13.0, work_order_code=wo.code,
                     equipment_code="MIX01", actor="marek")
    spec = check.spec
    row = read_sheet(client.get(f"{EXPORT}?since=2026-09-03&until=2026-09-03").content)[1]
    held = client.get("/quality/nonconformances?limit=500").json()["items"]
    hold = next(nc for nc in held if (nc.get("evidence") or {}).get("check") == check.id)

    assert row[1:] == ["FG-COLA", "brix", 13.0, spec.unit or None, spec.min_value, spec.max_value,
                       "fail", "marek", "WO-EXPORT-1", "MIX01", "not recorded", hold["code"]]


def test_a_missing_limit_is_an_empty_cell_and_never_a_zero(client, session):
    quality.create_spec(session, material_code="FG-COLA", characteristic="cap_torque",
                        unit="Nm", min_value=None, max_value=2.5, actor="test")
    session.flush()
    check, _nc, _ = quality.record_check(session, material_code="FG-COLA", characteristic="cap_torque",
                                          value=1.9, actor="ines")
    check.ts = datetime(2026, 9, 4, 7, 0)
    session.flush()
    row = read_sheet(client.get(f"{EXPORT}?since=2026-09-04&until=2026-09-04").content)[1]
    assert row[2] == "cap_torque"
    assert row[5] is None
    assert row[6] == 2.5
    # A pass has no hold; the cell is empty, not "not linked".
    assert row[12] is None
    assert row[9] == "no order"


def test_an_empty_range_is_a_file_with_the_header_and_no_rows(client, session):
    check_at(session, datetime(2026, 9, 2, 8, 0))
    r = client.get(f"{EXPORT}?since=2025-01-01&until=2025-02-28")
    assert r.status_code == 200
    assert r.headers["x-total-count"] == "0"
    rows = read_sheet(r.content)
    assert len(rows) == 1
    assert tuple(rows[0][1:]) == EXPORT_COLUMNS[1:]


def test_a_date_is_a_day_on_the_plants_clock_in_the_range_and_in_the_cells(client, session, chicago):
    # 03:00 UTC on the 2nd is 22:00 on the 1st in Chicago (CDT, UTC-5): the
    # 1st's last hours, not the 2nd's first.
    late_on_the_first = check_at(session, datetime(2026, 9, 2, 3, 0))
    early_on_the_second = check_at(session, datetime(2026, 9, 2, 6, 0))

    first = read_sheet(client.get(f"{EXPORT}?since=2026-09-01&until=2026-09-01").content)
    assert first[0][0] == "When (America/Chicago)"
    assert len(first) == 2
    assert as_datetime(first[1][0]) == datetime(2026, 9, 1, 22, 0)

    second = client.get("/quality/checks?since=2026-09-02&until=2026-09-02").json()
    assert second["total"] == 1
    assert second["items"][0]["ts"].startswith(early_on_the_second.ts.isoformat()[:16])
    assert late_on_the_first.id != early_on_the_second.id


def test_a_date_and_time_still_means_an_instant_in_utc(client, session, chicago):
    check_at(session, datetime(2026, 9, 2, 3, 0))
    body = client.get("/quality/checks?since=2026-09-02T00:00:00&until=2026-09-02T23:59:59").json()
    assert body["total"] == 1


def test_a_range_that_is_not_a_date_is_refused_in_words(client):
    r = client.get(f"{EXPORT}?since=last-tuesday")
    assert r.status_code == 400
    assert "last-tuesday" in r.text and "2026-09-01" in r.text


def test_anybody_who_may_read_the_checks_may_export_them_and_nobody_else(anon, sign_in, session):
    check_at(session, datetime(2026, 9, 2, 8, 0))
    assert anon.get(EXPORT).status_code == 401
    viewer = sign_in("VIEW", role="viewer")
    assert viewer.get("/quality/checks").status_code == 200
    r = viewer.get(EXPORT)
    assert r.status_code == 200
    assert len(read_sheet(r.content)) == 2


def test_a_failed_check_with_no_recorded_hold_says_not_linked(client, session):
    check = check_at(session, datetime(2026, 9, 5, 8, 0), value=13.0)
    # A hold raised before checks named their reading carried no evidence.
    for nc in client.get("/quality/nonconformances?limit=500").json()["items"]:
        if (nc.get("evidence") or {}).get("check") == check.id:
            from fsmes.domain import NonConformance
            session.query(NonConformance).filter_by(code=nc["code"]).update({"evidence": None})
    session.flush()
    session.expire_all()
    row = read_sheet(client.get(f"{EXPORT}?since=2026-09-05&until=2026-09-05").content)[1]
    assert row[7] == "fail" and row[12] == "not linked"


def test_every_reading_of_a_sample_names_the_hold_the_sample_opened(client, session):
    quality.create_spec(session, material_code="FG-COLA", characteristic="fill_ml", unit="ml",
                        min_value=495, max_value=505, sample_size=3, actor="test")
    session.flush()
    _sample, _checks, nc, _ = quality.record_sample(
        session, material_code="FG-COLA", characteristic="fill_ml", values=[500, 510, 499], actor="ines")
    session.flush()
    assert nc is not None
    rows = read_sheet(client.get(f"{EXPORT}?characteristic=fill_ml").content)[1:]
    assert len(rows) == 3
    assert {r[12] for r in rows} == {nc.code}


def test_the_writer_keeps_text_numbers_dates_and_gaps_apart():
    body = xlsx.workbook("Checks: 2/3", ["a", "b", "c", "d"],
                         [["<&> \"quoted\"", 0, None, datetime(2026, 9, 1, 6, 30)],
                          ["ünïcode °C", 1.25, -3, None]])
    rows = read_sheet(body)
    assert rows[0] == ["a", "b", "c", "d"]
    assert rows[1][:2] == ["<&> \"quoted\"", 0.0] and rows[1][2] is None
    assert as_datetime(rows[1][3]) == datetime(2026, 9, 1, 6, 30)
    assert rows[2] == ["ünïcode °C", 1.25, -3.0, None]
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        workbook = z.read("xl/workbook.xml").decode()
    # Excel refuses a sheet name with a colon or a slash in it.
    assert 'name="Checks 23"' in workbook


def test_the_screen_has_a_visible_date_range_and_an_export_beside_the_filters():
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
    html = (web / "quality.html").read_text(encoding="utf-8")
    bar = html[html.index('data-assist="history-filters"'):html.index('<div class="table-wrap">',
                                                                       html.index("Inspection history"))]
    assert 'id="h-from"' in bar and 'id="h-to"' in bar
    assert "h-when-toggle" not in html
    assert 'id="h-export"' in bar and "Export xlsx" in bar
    js = (web / "quality.js").read_text(encoding="utf-8")
    assert "/quality/checks/export.xlsx" in js
