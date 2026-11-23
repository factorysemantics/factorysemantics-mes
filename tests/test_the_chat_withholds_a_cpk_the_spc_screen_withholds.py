"""The chat is not told a capability figure the SPC screen withholds.

The steward, after the gauge study run of 2026-11-22: on the cutlery plant
the AI tab said a knife's Cpk "sits at 1.126" while the SPC page for the
same characteristic printed "withheld", because the chart was out of
control. The chart's payload carries Cp, Cpk and Pp either way; the screen
hides them while the process is out of control (`web/spc.js`), and the
chat's tools handed them on whole - though `spc_chart`'s own description
said they were withheld. Same chart, two answers.

So `spc_chart`, `spc_point` and `spc_sample` hand the model what the screen
shows: the capability block stays, saying why it is withheld, and the
figures do not. An in-control chart keeps them. The API is unchanged, so
the screen's tooltip still has the figure for the engineer who wants it.
"""

import pytest

from fsmes import mcp_server
from fsmes.mcp.quality import CAPABILITY_WITHHELD
from fsmes.services import auth, quality, spc

MATERIAL = "FG-COLA"
STEADY = [10.0, 10.02, 9.98, 10.01, 9.99]
FIGURES = ("cp", "cpk", "cpu", "cpl", "pp", "ppk")


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


def _charted(session, characteristic, last):
    """Twenty-five readings inside a 9-11 specification; the last one is `last`."""
    quality.create_spec(session, material_code=MATERIAL, characteristic=characteristic,
                        unit="mm", min_value=9.0, max_value=11.0, actor="test")
    checks = []
    for turn in range(25):
        value = last if turn == 24 else STEADY[turn % len(STEADY)]
        check, _nc, _signals = quality.record_check(
            session, material_code=MATERIAL, characteristic=characteristic,
            value=value, actor="OP-NIGHT")
        checks.append(check)
    session.flush()
    return checks


def test_an_out_of_control_chart_reaches_the_chat_with_its_capability_withheld(wired, session):
    checks = _charted(session, "blade width", last=10.8)
    drawn = spc.chart(session, MATERIAL, "blade width")
    assert drawn["stable"] is False and drawn["capability"]["cpk"] is not None, \
        "the fixture should be an out-of-control chart the API still has a Cpk for"

    answer = mcp_server.spc_chart("testplant", material=MATERIAL, characteristic="blade width")
    point = mcp_server.spc_point("testplant", material=MATERIAL, characteristic="blade width",
                                 check_id=checks[-1].id)

    for capability in (answer["capability"], point["chart"]["capability"]):
        assert not set(FIGURES) & set(capability), capability
        assert capability["withheld"] == CAPABILITY_WITHHELD
    assert "out of control" in CAPABILITY_WITHHELD


def test_an_in_control_chart_reaches_the_chat_with_its_cpk(wired, session):
    _charted(session, "blade length", last=10.0)
    answer = mcp_server.spc_chart("testplant", material=MATERIAL, characteristic="blade length")

    assert answer["stable"] is True
    assert answer["capability"]["cpk"] == spc.chart(session, MATERIAL, "blade length")["capability"]["cpk"]
