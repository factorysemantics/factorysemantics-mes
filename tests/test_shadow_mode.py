"""Shadow mode: the MES watches a real plant and can change nothing in it.

Two kinds of test, and the second is the one that keeps the first honest.

The **walk** takes every path in `fsmes.shadow.REGISTER` that shadow mode
closes and asks it to act, with shadow mode on, and holds the refusal. One
prose-named test per path, so a failure names the thing that got out.

The **ratchet** reads the package's own source for the primitives by which
this process can reach past its own database - an OPC UA node write, an HTTP
client, an MQTT publish, a socket - and fails on any call site the register
does not cover. Same shape as `test_mcp_parity.py` for write routes: the
point is that a new outbound path cannot be added without somebody deciding,
in writing, what shadow mode does to it.

The ratchet is a ratchet, not a proof. It sees the primitives it knows about
in code it can parse; it cannot see one reached by reflection, by a plugin,
or through a library that opens its own socket. That is why the register
carries a `note` for every entry rather than a tick.
"""

import ast
import asyncio
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from fsmes import shadow
from fsmes.config import Settings, get_settings
from fsmes.integrations.opc import agent as opc_agent
from fsmes.integrations.uns import transport as uns_transport

SRC = Path(__file__).resolve().parents[1] / "src" / "fsmes"


@pytest.fixture()
def shadow_on(monkeypatch):
    """This process, in shadow mode, for the length of one test."""
    monkeypatch.setenv("MES_SHADOW", "true")
    monkeypatch.setenv("MES_ERP_MODE", "off")
    monkeypatch.setenv("MES_UNS_MODE", "off")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


class FakeNode:
    """An OPC UA node that remembers what reached it."""

    def __init__(self, value=0.0):
        self.value = value
        self.writes = []

    async def write_value(self, value):
        self.writes.append(value)
        self.value = value

    async def read_value(self):
        return self.value


# --------------------------------------------------------------- the switch

def test_shadow_mode_is_off_unless_somebody_turns_it_on():
    assert Settings().shadow is False
    assert shadow.enabled(Settings()) is False
    assert shadow.enabled(Settings(shadow=True)) is True


def test_a_default_nobody_chose_is_closed_rather_than_refused():
    """MES_ERP_MODE defaults to 'rest' so a laptop runs with no setup.
    Refusing to start over a default a person never made would read as
    shadow mode being broken - so it is settled, and reported."""
    settled = Settings(shadow=True)
    assert settled.erp_mode == "off" and shadow.summary(settled)["erp_mode"] == "off"

    # A mode somebody actually set is a decision, and it is refused out loud.
    with pytest.raises(ValidationError, match="MES_ERP_MODE"):
        Settings(shadow=True, erp_mode="rest")


def test_a_live_erp_and_shadow_mode_cannot_both_be_asked_for():
    # Every process builds Settings, so every process gets this refusal, and
    # a live adapter is never constructed anywhere.
    for mode in ("rest", "erpnext", "some-connector-somebody-installed"):
        with pytest.raises(ValidationError) as caught:
            Settings(shadow=True, erp_mode=mode)
        assert "MES_ERP_MODE" in str(caught.value) and mode in str(caught.value)


def test_a_broker_and_shadow_mode_cannot_both_be_asked_for():
    with pytest.raises(ValidationError) as caught:
        Settings(shadow=True, uns_mode="mqtt")
    assert "MES_UNS_MODE" in str(caught.value)


def test_the_erp_modes_shadow_mode_allows_are_the_ones_that_reach_nothing():
    for mode in shadow.ERP_MODES_ALLOWED:
        assert Settings(shadow=True, erp_mode=mode).erp_mode == mode
    for mode in shadow.UNS_MODES_ALLOWED:
        assert Settings(shadow=True, uns_mode=mode).uns_mode == mode


