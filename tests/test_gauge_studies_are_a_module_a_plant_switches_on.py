"""Gauge studies are a module of their own, and a plant has to ask for them.

Scott's rule, 2026-11-23: every strategic module from now on can be switched
off for a plant, carries its own tables and tests, and its tools load only
when it is on. Gauge studies (gage R&R, #285) were inside `quality`; since
2026-11-24 they are `gauge_studies`, which needs `quality` and which `all`
leaves out.

The suite itself runs with them on (`conftest.py` sets
`MES_MODULES=all,gauge_studies`), so each test here says which plant it is.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fsmes import modules as registry
from fsmes.config import Settings, get_settings
from fsmes.db import Base
from fsmes.pack import format as pack_format
from test_module_wall import with_modules  # noqa: F401 - the fixture

REPO = Path(__file__).resolve().parents[1]
STUDIES = "/dashboard/gauges/studies"
ON = "all,gauge_studies"


def test_a_plant_that_names_no_module_does_not_get_gauge_studies(monkeypatch):
    """The bare default is `all`, and `all` leaves an opt-in module out."""
    monkeypatch.delenv("MES_MODULES", raising=False)
    get_settings.cache_clear()
    try:
        assert Settings().modules == "all"
        assert "gauge_studies" not in registry.resolve(Settings().modules)
    finally:
        get_settings.cache_clear()
    assert "gauge_studies" not in registry.resolve(None)
    assert "gauge_studies" not in registry.resolve("")
    assert "gauge_studies" not in registry.resolve("all")
    assert registry.OPT_IN_NAMES == ("gauge_studies",)
    # Every other module is on by default, exactly as before.
    assert registry.resolve("all") == set(registry.ALL_NAMES) - {"gauge_studies"}


def test_a_plant_switches_gauge_studies_on_by_writing_its_name():
    assert "gauge_studies" in registry.resolve(ON)
    assert "gauge_studies" in registry.resolve("quality,gauge_studies")
    assert "gauge_studies" not in registry.resolve("all,gauge_studies,-gauge_studies")


def test_gauge_studies_without_quality_is_refused_by_both_names():
    """They read quality's gauge register and specifications: served alone,
    every fetch on their screen would answer 404."""
    for spec in ("all,-quality,gauge_studies", "gauge_studies"):
        with pytest.raises(registry.UnknownModule) as caught:
            registry.resolve(spec)
        assert "gauge_studies" in str(caught.value)
        assert "quality" in str(caught.value)


def test_gauge_studies_own_their_router_screen_tools_and_two_tables():
    module = registry.BY_NAME["gauge_studies"]
    quality = registry.BY_NAME["quality"]
    assert module.opt_in and module.requires == ("quality",)
    assert [m.module for m in module.routers] == ["fsmes.api.routers.gage_studies"]
    assert [p.path for p in module.pages] == [STUDIES]
    assert module.tools == ("fsmes.mcp.gage_rr",)
    assert module.tables == ("gauge_studies", "gauge_study_readings")
    for table in module.tables:
        assert table in Base.metadata.tables
        assert table not in quality.tables
    assert "fsmes.mcp.gage_rr" not in quality.tools
    assert not [m for m in quality.routers if m.module.endswith("gage_studies")]
    assert STUDIES not in [p.path for p in quality.pages]


def test_with_gauge_studies_off_their_routes_and_screen_answer_404_and_the_nav_leaves_them_out(
        with_modules):  # noqa: F811
    client = with_modules("all")
    assert client.get("/quality/gage-studies").status_code == 404
    assert client.get("/quality/gage-studies/gauge-or-process",
                      params={"material": "FG-COLA", "characteristic": "fill"}).status_code == 404
    assert client.get(STUDIES).status_code == 404
    assert not [p for p in client.get("/openapi.json").json()["paths"]
                if p.startswith("/quality/gage-studies")]
    # Quality itself is untouched: its gauges, its screens.
    assert client.get("/quality/gauges").status_code == 200
    assert client.get("/dashboard/gauges").status_code == 200

    me = client.get("/auth/me").json()
    assert me["pages_off"] == [STUDIES]


def test_with_gauge_studies_on_their_routes_screen_and_nav_entry_are_there(
        with_modules):  # noqa: F811
    client = with_modules(ON)
    listed = client.get("/quality/gage-studies")
    assert listed.status_code == 200, listed.text
    assert "total" in listed.json()
    assert client.get(STUDIES).status_code == 200
    assert client.get("/auth/me").json()["pages_off"] == []


def test_the_nav_hides_the_chip_of_a_screen_this_plant_does_not_serve():
    """The one place the browser decides what to show. Read as text: the
    filter is a line in `renderNav`, and it reads the server's list."""
    common = (REPO / "src" / "fsmes" / "web" / "common.js").read_text(encoding="utf-8")
    nav = common[common.index("function renderNav"):common.index("function renderSubnav")]
    assert "who.pages_off" in nav
    assert "!off.has(" in nav


