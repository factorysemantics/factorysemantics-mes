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
from fsmes.services import NotFound, reasons, review, walkthroughs

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _anchors_on_the_dashboard() -> set[str]:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return set(re.findall(r'data-assist="([^"]+)"', html))


def _screen(page: str) -> str:
    """A step's page without whatever it asked that screen to show.

    A destination is more than a path now - which machine's station, which
    reason to open - and the anchors belong to the screen, not to the query
    it was asked with."""
    return page.split("?", 1)[0]


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

    for code, revision in (("breakdown", 2), ("jam_infeed", 1)):
        walk = admin.get(
            f"/dashboard/pending-approvals/downtime_reason/{code}/{revision}"
        ).json()["walkthrough"]
        for index, step in enumerate(walk["steps"], 1):
            screen = _screen(step["page"])
            present = walkthroughs.anchors_on(screen)
            assert present, (
                f"{code} rev {revision} step {index} points at {screen!r}, "
                "which is not a screen this product serves")
            assert step["anchor"] in present, (
                f"{code} rev {revision} step {index} points at "
                f"data-assist={step['anchor']!r}, which {screen} does not have")
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


# ------------------------------------------- where the change actually lands


def test_a_change_to_a_word_the_floor_uses_walks_to_the_floors_own_screen(
        admin, session):
    """The point of the walk, in one test. An approver is being asked what a
    change does to the plant, and the plant is the select an operator picks a
    stop from - so the step goes to the station screen and rings that control,
    rather than describing it on the panel the review is read on."""
    _in_force(session, "breakdown", "Breakdown", "The machine has stopped.")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Mechanical breakdown",
                   description="Something on the machine has failed.", actor="ENG")
    session.flush()

    walk = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()["walkthrough"]
    name_step = walk["steps"][0]
    assert name_step["page"] == "/dashboard/station?m=MIX01&reason=breakdown"
    assert name_step["anchor"] == "station-reason-code"
    # And the sentence beside the word rings the sentence, not the word again.
    assert walk["steps"][1]["anchor"] == "station-reason-help"
    # The anchor is on the real select, not on a card that describes it.
    station = (WEB / "station.html").read_text(encoding="utf-8")
    assert 'id="down-reason-code" data-assist="station-reason-code"' in station
    # Both values still, in the words the plant uses for them.
    assert "Breakdown." in name_step["body"] and "Mechanical breakdown." in name_step["body"]


def test_the_machine_the_walk_stands_on_is_the_one_that_records_the_stop_most(
        admin, session):
    """Which station is a fact the history holds, not a guess: the vocabulary
    is plant-wide, and the floor that chooses a word is the floor that has
    recorded it. The sentence says how many machines that is, so the choice
    is readable rather than mysterious."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown", minutes_ago=90)
    _down_with(session, "PACK01", "breakdown", minutes_ago=80)
    _down_with(session, "PACK01", "breakdown", minutes_ago=70)
    reasons.define(session, code="breakdown", name="Mechanical breakdown", actor="ENG")
    session.flush()

    change = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()["changes"][0]
    assert change["page"] == "/dashboard/station?m=PACK01&reason=breakdown"
    assert "the busiest of the 2 machines that have recorded breakdown" in change["note"]


def test_a_brand_new_reason_has_no_floor_to_stand_on_and_says_so_in_place(
        admin, session):
    """Nobody can have chosen a word the plant does not have yet, so there is
    no machine whose screen shows it and no honest place to walk to. The step
    stays on the review panel and describes the change, exactly as it did
    before any of this existed."""
    reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                   description="Stuck where product enters.", actor="ENG")
    session.flush()

    walk = admin.get(
        "/dashboard/pending-approvals/downtime_reason/jam_infeed/1").json()["walkthrough"]
    assert {step["page"] for step in walk["steps"]} == {"/dashboard"}
    assert walk["steps"][0]["anchor"] == "review-change"
    assert walk["steps"][0]["nth"] == 0


def test_a_word_on_the_list_that_no_machine_has_ever_chosen_also_stays_in_place(
        admin, session):
    """The other case with nowhere to stand. The code is on the list an
    operator sees, but no machine has recorded a stop under it, so naming one
    out of the equipment table would be inventing a place."""
    _in_force(session, "waiting_on_fitter", "Waiting on a fitter")
    reasons.define(session, code="waiting_on_fitter", name="Waiting for the fitter",
                   actor="ENG")
    session.flush()

    assert reasons.machines_labelling(session, "waiting_on_fitter") == []
    walk = admin.get(
        "/dashboard/pending-approvals/downtime_reason/waiting_on_fitter/2"
    ).json()["walkthrough"]
    assert {step["page"] for step in walk["steps"]} == {"/dashboard"}


def test_a_retirement_walks_to_the_list_the_code_is_about_to_leave(admin, session):
    """What retiring a word does is take it out of that select. Somebody
    signing that should be looking at the select."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Breakdown", retires=True,
                   labels_intervals=1, actor="ENG")
    session.flush()

    body = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()
    status = body["changes"][0]
    assert status["field"] == "status"
    assert status["page"] == "/dashboard/station?m=MIX01&reason=breakdown"
    # The count that may not be signed blind is still the first thing said.
    assert status["note"].startswith("1 recorded interval already carries")


