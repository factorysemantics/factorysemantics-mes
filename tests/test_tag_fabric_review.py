"""What the tag fabric review found, turned into tests.

Every case here is a defect that shipped on `opc/tag-fabric` and was caught
by review rather than by the suite - which is the point of writing them down.
"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsmes.integrations.opc import agent
from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map
from fsmes.kernel.tags import SEMANTIC_TAGS, STRUCTURAL_TAGS
from fsmes.sim import generate

REPO = Path(__file__).resolve().parents[1]

LINE = {
    "channel": "T", "dsn": "T", "seed": 3, "duration_s": 10,
    "orders": [1], "buffers": {"capacity": 4, "initial": 1},
    "stations": [{
        "name": "Washer", "rate_per_min": 60, "scrap_pct": 1.0,
        "analogs": [
            {"name": "WashTemp", "base": 70.0, "noise": 0.0, "decimals": 2,
             "setpoint": {"name": "WashTempSP", "min": 55.0, "max": 85.0,
                          "lag_s": 10.0}},
            {"name": "RinsePressure", "base": 3.0, "noise": 0.0, "decimals": 2},
        ],
    }],
    "events": [],
}


@pytest.fixture()
def line(tmp_path):
    config = tmp_path / "line.json"
    config.write_text(json.dumps(LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate.generate(config, out, write_docs=False)
    return out


def a_map(tmp_path, **over):
    entry = {"equipment": "WASH01", "object": "Washer", "cycle_seconds": 1.0,
             "analog": "WashTemp", "order_tag": None,
             "state_map": {"0": "idle", "1": "running", "4": "down", "5": "setup"}}
    entry.update(over)
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"machines": [entry]}), encoding="utf-8")
    return path


# ------------------------------------------- the drift that started this

def test_a_browsable_machine_subscribes_to_everything_its_line_publishes(line, tmp_path):
    """The list used to be transcribed into the tag map by hand. It drifted
    within a day: two of four maps were updated and two were not."""
    [spec] = load_tag_map(a_map(tmp_path), load_manifest(line))
    published = set(json.loads((line / "tags.json").read_text(encoding="utf-8"))["tables"]["Washer"]["tags"])

    assert set(spec.tags) == published
    assert "WashTempSP" in spec.tags and "AlarmWord" in spec.tags


def test_a_template_addressed_machine_is_left_alone(line, tmp_path):
    """A Kepware-style map reaches tags by formatting a node id and cannot
    check whether one exists. Subscribing to a node that does not fails the
    WHOLE subscription on most servers - so a real server gets only what its
    map states outright."""
    path = a_map(tmp_path, node_id="ns=2;s=SimLine.Washer.Washer_csv_{tag}")
    [spec] = load_tag_map(path, load_manifest(line))

    assert spec.tags == ("State", "GoodCount", "ScrapCount", "WashTemp")


def test_an_explicit_list_still_wins(line, tmp_path):
    path = a_map(tmp_path, extra_tags=["AlarmWord"])
    [spec] = load_tag_map(path, load_manifest(line))
    assert spec.tags == ("State", "GoodCount", "ScrapCount", "WashTemp", "AlarmWord")


def test_no_manifest_means_the_canonical_four(tmp_path):
    """A real server no generator produced is not an error."""
    [spec] = load_tag_map(a_map(tmp_path))
    assert spec.tags == ("State", "GoodCount", "ScrapCount", "WashTemp")


def test_the_committed_maps_agree_with_their_lines(tmp_path):
    """The guard this replaces read a gitignored path and silently skipped,
    so it had never run in CI. This one generates its own input."""
    for line_json, map_json in (
        (REPO / "labs" / "kepsim" / "line.json", REPO / "config" / "tag_map_kepsim.json"),
        (REPO / "labs" / "multiplant" / "machining" / "line.json",
         REPO / "labs" / "multiplant" / "machining" / "tag_map.json"),
    ):
        out = tmp_path / line_json.parent.name
        generate.generate(line_json, out, write_docs=False)
        manifest = load_manifest(out)
        for spec in load_tag_map(map_json, manifest):
            published = set(manifest["tables"][spec.object]["tags"])
            assert set(spec.tags) <= published, (
                f"{spec.equipment} subscribes to tags {line_json.name} does not "
                f"publish: {sorted(set(spec.tags) - published)}")
            assert len(spec.tags) >= 10


# ------------------------------------------------------- the lost readings

@pytest.mark.asyncio
async def test_a_closing_connection_flushes_what_it_buffered(monkeypatch):
    """Up to 199 readings were dropped on every disconnect and on shutdown,
    with no flush and no log."""
    handler = agent._Handler({object(): (None, "WashTemp")})
    handler._pending = [("WASH01", "WashTemp", 1.0, None, 0.0)] * 5
    written = []
    monkeypatch.setattr(handler, "_process", lambda batch: written.append(len(batch)))

    await handler.flush("test")
    assert written == [5]
    assert handler._pending == []


@pytest.mark.asyncio
async def test_flushing_nothing_is_free():
    handler = agent._Handler({})
    await handler.flush("test")          # must not raise, must not log noise


# ---------------------------------------------------------- the timestamps

def test_a_reading_keeps_the_moment_the_server_observed_it():
    """Batching collapsed every row onto the flush time, because TagValue.ts
    defaults at insert. Two hundred points on one tick, then a gap - in the
    one table whose job is arguing with the PLC about what it sent."""
    observed = datetime(2026, 9, 2, 11, 0, 0)
    notification = SimpleNamespace(
        monitored_item=SimpleNamespace(Value=SimpleNamespace(SourceTimestamp=observed)))

    assert agent._source_time(notification) == observed


def test_a_server_that_sends_no_timestamp_falls_back_to_now():
    assert agent._source_time(SimpleNamespace()) is None


# ------------------------------------------------------- the process value

def test_the_common_tags_are_never_mistaken_for_a_process_value():
    """Both screens pick "the process value" by excluding what every machine
    carries. The list did not grow with the tag fabric, so the analysis chart
    would have drawn ReadyBit."""
    for tag in ("TotalCount", "AlarmWord", "CycleTimeMs", "RunMinutes", "ReadyBit"):
        assert tag in STRUCTURAL_TAGS, f"{tag} would be charted as a process value"
    assert set(SEMANTIC_TAGS) <= set(STRUCTURAL_TAGS)


def test_analysis_asks_the_tag_map_before_guessing():
    source = (REPO / "src" / "fsmes" / "services" / "analysis.py").read_text(encoding="utf-8")
    assert "line_service.tag_map_analogs()" in source, (
        "the two screens must agree about which signal is the process value")


# --------------------------------------------------------- the friendly error

def test_a_line_whose_data_and_manifest_disagree_says_so(line, tmp_path):
    """A stale manifest used to raise a bare KeyError three lines past the
    diagnostic written for exactly that case."""
    import asyncio

    from fsmes.config import Settings
    from fsmes.integrations.opc import csv_replay

    (line / "tags.json").write_text(json.dumps({"tables": {}}), encoding="utf-8")
    settings = Settings(opc_endpoint="opc.tcp://127.0.0.1:48999/t")
    machines = load_tag_map(a_map(tmp_path, extra_tags=["WashTempSP"]))

    with pytest.raises(ValueError, match="no column 'WashTempSP'"):
        asyncio.run(csv_replay.build_server(settings, machines, line))


def test_a_batch_lands_with_each_readings_own_timestamp(session, scope, monkeypatch):
    """The end of the fidelity claim: not just that the source time is read,
    but that it reaches the row. Before this, two hundred readings shared the
    instant the batch happened to flush."""
    from sqlalchemy import select

    from fsmes.domain import Equipment, EquipmentLevel, TagValue
    from fsmes.services import masterdata

    session.add(Equipment(code="WASH01", name="Washer",
                          level=EquipmentLevel.WORK_UNIT))
    session.flush()

    monkeypatch.setattr(agent, "session_scope", scope)
    monkeypatch.setattr(masterdata, "get_equipment",
                        lambda s, code: session.scalar(
                            select(Equipment).where(Equipment.code == code)))

    handler = agent._Handler({})
    observed = [datetime(2026, 9, 2, 11, 0, second) for second in (1, 2, 3)]
    handler._write_history(
        [("WASH01", "WashTemp", 70.0 + i, ts) for i, ts in enumerate(observed)])

    stored = sorted(session.scalars(select(TagValue.ts)))
    assert stored == observed, "each reading must keep the moment it was observed"
