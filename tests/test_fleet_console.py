"""The console observes, and closes M8's *done when*.

Two things are proved here.

**The milestone.** Three plants in one fleet, two of them answering from two
packs with **different modules enabled**, the third stopped - and the page
saying *"3 plants, 2 answered, 0 answered but empty, 1 unknown"*, with the
stopped one shown as unknown rather than as down, and with the two answering ones showing the
modules each actually serves. The two answering plants are the real
application, built from the real packs, answering the real `/health` and
`/pack`; only the transport is replaced, because starting a plant on a
development machine is its operator's business and not a test's.

**That it cannot act.** By reading the source: the console's module imports
no verb, declares no route that is not a GET, and its script sends no
request but one GET to its own server. That is the property decision 0023
says survives intact for the page, and these are the tests that hold it.

Nothing here starts a plant, a broker, an OPC server or a port.
"""

import ast
import contextlib
import os
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsmes import plant as plants
from fsmes.fleet import commands, console, observe
from fsmes.fleet import owned as ownership

ROOT = Path(__file__).resolve().parents[1]
LABS = ROOT / "labs" / "multiplant"
SOURCE = ROOT / "src" / "fsmes" / "fleet" / "console.py"
WEB = ROOT / "src" / "fsmes" / "web"


# ------------------------------------------------------- plants that answer


def as_that_plant(pack_dir: Path, session, data_dir: Path) -> dict:
    """What the real application answers when it is that plant.

    Settings are process-wide and cached, so two plants cannot be alive in
    one process at once. Each question therefore builds the app from the
    pack it is about, asks it, and puts the caches back - which is slower
    than one client and is the only way two plants can both be answered
    truthfully from one interpreter.
    """
    from fsmes.api.app import create_app
    from fsmes.api.deps import get_db, get_read_db
    from fsmes.config import get_settings
    from fsmes.pack import format as fmt

    before = dict(os.environ)
    values = dict(fmt.settings(fmt.read(pack_dir)))
    values.pop("MES_DATABASE_URL", None)
    os.environ.update(values)
    os.environ["FSMES_DATA_DIR"] = str(data_dir)
    get_settings.cache_clear()
    try:
        app = create_app()

        # Only /health and /pack, neither of which signs anybody in, so the
        # request session is the only seam this one needs.
        def _same_session():
            yield session
            session.flush()

        app.dependency_overrides[get_db] = _same_session
        app.dependency_overrides[get_read_db] = _same_session
        with TestClient(app) as client:
            return {"/health": client.get("/health").json(),
                    "/pack": client.get("/pack").json()}
    finally:
        os.environ.clear()
        os.environ.update(before)
        get_settings.cache_clear()


@pytest.fixture()
def three_plants(tmp_path, monkeypatch, session):
    """A fleet of three: two that answer, one that is not running.

    `bottling` serves every module. `finewire` - the pack written to
    disagree with the format - serves every module except `serialization`
    and `coa`. `machining` is created and never started, which is the
    commonest state of a lab fleet and the one a console must not render as
    healthy.

    The one change made to a pack here: `finewire`'s `[storage]` table names
    a PostgreSQL this machine does not have, and is dropped so the suite's
    own database can stand in. What is being proved is which modules a plant
    *serves*, not where it stores rows.
    """
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    before = dict(os.environ)
    monkeypatch.delenv(plants.REGISTRY_ENV, raising=False)
    plants.environment.cache_clear()
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'fleet.db').as_posix()}")
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    # Two settings that are about this database being a **file**, which the
    # rest of the suite's is not. `MES_TAG_RETENTION_DAYS=0` switches off the
    # hourly pruner the API's lifespan starts: it opens its own session in a
    # worker thread through `asyncio.to_thread`, `task.cancel()` does not stop
    # a thread already inside a SQLite `BEGIN IMMEDIATE`, and against a file
    # it contends with whatever the test is actually doing. Nothing here tests
    # retention. And the engine is disposed rather than dropped, because on
    # Windows an open handle is a file `tmp_path` cannot delete.
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    monkeypatch.setenv("FSMES_FINEWIRE_OPERATOR_PASSWORD", "a-password-for-this-test")
    monkeypatch.setattr(observe, "health", lambda *a, **k: observe.Answer(
        "http://fake", False, why="did not answer (nothing listening)"))

    packs = {}
    for name in ("bottling", "finewire", "machining"):
        directory = tmp_path / name
        shutil.copytree(LABS / name, directory)
        packs[name] = directory
    text = (packs["finewire"] / "plant.toml").read_text(encoding="utf-8")
    text = re.sub(r"\n\[storage\][^\[]*", "\n", text, count=1)
    (packs["finewire"] / "plant.toml").write_text(text, encoding="utf-8")

    for name in ("bottling", "finewire", "machining"):
        commands.create(packs[name], root=tmp_path, echo=lambda _: None)

    yield tmp_path, packs, commands.data_dir(tmp_path)

    os.environ.clear()
    os.environ.update(before)
    plants.environment.cache_clear()
    if get_engine.cache_info().currsize:
        with contextlib.suppress(Exception):
            get_engine().dispose()
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