def test_the_person_who_set_the_wrong_variable_gets_one_sentence(monkeypatch):
    monkeypatch.setenv("MES_SHADOW", "true")
    monkeypatch.setenv("MES_ERP_MODE", "erpnext")
    get_settings.cache_clear()
    try:
        with pytest.raises(shadow.ShadowMisconfigured) as caught:
            get_settings()
    finally:
        get_settings.cache_clear()
    message = str(caught.value)
    assert "pydantic" not in message and "validation error" not in message.lower()
    assert message.startswith("MES_SHADOW is on")
    assert "unset mes_shadow" in message.lower()


def test_shadow_mode_is_read_at_start_up_and_not_toggled(shadow_on):
    """There is no runtime switch, on purpose: a shift must not be able to
    change what the MES is allowed to do halfway through."""
    assert not any(name.startswith("set_") or name in ("enable", "disable", "toggle")
                   for name in dir(shadow)), "shadow mode gained a runtime switch"


# ------------------------------------------------------------ the plant floor

def test_no_setpoint_reaches_the_plant_in_shadow_mode(shadow_on):
    node = FakeNode(65.0)
    with pytest.raises(shadow.ShadowRefused) as caught:
        asyncio.run(opc_agent.write_node(node, 60.0, path="opc.adjustment_write", what="MIX01.TemperatureSP"))
    assert node.writes == []
    assert "Shadow mode" in str(caught.value)


def test_an_approved_recommendation_waits_instead_of_reaching_the_machine(session, scope, shadow_on):
    from fsmes.domain import AdjustmentStatus, RecommendedAdjustment

    rec = RecommendedAdjustment(code="ADJ-S1", equipment_code="MIX01", tag="TemperatureSP",
                                drives="Temperature", proposed_value=60.0, minimum=40.0, maximum=80.0,
                                rationale="cool it", proposed_by="t", status=AdjustmentStatus.APPROVED)
    session.add(rec)
    session.flush()
    sp = FakeNode(65.0)
    nodes = {("MIX01", "TemperatureSP"): sp}
    bounds = {"MIX01": {"TemperatureSP": {"min": 40.0, "max": 80.0}}}

    assert asyncio.run(opc_agent.write_approved_adjustments(nodes, bounds, scope)) == []
    assert sp.writes == [], "a setpoint reached a real machine in shadow mode"
    session.refresh(rec)
    # Not failed: nothing was attempted, and an engineer's queue full of
    # failures that never happened is a lie about the plant.
    assert rec.status is AdjustmentStatus.APPROVED


def test_the_order_code_is_not_written_down_to_the_machines(shadow_on):
    """The write-down that was never approval-gated. A machine gets an order
    node by default, so this is the path shadow mode most needed to close."""
    node = FakeNode("")
    with pytest.raises(TimeoutError):
        # With shadow mode off the loop writes on its first pass; with it on
        # it holds the subscription open and writes nothing, so it times out.
        asyncio.run(asyncio.wait_for(opc_agent._order_code_loop({"MIX01": node}), 0.5))
    assert node.writes == []


# ------------------------------------------------------------------ the ERP

def test_no_live_erp_adapter_can_be_built_in_shadow_mode(shadow_on):
    from fsmes.integrations.erp.base import make_adapter

    # Not by refusing inside the factory - by never letting the settings that
    # would reach it exist. Proved from the other end: ask for them.
    for mode in ("rest", "erpnext"):
        with pytest.raises(ValidationError, match="MES_ERP_MODE"):
            make_adapter(Settings(shadow=True, erp_mode=mode))
    assert make_adapter(Settings(shadow=True, erp_mode="off")) is None


