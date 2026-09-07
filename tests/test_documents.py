"""Controlled work instructions.

The properties that make these *controlled* rather than merely stored: an
approved revision is immutable, history survives a change, and putting a
procedure in force is a different act from writing one.
"""

import pytest

from fsmes.services import capabilities as caps
from fsmes.services import documents


@pytest.fixture()
def wi(session):
    return documents.create(
        session, code="WI-FILL", title="Recording fill weight",
        body="Purpose: record what the scale reads.\n\n1. Weigh it.\n2. Record it.",
        anchors={"material": "FG-COLA", "characteristic": "brix"},
        actor="SUP", drafted_by_model="qwen3:8b")


# ------------------------------------------------------------- the lifecycle

def test_a_new_document_starts_as_an_unapproved_draft(wi):
    assert wi.revision == 1
    assert wi.status.value == "draft"
    # A plant is entitled to know whether a human or a model wrote the words
    # it is following.
    assert wi.drafted_by_model == "qwen3:8b"


def test_nothing_is_in_force_until_it_is_approved(session, wi):
    assert documents.current(session, "WI-FILL") is None
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    assert documents.current(session, "WI-FILL").revision == 1


def test_an_approved_revision_is_never_edited(session, wi):
    """The version somebody signed stays exactly as they signed it."""
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    original = documents.current(session, "WI-FILL").body

    revised = documents.revise(session, "WI-FILL", body="Completely different.",
                               actor="SUP")
    assert revised.revision == 2 and revised.status.value == "draft"
    # Rev 1 is untouched and still the one in force.
    in_force = documents.current(session, "WI-FILL")
    assert in_force.revision == 1 and in_force.body == original


def test_the_floor_keeps_its_instruction_while_a_change_is_being_written(session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    documents.revise(session, "WI-FILL", body="new text", actor="SUP")
    # Mid-change, the floor still has an approved procedure.
    assert documents.current(session, "WI-FILL").revision == 1
    documents.approve(session, "WI-FILL", 2, actor="ADMIN")
    assert documents.current(session, "WI-FILL").revision == 2


def test_superseded_revisions_stay_readable(session, wi):
    """'What did the instruction say in March' is a question a plant has to be
    able to answer."""
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    documents.revise(session, "WI-FILL", body="v2", actor="SUP")
    documents.approve(session, "WI-FILL", 2, actor="ADMIN")

    history = documents.revisions(session, "WI-FILL")
    assert [d.revision for d in history] == [1, 2]
    assert history[0].status.value == "superseded"
    assert "Weigh it" in history[0].body


def test_editing_an_unapproved_draft_does_not_open_another_revision(session, wi):
    """Nobody is following a draft, so it is fine to change it in place."""
    documents.revise(session, "WI-FILL", body="fixed a typo", actor="SUP")
    assert len(documents.revisions(session, "WI-FILL")) == 1


def test_a_document_cannot_be_created_twice(session, wi):
    from fsmes.services import Conflict
    with pytest.raises(Conflict):
        documents.create(session, code="WI-FILL", title="Again", body="x")


def test_approving_something_already_in_force_is_refused(session, wi):
    from fsmes.services import Invalid
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    with pytest.raises(Invalid):
        documents.approve(session, "WI-FILL", 1, actor="ADMIN")


# ----------------------------------------------------------------- anchoring

def test_an_instruction_is_found_by_what_it_is_about(session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    found = documents.for_anchor(session, material="FG-COLA", characteristic="brix")
    assert [d.code for d in found] == ["WI-FILL"]


def test_a_general_instruction_matches_a_specific_question(session):
    """An instruction about a material is relevant to every characteristic of
    it - otherwise the general procedure never surfaces anywhere."""
    documents.create(session, code="WI-MAT", title="Handling FG-COLA", body="x",
                     anchors={"material": "FG-COLA"})
    documents.approve(session, "WI-MAT", 1, actor="ADMIN")
    found = documents.for_anchor(session, material="FG-COLA", characteristic="brix")
    assert "WI-MAT" in [d.code for d in found]


def test_the_most_specific_instruction_comes_first(session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    documents.create(session, code="WI-MAT", title="Handling FG-COLA", body="x",
                     anchors={"material": "FG-COLA"})
    documents.approve(session, "WI-MAT", 1, actor="ADMIN")
    found = documents.for_anchor(session, material="FG-COLA", characteristic="brix")
    assert found[0].code == "WI-FILL"


def test_an_instruction_about_something_else_is_not_offered(session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    assert documents.for_anchor(session, material="FG-OTHER") == []


def test_drafts_are_never_surfaced_as_procedure(session, wi):
    """An unapproved draft must never reach the floor."""
    assert documents.for_anchor(session, material="FG-COLA") == []


def test_an_unknown_anchor_is_refused(session):
    from fsmes.services import Invalid
    with pytest.raises(Invalid, match="unknown anchor"):
        documents.create(session, code="WI-X", title="X", body="",
                         anchors={"colour": "blue"})


# ------------------------------------------------------- powers are separate

def test_writing_and_approving_are_different_powers():
    supervisor = set(caps.BUILTIN_ROLES["supervisor"]["capabilities"])
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    assert "documents.write" in supervisor
    assert "documents.approve" not in supervisor, (
        "drafting a procedure and putting it in force are different jobs")
    assert "documents.approve" in admin


def test_an_operator_can_read_but_not_write(client, session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    assert client.get("/documents").status_code == 200
    assert client.get("/documents/WI-FILL").status_code == 200
    assert client.post("/documents", json={"code": "WI-NEW", "title": "N"}).status_code == 403


def test_a_supervisor_can_draft_but_not_approve(sign_in, session, wi):
    sup = sign_in("SUP1", role="supervisor")
    assert sup.post("/documents/WI-FILL/revise", json={"body": "v2"}).status_code == 200
    assert sup.post("/documents/WI-FILL/approve/1").status_code == 403


def test_the_revision_history_is_readable_over_the_api(client, session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    history = client.get("/documents/WI-FILL/revisions").json()
    assert [h["revision"] for h in history] == [1]
    assert history[0]["approved_by"] == "ADMIN"


def test_approved_lists_everything_in_force(session, wi):
    """Distinct from for_anchor() with no arguments, which answers the
    narrower 'what applies when nothing in particular is being done' and
    correctly returns only the general instructions. Conflating the two told
    the assistant there were no procedures at all."""
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    assert [d.code for d in documents.approved(session)] == ["WI-FILL"]
    # for_anchor with no anchors still returns only un-anchored documents
    assert documents.for_anchor(session) == []


def test_approved_returns_the_revision_in_force_not_the_superseded_one(session, wi):
    documents.approve(session, "WI-FILL", 1, actor="ADMIN")
    documents.revise(session, "WI-FILL", body="v2", actor="SUP")
    documents.approve(session, "WI-FILL", 2, actor="ADMIN")
    in_force = documents.approved(session)
    assert len(in_force) == 1 and in_force[0].revision == 2
