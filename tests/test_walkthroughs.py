"""Recorded walkthroughs: a supervisor's SWI, stored as a controlled document
and played by the assistant.

The property under test: a walkthrough is only ever stored if every step can
be played - page and anchor exist in the plant's own HTML - and a trainee is
only offered the revision in force, and only if they may do the task.
"""

import pytest

from fsmes.services import Invalid, assistant, documents, walkthroughs

STEPS = [
    {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-machine",
     "title": "The machine that broke", "body": "Pick it here.", "fill": {"value": "FILL01"}},
    {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-summary",
     "title": "What needs doing", "body": "One line a fitter can act on."},
    {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-submit",
     "title": "Press Raise", "body": "It is raised under your name."},
]


def record(session, code="SWI-CORR", needs="maintenance.perform", steps=STEPS, actor="SUP"):
    return documents.create(session, code=code, title="Raise corrective work the way we do it",
                            body="raising corrective work, something broke, fitter job",
                            kind="walkthrough", steps=steps, needs=needs, actor=actor)


# ----------------------------------------------------------- only playable steps are stored

def test_every_page_in_the_map_is_a_real_file():
    for page, name in walkthroughs.PAGE_FILES.items():
        assert (walkthroughs.WEB / name).is_file(), f"{page} -> {name} does not exist"
        assert walkthroughs.anchors_on(page), f"{name} has no data-assist anchors at all"


def test_a_walkthrough_is_stored_clean(session):
    doc = record(session)
    assert doc.kind == "walkthrough" and doc.needs == "maintenance.perform"
    steps = doc.steps_list()
    assert [s["anchor"] for s in steps] == [s["anchor"] for s in STEPS]
    assert steps[0]["fill"] == {"value": "FILL01"} and "fill" not in steps[1]
    assert steps[0]["tab"] == "work"


@pytest.mark.parametrize("bad, message", [
    ([{**STEPS[0], "anchor": "no-such-control"}], "has no control"),
    ([{**STEPS[0], "page": "/dashboard/nowhere"}], "unknown page"),
    ([{**STEPS[0], "title": ""}], "no title"),
    ([{**STEPS[0], "open": "not-a-thing"}], "nothing to open"),
    ([{**STEPS[0], "fill": "FILL01"}], "fill must be"),
    ([{**STEPS[0], "selector": "#x"}], "unknown field"),
    ([], "at least one step"),
])
def test_an_unplayable_step_is_refused_by_name(session, bad, message):
    with pytest.raises(Invalid, match=message):
        record(session, steps=bad)


def test_an_unknown_capability_is_refused(session):
    with pytest.raises(Invalid, match="unknown capability"):
        record(session, needs="wizardry")


def test_an_instruction_may_not_carry_steps(session):
    with pytest.raises(Invalid, match="only a walkthrough"):
        documents.create(session, code="WI-X", title="x", body="", steps=STEPS)


# ------------------------------------------------------------- the assistant plays it

def test_a_trainee_is_offered_only_the_revision_in_force(session):
    record(session)
    caps = {"plant.read", "maintenance.perform"}
    assert not [g for g in assistant.visible_guides(caps, session) if g["id"] == "doc:SWI-CORR"]
    documents.approve(session, "SWI-CORR", 1, actor="ADMIN")
    offered = [g for g in assistant.visible_guides(caps, session) if g["id"] == "doc:SWI-CORR"]
    assert offered and offered[0]["recorded_by"] == "SUP" and offered[0]["approved_by"] == "ADMIN"
    assert offered[0]["steps"][0]["anchor"] == "maintenance-cm-machine"
    # never a task the person will be refused
    assert not [g for g in assistant.visible_guides({"plant.read"}, session) if g["id"] == "doc:SWI-CORR"]
    assert assistant.guide_by_id("doc:SWI-CORR", caps, session)["revision"] == 1
    assert assistant.guide_by_id("doc:SWI-CORR", {"plant.read"}, session) is None
    assert assistant.guide_by_id("record-check", {"plant.read", "quality.record"})["id"] == "record-check"


