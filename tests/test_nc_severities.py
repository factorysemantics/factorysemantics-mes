"""The second vocabulary: the plant's own non-conformance severities.

Decision 0035 set itself a test — *"if the loop is right, the second
vocabulary is a small change"* — and this file is where that claim is either
true or not. Half of it is the same loop again, pinned again because a
lifecycle that works for one list and not for the second is a lifecycle
nobody can build a third on. The other half is the three things only a
severity has: what carries a code is a record rather than an interval, two of
the words are written by the product itself, and a plant with no list at all
behaves exactly as this product did before any of this existed.
"""

import pytest

from fsmes.services import Invalid, quality, severities, spc


def _vocabulary(session, *terms, actor="QE"):
    """A severity list in force, the way a plant gets one: drafted, signed."""
    for code, name in terms:
        severities.define(session, code=code, name=name, description=f"{name}.",
                          actor=actor)
        severities.approve(session, code,
                           severities.open_draft(session, code).revision, actor="ADMIN")
    session.flush()


def _product_words(session):
    """The two the product writes, on the list. Most tests need them there
    before anything else is true, because a plant whose list lacks one cannot
    raise the holds the product raises."""
    _vocabulary(session, ("minor", "Minor"), ("major", "Major"))


# ------------------------------------------------------------- the lifecycle


def test_a_new_severity_starts_as_a_draft_nothing_can_be_graded_at(session):
    row = severities.define(session, code="critical", name="Critical", actor="QE")
    assert row.revision == 1 and row.status.value == "draft"
    assert severities.catalog(session) == {}


def test_signing_a_severity_puts_it_on_the_list_and_records_who(session):
    severities.define(session, code="critical", name="Critical",
                      description="Stop and call somebody.", actor="QE")
    row = severities.approve(session, "critical", 1, actor="ADMIN")
    assert row.status.value == "approved" and row.approved_by == "ADMIN"
    assert severities.catalog(session) == {"critical": "Stop and call somebody."}


def test_a_retired_severity_still_grades_the_records_it_graded(session):
    """The rule the whole lifecycle exists to keep, said about the second
    list: retiring changes what may be graded next, never what was graded
    before."""
    _product_words(session)
    _vocabulary(session, ("critical", "Critical"))
    nc = quality.open_nc(session, description="A bad one", severity="critical")
    session.flush()

    severities.define(session, code="critical", name="Critical", retires=True,
                      labels_records=1, actor="QE")
    severities.approve(session, "critical",
                       severities.open_draft(session, "critical").revision, actor="ADMIN")
    session.flush()

    assert "critical" not in severities.catalog(session)
    assert nc.severity == "critical"
    # And it still reads as the word somebody chose, not as a bare code.
    assert severities.labels(session)["critical"] == "Critical"


def test_retiring_a_severity_that_grades_records_says_how_many_first(session):
    _product_words(session)
    _vocabulary(session, ("critical", "Critical"))
    quality.open_nc(session, description="A bad one", severity="critical")
    session.flush()

    with pytest.raises(Invalid) as refused:
        severities.define(session, code="critical", name="Critical", retires=True,
                          actor="QE")
    assert "1 non-conformance" in str(refused.value)


def test_undo_is_approving_the_previous_revision(session):
    _vocabulary(session, ("critical", "Critical"))
    severities.define(session, code="critical", name="Show-stopper", actor="QE")
    severities.approve(session, "critical", 2, actor="ADMIN")
    session.flush()
    assert severities.in_force(session, "critical").name == "Show-stopper"

    severities.approve(session, "critical", 1, actor="ADMIN")
    session.flush()
    assert severities.in_force(session, "critical").name == "Critical"
    # Nothing was deleted: both revisions are still readable.
    assert [r.revision for r in severities.revisions(session, "critical")] == [1, 2]


