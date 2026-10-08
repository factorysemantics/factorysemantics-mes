"""The chat can open the same dossier a person opens by clicking a point.

Before this, the analysis agent could read `spc_chart` - so it could say *that*
a rule fired, and where - and had no way to read the records behind the point it
had just named. Asked *why* a sample was high it had two choices: say it did not
know, or reason out a cause from the limits. The second is the dangerous one,
because a plausible cause reads, to the person acting on it, exactly like one
this plant measured.

`spc_point` and `spc_sample` are pass-throughs to the two dossier routes
`services/spc_point.py` already serves to the SPC screen's panel. What this file
pins:

* both are in the analysis catalogue, and neither can change anything;
* each description names every block the answer carries, so a model asks once
  instead of reading the chart and guessing;
* what the tool hands the model is what the route hands the panel - with one
  deliberate exception, the drawable bucket series of each analog trend, which
  is 60 % of the answer's tokens and the one part of it that exists to be looked
  at rather than read. It is left out by default, every figure the plant
  computed about it stays, and the trend says how many points were left out and
  how to ask for them. A list that quietly came back shorter would be the one
  trim this product does not allow (house rule: every list states its total).
* the chat's own words send a *why* question here rather than to its reasoning.
"""

from datetime import timedelta

import pytest

from fsmes import mcp_server
from fsmes.db import utcnow
from fsmes.domain import EquipmentState, EquipmentStateName, TagValue
from fsmes.mcp.quality import _without_trend_points
from fsmes.services import agent, auth, gauges, masterdata, quality

#: Fourteen settled samples of five and then one that sits high, forty seconds
#: after a changeover ended - the smallest arrangement in which a rule fires on
#: the plant's own limits and there is something in the window to find. The
#: fuller version of this fixture, with every block populated, is in
#: `test_a_sample_on_the_control_chart_carries_the_five_bottles_behind_it.py`;
#: this file is about the tool in front of it, not the dossier itself.
STEADY = [141.0, 141.5, 142.0, 142.5, 143.0]
SHIFTED = [144.0, 144.5, 145.0, 145.5, 147.0]
SAMPLES = 14

#: How often the fixture's tag arrives. Five seconds over a twenty-minute
#: window is some hundreds of readings bucketed into a drawable series, which
#: is what makes the trim worth measuring rather than hypothetical.
SAMPLE_SECONDS = 5

#: Names with a space and an upper case in them, as a characteristic on a real
#: plant has. They travel through a URL path, so they are the reason the tools
#: quote rather than interpolate.
MATERIAL, CHARACTERISTIC = "FG-COLA", "fill height"

ANALYST = {"plant.read", "audit.read"}

#: The keys of a dossier that are not blocks: which chart was asked about, over
#: what window, and how much of it anybody watched. Everything else at the top
#: level is a block, and a description that does not name it is a block the
#: model finds out about by paying for it.
ENVELOPE = {"plant", "material", "characteristic", "unit", "subject", "window",
            "coverage", "coverage_note"}


@pytest.fixture()
def wired(make_client, session, monkeypatch):
    """The MCP tools pointed at the in-process app, signed in as AGENT.

    The arrangement `test_mcp_product.py` uses: a tool only ever reaches a
    plant through its HTTP API as an account, and that is not bypassed here.
    """
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    session.flush()
    clients = mcp_server.wire_clients("testplant", make_client)
    monkeypatch.setattr(mcp_server, "_clients", clients)
    client = clients[mcp_server.client_key("testplant", mcp_server.AGENT_USER)]
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})
    return client


@pytest.fixture()
def flagged(session):
    """One shifted sample on the filler, with a changeover behind it.

    Returns the sample row and the five checks under it, so a test can ask for
    the dossier by either id without going looking for one.
    """
    gauges.register(session, code="HEIGHT-01", name="Bench height gauge",
                    kind="height gauge", resolution=0.1, interval_days=180,
                    location="MIX01", actor="test")
    gauges.calibrate(session, "HEIGHT-01", result="pass", performed_by="QA-LEAD",
                     performed_on=utcnow().date() - timedelta(days=14),
                     certificate="CERT-H-1", actor="test")
    quality.create_spec(session, material_code=MATERIAL, characteristic=CHARACTERISTIC,
                        unit="mm", min_value=139.0, max_value=145.0, sample_size=5,
                        actor="test")
    mixer = masterdata.get_equipment(session, "MIX01")

    def take(values):
        row, checks, _nc, _signals = quality.record_sample(
            session, material_code=MATERIAL, characteristic=CHARACTERISTIC,
            values=values, gauge_code="HEIGHT-01", equipment_code="MIX01",
            actor="OP-NIGHT")
        session.flush()
        return row, checks

    for _ in range(SAMPLES):
        take(STEADY)
    row, checks = take(SHIFTED)
    at = row.ts

    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.SETUP,
        reason="Product change", reason_code="changeover",
        started_at=at - timedelta(minutes=12), ended_at=at - timedelta(seconds=40)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
        started_at=at - timedelta(seconds=40)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.IDLE,
        started_at=at - timedelta(hours=3), ended_at=at - timedelta(minutes=12)))

    moment = at - timedelta(minutes=20)
    while moment <= at + timedelta(minutes=4):
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                             ts=moment, value_num=62.0))
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                             ts=moment, value_num=2.6))
        moment += timedelta(seconds=SAMPLE_SECONDS)
    session.flush()
    return row, checks


