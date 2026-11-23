"""The non-conformance list narrows to the severities somebody asks for.

Scott, 2026-11-22: "Why can't I rank the severity on an ncr!?" - and the
walk it proposes: "find the worst ones first". #284 let a reviewer grade
one. This is the other half: the list, its spreadsheet and the Quality
screen take `severity` (repeatable, as `status` is), so "show me the major
ones" is one choice. It is a filter and not a sort on purpose: a plant's
severities are its own words with no order the product could know, and a
sort that guessed which word is worse would be the product deciding.
"""

import re
from pathlib import Path

from tests.test_a_supervisor_exports_the_quality_checks_of_a_date_range_as_xlsx import read_sheet

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _raise(supervisor, what, severity):
    made = supervisor.post("/quality/nonconformances", json={"description": what, "severity": severity})
    assert made.status_code == 201, made.text
    return made.json()["code"]


def test_the_list_returns_only_the_severities_asked_for(supervisor):
    minor = _raise(supervisor, "Scuff on the handle.", "minor")
    major = _raise(supervisor, "Tines bent on the pallet.", "major")

    asked = supervisor.get("/quality/nonconformances?severity=major").json()
    both = supervisor.get("/quality/nonconformances?severity=major&severity=minor").json()

    assert {nc["code"] for nc in asked["items"]} == {major} and asked["total"] == 1
    assert {minor, major} <= {nc["code"] for nc in both["items"]}


def test_the_spreadsheet_has_the_rows_the_filtered_list_has(supervisor):
    _raise(supervisor, "Scuff on the handle.", "minor")
    major = _raise(supervisor, "Tines bent on the pallet.", "major")

    sheet = supervisor.get("/quality/nonconformances/export.xlsx?severity=major")
    assert sheet.status_code == 200, sheet.text
    rows = read_sheet(sheet.content)
    codes = {row[0] for row in rows if row and isinstance(row[0], str) and row[0].startswith("NC")}

    assert codes == {major}


def test_the_quality_screen_offers_the_severity_and_sends_it():
    html = (WEB / "quality.html").read_text(encoding="utf-8")
    script = (WEB / "quality.js").read_text(encoding="utf-8")
    filters = script.split("function ncFilters()", 1)[1].split("\n}\n", 1)[0]

    assert re.search(r'<select id="n-severity"', html), "the Quality screen has no severity filter"
    assert re.search(r'append\("severity"', filters), "the list and its export are not asked for a severity"
