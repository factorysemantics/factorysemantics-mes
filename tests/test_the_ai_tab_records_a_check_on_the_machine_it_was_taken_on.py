"""A quality check recorded from the AI tab names the machine and the gauge.

The Floor form and the station screen both send the machine a reading was
taken on (#239), and the API has taken a gauge since decision 0027. The MCP
`record_check` tool - which is what the AI tab and any outside agent call -
sent neither, so the same reading recorded by asking came out with no
machine on it. Structure report 2026-11-14, finding 4: one change, every door.
"""

from fsmes import mcp_server
from fsmes.services import agent


def test_record_check_sends_the_machine_and_the_gauge_it_was_given():
    out = mcp_server.record_check("bottling", material="FG-COLA",
                                  characteristic="fill_weight", value=500.2,
                                  order="WO-1", equipment="FILLER-01",
                                  gauge="SCALE-3", dry_run=True)
    assert out["request"]["body"] == {
        "material": "FG-COLA", "characteristic": "fill_weight", "value": 500.2,
        "order": "WO-1", "equipment": "FILLER-01", "gauge": "SCALE-3"}


def test_record_check_left_without_a_machine_sends_none():
    out = mcp_server.record_check("bottling", material="FG-COLA",
                                  characteristic="fill_weight", value=500.2,
                                  dry_run=True)
    assert out["request"]["body"] == {
        "material": "FG-COLA", "characteristic": "fill_weight", "value": 500.2}


def test_the_ai_tab_is_offered_the_machine_and_the_gauge_on_record_check():
    tools = {t["name"]: t for t in agent.catalogue({"plant.read", "quality.record"})}
    props = tools["record_check"]["input_schema"]["properties"]
    assert "equipment" in props and "gauge" in props
    assert "equipment" not in tools["record_check"]["input_schema"]["required"]