def test_a_severity_may_not_spell_a_word_the_product_owns(session):
    """The same protected list the pack checker reads, so a severity cannot
    quietly mean two things."""
    with pytest.raises(Invalid) as refused:
        severities.define(session, code="scrap", name="Scrap", actor="QE")
    assert "a KPI" in str(refused.value)


def test_a_severity_may_not_spell_one_of_its_own_addresses(session):
    with pytest.raises(Invalid) as refused:
        severities.define(session, code="drafts", name="Drafts", actor="QE")
    assert "/quality/severities/drafts" in str(refused.value)


def test_a_severity_code_fits_the_column_every_record_stores_it_in(session):
    """Twenty characters, not forty. A list that could approve a word too
    long to store would refuse at the one moment it mattered."""
    with pytest.raises(Invalid) as refused:
        severities.define(session, code="a" * 21, name="Too long", actor="QE")
    assert "twenty characters" in str(refused.value)


# ------------------------------------------- a plant with no list at all


def test_a_plant_with_no_severity_list_behaves_exactly_as_it_did(session):
    """Rule one of the configuration audit, in a test: nothing this change
    added moves a plant that configures nothing."""
    assert severities.has_vocabulary(session) is False
    nc = quality.open_nc(session, description="Out of spec", severity="minor")
    session.flush()
    assert nc.severity == "minor"
    # And a word nobody has ever heard of is still stored, because that is
    # what this column did before it had a list behind it.
    other = quality.open_nc(session, description="Odd", severity="whatever")
    session.flush()
    assert other.severity == "whatever"


def test_a_typed_severity_is_refused_once_the_plant_has_a_list(session):
    """The moment a plant has one word in force, the column stops being free
    text - and the refusal names the list rather than the rule."""
    _product_words(session)
    with pytest.raises(Invalid) as refused:
        quality.open_nc(session, description="Odd", severity="whatever")
    assert "'whatever' is not one of this plant's non-conformance severities" in str(
        refused.value)
    assert "major, minor" in str(refused.value)


def test_records_raised_before_the_list_keep_their_word(session):
    """No row is read and no row is rewritten. A hold graded last March under
    a word this plant has since stopped using is still graded with it."""
    before = quality.open_nc(session, description="Old", severity="showstopper")
    session.flush()
    _product_words(session)
    assert before.severity == "showstopper"
    # And the list refuses the same word for a new record, which is the whole
    # of what having a list changed.
    with pytest.raises(Invalid):
        quality.open_nc(session, description="New", severity="showstopper")


# ------------------------------------ the two words the product writes


def test_a_word_the_product_writes_itself_cannot_be_retired(session):
    """The refusal is moved to the only moment a person is present. Retiring
    `major` would otherwise be found out by a machine raising a hold at three
    in the morning."""
    _product_words(session)
    with pytest.raises(Invalid) as refused:
        severities.define(session, code="major", name="Major", retires=True,
                          actor="QE")
    assert "this product writes itself" in str(refused.value)
    assert "SPC rule 1" in str(refused.value)


def test_a_word_the_product_writes_is_still_the_plant_to_rename(session):
    """It is a refusal to retire, never to rename. The code is the key; the
    name and the sentence beside it were always the plant's."""
    _product_words(session)
    severities.define(session, code="major", name="Serious",
                      description="Somebody looks at this now.", actor="QE")
    severities.approve(session, "major", 2, actor="ADMIN")
    session.flush()
    assert severities.in_force(session, "major").name == "Serious"
    assert severities.catalog(session)["major"] == "Somebody looks at this now."


def test_an_spc_hold_is_raised_at_a_word_from_the_plants_own_list(session):
    """The end of the loop: the product's own write path goes through the
    same list a person drafts."""
    _product_words(session)
    for value in [11.0 + (i % 3 - 1) * 0.02 for i in range(30)]:
        quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                             value=value, actor="test")
    _check, _nc, raised = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=11.9,
        actor="test")
    session.flush()
    assert [s["rule"] for s in raised] == [1]
    assert severities.records_labelled(session, "major") == 1