@pytest.fixture()
def watching(three_plants, session):
    """A console over that fleet, with two of the three plants answering."""
    root, packs, data_dir = three_plants
    record = ownership.load(data_dir)
    answering = {}
    for name in ("bottling", "finewire"):
        entry = record.entry(name)
        answering[entry.base] = as_that_plant(packs[name], session, data_dir)

    def health(base, **_kwargs) -> observe.Answer:
        if base not in answering:
            return observe.Answer(base, False, why="did not answer (nothing listening)")
        return observe.Answer(base, True, body=answering[base]["/health"], status=200)

    def pack(base, **_kwargs) -> observe.Answer:
        if base not in answering:
            return observe.Answer(base, False, why="did not answer (nothing listening)")
        return observe.Answer(base, True, body=answering[base]["/pack"], status=200)

    def book(base, **_kwargs):
        # bottling has a book; finewire answers and has nothing left to run.
        if base not in answering:
            return None
        if "8010" in base:
            return {"planned": 9, "released": 0, "running": 1, "open": 10}
        return {"planned": 0, "released": 0, "running": 0, "open": 0}

    return console.Console(root, health=health, pack=pack, book=book)


# ---------------------------------------------------------- the *done when*


def test_three_plants_with_one_stopped_read_as_two_answered_and_one_unknown(watching):
    fleet = watching.look()
    assert fleet["says"] == "3 plants, 2 answered, 0 answered but empty, 1 unknown"
    assert fleet["totals"] == {"plants": 3, "answered": 2, "empty": 0, "unknown": 1,
                               "owned": 2, "claimed_but_silent": 1, "observed": 0}

    silent = next(p for p in fleet["plants"] if p["name"] == "machining")
    assert silent["state"] == "unknown"
    assert silent["answered"] is False
    assert "down" not in str(fleet).lower().replace("shutdown", "")


def test_the_console_shows_two_packs_running_different_modules_from_one_codebase(watching):
    """M8's *done when*, on the page: the milestone asks for two packs with
    different modules enabled, and for the console to show both."""
    fleet = {p["name"]: p for p in watching.look()["plants"]}

    assert fleet["bottling"]["modules_off"] == []
    assert set(fleet["finewire"]["modules_off"]) == {"serialization", "coa"}
    assert fleet["bottling"]["modules_total"] == fleet["finewire"]["modules_total"]
    assert len(fleet["finewire"]["modules_on"]) == len(fleet["bottling"]["modules_on"]) - 2


def test_each_answering_plant_says_who_it_is_rather_than_where_it_was_dialled(watching):
    fleet = {p["name"]: p for p in watching.look()["plants"]}
    assert fleet["finewire"]["profile"] == "plant"
    assert fleet["finewire"]["timezone"] == "Europe/Berlin"
    assert fleet["bottling"]["profile"] == "laptop"


def test_a_plant_that_did_not_answer_is_never_reported_as_owned(watching):
    """Nothing corroborates an instance id a plant is not there to give, and
    ownership is never carried forward on faith."""
    fleet = {p["name"]: p for p in watching.look()["plants"]}
    assert fleet["machining"]["owned"] == "unknown"
    assert "did not answer" in fleet["machining"]["ownership"]
    assert fleet["bottling"]["owned"] == "yes"


