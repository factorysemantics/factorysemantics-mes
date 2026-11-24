"""A supervisor grades a non-conformance when taking it under review.

Scott, 2026-11-22: "Why can't I rank the severity on an ncr!?" A record
kept the severity it was raised with. Every one the MES raises itself - an
out-of-spec reading, an SPC rule - is raised at the plant's default, and
nobody could change it afterwards; class 4.2 found the same gap ("severity
can't be re-graded at review"). Taking it under review is where somebody
first reads the record, so that is where the grade is given: the review
may say which of the plant's severities it is. A word not on the plant's
list is refused and the record is not picked up, and the change is in the
audit trail with the grade before and after.
"""

import re
from pathlib import Path

from sqlalchemy import select

from fsmes.domain.audit import AuditLog
from fsmes.services import severities

QUALITY_JS = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web" / "quality.js"


def _plant_with_two_severities(session):
    for code, name in (("minor", "Minor"), ("major", "Major")):
        severities.define(session, code=code, name=name, description=f"{name}.", actor="QE")
        severities.approve(session, code, severities.open_draft(session, code).revision, actor="ADMIN")
    session.flush()


def _raise(supervisor):
    made = supervisor.post("/quality/nonconformances", json={"description": "Tines bent on the pallet."})
    assert made.status_code == 201, made.text
    return made.json()["code"]


def test_the_review_sets_the_grade_and_the_audit_says_what_it_was(supervisor, session):
    _plant_with_two_severities(session)
    code = _raise(supervisor)

    reviewed = supervisor.post(f"/quality/nonconformances/{code}/review", json={"severity": "major"})

    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "under_review" and reviewed.json()["severity"] == "major"
    assert supervisor.get(f"/quality/nonconformances/{code}").json()["severity"] == "major"
    entry = session.scalars(select(AuditLog).where(AuditLog.entity_id == code,
                                                     AuditLog.action == "nonconformance.under_review")).one()
    assert entry.before["severity"] == "minor" and entry.after["severity"] == "major"


def test_a_grade_not_on_the_plants_list_is_refused_and_nothing_is_picked_up(supervisor, session):
    _plant_with_two_severities(session)
    code = _raise(supervisor)

    refused = supervisor.post(f"/quality/nonconformances/{code}/review", json={"severity": "catastrophic"})

    assert refused.status_code == 400 and "catastrophic" in refused.json()["detail"]
    still = supervisor.get(f"/quality/nonconformances/{code}").json()
    assert still["status"] == "open" and still["severity"] == "minor"


def test_a_review_that_gives_no_grade_keeps_the_one_it_had(supervisor, session):
    _plant_with_two_severities(session)
    code = _raise(supervisor)

    reviewed = supervisor.post(f"/quality/nonconformances/{code}/review")

    assert reviewed.status_code == 200 and reviewed.json()["severity"] == "minor"


def test_the_quality_screen_sends_the_grade_with_the_review():
    source = QUALITY_JS.read_text(encoding="utf-8")
    call = source.split("/review`", 1)[1].split(")", 1)[0]

    assert re.search(r"\bseverity\b", call), "the Quality screen's review sends no severity"