# ----------------------------------------------------------------- the API


def test_the_catalogue_and_the_vocabulary_answer_different_questions(admin, session):
    """The catalogue is what a record may be graded at; the vocabulary is
    everything the plant has ever had, drafts and retirements included. A
    screen reading the catalogue alone would show a person a list their own
    draft was missing from."""
    _product_words(session)
    severities.define(session, code="critical", name="Critical", actor="QE")
    session.flush()

    catalogue = admin.get("/quality/severities").json()
    assert sorted(catalogue["severities"]) == ["major", "minor"]
    assert catalogue["total"] == 2

    everything = admin.get("/quality/severities/vocabulary").json()
    assert everything["total"] == 3
    assert sorted(row["code"] for row in everything["severities"]) == [
        "critical", "major", "minor"]
    assert everything["product_writes"] == ["minor", "major"]


def test_the_vocabulary_says_which_words_the_product_writes(admin, session):
    """Per row, so the screen can explain an absent Retire button rather than
    let somebody press one that refuses."""
    _product_words(session)
    session.flush()
    rows = {row["code"]: row for row
            in admin.get("/quality/severities/vocabulary").json()["severities"]}
    assert "SPC rule 1" in rows["major"]["product_writes"]
    assert rows["minor"]["product_writes"]


def test_drafting_a_severity_needs_quality_define_and_signing_needs_approve(
        sign_in, session):
    """Two powers, because writing down what this plant calls a serious
    finding and putting it in front of every quality record are different
    jobs."""
    operator = sign_in("OP-SEV", role="operator")
    assert operator.post("/quality/severities",
                         json={"code": "critical", "name": "Critical"}).status_code == 403

    agent = sign_in("AGENT-SEV", role="agent")
    drafted = agent.post("/quality/severities",
                         json={"code": "critical", "name": "Critical"})
    assert drafted.status_code == 201
    # An agent drafts and never signs - the role is built that way, in one
    # place, deliberately.
    assert agent.post("/quality/severities/critical/approve/1").status_code == 403


def test_an_agent_drafting_a_severity_for_somebody_records_who_for(sign_in, session):
    agent = sign_in("AGENT-OBO-SEV", role="agent")
    agent.post("/quality/severities", json={"code": "critical", "name": "Critical"},
               headers={"X-On-Behalf-Of": "ADMIN"})
    assert severities.open_draft(session, "critical").on_behalf_of == "ADMIN"


# --------------------------------------------- the panel takes a second kind


def test_a_second_kind_joins_the_approvals_panel_without_the_panel_changing(
        admin, session):
    """The claim decision 0035 made about its own design, as a test. What
    arrived with the severities was one `KINDS` entry; the panel, the endpoint
    that answers from a caller's capabilities and the walk were not touched,
    so a draft of the new kind has to be discoverable through exactly the same
    call."""
    severities.define(session, code="critical", name="Critical", actor="QE")
    session.flush()

    panel = admin.get("/dashboard/pending-approvals").json()
    assert panel["total"] == 1
    assert panel["kinds"] == ["downtime_reason", "nc_severity"]
    row = panel["items"][0]
    assert row["kind"] == "nc_severity"
    assert row["approve"] == "/quality/severities/critical/approve/1"
    assert row["headline"] == "a new severity for the list"


def test_the_severity_panel_is_absent_for_somebody_who_cannot_sign_one(
        sign_in, session):
    """On the screen of the role that can act on it, and on no screen that
    cannot."""
    severities.define(session, code="critical", name="Critical", actor="QE")
    session.flush()
    supervisor = sign_in("SUP-SEV", role="supervisor")
    theirs = supervisor.get("/dashboard/pending-approvals").json()
    assert "nc_severity" not in theirs["kinds"]
    assert theirs["items"] == []


