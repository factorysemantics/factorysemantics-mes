"""Reading a draft before signing it, and being walked through what changes.

The failure this exists to end: the *Waiting for you* panel shipped with a
row and an approve button. The row said a draft existed and nothing about
what it would do to the plant, so putting a vocabulary in front of every
operator was one click on a line of text. The design had already named what a
person is shown before they sign - the diff, the coverage, what is retired
and how many records carry it - and none of it was there.

What is pinned here is that reading, and the walk generated from it: a step
per change, the button last, every step pointing at a control the screen
actually has, and nothing in any of it written by a model.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import (
    DowntimeReason,
    Equipment,
    EquipmentState,
    EquipmentStateName,
)
from fsmes.services import NotFound, reasons, review

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _anchors_on_the_dashboard() -> set[str]:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return set(re.findall(r'data-assist="([^"]+)"', html))


def _in_force(session, code, name, description=""):
    """A reason the plant is living with, the way a plant gets one."""
    reasons.define(session, code=code, name=name, description=description, actor="ENG")
    reasons.approve(session, code, reasons.open_draft(session, code).revision,
                    actor="ADMIN")
    session.flush()


def _down_with(session, machine, code, minutes_ago=60, minutes=10):
    unit = session.scalar(select(Equipment).where(Equipment.code == machine))
    now = utcnow()
    session.add(EquipmentState(
        equipment_id=unit.id, state=EquipmentStateName.DOWN, reason=code,
        reason_code=code, started_at=now - timedelta(minutes=minutes_ago),
        ended_at=now - timedelta(minutes=minutes_ago - minutes)))
    session.flush()


# ------------------------------------------------------------- the reading


def test_the_panel_hands_each_row_the_path_that_opens_its_review(admin, session):
    """The row carries where to read it, the same way it carries where to sign
    it - so one panel serves several kinds without a screen knowing any of
    their paths."""
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()

    item = admin.get("/dashboard/pending-approvals").json()["items"][0]
    assert item["review"] == "/dashboard/pending-approvals/downtime_reason/jam_infeed/1"
    assert admin.get(item["review"]).status_code == 200


def test_a_change_to_a_reason_reads_as_what_the_plant_says_now_and_what_it_would_say(
        admin, session):
    """A diff, not a JSON dump: both values, in the plant's own words."""
    _in_force(session, "jam_infeed", "Jam at the infeed", "Something stuck at the infeed.")
    reasons.define(session, code="jam_infeed", name="Infeed jam",
                   description="Product stuck where it enters the machine.", actor="ENG")
    session.flush()

    body = admin.get("/dashboard/pending-approvals/downtime_reason/jam_infeed/2").json()
    fields = {change["field"]: change for change in body["changes"]}
    assert fields["name"]["before"] == "Jam at the infeed"
    assert fields["name"]["after"] == "Infeed jam"
    assert fields["description"]["before"] == "Something stuck at the infeed."
    assert body["drafted_by"] == "ENG"


def test_a_draft_drafted_on_somebodys_behalf_says_so_in_the_review(sign_in, admin, session):
    """Who drafted it and for whom, where the person signing it reads it."""
    agent = sign_in("AGENT-REVIEW", role="agent")
    agent.post("/equipment/downtime-reasons",
               json={"code": "jam_infeed", "name": "Jam at the infeed"},
               headers={"X-On-Behalf-Of": "ADMIN"})

    body = admin.get("/dashboard/pending-approvals/downtime_reason/jam_infeed/1").json()
    assert body["drafted_by"] == "AGENT-REVIEW" and body["on_behalf_of"] == "ADMIN"


def test_a_new_code_says_that_the_plant_has_nothing_like_it_today(admin, session):
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()

    body = admin.get("/dashboard/pending-approvals/downtime_reason/jam_infeed/1").json()
    first = body["changes"][0]
    assert first["field"] == "code" and first["before"] is None
    assert body["supersedes"] is None and body["undo"] is None


def test_the_review_says_how_large_the_list_is_now_and_how_large_it_would_be(
        admin, session):
    """One number would let a plant read a list that grows and a list that
    shrinks as the same act."""
    _in_force(session, "breakdown", "Breakdown")
    _in_force(session, "starved", "Starved")
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()

    coverage = admin.get(
        "/dashboard/pending-approvals/downtime_reason/jam_infeed/1").json()["coverage"]
    assert coverage["vocabulary_total"] == 2
    assert coverage["vocabulary_total_after"] == 3