def test_gauge_study_tools_load_only_when_the_module_is_on():
    """In a subprocess, because the MCP server registers its tools at import."""
    script = ("import json, fsmes.mcp_server as s;"
              "print(json.dumps({'modules': list(s.TOOL_MODULES),"
              " 'gauge_or_process': hasattr(s, 'gauge_or_process'),"
              " 'plan_gage_study': hasattr(s, 'plan_gage_study')}))")

    def ask(spec: str) -> dict:
        done = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            encoding="utf-8", timeout=180, env={**os.environ, "MES_MODULES": spec})
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    off = ask("all")
    assert "gauge_studies" not in off["modules"]
    assert not off["gauge_or_process"] and not off["plan_gage_study"]

    on = ask(ON)
    assert "gauge_studies" in on["modules"]
    assert on["gauge_or_process"] and on["plan_gage_study"]
    assert set(on["modules"]) - set(off["modules"]) == {"gauge_studies"}


def test_the_chat_is_told_gauge_studies_are_switched_off_rather_than_left_to_guess(monkeypatch):
    from fsmes.services import agent

    monkeypatch.setenv("MES_MODULES", "all")
    get_settings.cache_clear()
    try:
        note = agent.switched_off_note()
    finally:
        get_settings.cache_clear()
    assert "Gauge studies" in note and "`gauge_studies`" in note
    assert "switched off on this plant" in note
    with_tools = sum(1 for m in registry.REGISTRY if m.tools)
    assert f"1 of the {with_tools} modules" in note

    monkeypatch.setenv("MES_MODULES", ON)
    get_settings.cache_clear()
    try:
        assert agent.switched_off_note() == ""
    finally:
        get_settings.cache_clear()


def test_the_cutlery_pack_switches_gauge_studies_on_and_bottling_does_not():
    """The lab plant and the film's plant are cutlery; bottling and machining
    are the other packs the wheel ships, and they stay without."""
    cutlery = pack_format.read(REPO / "labs" / "cutlery")
    assert "gauge_studies" in registry.resolve(pack_format.module_spec(cutlery))
    for name in ("bottling", "machining"):
        other = pack_format.read(REPO / "labs" / "multiplant" / name)
        assert "gauge_studies" not in registry.resolve(pack_format.module_spec(other)), name


def test_the_study_simulator_writes_nothing_on_a_plant_without_gauge_studies(
        with_modules):  # noqa: F811
    """It used to register and calibrate the gauge first, and only then meet
    the refusal - a half-made study is worse than none."""
    from fsmes.sim import gage_study

    client = with_modules("all")
    case = gage_study.load(REPO / "labs" / "cutlery" / "gage_studies.json")["worn-gauge"]
    before = client.get("/quality/gauges", params={"q": case.gauge}).json()["gauges"]

    with pytest.raises(gage_study.Refused) as caught:
        gage_study.run(client, case)
    assert "switched off" in str(caught.value)
    assert "gauge_studies = true" in str(caught.value)

    after = client.get("/quality/gauges", params={"q": case.gauge}).json()["gauges"]
    assert after == before