# ------------------------------------------------ in the catalogue, read-only

def test_both_dossier_tools_are_offered_to_the_analysis_agent():
    """They enter by the existing rule and not by being listed anywhere: a
    `plant` property, no `dry_run`. Checked against the catalogue the kind
    actually builds, because a tool the chat cannot see is a tool that does not
    exist as far as a *why* question is concerned."""
    offered = {t["name"] for t in agent.catalogue(ANALYST, for_kind=agent.ANALYSIS)}
    assert {"spc_point", "spc_sample"} <= offered


def test_neither_dossier_tool_can_change_anything_about_this_plant():
    """Read-only is the claim the whole analysis kind rests on, so the two new
    tools are held to it by their own schemas rather than by inheritance: no
    `dry_run`, no `on_behalf_of`, and nothing in the list of what a write is
    gated on."""
    registry = {tool.name: tool for tool in agent.registry_tools()}
    for name in ("spc_point", "spc_sample"):
        tool = registry[name]
        props = (tool.input_schema or {}).get("properties") or {}
        assert "dry_run" not in props, name
        assert "on_behalf_of" not in props, name
        assert "client_ref" not in props, name
        assert name not in agent.NEEDS, name
        assert name not in agent.PER_CALL_NEEDS, name


@pytest.mark.parametrize("name", ["spc_point", "spc_sample"])
def test_each_dossier_tool_names_every_block_its_answer_carries(wired, flagged, name):
    """A description that said "the records behind the point" would leave the
    model to find out what it got by getting it. Every block is named, so the
    decision to ask is made before the call and not after it.

    The list of blocks is read off the answer the route actually gives, not
    written out here: a block added to the dossier next year should fail this
    test and have the sentence added to it, rather than arriving unannounced.
    """
    row, checks = flagged
    asked = {"spc_point": {"check_id": checks[-1].id},
             "spc_sample": {"sample_id": row.id}}[name]
    answer = getattr(mcp_server, name)("testplant", material=MATERIAL,
                                       characteristic=CHARACTERISTIC, **asked)
    assert "error" not in answer, answer
    blocks = set(answer) - ENVELOPE
    assert blocks, "the dossier came back with no blocks at all"
    registry = {tool.name: tool for tool in agent.registry_tools()}
    said = (registry[name].description or "").lower()
    missing = sorted(block for block in blocks if f"`{block}`" not in said)
    assert not missing, f"{name} does not name {missing}"
    assert "why" in said, "the description has to say which question it answers"
    assert "coverage" in said, "and that every block says what it did not see"


# ------------------------------------- the same answer the panel is given

def test_the_sample_tool_hands_over_the_dossier_the_panel_is_given(wired, flagged):
    """Key for key against the route, with the drawable series asked for, so
    the pass-through is held to being one. The only key the tool adds is
    `plant`, which a route serving one plant has no reason to say."""
    row, _checks = flagged
    answer = mcp_server.spc_sample("testplant", material=MATERIAL,
                                   characteristic=CHARACTERISTIC, sample_id=row.id,
                                   trends=True)
    assert "error" not in answer, answer
    served = wired.get(f"/quality/spc/{MATERIAL}/{CHARACTERISTIC}/sample/{row.id}")
    assert served.status_code == 200, served.text
    assert {k: v for k, v in answer.items() if k != "plant"} == served.json()


def test_the_point_tool_answers_for_one_reading_by_the_check_on_the_chart(wired, flagged):
    """The other kind of point: a reading, named by the `check` an `spc_chart`
    answer carries on it."""
    _row, checks = flagged
    check = checks[-1]
    answer = mcp_server.spc_point("testplant", material=MATERIAL,
                                  characteristic=CHARACTERISTIC, check_id=check.id,
                                  trends=True)
    assert "error" not in answer, answer
    served = wired.get(f"/quality/spc/{MATERIAL}/{CHARACTERISTIC}/point/{check.id}")
    assert served.status_code == 200, served.text
    assert {k: v for k, v in answer.items() if k != "plant"} == served.json()
    assert answer["reading"]["check"] == check.id