def test_the_file_adapter_reads_the_inbox_and_leaves_it_exactly_as_it_found_it(tmp_path, shadow_on):
    """The inbox may be a folder the plant's own MES is reading. Renaming a
    file out of it takes that MES's orders away."""
    from fsmes.integrations.erp.file_adapter import FileErpAdapter

    inbox, outbox, archive = tmp_path / "in", tmp_path / "out", tmp_path / "arc"
    inbox.mkdir()
    order = inbox / "order.json"
    order.write_text('{"code": "WO-1", "material": "M1", "quantity": 10}', encoding="utf-8")
    unreadable = inbox / "broken.json"
    unreadable.write_text("{not json", encoding="utf-8")

    adapter = FileErpAdapter(inbox, outbox, archive)
    assert [r.code for r in adapter.fetch_orders()] == ["WO-1"]
    assert sorted(p.name for p in inbox.iterdir()) == ["broken.json", "order.json"]
    assert list(archive.iterdir()) == [], "shadow mode moved a file out of somebody's inbox"

    # And it does not re-import the same file on every poll.
    assert adapter.fetch_orders() == []


def test_the_file_adapter_still_writes_confirmations_for_a_person_to_compare(tmp_path, shadow_on):
    """Deliberately allowed. A shadow run exists to produce confirmations
    somebody can hold against the incumbent's; a file changes nothing until
    something imports it, so the outbox goes in a folder no ERP is watching."""
    from fsmes.integrations.erp.contract import OrderCompletion
    from fsmes.integrations.erp.file_adapter import FileErpAdapter

    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "arc")
    adapter.send_confirmation(OrderCompletion(message_key="WO-1:done", order="WO-1", material="M1",
                                              ordered_qty=10.0, good_qty=10.0, scrap_qty=0.0))
    assert len(list((tmp_path / "out").iterdir())) == 1


# ------------------------------------------------------- the namespace (MQTT)

def test_no_broker_is_contacted_in_shadow_mode(shadow_on):
    with pytest.raises(shadow.ShadowRefused):
        uns_transport.make_transport(Settings(shadow=True, uns_mode="off").model_copy(
            update={"uns_mode": "mqtt"}))


def test_an_mqtt_transport_built_some_other_way_still_refuses(shadow_on):
    live = uns_transport.MqttTransport(uns_transport.BrokerAddress("mqtt://broker.invalid:1883"))
    with pytest.raises(shadow.ShadowRefused):
        asyncio.run(live.connect())
    with pytest.raises(shadow.ShadowRefused):
        asyncio.run(live.publish("umh/v1/x", b"{}", qos=1, retain=False))


def test_the_namespace_can_still_be_seen_in_log_mode(shadow_on):
    """Shadow mode closes the broker, not the topic tree: a plant should be
    able to see what it would publish."""
    transport = uns_transport.make_transport(Settings(shadow=True, uns_mode="log"))
    asyncio.run(transport.publish("umh/v1/x", b"{}", qos=1, retain=False))
    assert transport.sent == [("umh/v1/x", b"{}")]


# ---------------------------------------------------------- language models

def test_this_plants_numbers_do_not_leave_the_box(shadow_on, monkeypatch):
    """The cloud brain changes nothing in the plant, but it carries the
    plant's own numbers off it, and a plant lending us its data to watch did
    not agree to that."""
    from fsmes.services import agent as agent_service

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_AGENT_BRAIN", "claude")
    ok, why = agent_service.available()
    assert ok is False and "shadow mode" in why

    from fsmes.services import design

    assert design.claude_available() is False


def test_the_local_model_still_answers_in_shadow_mode(shadow_on):
    """It runs on this machine; nothing about the plant leaves the box."""
    assert [p for p in shadow.REGISTER if p.name.startswith("llm.local_")], "the register lost them"
    assert all(p.verdict == "allowed" for p in shadow.REGISTER if p.name.startswith("llm.local_"))


def test_the_demo_refuses_to_run_a_fake_plant_next_to_a_real_one(shadow_on):
    """`fsmes demo` builds its own REST adapter, around every other gate."""
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 2
    assert "Shadow mode" in result.output


# ------------------------------------------------------------ saying it out loud

def test_health_says_whether_this_mes_may_act_on_its_plant(anon):
    assert anon.get("/health").json()["shadow"] is False


def test_the_whole_register_is_public_because_the_person_who_needs_it_has_no_account(anon):
    body = anon.get("/shadow").json()
    assert body["shadow"] is False
    assert body["outbound_paths_total"] == len(shadow.REGISTER)
    assert {p["name"] for p in body["paths"]} == {p.name for p in shadow.REGISTER}