def test_a_plant_this_installation_never_created_is_shown_as_observed(three_plants, session):
    root, _packs, data_dir = three_plants
    record = ownership.load(data_dir)
    ownership.save(ownership.Record(
        where=record.where, owned=record.owned,
        observed=(ownership.Observed(name="hall2", url="http://10.20.30.41:8050",
                                     about="somebody else's plant"),)))
    watcher = console.Console(root, health=lambda base, **k: observe.Answer(
        base, False, why="did not answer"), pack=lambda base, **k: observe.Answer(base, False),
        book=lambda base, **k: None)
    fleet = {p["name"]: p for p in watcher.look()["plants"]}
    assert fleet["hall2"]["owned"] == "no"
    assert "did not create it" in fleet["hall2"]["ownership"]
    assert watcher.look()["totals"]["observed"] == 1


def test_the_page_and_its_json_are_the_two_routes_the_console_has(watching, tmp_path):
    app = console.create_app(tmp_path, console=watching)
    methods = sorted({method for path, ops in app.openapi()["paths"].items()
                      for method in ops})
    assert methods == ["get"], f"the console declares a route that is not a read: {methods}"
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        body = client.get("/fleet.json").json()
        assert body["says"] == "3 plants, 2 answered, 0 answered but empty, 1 unknown"
        assert client.post("/fleet.json").status_code == 405


def test_the_plant_says_unknown_rather_than_no_drift_when_it_was_never_applied(session, tmp_path):
    """`drifted` is tri-state: None means never applied, which a console that
    rendered it as green would be turning into a lie."""
    from fsmes.config import get_settings

    before = dict(os.environ)
    os.environ["FSMES_DATA_DIR"] = str(tmp_path / "nothing-here")
    os.environ["MES_PLANT_NAME"] = "unapplied"
    get_settings.cache_clear()
    try:
        from fsmes.pack import apply as applier

        said = applier.what_this_plant_runs()
    finally:
        os.environ.clear()
        os.environ.update(before)
        get_settings.cache_clear()
    assert said["drifted"] is None
    assert said["pack"] is None
    assert "no pack has been applied" in said["unknown"]["pack"]


# --------------------------------------------- it cannot act, by inspection