def test_the_walk_knows_the_screen_that_signs_and_ends_on_it(admin, session):
    """The round trip. A walk that has crossed onto the floor's screen can be
    brought back to the panel that signs, and its last step is that button -
    so following it to the end lands on the signature rather than stranding
    somebody in front of a select."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Mechanical breakdown", actor="ENG")
    session.flush()

    walk = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()["walkthrough"]
    assert walk["home"] == "/dashboard"
    assert walk["home_label"]
    assert walk["steps"][-1]["page"] == "/dashboard"
    assert walk["steps"][-1]["anchor"] == "review-approve"


def test_reading_a_draft_and_its_walk_signs_nothing(admin, session):
    """The whole round trip changes nothing until the button is pressed. A
    review that quietly put a word in front of every operator would be the
    failure this panel exists to end."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Mechanical breakdown", actor="ENG")
    session.flush()

    for _ in range(2):
        admin.get("/dashboard/pending-approvals/downtime_reason/breakdown/2")
    assert reasons.in_force(session, "breakdown").name == "Breakdown"
    assert reasons.open_draft(session, "breakdown").revision == 2
    assert admin.get("/dashboard/pending-approvals").json()["total"] == 1

    admin.post("/equipment/downtime-reasons/breakdown/approve/2")
    assert reasons.in_force(session, "breakdown").name == "Mechanical breakdown"
    assert admin.get("/dashboard/pending-approvals").json()["total"] == 0


def test_the_screen_that_is_walked_to_opens_its_list_to_be_read_not_answered(
        admin, session):
    """The destination carries which machine and which reason, and the station
    screen opens that list with the buttons that would change the machine off
    the screen. An approver reading a list must not be one mis-click from
    putting a running machine down."""
    _in_force(session, "breakdown", "Breakdown")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Mechanical breakdown", actor="ENG")
    session.flush()

    page = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2"
    ).json()["walkthrough"]["steps"][0]["page"]
    assert "m=MIX01" in page and "reason=breakdown" in page

    js = (WEB / "station.js").read_text(encoding="utf-8")
    # The reveal reads the same parameter the step names, arms no state
    # change, and takes the two buttons that would change one off the screen.
    assert 'searchParams.get("reason")' in js
    assert '$("#reason-actions").classList.add("hidden")' in js
    assert "pendingState = null;" in js


def test_which_machine_the_walk_stands_on_is_said_once_for_the_whole_diff(
        admin, session):
    """It is one answer about one draft. Repeating it under every change row
    reads like two facts about two machines."""
    _in_force(session, "breakdown", "Breakdown", "The machine has stopped.")
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Mechanical breakdown",
                   description="Something on the machine has failed.", actor="ENG")
    session.flush()

    body = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()
    said = [change for change in body["changes"] if "MIX01" in (change.get("note") or "")]
    assert len(said) == 1
    # Every row still goes there; only the sentence about it is said once.
    assert {change["page"] for change in body["changes"]} == {
        "/dashboard/station?m=MIX01&reason=breakdown"}


def test_a_sentence_the_plant_has_not_written_yet_rings_the_word_instead(
        admin, session):
    """The screen shows a reason's sentence in a paragraph under the list, and
    that paragraph is empty until a plant writes one. A ring around an empty
    paragraph is a line on the screen pointing at nothing, so the step rings
    the word, which is what there is to look at."""
    _in_force(session, "breakdown", "Breakdown")          # no description
    _down_with(session, "MIX01", "breakdown")
    reasons.define(session, code="breakdown", name="Breakdown",
                   description="Something on the machine has failed.", actor="ENG")
    session.flush()

    change = admin.get(
        "/dashboard/pending-approvals/downtime_reason/breakdown/2").json()["changes"][0]
    assert change["field"] == "description"
    assert change["before"] is None
    assert change["anchor"] == "station-reason-code"
