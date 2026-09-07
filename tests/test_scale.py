"""Scale hardening before any real plant: retention on tag history, the
index the latest-value queries needed, metrics that do not count the
biggest table, one agent per endpoint, and a handler whose history writes
cannot block or take down the breakdown-detection path.
"""

from datetime import timedelta
from pathlib import Path

from sqlalchemy import inspect, select

from fsmes.config import Settings
from fsmes.db import utcnow
from fsmes.domain import Equipment, TagValue
from fsmes.integrations.opc import agent as opc_agent
from fsmes.services import retention


def _unit(session):
    return session.scalar(select(Equipment).where(Equipment.code == "MIX01"))


def test_pruning_deletes_only_what_is_older_than_the_window(session):
    unit = _unit(session)
    for age_days in (30, 20, 10, 1, 0):
        session.add(TagValue(equipment_id=unit.id, tag="MIX01.Temperature", value_num=60.0,
                             ts=utcnow() - timedelta(days=age_days)))
    session.flush()
    before = retention.report(session, keep_days=14)
    assert before["tag_values"] == 5 and "deleted hourly" in before["policy"]
    assert retention.prune_tag_values(session, keep_days=14, batch=1) == 2
    left = sorted(r.ts for r in session.scalars(select(TagValue)))
    assert len(left) == 3 and (utcnow() - left[0]).days == 10
    assert retention.prune_tag_values(session, keep_days=14) == 0
    assert retention.prune_tag_values(session, keep_days=0) == 0, "zero means off, not delete everything"
    assert "kept for ever" in retention.report(session, keep_days=0)["policy"]


def test_the_latest_value_index_exists():
    names = {idx.name for idx in TagValue.__table__.indexes}
    assert "ix_tag_values_equipment_tag_id" in names


def test_the_migration_creates_the_index(session):
    names = {i["name"] for i in inspect(session.get_bind()).get_indexes("tag_values")}
    assert "ix_tag_values_equipment_tag_id" in names


def test_metrics_do_not_count_the_biggest_table(sign_in):
    client = sign_in("MET", role="viewer")
    text = client.get("/metrics").text
    assert "mes_tag_values_max_id" in text and "mes_tag_values_total" not in text
    assert "mes_audit_entries_max_id" in text


def test_one_agent_config_per_endpoint():
    single = Settings(opc_endpoint="opc.tcp://a:4840/x", tag_map_file="config/tag_map.json")
    [only] = opc_agent.agent_configs(single)
    assert only.opc_endpoint == "opc.tcp://a:4840/x"

    many = Settings(opc_endpoints='[{"endpoint": "opc.tcp://a:4840/x", "tag_map": "config/a.json"},'
                                  ' {"endpoint": "opc.tcp://b:4840/y", "tag_map": "config/b.json",'
                                  ' "namespace": "urn:b"}]')
    configs = opc_agent.agent_configs(many)
    assert [c.opc_endpoint for c in configs] == ["opc.tcp://a:4840/x", "opc.tcp://b:4840/y"]
    assert configs[1].tag_map_file == Path("config/b.json") and configs[1].opc_namespace == "urn:b"
    assert configs[0].opc_namespace == many.opc_namespace, "unset fields inherit the plant's settings"


def test_history_and_decisions_are_booked_in_separate_sessions(monkeypatch):
    """A history batch must never hold up - or take down - the State change
    that says a machine broke down. Once two locks; now two sessions inside
    one ordered batch, decisions first."""
    handler = opc_agent._Handler({})
    order = []
    monkeypatch.setattr(handler, "_book", lambda session, decisions: order.append("decisions"))
    monkeypatch.setattr(handler, "_write_history", lambda rows: order.append("history"))
    monkeypatch.setattr(opc_agent, "session_scope",
                        lambda: __import__("contextlib").nullcontext(object()))
    spec = opc_agent.MachineMap(equipment="MIX01", object="Mixer", cycle_seconds=1.0)
    handler._process([(spec, "Temperature", 60.0, None, 0.0), (spec, "State", 4, None, 0.0)])
    assert order == ["decisions", "history"]