def test_a_retirement_states_how_many_recorded_intervals_it_would_affect(
        admin, session):
    """PR #85 made the number exist. This is where the person signing it reads
    it - counted now, beside what the draft said when it was written."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    _down_with(session, "MIX01", "breakdown", minutes_ago=120)
    reasons.define(session, code="breakdown", name="Breakdown", retires=True,
                   labels_intervals=2, actor="ENG")
    session.flush()

    body = admin.get("/dashboard/pending-approvals/downtime_reason/breakdown/2").json()
    assert body["affected"]["intervals_labelled"] == 2
    assert body["affected"]["stated_in_the_draft"] == 2
    assert body["affected"]["moved_since_the_draft"] is False
    assert body["coverage"]["vocabulary_total_after"] == 0
    assert "2 recorded intervals" in body["changes"][0]["note"]


def test_history_that_moved_while_the_draft_waited_is_said_rather_than_hidden(
        admin, session):
    """A draft written against 2 intervals and signed against 3 was written
    about a smaller plant. An approver reading one number would not know
    which number it was."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Breakdown", retires=True,
                   labels_intervals=1, actor="ENG")
    session.flush()
    _down_with(session, "MIX01", "breakdown", minutes_ago=200)

    affected = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()["affected"]
    assert affected["intervals_labelled"] == 2
    assert affected["stated_in_the_draft"] == 1
    assert affected["moved_since_the_draft"] is True


def test_the_revision_it_would_supersede_is_one_click_away_from_the_review(
        admin, session):
    """Undo is approving the previous revision. It is in the same reading as
    the change, not a hunt through a history screen."""
    _in_force(session, "jam_infeed", "Jam at the infeed")
    reasons.define(session, code="jam_infeed", name="Infeed jam", actor="ENG")
    session.flush()

    body = admin.get("/dashboard/pending-approvals/downtime_reason/jam_infeed/2").json()
    assert body["supersedes"]["revision"] == 1
    assert body["supersedes"]["name"] == "Jam at the infeed"
    assert body["undo"] == "/equipment/downtime-reasons/jam_infeed/approve/1"
    # And it works: the undo path is a real approval of the older revision.
    assert admin.post(body["approve"]).status_code == 200
    assert admin.post(body["undo"]).status_code == 200
    assert reasons.in_force(session, "jam_infeed").revision == 1


# --------------------------------------------------------------- the walk


def test_a_two_field_change_walks_two_steps_and_then_the_button(admin, session):
    """One step per change, the approve control last. The count is the diff's,
    so a walk can never be longer or shorter than what is about to happen."""
    _in_force(session, "jam_infeed", "Jam at the infeed", "Stuck at the infeed.")
    reasons.define(session, code="jam_infeed", name="Infeed jam",
                   description="Product stuck where it enters.", actor="ENG")
    session.flush()

    walk = admin.get(
        "/dashboard/pending-approvals/downtime_reason/jam_infeed/2").json()["walkthrough"]
    assert len(walk["steps"]) == 3
    assert [step["nth"] for step in walk["steps"][:2]] == [0, 1]
    assert walk["steps"][-1]["anchor"] == "review-approve"
    assert "nth" not in walk["steps"][-1]