def test_the_review_of_a_severity_counts_records_not_intervals(admin, session):
    """The one thing in the approvals machinery that had to change to take a
    second kind: the panel spelled *recorded interval* in the browser, which
    would have told a severity approver something untrue about their own
    plant. The noun is the server's now."""
    _product_words(session)
    _vocabulary(session, ("critical", "Critical"))
    quality.open_nc(session, description="A bad one", severity="critical")
    severities.define(session, code="critical", name="Critical", retires=True,
                      labels_records=1, actor="QE")
    session.flush()

    body = admin.get(
        "/dashboard/pending-approvals/nc_severity/critical/2").json()
    assert body["affected"]["records"] == 1
    assert body["affected"]["of"] == "non-conformance"
    assert body["coverage"]["vocabulary_total"] == 3
    assert body["coverage"]["vocabulary_total_after"] == 2
    assert body["undo"] == "/quality/severities/critical/approve/1"


def test_a_severity_walk_stands_where_the_word_is_actually_read(admin, session):
    """A change is read in front of the records it grades, not described on a
    card - and only where there is somewhere honest to stand."""
    _product_words(session)
    _vocabulary(session, ("critical", "Critical"))
    quality.open_nc(session, description="A bad one", severity="critical")
    severities.define(session, code="critical", name="Show-stopper", actor="QE")
    session.flush()

    walk = admin.get(
        "/dashboard/pending-approvals/nc_severity/critical/2").json()["walkthrough"]
    assert walk["steps"][0]["page"] == "/dashboard/quality"
    assert walk["steps"][0]["anchor"] == "nc-list"
    # The last step is always the button that signs, wherever the walk went.
    assert walk["steps"][-1]["anchor"] == "review-approve"


def test_every_step_of_a_severity_walk_points_at_a_control_that_exists(
        admin, session):
    """A guide nobody authored can still be wrong about where a control is,
    and a ring around nothing is worse than no walk. Held against the screens
    themselves, exactly as the downtime vocabulary's walk is."""
    from fsmes.services import walkthroughs

    _product_words(session)
    _vocabulary(session, ("critical", "Critical"))
    quality.open_nc(session, description="A bad one", severity="critical")
    severities.define(session, code="critical", name="Show-stopper", actor="QE")
    severities.define(session, code="major", name="Major",
                      description="Somebody looks at this now.", actor="QE")
    session.flush()

    for code, revision in (("critical", 2), ("major", 2)):
        walk = admin.get(
            f"/dashboard/pending-approvals/nc_severity/{code}/{revision}"
        ).json()["walkthrough"]
        for index, step in enumerate(walk["steps"], 1):
            screen = step["page"].split("?", 1)[0]
            present = walkthroughs.anchors_on(screen)
            assert present, (
                f"{code} rev {revision} step {index} points at {screen!r}, "
                "which is not a screen this product serves")
            assert step["anchor"] in present, (
                f"{code} rev {revision} step {index} points at "
                f"data-assist={step['anchor']!r}, which {screen} does not have")
            assert step["title"] and step["body"]


def test_a_severity_nothing_carries_is_read_on_the_review_itself(admin, session):
    """No record carries a brand-new word, so there is nowhere on the floor to
    point at. Pointing at the list anyway would be a ring around a screen that
    holds nothing this draft is about."""
    severities.define(session, code="critical", name="Critical", actor="QE")
    session.flush()
    walk = admin.get(
        "/dashboard/pending-approvals/nc_severity/critical/1").json()["walkthrough"]
    assert all(step["page"] == "/dashboard" for step in walk["steps"])


# ------------------------------------------------- the Configuration entry


def test_quality_has_one_configuration_entry_and_the_severities_are_in_it(
        admin, session):
    page = admin.get("/dashboard/config/quality/sections").json()
    assert page["title"] == "Quality"
    assert page["total"] == len(page["items"])
    rows = {row["key"]: row for row in page["items"]}
    assert rows["nc_severities"]["href"] == "/dashboard/severities"
    assert rows["nc_severities"]["define"] == "quality.define"
    assert rows["nc_severities"]["approve"] == "quality.approve"
    assert rows["nc_severities"]["may_approve"] is True


