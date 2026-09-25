"""The third pack runs from the same wheel with two modules off.

M8's *done when* asks for "two packs with different modules enabled running
from one codebase". Piece 2 made a module switchable with `MES_MODULES`;
piece 3 put the switch in the pack, and this is the plant that uses it.

`labs/multiplant/finewire` is invented - a wire-drawing hall that exists
nowhere, written to disagree with the pack **format** rather than with the
line. It is in a different time zone, on PostgreSQL, in the `plant` profile,
with an ERP mode set, with its own word for two labels, and with
`serialization` and `coa` switched off.

**Nothing here starts a plant.** The settings are built from the pack, the
app from the settings, and the agent's tool list from a subprocess given the
pack's environment. There is no database for this plant, no broker, no OPC
server and no process; the standing rule is that the lab fleet is the
operator's to start, and a test that needed one would be a test nobody could
run in CI.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import use_the_test_session
from fsmes import identity
from fsmes import modules as registry
from fsmes.api.app import create_app
from fsmes.config import Settings, get_settings
from fsmes.pack import format as fmt

PACK = Path(__file__).resolve().parents[1] / "labs" / "multiplant" / "finewire"

#: What this plant switches off, and what each takes with it.
OFF = {"serialization": ("/trace/serial/X", "/dashboard/trace"),
       "coa": ("/coa/1", None)}


@pytest.fixture()
def from_the_pack(monkeypatch, session):
    """An API client for the plant this pack describes.

    Everything the app reads comes out of `plant.toml`: the settings are
    compiled from the pack, put into the environment the way
    `fsmes plant finewire start` would, and the app is built from them. The
    one thing that does not come from the pack is the database - the pack
    names a PostgreSQL this machine does not have, and the suite's own
    session stands in for it, because what is being asked here is what the
    plant *serves*, not what it stores.
    """
    values = dict(fmt.settings(fmt.read(PACK)))
    values.pop("MES_DATABASE_URL", None)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    app = create_app()
    use_the_test_session(app, session, monkeypatch)
    client = TestClient(app)
    client.__enter__()
    response = client.post("/auth/login", json={"code": "ADMIN", "password": "admin"})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    yield client
    client.__exit__(None, None, None)
    get_settings.cache_clear()


# ------------------------------------------------------------------ the plant


def test_the_pack_compiles_to_a_plant_this_product_will_start():
    """The whole of the identity, from the file, with no plant running: the
    settings model is what a process builds at start-up, and it refuses an
    identity a reader could not trust."""
    values = fmt.settings(fmt.read(PACK))
    settings = Settings(**{k.removeprefix("MES_").lower(): v for k, v in values.items()})
    assert settings.plant_name == "finewire"
    assert settings.plant_profile == "plant"
    assert settings.plant_timezone == "Europe/Berlin"
    assert settings.erp_mode == "file"
    assert settings.uns_mode == "log"
    assert identity.clock(settings).defaulted is False


def test_the_plant_says_what_it_calls_things_and_renames_nothing_else():
    """Display only. Two labels, and every state, capability, role and event
    kind untouched - which `fsmes pack check` is what enforces."""
    values = fmt.settings(fmt.read(PACK))
    settings = Settings(words=values["MES_WORDS"])
    assert identity.words(settings) == {"lot": "coil", "material": "alloy"}
    assert identity.say("lot", settings) == "coil"
    # A word this plant did not rename is the product's own.
    assert identity.say("work order", settings) == "work order"
    assert identity.say("running", settings) == "running"
    assert identity.summary(settings)["words"] == {"lot": "coil", "material": "alloy"}


# ------------------------------------------------------------------- the API


def test_a_module_this_pack_switches_off_answers_404(from_the_pack):
    """The difference that matters to a caller. Not routes that refuse - no
    routes at all."""
    for path in ("/trace/serial/COIL-1", "/coa/1"):
        assert from_the_pack.get(path).status_code == 404, path


def test_everything_this_pack_keeps_still_answers(from_the_pack):
    """The other half of the claim, and the one a plant cares about: two
    modules went, and only two."""
    assert from_the_pack.get("/health").status_code == 200
    assert from_the_pack.get("/workorders").status_code == 200
    assert from_the_pack.get("/quality/specs").status_code == 200
    assert from_the_pack.get("/maintenance/orders").status_code != 404


def _every_path(monkeypatch) -> dict:
    """What a plant with every module on documents. Built inside the test
    because by the time it runs, this process's environment *is* the third
    plant's - which is the whole point of the fixture."""
    monkeypatch.setenv("MES_MODULES", "all")
    get_settings.cache_clear()
    return create_app().openapi()["paths"]


