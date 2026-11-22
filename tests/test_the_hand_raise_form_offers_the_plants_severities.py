"""The Quality page's hand-raise form offers the plant's own severities.

Class 4.2 (2026-11-21) raised a non-conformance by hand and could pick
nothing but "The plant's default": the form asked `/severities`, which is not
an address the API has (the catalogue is under `/quality/severities`, where
the severity editor already asks), the 404 was caught as "no list", and every
hand-raised record came out minor whatever the supervisor meant.

The address is read out of `quality.js` here rather than typed, so the test
asks exactly what the form asks.
"""

import re
from pathlib import Path

from fsmes.services import severities

QUALITY_JS = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web" / "quality.js"


def _the_address_the_form_asks():
    source = QUALITY_JS.read_text(encoding="utf-8")
    body = source.split("async function fillSeverities()", 1)[1].split("\n}\n", 1)[0]
    return re.search(r'api\("([^"]+)"\)', body).group(1)


def test_the_hand_raise_form_asks_an_address_that_answers_with_the_plants_list(admin, session):
    for code, name in (("minor", "Minor"), ("major", "Major")):
        severities.define(session, code=code, name=name, description=f"{name}.", actor="QE")
        severities.approve(session, code, severities.open_draft(session, code).revision, actor="ADMIN")
    session.flush()

    reply = admin.get(_the_address_the_form_asks())
    assert reply.status_code == 200, f"{_the_address_the_form_asks()} answered {reply.status_code}"
    assert reply.json()["names"] == {"minor": "Minor", "major": "Major"}
