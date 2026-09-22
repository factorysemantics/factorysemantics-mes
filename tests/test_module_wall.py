"""A module can be switched off, and this is what off looks like.

M8's *done when* asks for "two packs with different modules enabled running
from one codebase". Before the module registry that was not a configuration
change, it was a refactor: `api/app.py` mounted all twenty-three routers
unconditionally and `mcp_server.py` registered all ten tool files at import.

One prose-named test per claim, with `quality` as the module switched off
because it has all four of the things a module can have - routes, screens,
agent tools and tables of its own.

The claims, in order: nothing changes by default; the routes answer 404; the
screens are not served; the agent tools are gone; the rows are still there;
the write-route parity ratchet is still satisfied; and a name this version
does not have is refused rather than ignored.
"""

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import use_the_test_session
from fsmes import modules as registry
from fsmes.api.app import create_app
from fsmes.config import Settings, get_settings
from fsmes.db import Base

WITHOUT_QUALITY = "all,-quality"


@pytest.fixture()
def with_modules(monkeypatch, session):
    """An API client for a plant whose MES_MODULES says something particular.

    The app reads the setting once, when it is built, which is the whole point
    - a plant's modules are decided at start-up, like shadow mode, not per
    request. So the setting goes into the environment before `create_app`, and
    the settings cache is cleared on both sides so no other test inherits it.
    """
    made: list[TestClient] = []

    def _build(spec: str) -> TestClient:
        monkeypatch.setenv("MES_MODULES", spec)
        get_settings.cache_clear()
        app = create_app()
        use_the_test_session(app, session, monkeypatch)
        client = TestClient(app)
        client.__enter__()
        made.append(client)
        response = client.post("/auth/login", json={"code": "ADMIN", "password": "admin"})
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['token']}"
        return client

    yield _build
    for client in made:
        client.__exit__(None, None, None)
    get_settings.cache_clear()


# ------------------------------------------------------------------ default


def test_a_plant_that_has_never_heard_of_the_setting_serves_every_module():
    """The promise that makes this safe to land: nothing changes for anyone
    today. Everything else in this file describes a plant that opted in."""
    assert Settings().modules == "all"
    assert len(registry.enabled()) == len(registry.REGISTRY)
    assert registry.disabled() == ()


def test_the_registry_says_how_many_modules_there_are_and_how_many_can_be_switched():
    """Every list states its total. Twenty-three modules: nine kernel, which
    a plant may not switch off, and fourteen it may."""
    assert len(registry.REGISTRY) == 23
    assert len(registry.KERNEL_NAMES) == 9
    assert len(registry.OPTIONAL_NAMES) == 14
    assert len(registry.KERNEL_NAMES) + len(registry.OPTIONAL_NAMES) == len(registry.REGISTRY)

    mounts = sum(len(m.routers) for m in registry.REGISTRY)
    tools = sum(len(m.tools) for m in registry.REGISTRY)
    # Twenty-five router mounts across twenty-three modules: two modules mount
    # two each at one prefix, with the vocabulary first - `equipment` because
    # the machine router ends with `/{code}` and would swallow it, `quality`
    # for the same structural reason before it has such a route.
    assert mounts == 25, f"{mounts} routers in the registry; the app mounted 24 before it existed"
    assert tools == 10, f"{tools} tool files in the registry; the MCP server registered 10"


# ----------------------------------------------------------------- routes


def test_a_module_that_is_off_answers_404_rather_than_answering_differently(with_modules):
    """The difference that matters to a caller. A disabled module has no
    routes at all - not routes that refuse, which would still be a surface to
    keep working."""
    client = with_modules(WITHOUT_QUALITY)
    for path in ("/quality/specs", "/quality/checks", "/quality/nonconformances"):
        assert client.get(path).status_code == 404, path

    # And the kernel, plus every module still on, is untouched.
    assert client.get("/health").status_code == 200
    assert client.get("/workorders").status_code == 200
    assert client.get("/maintenance/orders").status_code != 404