def test_the_api_document_describes_neither_module(from_the_pack, monkeypatch):
    """An integrator reads /openapi.json to find out what a plant offers.
    This plant should not describe a traceability screen it does not serve."""
    served = from_the_pack.get("/openapi.json").json()["paths"]
    full = _every_path(monkeypatch)
    assert any(p.startswith("/trace") for p in full)
    assert not [p for p in served if p.startswith(("/trace", "/coa"))]
    assert set(served) == {p for p in full if not p.startswith(("/trace", "/coa"))}


def test_the_screens_of_a_module_that_is_off_go_with_it(from_the_pack):
    """A page whose every fetch answers 404 is worse than no page: it looks
    broken rather than absent."""
    assert from_the_pack.get("/dashboard/trace").status_code == 404
    assert from_the_pack.get("/dashboard").status_code == 200
    assert from_the_pack.get("/dashboard/quality").status_code == 200


def test_the_plant_answers_health_with_its_own_name_clock_and_words(from_the_pack):
    """What a fleet console will read. Piece 1 put the name and the clock on
    /health; a plant's own words ride with them, because a console about to
    show two plants' numbers side by side has to know which word is a rename."""
    said = from_the_pack.get("/health").json()
    assert said["plant"] == "finewire"
    assert said["timezone"] == "Europe/Berlin" and said["timezone_defaulted"] is False
    assert said["words"] == {"lot": "coil", "material": "alloy"}


# ------------------------------------------------------------------ the agent


def test_the_agent_tool_list_is_shorter_for_this_plant():
    """`fsmes/mcp/__init__.py` has said since 2026-09-02 that "a module a
    plant pack disables takes its tools with it by not being registered". A
    pack now disables one.

    In a subprocess because the MCP server registers its tools at import, so
    the honest way to ask what this plant's agent would offer is to start a
    process configured as this plant.
    """
    script = ("import json, fsmes.mcp_server as s;"
              "print(json.dumps(sorted(s.TOOL_MODULES)))")

    def ask(extra: dict[str, str]) -> list[str]:
        done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=180, env={**os.environ, **extra})
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    everything = ask({"MES_MODULES": "all"})
    assert len(everything) == 11, everything

    values = fmt.settings(fmt.read(PACK))
    this_plant = ask({"MES_MODULES": values["MES_MODULES"]})
    assert len(this_plant) == 9, this_plant
    assert set(everything) - set(this_plant) == {"serialization", "coa"}


# ------------------------------------------------------------- the disagreement


def test_the_third_pack_disagrees_with_the_format_and_not_only_with_the_line():
    """The point of writing it. The first two lab packs agree about every key
    a `plant.toml` carries - same modules, same clock, same words, same ERP
    mode, same storage - so a format proved against those two would be the
    demo plant's shape with a different name on it."""
    labs = PACK.parent
    third = fmt.read(PACK)
    others = [fmt.read(labs / name) for name in ("bottling", "machining")]

    assert all(o.table("plant")["timezone"] != third.table("plant")["timezone"] for o in others)
    assert all(o.table("plant").get("profile") != third.table("plant")["profile"] for o in others)
    assert all(not o.table("modules") for o in others) and third.table("modules")
    assert all(not o.table("words") for o in others) and len(third.table("words")) == 2
    assert all(not o.table("storage") for o in others) and third.table("storage")
    assert all(o.table("erp").get("mode") == "off" for o in others)
    assert third.table("erp")["mode"] == "file"
    assert all(not o.table("inbound") for o in others) and third.table("inbound")
    assert all(not o.accounts() for o in others) and len(third.accounts()) == 1


def test_switching_two_modules_off_is_still_a_plant_and_not_a_different_product():
    """Twenty-three modules, nine of them kernel. This plant serves
    twenty-one, and the nine it could never switch off are all there."""
    on = registry.resolve(fmt.module_spec(fmt.read(PACK)))
    assert len(on) == len(registry.REGISTRY) - 2
    assert on >= registry.KERNEL_NAMES
    assert {m.name for m in registry.disabled(fmt.module_spec(fmt.read(PACK)))} == {
        "serialization", "coa"}
