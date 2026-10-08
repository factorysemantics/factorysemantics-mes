"""What the chat is handed when it asks for a control chart, and what it costs.

An X-bar and R chart's payload carries two things: the points it is drawn
from - each one a sample's own mean and range - and, beside them, the n
readings behind every one of those points. The points are the chart. The
readings are what `spc_sample` answers about ONE point, with each reading's
distance from its own mean, which is the only form in which they say anything.

Measured on 2026-10-08: on a lab-sized plant (30 samples of five bottles) the
whole payload was 15,740 characters and the readings were half of it; on the
lab's own 147-sample chart it was 75,586 characters, of which 38,710 were
readings. `agent.RESULT_LIMIT` is 12,000, so both were cut - and because
`points` comes first in key order, what the cut took away was the control
limits, the signals and the verdict. The model paid for every bottle and was
handed nothing to judge them against.

So the readings are left out by default, the same way `spc_point` leaves out
each analog trend's drawable buckets, and this file pins the three things that
makes it honest rather than quiet: every other block travels whole, the answer
says how many samples and readings are missing and how to get them, and
`readings=true` brings them back exactly as the plant computed them.
"""

import json
from datetime import timedelta

import pytest

from fsmes import mcp_server
from fsmes.db import utcnow
from fsmes.mcp.quality import _without_sample_readings
from fsmes.services import agent, auth, gauges, quality

MATERIAL, CHARACTERISTIC = "FG-COLA", "fill height"

#: Thirty samples of five, the size the lab plant runs, so the figures this
#: file asserts about are the ones the measurement was taken on.
SAMPLES = 30
STEADY = [141.0, 141.5, 142.0, 142.5, 143.0]
SHIFTED = [144.0, 144.5, 145.0, 145.5, 147.0]

#: The keys of the answer that are not the readings. Every one of them has to
#: survive the trim: a chart with no limits in it is the failure this trim
#: exists to undo, not a cheaper version of it.
THE_CHART = ("points", "control", "capability", "signals", "range_chart",
             "verdict", "n", "readings", "kind", "sample_size", "rules",
             "coverage", "coverage_note")


@pytest.fixture()
def wired(make_client, session, monkeypatch):
    """The MCP tools pointed at the in-process app, signed in as AGENT."""
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    session.flush()
    clients = mcp_server.wire_clients("testplant", make_client)
    monkeypatch.setattr(mcp_server, "_clients", clients)
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})
    return clients[mcp_server.client_key("testplant", mcp_server.AGENT_USER)]


@pytest.fixture()
def charted(session):
    """A lab-sized sampled chart with one sample out of place on it."""
    gauges.register(session, code="HEIGHT-01", name="Bench height gauge",
                    kind="height gauge", resolution=0.1, interval_days=180,
                    location="MIX01", actor="test")
    gauges.calibrate(session, "HEIGHT-01", result="pass", performed_by="QA-LEAD",
                     performed_on=utcnow().date() - timedelta(days=14),
                     certificate="CERT-H-1", actor="test")
    quality.create_spec(session, material_code=MATERIAL,
                        characteristic=CHARACTERISTIC, unit="mm",
                        min_value=139.0, max_value=145.0, sample_size=5,
                        actor="test")
    rows = []
    for turn in range(SAMPLES):
        row, _checks, _nc, _signals = quality.record_sample(
            session, material_code=MATERIAL, characteristic=CHARACTERISTIC,
            values=SHIFTED if turn == SAMPLES - 1 else STEADY,
            gauge_code="HEIGHT-01", equipment_code="MIX01", actor="OP-NIGHT")
        session.flush()
        rows.append(row)
    return rows


def _chart(readings=False):
    return mcp_server.spc_chart("testplant", material=MATERIAL,
                                characteristic=CHARACTERISTIC, readings=readings)


def test_the_chart_the_chat_asks_for_does_not_carry_every_bottle(wired, charted):
    """The default answer has no `samples` block and says so in its own words.

    The counts the plant worked out - how many points the chart draws and how
    many readings are behind them - are untouched, because both figures are
    true and a reader told only one of them believes the wrong thing about the
    other.
    """
    trimmed = _chart()
    assert "samples" not in trimmed
    assert trimmed["samples_left_out"] == SAMPLES
    assert trimmed["readings_left_out"] == SAMPLES * 5
    assert trimmed["n"] == SAMPLES
    assert trimmed["readings"] == SAMPLES * 5
    note = trimmed["samples_note"]
    assert str(SAMPLES * 5) in note and str(SAMPLES) in note
    # And it says where the readings behind one point are, by the name of the
    # tool that answers for them.
    assert "spc_sample" in note
    assert "readings=true" in note


def test_everything_the_chart_is_drawn_from_survives_the_trim(wired, charted):
    """The limits, the signals, the range chart and the verdict are the small
    blocks that answer *how does this chart look*. They travel whole."""
    whole, trimmed = _chart(readings=True), _chart()
    for key in THE_CHART:
        assert key in trimmed, key
        assert trimmed[key] == whole[key], key
    # Each point still names the sample to ask about, so the follow-up goes by
    # id and not by a time the model read off a chart.
    assert all("sample" in point and "range" in point
               for point in trimmed["points"])


def test_asking_for_the_readings_brings_them_back_as_the_plant_holds_them(wired, charted):
    """`readings=true` is the whole answer and not a reconstruction of it."""
    whole = _chart(readings=True)
    assert len(whole["samples"]) == SAMPLES
    assert all(len(sample["readings"]) == 5 for sample in whole["samples"])
    assert "samples_left_out" not in whole
    assert "samples_note" not in whole


def test_the_trimmed_chart_fits_in_what_the_model_is_shown(wired, charted):
    """The point of the trim, stated as the number that moved.

    Whole, a lab-sized chart is past `agent.RESULT_LIMIT` and `_tool_result`
    cuts it by the character - which, with `points` first in key order, drops
    the control limits and the verdict off the end. Trimmed, it fits, so the
    model reads the whole chart instead of the first two thirds of one.
    """
    whole, trimmed = _chart(readings=True), _chart()
    size = len(json.dumps(whole, default=str))
    kept = len(json.dumps(trimmed, default=str))
    assert size > agent.RESULT_LIMIT > kept
    block = agent._tool_result("t1", trimmed, agent.RESULT_LIMIT)
    shown = block["content"] if isinstance(block["content"], str) else json.dumps(
        block["content"], default=str)
    assert "control" in shown and "verdict" in shown


def test_a_chart_with_no_samples_block_passes_through_untouched():
    """An individuals chart has no `samples` to leave out, and is not given a
    note about one. `imr` and `xbar_r` are two shapes of answer and this trim
    is about one of them."""
    individuals = {"kind": "imr", "points": [{"value": 1.0, "ts": "t", "check": 1}],
                   "n": 1, "readings": 1}
    assert _without_sample_readings(individuals) == individuals
    assert _without_sample_readings({"error": "404: no such spec"}) == {
        "error": "404: no such spec"}