def test_a_revision_keeps_the_old_one_in_force_until_approved(session):
    record(session)
    documents.approve(session, "SWI-CORR", 1, actor="ADMIN")
    documents.revise(session, "SWI-CORR", steps=STEPS[:2], actor="SUP")
    caps = {"plant.read", "maintenance.perform"}
    assert len(assistant.guide_by_id("doc:SWI-CORR", caps, session)["steps"]) == 3
    documents.approve(session, "SWI-CORR", 2, actor="ADMIN")
    assert len(assistant.guide_by_id("doc:SWI-CORR", caps, session)["steps"]) == 2
    documents.withdraw(session, "SWI-CORR", actor="ADMIN")
    assert assistant.guide_by_id("doc:SWI-CORR", caps, session) is None


def test_how_do_i_routes_to_a_recorded_walk(session, monkeypatch):
    record(session)
    documents.approve(session, "SWI-CORR", 1, actor="ADMIN")
    monkeypatch.setattr(assistant, "_ask_model", lambda prompt, timeout=60.0: "doc:swi-corr")
    caps = {"plant.read", "maintenance.perform"}
    found = assistant.route("how do I raise corrective work?", caps, session)
    assert found and found["id"] == "doc:SWI-CORR"
    monkeypatch.setattr(assistant, "_ask_model", lambda prompt, timeout=60.0: None)
    found = assistant.route("show me raising corrective work", caps, session)
    assert found and found["id"] == "doc:SWI-CORR"


# ---------------------------------------------------------------------- the api

def test_recording_through_the_api(supervisor, admin):
    made = supervisor.post("/documents", json={
        "code": "SWI-API", "title": "Corrective work, our way", "body": "corrective work",
        "kind": "walkthrough", "steps": STEPS, "needs": "maintenance.perform"})
    assert made.status_code == 201, made.text
    assert made.json()["kind"] == "walkthrough" and len(made.json()["steps"]) == 3
    bad = supervisor.post("/documents", json={
        "code": "SWI-BAD", "title": "x", "kind": "walkthrough",
        "steps": [{**STEPS[0], "anchor": "gone"}], "needs": "maintenance.perform"})
    assert bad.status_code in (400, 409, 422) and "gone" in bad.text
    assert not [g for g in admin.get("/assist/guides").json()["guides"] if g["id"] == "doc:SWI-API"]
    assert admin.post("/documents/SWI-API/approve/1").status_code == 200
    guides = admin.get("/assist/guides").json()["guides"]
    assert any(g["id"] == "doc:SWI-API" and g["kind"] == "walkthrough" for g in guides)
    played = admin.get("/assist/guides/doc:SWI-API").json()
    assert played["steps"][2]["anchor"] == "maintenance-cm-submit" and played["recorded_by"]
    listed = [d for d in admin.get("/documents").json() if d["code"] == "SWI-API"]
    assert listed and listed[0]["kind"] == "walkthrough" and listed[0]["needs"] == "maintenance.perform"


# ---------------------------------------------------------------- the recorder

def test_the_recorder_is_wired_for_writers_only():
    web = walkthroughs.WEB
    assist = (web / "assist.js").read_text(encoding="utf-8")
    recorder = (web / "assist-record.js").read_text(encoding="utf-8")
    assert "documents.write" in assist and "/static/assist-record.js" in assist
    assert "window.fsmesAssist" in assist and "headButton" in recorder
    # the recorder only ever records anchored controls and posts the document shape the API validates
    assert '[data-assist]' in recorder and '"/documents"' in recorder and 'kind: "walkthrough"' in recorder


def test_a_recorder_step_shape_is_what_the_server_accepts(session):
    """The recorder's cleanSteps() emits exactly these keys; keep them in step."""
    recorded = [{"page": "/dashboard/orders", "anchor": "order-new-code", "open": "order-create",
                 "title": "Enter Order code", "body": "Codes are yours to choose.", "fill": {"value": "WO-1"}},
                {"page": "/dashboard/orders", "anchor": "order-new-create", "open": "order-create",
                 "title": "Press Create", "body": ""}]
    doc = record(session, code="SWI-REC", needs="orders.create", steps=recorded)
    assert doc.steps_list()[0]["open"] == "order-create" and doc.steps_list()[0]["fill"] == {"value": "WO-1"}