def test_a_characteristic_with_a_space_in_its_name_reaches_its_own_dossier(wired, flagged):
    """`fill height` is what a plant calls it, and it travels through a URL
    path. A tool that interpolated it rather than quoting it would ask for a
    characteristic this plant does not have and the model would be told, in
    good faith, that there is no such chart."""
    row, _checks = flagged
    answer = mcp_server.spc_sample("testplant", material=MATERIAL,
                                   characteristic=CHARACTERISTIC, sample_id=row.id)
    assert "error" not in answer, answer
    assert answer["sample"]["sample"] == row.id


def test_a_window_nobody_named_is_the_routes_own_and_not_one_this_layer_picked(wired, flagged):
    """Three windows the dossier takes, none of them chosen here: a tool that
    defaulted `before_minutes` to its own number would answer a different
    question from the panel's for the same click."""
    row, _checks = flagged
    asked = mcp_server.spc_sample("testplant", material=MATERIAL,
                                  characteristic=CHARACTERISTIC, sample_id=row.id)
    served = wired.get(f"/quality/spc/{MATERIAL}/{CHARACTERISTIC}/sample/{row.id}")
    assert asked["window"] == served.json()["window"]
    assert asked["window"]["before_minutes"] is not None


# --------------------------------- the one thing left out, and said to be out

def test_the_trends_it_leaves_out_keep_every_figure_the_plant_worked_out(wired, flagged):
    """What `trends=false` costs and what it may not cost.

    The buckets go; `samples`, `buckets`, `coverage` and the rest stay exactly
    as the plant computed them, and the trend says how many points it left out
    and how to ask for them. Nothing is summarised in their place - a mean of
    the buckets would be a figure this MES never measured.
    """
    row, _checks = flagged
    whole = mcp_server.spc_sample("testplant", material=MATERIAL,
                                  characteristic=CHARACTERISTIC, sample_id=row.id,
                                  trends=True)
    trimmed = mcp_server.spc_sample("testplant", material=MATERIAL,
                                    characteristic=CHARACTERISTIC, sample_id=row.id)
    was = {t["tag"]: t for t in whole["tags"]["trends"]}
    now = {t["tag"]: t for t in trimmed["tags"]["trends"]}
    assert was and set(was) == set(now)
    for tag, trend in now.items():
        assert "points" not in trend, tag
        assert trend["points_left_out"] == len(was[tag]["points"]) > 0
        assert str(trend["points_left_out"]) in trend["points_note"]
        assert "trends=true" in trend["points_note"]
        for field, value in was[tag].items():
            if field != "points":
                assert trend[field] == value, f"{tag}.{field}"
    # And every other block travels whole: they are the small ones, and they
    # are the ones that answer why.
    assert {k: v for k, v in trimmed.items() if k != "tags"} == \
           {k: v for k, v in whole.items() if k != "tags"}


def test_the_drawable_series_is_the_largest_thing_in_the_answer(wired, flagged):
    """Why the trim is here and not somewhere else, measured rather than
    remembered.

    On this fixture - one station, two analog signals, twenty-four minutes at
    the cadence this product stores analogs at - the bucket series is bigger
    than any other block of the dossier, and leaving it out takes well over a
    third off what the conversation pays for. A station publishing six signals
    takes more. The blocks that actually say why a point is where it is are
    the small ones, which is the whole argument.
    """
    import json

    row, _checks = flagged
    whole = mcp_server.spc_sample("testplant", material=MATERIAL,
                                  characteristic=CHARACTERISTIC, sample_id=row.id,
                                  trends=True)
    trimmed = mcp_server.spc_sample("testplant", material=MATERIAL,
                                    characteristic=CHARACTERISTIC, sample_id=row.id)

    def size(value) -> int:
        return len(json.dumps(value, default=str))

    series = size(whole) - size(trimmed)
    biggest_block = max(size(v) for k, v in trimmed.items() if k != "tags")
    assert series > biggest_block, (series, biggest_block)
    assert size(trimmed) < size(whole) * 0.65, (size(trimmed), size(whole))


def test_a_dossier_with_no_trends_at_all_passes_through_untouched():
    """The trim is a shape it recognises and not one it insists on: a point
    whose station publishes no analog signal, or a route that answers with an
    error, comes back as it arrived."""
    assert _without_trend_points({"tags": {"trends": [], "total": 0}}) == \
           {"tags": {"trends": [], "total": 0}}
    assert _without_trend_points({"error": "no such check"}) == {"error": "no such check"}
    assert _without_trend_points("not a dossier") == "not a dossier"


# ------------------------------------------- and the chat knows to ask at all

def test_the_chats_own_words_send_a_why_question_to_the_dossier():
    """A tool in the catalogue is not a tool the model reaches for. The
    analysis prompt names both, says which id each takes, and says the thing
    that matters more than either: a record in the same window is not a cause.
    """
    said = agent.system_for(agent.ANALYSIS)
    assert "spc_sample" in said and "spc_point" in said
    assert "dossier" in said
    assert "is not a cause" in said