def test_every_screen_carries_the_bar_because_the_bar_is_in_the_one_header():
    common = (SRC / "web" / "common.js").read_text(encoding="utf-8")
    assert "shadowBar" in common and "/shadow" in common
    assert "shadow-bar" in (SRC / "web" / "styles.css").read_text(encoding="utf-8")


def test_the_agent_surface_says_it_too():
    server = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    assert "SHADOW MODE" in server, "the MCP server's description does not mention it"
    assert '"shadow": shadow' in server, "list_plants does not report it per plant"


def test_the_command_line_says_it():
    cli = (SRC / "cli.py").read_text(encoding="utf-8")
    assert "shadow mode   ON" in cli and "shadow mode   off" in cli


def test_leaving_shadow_mode_leaves_a_mark_in_the_audit_trail():
    app = (SRC / "api" / "app.py").read_text(encoding="utf-8")
    assert "shadow.on" in app and "shadow.off" in app


# ---------------------------------------------------------------- the register

def test_the_register_says_how_many_entries_it_has_and_means_it():
    """House rule: every list states its total."""
    source = (SRC / "shadow.py").read_text(encoding="utf-8")
    stated = re.search(r"\*\*(\d+) entries\.\*\*", source)
    assert stated, "the register no longer states its total"
    assert int(stated.group(1)) == len(shadow.REGISTER)


def test_every_entry_in_the_register_is_a_distinct_place_with_a_verdict_and_a_reason():
    assert len({p.name for p in shadow.REGISTER}) == len(shadow.REGISTER)
    assert len({p.where for p in shadow.REGISTER}) == len(shadow.REGISTER)
    for entry in shadow.REGISTER:
        assert entry.verdict in ("refused", "restricted", "allowed"), entry.name
        assert len(entry.note) > 20, f"{entry.name} has no reason written down"
        assert entry.reaches, entry.name


#: Every path the register says shadow mode closes, and the test above that
#: proves it. A closed path with no proof is a claim, not a guarantee.
PROVED_BY = {
    "opc.node_write": "test_no_setpoint_reaches_the_plant_in_shadow_mode",
    "opc.adjustment_write": "test_an_approved_recommendation_waits_instead_of_reaching_the_machine",
    "opc.order_code": "test_the_order_code_is_not_written_down_to_the_machines",
    "erp.adapter": "test_no_live_erp_adapter_can_be_built_in_shadow_mode",
    "erp.rest": "test_a_live_erp_and_shadow_mode_cannot_both_be_asked_for",
    "erp.erpnext": "test_a_live_erp_and_shadow_mode_cannot_both_be_asked_for",
    "erp.erpnext_setup": "test_a_live_erp_and_shadow_mode_cannot_both_be_asked_for",
    "erp.file_inbox": "test_the_file_adapter_reads_the_inbox_and_leaves_it_exactly_as_it_found_it",
    "uns.transport": "test_no_broker_is_contacted_in_shadow_mode",
    "uns.mqtt_connect": "test_an_mqtt_transport_built_some_other_way_still_refuses",
    "uns.mqtt_publish": "test_an_mqtt_transport_built_some_other_way_still_refuses",
    "uns.publish_loop": "test_no_broker_is_contacted_in_shadow_mode",
    "cli.demo": "test_the_demo_refuses_to_run_a_fake_plant_next_to_a_real_one",
    "cli.demo_wait": "test_the_demo_refuses_to_run_a_fake_plant_next_to_a_real_one",
    "llm.cloud_agent": "test_this_plants_numbers_do_not_leave_the_box",
    "llm.cloud_design": "test_this_plants_numbers_do_not_leave_the_box",
}