def test_a_module_that_is_off_is_absent_from_the_api_document(with_modules):
    """An integrator reads /openapi.json to find out what a plant offers. A
    plant that does not serve quality should not describe quality."""
    on = create_app().openapi()["paths"]
    off = with_modules(WITHOUT_QUALITY).get("/openapi.json").json()["paths"]

    assert any(p.startswith("/quality") for p in on)
    assert not [p for p in off if p.startswith("/quality")]
    # Only quality went. Everything else the full app documents is still here.
    assert set(off) == {p for p in on if not p.startswith("/quality")}


def test_a_module_that_is_off_takes_its_screens_with_it(with_modules):
    """A page whose every fetch answers 404 is worse than no page: it looks
    broken rather than absent. The registry carries the screens too."""
    client = with_modules(WITHOUT_QUALITY)
    for page in ("/dashboard/quality", "/dashboard/spc", "/dashboard/gauges"):
        assert client.get(page).status_code == 404, page
    assert client.get("/dashboard").status_code == 200
    assert client.get("/dashboard/orders").status_code == 200


# ------------------------------------------------------------- agent tools


def test_a_module_that_is_off_registers_no_agent_tools():
    """`fsmes/mcp/__init__.py` has said since 2026-09-02 that "a module a
    plant pack disables takes its tools with it by not being registered". It
    describes a loop that now exists.

    In a subprocess because the MCP server registers its tools at import, so
    the honest way to ask what a differently-configured process would register
    is to start one.
    """
    script = ("import json, fsmes.mcp_server as s;"
              "print(json.dumps({'modules': list(s.TOOL_MODULES),"
              " 'has_specs': hasattr(s, 'specs')}))")

    def ask(spec: str) -> dict:
        done = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=180,
            env={**os.environ, "MES_MODULES": spec})
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    everything = ask("all")
    assert "quality" in everything["modules"]
    assert len(everything["modules"]) == 10

    without = ask(WITHOUT_QUALITY)
    assert "quality" not in without["modules"]
    assert len(without["modules"]) == 9, without["modules"]
    # Every other module's tools are still registered: one module left, not ten.
    assert set(everything["modules"]) - set(without["modules"]) == {"quality"}


def test_a_write_route_that_is_not_mounted_needs_no_tool(with_modules):
    """The parity ratchet (`test_mcp_parity.py`) asks every write route for a
    tool or a written reason. A plant with a module off has fewer routes and
    the same tool files, so the ratchet is satisfied by construction - but
    "by construction" is the kind of claim that stops being true quietly."""
    from test_mcp_parity import EXCLUDED, _pattern, tool_source

    source = tool_source()
    paths = with_modules(WITHOUT_QUALITY).get("/openapi.json").json()["paths"]
    writes = {f"{method.upper()} {path}" for path, ops in paths.items() for method in ops
              if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}}

    unexplained = sorted(w for w in writes
                         if not _pattern(w.split(" ", 1)[1]).search(source)
                         and w not in EXCLUDED)
    assert not unexplained, unexplained
    assert not [w for w in writes if w.startswith("POST /quality")], (
        "quality is off; its write routes should not be in this plant's document")


# ------------------------------------------------------- off is not deleted


def test_a_module_that_is_off_keeps_its_rows(with_modules, session):
    """Off means not served. It does not mean not stored.

    The schema is one migration chain for every plant (decisions 0021 and
    0022), so a disabled module's tables are still created and its rows are
    still there - and come back untouched when the module is switched on
    again. Nothing in the registry drops a table, and this is the test that
    says so.
    """
    from fsmes.domain import Material, QualitySpec

    quality = registry.BY_NAME["quality"]
    assert quality.tables, "the quality module should name the tables its rows live in"
    for table in quality.tables:
        assert table in Base.metadata.tables, f"{table} is not in the schema"

    # A row written while the module was on.
    material = session.query(Material).first()
    assert material is not None, "the seeded demo plant should have materials"
    session.add(QualitySpec(material_id=material.id, characteristic="a-kept-measurement",
                            unit="g", min_value=494.0, max_value=506.0))
    session.flush()

    # The plant now serves no quality routes at all...
    client = with_modules(WITHOUT_QUALITY)
    assert client.get("/quality/specs").status_code == 404

    # ...and the row is exactly where it was, in a table that still exists.
    kept = session.query(QualitySpec).filter_by(characteristic="a-kept-measurement").all()
    assert len(kept) == 1 and kept[0].max_value == 506.0