def test_the_console_never_imports_a_verb():
    """The strongest form of "no path from the page to the fleet tool": the
    module that serves the page cannot reach the module that acts."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    offenders = sorted(name for name in imported if "commands" in name)
    assert not offenders, (
        f"the console imports {offenders}. The verbs live in fsmes fleet, at a terminal; "
        "a page that can reach them is a page whose blast radius is a network.")


def test_the_console_declares_no_route_that_writes():
    source = SOURCE.read_text(encoding="utf-8")
    for verb in ("post", "put", "patch", "delete"):
        assert f"app.{verb}(" not in source, f"the console declares an app.{verb} route"


def test_the_consoles_script_sends_one_kind_of_request_and_it_is_a_get():
    script = (WEB / "fleet.js").read_text(encoding="utf-8")
    calls = re.findall(r"fetch\(([^)]*)\)", script, re.S)
    assert calls, "the script fetches nothing; this test has stopped working"
    for call in calls:
        assert '"/fleet.json"' in call, f"the page calls something other than its own server: {call}"
        assert "GET" in call, f"a fetch in the page does not say it is a GET: {call}"
    for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
        assert verb not in script


def test_the_page_offers_no_control():
    html = (WEB / "fleet.html").read_text(encoding="utf-8")
    for control in ("<form", "<button", "<input"):
        assert control not in html, (
            f"the console page has a {control}. The first version of the console has no "
            "write path at all; the verbs it would need are the command's.")


# ------------------------------------------------------- answered, but empty


def test_the_console_shows_a_plant_with_no_line_as_empty_rather_than_as_answered(
        three_plants, session):
    """The third state. A plant that is up, at head, and has nothing on it
    reads as *answered, but empty* - which is what a person who has just
    built a fleet needs to see, and what the page said nothing about until
    2026-09-14."""
    root, _packs, data_dir = three_plants
    at_head_and_empty = {
        "schema": {"revision": "abc123", "head": "abc123", "at_head": True,
                   "answered": True},
        "line": {"equipment": 0, "answered": True},
        "modules": {"on": [], "off": [], "total": 0},
    }
    watching = console.Console(
        root,
        health=lambda where, **k: observe.Answer(
            f"{where}/health", True, status=200,
            body={"plant": "bottling", "instance_id": ownership.load(data_dir)
                  .entry("bottling").instance_id}),
        pack=lambda where, **k: observe.Answer(f"{where}/pack", True, status=200,
                                               body=at_head_and_empty),
        book=lambda where, **k: None)
    fleet = watching.look()
    rows = {row["name"]: row for row in fleet["plants"]}
    assert rows["bottling"]["state"] == "empty"
    assert rows["bottling"]["line_equipment"] == 0
    assert "no line" in rows["bottling"]["empty_because"]
    assert "answered but empty" in fleet["says"]


def test_a_plant_that_could_not_count_its_line_is_not_shown_as_empty(three_plants, session):
    """Unknown is not zero. A plant whose database did not answer says so, and
    an empty pill would be inventing a line that nothing looked at."""
    root, _packs, data_dir = three_plants
    could_not_look = {
        "schema": {"revision": "abc123", "head": "abc123", "at_head": True,
                   "answered": True},
        "line": {"equipment": None, "answered": False},
        "unknown": {"line": "this plant's equipment could not be counted"},
    }
    watching = console.Console(
        root,
        health=lambda where, **k: observe.Answer(
            f"{where}/health", True, status=200,
            body={"plant": "bottling", "instance_id": ownership.load(data_dir)
                  .entry("bottling").instance_id}),
        pack=lambda where, **k: observe.Answer(f"{where}/pack", True, status=200,
                                               body=could_not_look),
        book=lambda where, **k: None)
    rows = {row["name"]: row for row in watching.look()["plants"]}
    assert rows["bottling"]["state"] == "answered"
    assert rows["bottling"]["line_equipment"] is None


# ------------------------------------------------------------------ the port


def test_the_console_does_not_sit_in_the_range_a_scored_run_takes_its_port_from():
    """8100 was both the console's default and the first port
    `sim.runner.scored_run` hands an ephemeral plant, so a `fsmes score` in
    the same minute took the console's port and served a plant's sign-in page
    on it. Two named constants and this, rather than two numbers that happened
    to differ."""
    from fsmes.sim import runner

    low, high = runner.API_RANGE
    assert not low <= console.PORT <= high, (
        f"the console's default port {console.PORT} is inside the simulator's "
        f"range {low}-{high}; an ephemeral run will take it from under a person")


def test_the_console_has_one_default_port_and_the_cli_uses_it():
    """One spelling. Two is how it drifted into the range in the first place."""
    from fsmes import cli

    assert cli.CONSOLE_PORT == console.PORT


# ------------------------------------------------------------------- the book


def test_the_console_says_how_many_orders_a_plant_has_left_to_run(watching):
    """The column that would have caught 2026-09-18. A plant running one order
    seventy times over had every other light on this page green."""
    rows = {row["name"]: row for row in watching.look()["plants"]}
    assert rows["bottling"]["book"] == {"planned": 9, "released": 0, "running": 1, "open": 10}
    assert rows["finewire"]["book"]["open"] == 0, "a plant with nothing left to run says so"
    assert rows["machining"]["book"] is None, (
        "a plant nobody could ask is not a plant with an empty book")


def test_the_order_book_is_read_off_the_metrics_a_plant_already_answers():
    """Nothing here signs in. The counts come from the Prometheus text, which
    a plant has answered without a credential since it had metrics at all."""
    text = (
        'mes_plant_info{plant="bottling"} 1\n'
        'mes_work_orders{plant="bottling",status="planned"} 8\n'
        'mes_work_orders{plant="bottling",status="released"} 1\n'
        'mes_work_orders{plant="bottling",status="running"} 1\n'
        'mes_work_orders{plant="bottling",status="completed"} 3\n'
        'mes_work_orders{plant="bottling",status="cancelled"} 0\n'
    )
    counts = _book_from(text)
    assert counts == {"planned": 8, "released": 1, "running": 1, "open": 10}


def test_a_plant_whose_metrics_carry_no_orders_did_not_say_rather_than_said_none():
    assert _book_from('mes_plant_info{plant="bottling"} 1\n') is None


def _book_from(text: str):
    """`observe.book` against a plant that answered this, and nothing else."""

    class _Answer:
        status = 200

        def read(self):
            return text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    import urllib.request

    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _Answer()
    try:
        return observe.book("http://plant:8010")
    finally:
        urllib.request.urlopen = real