def test_every_path_shadow_mode_closes_is_proved_closed_in_this_file():
    closed = {p.name for p in shadow.REGISTER if p.verdict in ("refused", "restricted")}
    here = {name for name in globals() if name.startswith("test_")}

    unproved = sorted(closed - set(PROVED_BY))
    assert not unproved, ("the register closes these paths and nothing here proves it; "
                          f"write the test and name it in PROVED_BY: {unproved}")

    stale = sorted(name for name in PROVED_BY if name not in closed)
    assert not stale, f"PROVED_BY names paths the register no longer closes: {stale}"

    missing = sorted(t for t in PROVED_BY.values() if t not in here)
    assert not missing, f"PROVED_BY names tests that do not exist: {missing}"


# ------------------------------------------------------------------ the ratchet

#: The primitives by which this package can reach past its own database, as
#: patterns matched against the source of each call. Named, because a
#: failure should say what kind of reach it found.
PRIMITIVES = {
    "an OPC UA node write": r"\.write_value\s*\(|\.write_attribute_value\s*\(|\.set_writable\s*\(",
    # `session` is deliberately absent from the receiver list: a SQLAlchemy
    # Session's .get() is a database read and appears in half the package.
    "an HTTP call": (r"\b(?:httpx|requests)\.\w*\s*\(|\burlopen\s*\(|"
                     r"\b(?:httpx|requests|_?clients?|http)\w*\s*(?:\([^()]*\))?"
                     r"\.(?:get|post|put|patch|delete|request|stream)\s*\("),
    "an MQTT publish": r"\baiomqtt\b|\.publish\s*\(",
    "a mail server": r"\bsmtplib\.",
    "a socket": r"\bsocket\.(?:socket|create_connection)\s*\(",
    "a model provider": r"\banthropic\.\w+\s*\(|\.messages\.create\s*\(",
}

#: Call sites the scan finds that are not outbound reach, and why. Keep it
#: short: an entry here is a claim that the reader should not worry, and the
#: register is the better place for anything that touches the world.
NOT_OUTBOUND: dict[str, str] = {}


def _sites() -> dict[str, set[str]]:
    """module:qualname -> the kinds of outbound reach found in it."""
    found: dict[str, set[str]] = {}
    for file in sorted(SRC.rglob("*.py")):
        module = "fsmes." + ".".join(file.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
        source = file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        stack: list[str] = []

        def walk(node, stack=stack, module=module, source=source):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    stack.append(child.name)
                    walk(child)
                    stack.pop()
                    continue
                if isinstance(child, ast.Call):
                    text = ast.get_source_segment(source, child) or ""
                    head = text.split("\n")[0]
                    for kind, pattern in PRIMITIVES.items():
                        if re.search(pattern, head):
                            where = f"{module}:{'.'.join(stack)}" if stack else module
                            found.setdefault(where, set()).add(kind)
                walk(child)

        walk(tree)
    return found


def _covers(entry_where: str, site: str) -> bool:
    """A register entry covers a site it names, or any site beneath it."""
    return site == entry_where or site.startswith(entry_where + ".") or site.startswith(entry_where + ":")


def test_a_new_outbound_path_cannot_be_added_without_the_register_hearing_about_it():
    sites = _sites()
    assert len(sites) > 20, "the scan found almost nothing; it has stopped working"

    known = [p.where for p in shadow.REGISTER] + list(NOT_OUTBOUND)
    unlisted = sorted(site for site in sites if not any(_covers(w, site) for w in known))
    assert not unlisted, (
        "these reach past this MES's own database and the register does not mention them. "
        "Add an entry to fsmes.shadow.REGISTER saying what shadow mode does to each, and if "
        f"it is closed, a test naming it in PROVED_BY: {unlisted}")


def test_the_register_does_not_name_places_that_no_longer_exist():
    modules = {"fsmes." + ".".join(f.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
               for f in SRC.rglob("*.py")}
    packages = {m.rsplit(".", 1)[0] for m in modules} | {"fsmes"}
    for entry in shadow.REGISTER:
        module = entry.where.split(":")[0]
        assert module in modules or module in packages, f"{entry.name} names {module}, which is gone"