def test_every_generated_step_points_at_a_control_the_screen_actually_has(
        admin, session):
    """The same promise the authored guides keep: a step points at a real
    control on a real screen, or it teaches somebody the wrong habit."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Breakdown", retires=True,
                   labels_intervals=1, actor="ENG")
    reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                   description="Stuck at the infeed.", actor="ENG")
    session.flush()

    present = _anchors_on_the_dashboard()
    for code, revision in (("breakdown", 2), ("jam_infeed", 1)):
        walk = admin.get(
            f"/dashboard/pending-approvals/downtime_reason/{code}/{revision}"
        ).json()["walkthrough"]
        for index, step in enumerate(walk["steps"], 1):
            assert step["page"] == "/dashboard"
            assert step["anchor"] in present, (
                f"{code} rev {revision} step {index} points at "
                f"data-assist={step['anchor']!r}, which index.html does not have")
            assert step["title"] and step["body"]


def test_the_walk_says_both_values_rather_than_a_summary_of_them(admin, session):
    """Deterministic, from the diff itself. Nothing here is narrated by a
    model: decisions 0031 and 0032 hold, and what is about to be signed is the
    one place in this product that may not be paraphrased."""
    _in_force(session, "jam_infeed", "Jam at the infeed")
    reasons.define(session, code="jam_infeed", name="Infeed jam", actor="ENG")
    session.flush()

    walk = admin.get(
        "/dashboard/pending-approvals/downtime_reason/jam_infeed/2").json()["walkthrough"]
    assert "Jam at the infeed" in walk["steps"][0]["body"]
    assert "Infeed jam" in walk["steps"][0]["body"]
    assert walk["generated"] is True
    assert walk["id"] == "review:downtime_reason:jam_infeed:2"


def test_a_walk_built_twice_from_one_draft_says_the_same_thing_twice(session):
    """A generated guide is a function of the draft, not of the reading."""
    _in_force(session, "jam_infeed", "Jam at the infeed")
    reasons.define(session, code="jam_infeed", name="Infeed jam", actor="ENG")
    session.flush()

    once = review.review(session, "downtime_reason", "jam_infeed", 2)["walkthrough"]
    twice = review.review(session, "downtime_reason", "jam_infeed", 2)["walkthrough"]
    assert once == twice


def test_the_last_step_of_a_walk_says_what_undoing_it_would_take(session):
    _in_force(session, "jam_infeed", "Jam at the infeed")
    reasons.define(session, code="jam_infeed", name="Infeed jam", actor="ENG")
    session.flush()

    walk = review.review(session, "downtime_reason", "jam_infeed", 2)["walkthrough"]
    assert "revision 1" in walk["steps"][-1]["body"]


# ------------------------------------------------------------- who may read


def test_a_caller_who_cannot_approve_the_kind_cannot_open_its_review(
        sign_in, session):
    """A review is a reading of configuration nobody has put in force. A panel
    that refuses the button while showing the substance is a panel that leaks
    the draft."""
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()

    operator = sign_in("OP-REVIEW", role="operator")
    refused = operator.get("/dashboard/pending-approvals/downtime_reason/jam_infeed/1")
    assert refused.status_code == 403
    assert "process.approve" in refused.json()["detail"]


def test_an_agent_may_draft_one_and_still_may_not_read_the_review(sign_in, session):
    """The agent role holds the drafting half and never the approving half."""
    agent = sign_in("AGENT-READ", role="agent")
    agent.post("/equipment/downtime-reasons",
               json={"code": "jam_infeed", "name": "Jam at the infeed"})
    assert agent.get(
        "/dashboard/pending-approvals/downtime_reason/jam_infeed/1").status_code == 403


def test_a_revision_that_does_not_exist_is_not_found(admin, session):
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()
    assert admin.get(
        "/dashboard/pending-approvals/downtime_reason/jam_infeed/9").status_code == 404


def test_a_kind_nothing_here_reviews_is_not_found(admin):
    """And it is a 404, not a 403: refusing a kind that does not exist with a
    capability message would tell a caller that it does."""
    assert admin.get(
        "/dashboard/pending-approvals/work_instruction/DOC-1/1").status_code == 404


# ------------------------------------------------------ the extension point


def test_every_kind_the_panel_lists_is_a_kind_it_can_also_show(admin):
    """The whole point. A kind that can be listed but not read is the blind
    signature this change exists to end, so the two come from one registry."""
    kinds = admin.get("/dashboard/pending-approvals").json()["kinds_known"]
    assert kinds == sorted(review.KINDS)
    for name in kinds:
        assert callable(review.KINDS[name].waiting)
        assert callable(review.KINDS[name].review)


def test_a_kind_registers_its_own_diff_and_inherits_the_walk(session):
    """A second kind writes a reviewer. The walk reads change rows and nothing
    else, so it needs no change to carry one."""
    made_up = {
        "kind": "made_up", "code": "X-1", "revision": 3,
        "changes": [
            {"field": "a", "label": "The first thing", "before": "one", "after": "two"},
            {"field": "b", "label": "The second thing", "before": None, "after": "three"},
        ],
        "supersedes": {"revision": 2},
    }
    walk = review.walkthrough(made_up)
    assert [step["title"] for step in walk["steps"]] == [
        "The first thing", "The second thing", "Sign it, or leave it waiting"]
    assert walk["id"] == "review:made_up:X-1:3"


def test_a_kind_may_point_a_step_at_a_control_of_its_own(session):
    """A change that has a screen elsewhere says so, and the step goes there
    instead of at the review panel's row."""
    walk = review.walkthrough({
        "kind": "made_up", "code": "X-1", "revision": 1, "supersedes": None,
        "changes": [{"field": "a", "label": "Over there", "before": None, "after": "x",
                     "page": "/dashboard/station", "anchor": "station-reason-code"}],
    })
    assert walk["steps"][0]["page"] == "/dashboard/station"
    assert walk["steps"][0]["anchor"] == "station-reason-code"
    assert "nth" not in walk["steps"][0]


def test_reviewing_a_kind_the_registry_does_not_hold_is_refused(session):
    with pytest.raises(NotFound):
        review.review(session, "nothing_like_it", "X-1", 1)


def test_an_already_signed_revision_reviews_against_what_is_in_force(session):
    """Reviewing is not only for drafts: somebody about to undo is owed the
    same reading of what it would change."""
    _in_force(session, "jam_infeed", "Jam at the infeed")
    reasons.define(session, code="jam_infeed", name="Infeed jam", actor="ENG")
    reasons.approve(session, "jam_infeed", 2, actor="ADMIN")
    session.flush()

    # Revision 1 is superseded. Putting it back would change the name again.
    body = review.review(session, "downtime_reason", "jam_infeed", 1)
    assert body["supersedes"]["revision"] == 2
    assert body["changes"][0]["after"] == "Jam at the infeed"
    assert body["status"] == "superseded"
    assert body["waiting_seconds"] is None


def test_the_row_and_the_review_tell_the_same_story_about_a_draft(admin, session):
    """One headline, one place it is built. The panel's row and the review
    were two chances to say different things about the same draft."""
    from fsmes.domain import DowntimeReasonStatus  # noqa: F401  (documents intent)

    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()
    row = session.scalar(select(DowntimeReason).where(DowntimeReason.code == "jam_infeed"))
    row.created_at = utcnow() - timedelta(days=11)
    session.flush()

    item = admin.get("/dashboard/pending-approvals").json()["items"][0]
    body = admin.get(item["review"]).json()
    assert body["headline"] == item["headline"]
    assert body["title"] == item["title"]
    assert body["waiting_seconds"] > 11 * 24 * 3600 - 60
