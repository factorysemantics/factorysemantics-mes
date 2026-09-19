"""The plant's downtime vocabulary, end to end.

The failure this exists to end: the box that asked why a machine stopped was
a text input, the pareto grouped on whatever came back, and *jam*, *Jam*,
*jam at infeed* and *infed jam* were four bars too small to act on while the
real top reason was invisible.

What is pinned here is the whole loop, because every part of it has a way of
being wrong that looks fine: a list nobody has to use, an approval an agent
can give itself, a retirement that relabels history, a draft nobody is ever
told about, and a pareto that reports one total where there are two.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import Equipment, EquipmentState, EquipmentStateName
from fsmes.services import analysis, reasons, workorders


@pytest.fixture()
def line(session):
    workorders.create(session, code="WO-DR-1", material_code="FG-COLA", quantity=10)
    workorders.release(session, "WO-DR-1")
    session.flush()
    return "LINE1"


def _vocabulary(session, *terms, actor="ENG"):
    """A vocabulary in force, the way a plant gets one: drafted, then signed."""
    for code, name in terms:
        reasons.define(session, code=code, name=name, description=f"{name}.", actor=actor)
        reasons.approve(session, code, reasons.open_draft(session, code).revision,
                        actor="ADMIN")
    session.flush()


def _down(session, code, *, minutes_ago, minutes, reason=None, reason_code=None):
    unit = session.scalar(select(Equipment).where(Equipment.code == code))
    now = utcnow()
    session.add(EquipmentState(
        equipment_id=unit.id, state=EquipmentStateName.DOWN, reason=reason,
        reason_code=reason_code,
        started_at=now - timedelta(minutes=minutes_ago),
        ended_at=now - timedelta(minutes=minutes_ago - minutes)))
    session.flush()


# ------------------------------------------------------------- the lifecycle


def test_a_new_reason_starts_as_a_draft_nobody_can_choose_from(session):
    row = reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                         actor="ENG")
    assert row.revision == 1 and row.status.value == "draft"
    assert reasons.catalog(session) == {}


def test_nothing_is_on_the_list_until_a_person_approves_it(session):
    reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                   description="Something stuck at the infeed.", actor="ENG")
    assert reasons.catalog(session) == {}
    reasons.approve(session, "jam_infeed", 1, actor="ADMIN")
    assert reasons.catalog(session) == {"jam_infeed": "Something stuck at the infeed."}
    assert reasons.in_force(session, "jam_infeed").approved_by == "ADMIN"


def test_the_catalog_is_the_approved_revision_not_the_newest_one(session):
    """A change being written does not reach the floor mid-change - the same
    rule a work instruction has."""
    _vocabulary(session, ("jam_infeed", "Jam at the infeed"))
    reasons.define(session, code="jam_infeed", name="Infeed jam", actor="ENG")
    assert reasons.names(session) == {"jam_infeed": "Jam at the infeed"}
    reasons.approve(session, "jam_infeed", 2, actor="ADMIN")
    assert reasons.names(session) == {"jam_infeed": "Infeed jam"}


def test_undo_is_approving_the_previous_revision(session):
    _vocabulary(session, ("jam_infeed", "Jam at the infeed"))
    reasons.define(session, code="jam_infeed", name="A regrettable name", actor="ENG")
    reasons.approve(session, "jam_infeed", 2, actor="ADMIN")
    assert reasons.names(session)["jam_infeed"] == "A regrettable name"

    reasons.approve(session, "jam_infeed", 1, actor="ADMIN")
    assert reasons.names(session)["jam_infeed"] == "Jam at the infeed"
    # Nothing was deleted: both revisions are still readable.
    assert [r.revision for r in reasons.revisions(session, "jam_infeed")] == [1, 2]


def test_a_code_the_product_already_owns_is_refused(session):
    """`running` is an equipment state, `admin` is a role, `oee` is a KPI. A
    downtime reason that spells one of them means two things at once."""
    from fsmes.services import Invalid

    for taken in ("running", "admin", "oee"):
        with pytest.raises(Invalid) as refused:
            reasons.define(session, code=taken, name="Something", actor="ENG")
        assert taken in str(refused.value)


def test_a_code_is_a_code_and_not_a_sentence(session):
    from fsmes.services import Invalid

    for bad in ("Jam At Infeed", "jam at infeed", "1jam", "j"):
        with pytest.raises(Invalid):
            reasons.define(session, code=bad, name="Something", actor="ENG")


# ------------------------------------------------------------- what it costs


def test_a_retired_code_keeps_labelling_the_intervals_it_labelled(session, line):
    """Retiring a code changes what may be chosen next. It never changes what
    was chosen before."""
    _vocabulary(session, ("jam_infeed", "Jam at the infeed"))
    _down(session, "MIX01", minutes_ago=60, minutes=10,
          reason="Jam at the infeed", reason_code="jam_infeed")

    reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                   retires=True, labels_intervals=1, actor="ENG")
    reasons.approve(session, "jam_infeed", 2, actor="ADMIN")

    assert "jam_infeed" not in reasons.catalog(session)
    interval = session.scalar(select(EquipmentState).where(
        EquipmentState.reason_code == "jam_infeed"))
    assert interval is not None and interval.reason == "Jam at the infeed"
    # And the pareto keeps showing it, under the name it had.
    bars = {b["code"]: b for b in
            analysis.downtime_pareto(session, line_code=line, hours=8)["reasons"]}
    assert bars["jam_infeed"]["seconds"] == pytest.approx(600, abs=5)


def test_retiring_a_code_that_labels_live_intervals_says_how_many(session, line):
    """A code may leave the list with somebody having looked at what it
    already labels, or it leaves it blind."""
    from fsmes.services import Invalid

    _vocabulary(session, ("jam_infeed", "Jam at the infeed"))
    _down(session, "MIX01", minutes_ago=60, minutes=10, reason_code="jam_infeed")
    _down(session, "PACK01", minutes_ago=40, minutes=5, reason_code="jam_infeed")

    with pytest.raises(Invalid) as refused:
        reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                       retires=True, actor="ENG")
    assert "2 recorded intervals" in str(refused.value)

    # Said, and then allowed.
    reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                   retires=True, labels_intervals=2, actor="ENG")
    reasons.approve(session, "jam_infeed", 2, actor="ADMIN")
    assert "jam_infeed" not in reasons.catalog(session)


def test_a_code_that_labels_nothing_retires_without_ceremony(session):
    _vocabulary(session, ("jam_infeed", "Jam at the infeed"))
    reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                   retires=True, actor="ENG")
    reasons.approve(session, "jam_infeed", 2, actor="ADMIN")
    assert reasons.catalog(session) == {}


# --------------------------------------------------------------- the station


def test_an_operator_cannot_type_a_reason_once_the_plant_has_a_vocabulary(
        session, client):
    """Scott's answer to question 3: once a vocabulary exists a reason is
    required, and it comes from the list. A list nobody has to use is not a
    list - it is a suggestion beside the text box that caused the problem."""
    _vocabulary(session, ("breakdown", "Breakdown"),
                ("not_yet_determined", "Not yet determined"))

    typed = client.post("/equipment/MIX01/state",
                        json={"state": "down", "reason": "jam at infeed"})
    assert typed.status_code == 400
    assert "reason_code" in typed.text

    blank = client.post("/equipment/MIX01/state", json={"state": "down"})
    assert blank.status_code == 400

    # And the explicit "nobody knows yet" code is an answer, where a blank is not.
    honest = client.post("/equipment/MIX01/state",
                         json={"state": "down", "reason_code": "not_yet_determined"})
    assert honest.status_code == 200, honest.text
    assert honest.json()["reason_code"] == "not_yet_determined"
    # Both are stored: the code for grouping, the sentence for every reader
    # this product already had.
    assert honest.json()["reason"] == "Not yet determined"


def test_an_operator_may_still_type_one_when_the_plant_has_no_vocabulary(client):
    """Nothing changes for a plant that has not named its reasons. There is no
    list, so the text box is what it has and the text is what it gets."""
    typed = client.post("/equipment/MIX01/state",
                        json={"state": "down", "reason": "jam at infeed"})
    assert typed.status_code == 200, typed.text
    assert typed.json()["reason"] == "jam at infeed"
    assert typed.json()["reason_code"] is None


def test_a_code_that_is_not_on_the_list_is_refused_with_the_total(session, client):
    _vocabulary(session, ("breakdown", "Breakdown"))
    refused = client.post("/equipment/MIX01/state",
                          json={"state": "down", "reason_code": "jam_infeed"})
    assert refused.status_code == 400
    assert "1 approved downtime reasons" in refused.text


def test_a_machine_going_idle_needs_no_reason_at_all(session, client):
    """The rule is about stopping, not about every state change."""
    _vocabulary(session, ("breakdown", "Breakdown"))
    assert client.post("/equipment/MIX01/state", json={"state": "idle"}).status_code == 200


def test_the_catalog_is_not_read_as_a_machine_called_downtime_reasons(session, client):
    """`/equipment/{code}` matches any single segment. The vocabulary's routes
    are mounted first for exactly this reason."""
    response = client.get("/equipment/downtime-reasons")
    assert response.status_code == 200, response.text
    assert response.json() == {"reasons": {}, "names": {}, "total": 0}


# ------------------------------------------------------------------ the gate


def test_an_agent_may_draft_and_may_not_approve(sign_in, session):
    """The agent role holds the drafting half of every lifecycle in this
    product and the approving half of none of them."""
    agent = sign_in("AGENT-DR", role="agent")
    drafted = agent.post("/equipment/downtime-reasons",
                         json={"code": "jam_infeed", "name": "Jam at the infeed"})
    assert drafted.status_code == 201, drafted.text
    assert drafted.json()["status"] == "draft"

    refused = agent.post("/equipment/downtime-reasons/jam_infeed/approve/1")
    assert refused.status_code == 403
    assert "process.approve" in refused.text
    # And it really is still a draft.
    assert reasons.catalog(session) == {}


def test_an_operator_may_not_draft_the_vocabulary(client):
    refused = client.post("/equipment/downtime-reasons",
                          json={"code": "jam_infeed", "name": "Jam at the infeed"})
    assert refused.status_code == 403


def test_a_draft_records_who_it_was_drafted_for(sign_in, session):
    """An agent drafting for a person says so, on the row the panel reads."""
    agent = sign_in("AGENT-OBO", role="agent")
    agent.post("/equipment/downtime-reasons",
               json={"code": "jam_infeed", "name": "Jam at the infeed"},
               headers={"X-On-Behalf-Of": "ADMIN"})
    assert reasons.open_draft(session, "jam_infeed").on_behalf_of == "ADMIN"


# ----------------------------------------------------------------- discovery


def test_the_panel_shows_a_draft_only_to_a_caller_who_may_approve_it(
        sign_in, admin, session):
    """A pending item appears on the screen of the role that can act on it -
    and on no screen that cannot."""
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()

    mine = admin.get("/dashboard/pending-approvals").json()
    assert mine["total"] == 1 and mine["kinds"] == ["downtime_reason"]
    assert mine["items"][0]["code"] == "jam_infeed"
    assert mine["items"][0]["approve"] == "/equipment/downtime-reasons/jam_infeed/approve/1"

    operator = sign_in("OP-PANEL", role="operator")
    theirs = operator.get("/dashboard/pending-approvals").json()
    # Not an empty list of things they could act on: no kinds at all, which is
    # what the screen reads as "this panel is not for you".
    assert theirs["kinds_total"] == 0 and theirs["items"] == [] and theirs["total"] == 0


def test_the_panel_states_the_whole_queue_not_the_page(admin, session):
    """A deep queue that reads short is the failure this panel exists to end -
    the adjustments tile counts the loaded page of fifty and does exactly that."""
    for index in range(7):
        reasons.define(session, code=f"reason_{index}", name=f"Reason {index}", actor="ENG")
    session.flush()

    page = admin.get("/dashboard/pending-approvals?limit=3").json()
    assert len(page["items"]) == 3
    assert page["total"] == 7 and page["has_more"] is True


def test_a_draft_waits_visibly_and_says_how_long(admin, session):
    """It does not expire and it does not go live by itself. The only thing
    that changes with time is how long it has waited."""
    from fsmes.domain import DowntimeReason

    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    row = session.scalar(select(DowntimeReason).where(DowntimeReason.code == "jam_infeed"))
    row.created_at = utcnow() - timedelta(days=11)
    session.flush()

    item = admin.get("/dashboard/pending-approvals").json()["items"][0]
    assert item["waiting_seconds"] > 11 * 24 * 3600 - 60
    assert reasons.catalog(session) == {}


def test_the_panel_signs_a_draft_and_the_queue_empties(admin, session):
    reasons.define(session, code="jam_infeed", name="Jam at the infeed", actor="ENG")
    session.flush()
    item = admin.get("/dashboard/pending-approvals").json()["items"][0]

    assert admin.post(item["approve"]).status_code == 200
    assert admin.get("/dashboard/pending-approvals").json()["total"] == 0
    assert "jam_infeed" in reasons.catalog(session)


# ------------------------------------------------------------------ the pareto


def test_the_pareto_groups_by_code_where_there_is_one_and_by_text_where_there_is_not(
        session, line):
    _vocabulary(session, ("breakdown", "Breakdown"))
    _down(session, "MIX01", minutes_ago=60, minutes=10,
          reason="Breakdown", reason_code="breakdown")
    _down(session, "PACK01", minutes_ago=50, minutes=6,
          reason="Breakdown", reason_code="breakdown")
    # The same word, typed. Not the same fact, and never the same bar: one is
    # a choice from a list somebody approved and the other is not.
    _down(session, "MIX01", minutes_ago=40, minutes=4, reason="Breakdown")

    bars = analysis.downtime_pareto(session, line_code=line, hours=8)["reasons"]
    by_code = {bar["code"]: bar for bar in bars if bar["code"]}
    typed = [bar for bar in bars if not bar["code"] and bar["reason"] == "Breakdown"]
    assert by_code["breakdown"]["seconds"] == pytest.approx(960, abs=5)
    assert by_code["breakdown"]["from_the_list"] is True
    assert len(typed) == 1 and typed[0]["seconds"] == pytest.approx(240, abs=5)


def test_the_pareto_states_both_totals(session, line):
    """How much of this window came from the list, and how much did not. One
    number alone would let a plant with six codes and a hundred typed
    sentences look like a plant with six reasons."""
    _vocabulary(session, ("breakdown", "Breakdown"))
    _down(session, "MIX01", minutes_ago=60, minutes=10, reason_code="breakdown")
    _down(session, "PACK01", minutes_ago=50, minutes=5, reason="something else")
    _down(session, "MIX01", minutes_ago=40, minutes=5)

    out = analysis.downtime_pareto(session, line_code=line, hours=8)
    assert out["from_the_list_seconds"] == pytest.approx(600, abs=5)
    assert out["typed_seconds"] == pytest.approx(300, abs=5)
    assert out["vocabulary_total"] == 1
    # The three account for the whole window's downtime, to the second.
    unlabelled = out["unlabelled_share"] * out["total_seconds"]
    assert (out["from_the_list_seconds"] + out["typed_seconds"] + unlabelled
            == pytest.approx(out["total_seconds"], abs=1))


def test_a_plant_with_no_vocabulary_says_none_of_it_came_from_a_list(session, line):
    """Not a silence, and not a zero that looks like a plant ignoring its own
    list: there is no list, and the totals say so."""
    _down(session, "MIX01", minutes_ago=60, minutes=10, reason="jam at infeed")
    out = analysis.downtime_pareto(session, line_code=line, hours=8)
    assert out["vocabulary_total"] == 0
    assert out["from_the_list_seconds"] == 0.0
    assert out["typed_seconds"] == pytest.approx(600, abs=5)


# -------------------------------------------------------------------- packs


def test_pack_apply_seeds_a_vocabulary_and_never_rewrites_one(session, tmp_path):
    """A pack seeds what a plant starts with. The plant owns it from its first
    edit, and applying the pack again never takes that edit back."""
    import json

    from fsmes.pack import masterdata as pack_masterdata

    directory = tmp_path / "masterdata"
    directory.mkdir()
    (directory / "downtime_reasons.json").write_text(json.dumps([
        {"code": "breakdown", "name": "Breakdown", "description": "It broke."},
        {"code": "starved", "name": "Starved", "description": "Nothing arrived."},
    ]), encoding="utf-8")

    receipt = pack_masterdata.seed(session, directory)
    session.flush()
    assert receipt["downtime_reasons"] == {"made": 2, "present": 0}
    # In force, because applying a pack is a deliberate act by a person.
    assert set(reasons.catalog(session)) == {"breakdown", "starved"}

    # The plant renames one, the way it owns it.
    reasons.define(session, code="breakdown", name="Machine breakdown", actor="ENG")
    reasons.approve(session, "breakdown", 2, actor="ADMIN")
    session.flush()

    again = pack_masterdata.seed(session, directory)
    assert again["downtime_reasons"] == {"made": 0, "present": 2}
    assert reasons.names(session)["breakdown"] == "Machine breakdown"


def test_a_pack_may_not_name_a_reason_the_product_already_owns(tmp_path):
    """The pack seeds straight into the plant, so a pack that skipped the
    check would be the one way round it."""
    import json

    from fsmes.pack import masterdata as pack_masterdata

    directory = tmp_path / "masterdata"
    directory.mkdir()
    (directory / "downtime_reasons.json").write_text(json.dumps([
        {"code": "running", "name": "Running"},
        {"code": "Jam At Infeed", "name": "Jam"},
    ]), encoding="utf-8")

    problems = " ".join(pack_masterdata.problems(directory))
    assert "running" in problems and "an equipment state" in problems
    assert "Jam At Infeed" in problems


def test_the_lab_packs_ship_a_starting_vocabulary():
    """Six words, ISO-22400-shaped, that a plant edits. Pinned so a pack that
    loses its vocabulary is noticed here rather than on a station screen with
    a text box on it."""
    import json
    from pathlib import Path

    labs = Path(__file__).resolve().parents[1] / "labs"
    packs = ["multiplant/bottling", "multiplant/machining", "multiplant/finewire",
             "cutlery"]
    for pack in packs:
        rows = json.loads((labs / pack / "masterdata" / "downtime_reasons.json")
                          .read_text(encoding="utf-8"))
        codes = {row["code"] for row in rows}
        assert "not_yet_determined" in codes, pack
        assert {"breakdown", "changeover", "micro_stop", "starved", "blocked"} <= codes, pack
        assert all(row.get("description") for row in rows), pack