def test_every_table_the_registry_names_is_a_real_table():
    """The registry's table lists are how the docs' claim stays checkable, so
    a renamed table has to be renamed here too."""
    known = set(Base.metadata.tables)
    claimed: dict[str, str] = {}
    for module in registry.REGISTRY:
        for table in module.tables:
            assert table not in claimed, (
                f"{table} is claimed by both {claimed[table]} and {module.name}; a table "
                "belongs to one module, or the module that is off is ambiguous")
            claimed[table] = module.name

    missing = sorted(set(claimed) - known)
    assert not missing, f"the registry names tables that do not exist: {missing}"

    unclaimed = sorted(known - set(claimed))
    assert not unclaimed, (
        f"{len(known)} tables in the schema and these belong to no module: {unclaimed}. "
        "Every table has an owner, which is how the docs' claim - that a disabled "
        "module keeps its rows - stays checkable.")

    optional = sum(len(m.tables) for m in registry.REGISTRY if not m.kernel)
    assert optional == 21, (
        f"{optional} of {len(known)} tables belong to a module a plant may switch off")


# ---------------------------------------------------------------- refusals


def test_a_module_name_this_version_does_not_have_is_refused():
    """Ignored would be worse. A typo that silently changes nothing is how a
    plant comes to believe it disabled a module it is still serving."""
    with pytest.raises(registry.UnknownModule) as caught:
        registry.resolve("all,-qualtiy")
    assert "qualtiy" in str(caught.value)
    # The refusal says what the choices are, rather than only that it is wrong.
    assert "quality" in str(caught.value)

    # And the refusal reaches the settings, so a plant hears it at start-up
    # rather than the first time somebody looks for the screen.
    with pytest.raises(ValidationError):
        Settings(modules="all,-qualtiy")


def test_the_kernel_cannot_be_switched_off():
    """A plant without orders, execution, audit or auth is not an MES. Asking
    is refused out loud rather than honoured or quietly dropped."""
    for kernel_module in sorted(registry.KERNEL_NAMES):
        with pytest.raises(registry.UnknownModule) as caught:
            registry.resolve(f"all,-{kernel_module}")
        assert kernel_module in str(caught.value)
        assert "kernel" in str(caught.value)


def test_the_setting_reads_left_to_right_so_the_last_word_wins():
    """`all,-quality` and `quality,maintenance` are the two shapes a pack will
    compile to, and they have to mean what they look like."""
    assert "quality" not in registry.resolve("all,-quality")
    assert "quality" in registry.resolve("all,-quality,quality")
    assert "quality" in registry.resolve("all")

    only_two = registry.resolve("quality,maintenance")
    assert {m for m in registry.OPTIONAL_NAMES if m in only_two} == {"quality", "maintenance"}
    # The kernel is there whether or not anybody asked for it.
    assert only_two >= registry.KERNEL_NAMES

    # An empty value is a plant that said nothing, which is every module on.
    assert registry.resolve("") == registry.resolve("all")
    assert registry.resolve(None) == registry.resolve("all")


def test_every_router_page_and_tool_the_registry_names_can_be_imported():
    """The registry is read lazily, so a typo in a dotted name would not
    surface until the day a plant enabled that module."""
    from importlib import import_module

    from fsmes.api.app import WEB_DIR

    for module in registry.REGISTRY:
        for mount in module.routers:
            assert hasattr(import_module(mount.module), "router"), mount.module
        for dotted in module.tools:
            assert hasattr(import_module(dotted), "register"), dotted
        for page in module.pages:
            assert (WEB_DIR / page.file).is_file(), page.file
            assert page.about.strip(), f"{page.path} has no sentence saying what it is for"