def test_a_section_that_is_a_pack_key_says_so_rather_than_naming_a_capability(
        admin):
    """Neither capability column can say the truth about a key in the plant's
    pack: nobody drafts it and nobody signs it. A page that said *anybody who
    can see this screen* would be worse than saying nothing."""
    page = admin.get("/dashboard/config/quality/sections").json()
    rules = next(row for row in page["items"] if row["key"] == "spc_hold_rules")
    assert [key["key"] for key in rules["pack_keys"]] == ["[quality] hold_rules"]
    assert rules["define"] is None and rules["approve"] is None
    assert rules["href"] == "/dashboard/spc"


def test_the_severities_screen_is_served_and_sits_inside_quality_configuration(
        client):
    answer = client.get("/dashboard/severities")
    assert answer.status_code == 200
    assert 'data-nav="config/quality"' in answer.text


# --------------------------------------------------- which rules hold


def _steady(session, characteristic="brix"):
    for value in [11.0 + (i % 3 - 1) * 0.02 for i in range(30)]:
        quality.record_check(session, material_code="FG-COLA",
                             characteristic=characteristic, value=value, actor="test")


def test_every_rule_is_drawn_and_recorded_whatever_the_plant_holds_on(
        session, monkeypatch):
    """Decision 0036's first half. A plant that holds on nothing still gets
    the signal row, the chart and the verdict - a rule a plant could switch
    off the chart would be a chart that lies."""
    from fsmes.config import get_settings

    monkeypatch.setattr(spc, "hold_rules", lambda: ())
    _steady(session)
    raised = quality.record_check(session, material_code="FG-COLA",
                                  characteristic="brix", value=11.9, actor="test")[2]
    session.flush()

    assert [s["rule"] for s in raised] == [1]
    assert raised[0]["nonconformance"] is None and raised[0]["held"] is False
    chart = spc.chart(session, "FG-COLA", "brix")
    # The chart reads the whole window, so it shows every firing in it - the
    # point here is that rule 1 is drawn and that not one signal is held.
    assert 1 in [s["rule"] for s in chart["signals"]]
    assert all(s["held"] is False for s in chart["signals"])
    assert all(s["nonconformance"] is None for s in chart["signals"])
    assert chart["stable"] is False and "out of control" in chart["verdict"]
    assert chart["hold_rules"] == []
    assert chart["rules"] == [1, 2, 3, 4]
    # The setting itself is untouched by this test's monkeypatch: what the
    # default is stays the product's answer.
    assert get_settings().hold_rules() == (1, 2, 3, 4)


def test_a_rule_the_plant_does_not_hold_on_opens_no_nonconformance(
        session, monkeypatch):
    from sqlalchemy import select

    from fsmes.domain import NonConformance

    monkeypatch.setattr(spc, "hold_rules", lambda: (2, 3, 4))
    _steady(session)
    quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                         value=11.9, actor="test")
    session.flush()
    spc_holds = [nc for nc in session.scalars(select(NonConformance))
                 if (nc.evidence or {}).get("source") == "spc"]
    assert spc_holds == []


def test_a_plant_that_chooses_nothing_holds_on_all_four_as_it_always_did(session):
    """Rule one again: the shipped default is the literal that was in the
    source, and a plant that writes no key behaves exactly as it does now."""
    _steady(session)
    raised = quality.record_check(session, material_code="FG-COLA",
                                  characteristic="brix", value=11.9, actor="test")[2]
    session.flush()
    assert raised[0]["nonconformance"] is not None and raised[0]["held"] is True
    assert spc.chart(session, "FG-COLA", "brix")["hold_rules"] == [1, 2, 3, 4]
