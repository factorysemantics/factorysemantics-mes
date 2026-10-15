"""Two nodes of one plant on one box, named by the pack.

Decision 0042 made a plant a database and a node one `fsmes plant <name> run
--node <id>` process tree, and part 1 proved two of them survive one dying.
Nothing then could *start* the second one except a person typing the command
with the right port: the fleet knew one address per plant, the console drew one
row per plant, and `fsmes fleet stop` stopped whichever node had written the
pid file last.

So a pack may now name the nodes that run on this box:

    [[serve.nodes]]
    id = "a"
    api_port = 8010

    [[serve.nodes]]
    id = "b"
    api_port = 8011

and `fsmes fleet start/stop/status` act on every one of them. A pack that
names none is the ordinary plant on one address, which is almost every plant,
and these tests hold that it behaves exactly as it did before this file
existed.

Nothing here starts a process, binds a port or asks the network anything.
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import ClassVar

import pytest

from fsmes import plant as plants
from fsmes.fleet import commands, console, observe
from fsmes.fleet import owned as ownership
from fsmes.pack import check as checker
from fsmes.pack import fleet as packs
from fsmes.pack import format as fmt

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "multiplant"

TWO_NODES = """
[redundancy]
enabled = true
lease_seconds = 6.0
"""


def two_node_pack(tmp_path: Path, *, nodes=(("a", 8010), ("b", 8011))) -> Path:
    """The lab's bottling pack with its nodes named, and nothing else changed.

    PostgreSQL and a signing key go with `[redundancy]` - the plant itself
    refuses to start otherwise - so they are written here too, as the steward
    writes them on this box.
    """
    directory = tmp_path / "bottling"
    shutil.copytree(LAB / "bottling", directory)
    manifest = directory / fmt.PLANT_FILE
    text = manifest.read_text(encoding="utf-8")
    written = [f'\n[[serve.nodes]]\nid = "{node}"\napi_port = {port}\n'
               for node, port in nodes]
    text = text.replace("[files]", TWO_NODES + "".join(written) + "\n[files]", 1)
    text = text.replace("[storage]",
                        '[storage]\ndatabase_url = '
                        '"postgresql+psycopg://mes@127.0.0.1:5432/mes_bottling"\n', 1)
    if "secret_key_env" not in text:
        text = text.replace("[serve]", '[serve]\nsecret_key_env = "MES_BOTTLING_SECRET"\n', 1)
    manifest.write_text(text, encoding="utf-8")
    return directory


# ------------------------------------------------------- what the pack says


def test_a_pack_that_names_two_nodes_compiles_to_two_nodes(tmp_path):
    directory = two_node_pack(tmp_path)
    cfg = packs.compile_pack(fmt.read(directory))

    assert cfg["nodes"] == [{"id": "a", "api_port": 8010}, {"id": "b", "api_port": 8011}]
    assert plants.node_list(cfg) == cfg["nodes"], "read back in the order the pack writes them"
    assert cfg["api_port"] == 8010, (
        "the plant's own address is unchanged; naming nodes adds a second one")


def test_a_pack_that_names_no_nodes_has_none_and_is_the_plant_it_was(tmp_path):
    """The check that matters most: every pack that shipped keeps working."""
    cfg = packs.compile_pack(fmt.read(LAB / "machining"))

    assert cfg["nodes"] == []
    assert plants.node_list(cfg) == []
    assert plants.keyed("machining") == "machining", (
        "a plant with one node keeps the file names it has always had")
    assert plants.keyed("machining", "a") == "machining-a"


def test_the_lab_packs_this_repository_ships_still_check_out():
    """`fsmes pack check` on every pack in the lab, because the schema grew."""
    looked = sorted(p for p in LAB.iterdir() if (p / fmt.PLANT_FILE).is_file())
    assert len(looked) >= 2, f"the lab has {len(looked)} packs; this test wants them all"
    for directory in looked:
        report = checker.check(directory)
        assert report.ok, f"{directory.name}: {[str(p) for p in report.problems]}"


def test_a_two_node_pack_checks_out(tmp_path):
    report = checker.check(two_node_pack(tmp_path))
    assert report.ok, [str(p) for p in report.problems]


@pytest.mark.parametrize(("nodes", "expected"), [
    ((("a", 8010),), "names 1 node"),
    ((("a", 8010), ("a", 8011)), "'a' on more than one node"),
    ((("a", 8010), ("b", 8010)), "8010 on more than one node"),
    ((("a", 8011), ("b", 8112)), "no node serves there"),
])
def test_the_checker_refuses_a_node_list_that_would_half_start_a_plant(
        tmp_path, nodes, expected):
    report = checker.check(two_node_pack(tmp_path, nodes=nodes))
    assert not report.ok
    assert any(expected in str(p) for p in report.problems), \
        [str(p) for p in report.problems]


def test_a_node_without_a_name_or_a_port_is_refused_by_name(tmp_path):
    directory = two_node_pack(tmp_path)
    manifest = directory / fmt.PLANT_FILE
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        'id = "b"\napi_port = 8011', 'api_port = 8011\nhostname = "elsewhere"'),
        encoding="utf-8")

    said = [str(p) for p in checker.check(directory).problems]
    assert any("#2 id" in p and "missing" in p for p in said), said
    assert any("hostname" in p and "not a key a node has" in p for p in said), said


def test_naming_nodes_without_turning_redundancy_on_is_refused(tmp_path):
    """Two nodes without a lease both read the machines and book every count
    twice. That is the one failure this list could cause that a person would
    not see on a screen."""
    directory = two_node_pack(tmp_path)
    manifest = directory / fmt.PLANT_FILE
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        "enabled = true", "enabled = false"), encoding="utf-8")

    said = [str(p) for p in checker.check(directory).problems]
    assert any("[redundancy] enabled" in p and "book every count twice" in p
               for p in said), said


def test_two_nodes_with_no_signing_key_named_is_refused(tmp_path):
    directory = two_node_pack(tmp_path)
    manifest = directory / fmt.PLANT_FILE
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        'secret_key_env = "MES_BOTTLING_SECRET"', ""), encoding="utf-8")

    said = [str(p) for p in checker.check(directory).problems]
    assert any("secret_key_env" in p and "signed out" in p for p in said), said


def test_a_node_list_compiles_to_no_setting_of_its_own(tmp_path):
    """A node's name and port reach one child process each, as arguments.
    Nothing about the node list belongs in the plant's own environment, where
    both nodes would read the same value."""
    settings = fmt.settings(fmt.read(two_node_pack(tmp_path)))
    assert not any("NODE" in key for key in settings), sorted(settings)
    assert settings["MES_API_PORT"] == "8010"
    assert settings["MES_REDUNDANCY"] == "true"


# --------------------------------------------------- starting and stopping


class Fake:
    """A `Popen` that starts nothing and records the port each child was given."""

    started: ClassVar[list[tuple[int, str]]] = []

    def __init__(self, args, **kwargs) -> None:
        env = kwargs.get("env") or {}
        Fake.started.append((7000 + len(Fake.started), env.get("MES_NODE_ID", "")))
        self.pid = Fake.started[-1][0]
        self.stdin = type("P", (), {"close": lambda self: None})()


@pytest.fixture
def a_plant_that_starts_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    Fake.started = []
    monkeypatch.setattr(plants.subprocess, "Popen", Fake)
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "fsmes")
    monkeypatch.setattr(plants, "running_pids", lambda root, name: [])
    monkeypatch.setattr(plants.time, "sleep", lambda seconds: None)
    (tmp_path / "data").mkdir()
    return tmp_path


def _two_nodes(simulate: bool = False) -> dict:
    return {"label": "Bottling (two nodes)", "simulate": simulate, "api_port": 8010,
            "env": {"MES_REDUNDANCY": "true"},
            "nodes": [{"id": "a", "api_port": 8010}, {"id": "b", "api_port": 8011}]}


def test_starting_a_two_node_plant_starts_both_nodes_with_a_pid_file_each(
        a_plant_that_starts_nothing):
    root = a_plant_that_starts_nothing
    said: list[str] = []
    plants.start("bottling", _two_nodes(), root, echo=said.append)

    assert [line.split()[0:3] for line in said] == [["bottling", "node", "a"],
                                                    ["bottling", "node", "b"]]
    assert "8010/dashboard" in said[0] and "8011/dashboard" in said[1], (
        "each node is announced on its own address, which is where a person goes")
    assert {node for _pid, node in Fake.started} == {"a", "b"}, (
        "every child knows which node it belongs to")

    for node in ("a", "b"):
        pids = plants.pid_file(root, f"bottling-{node}")
        assert pids.is_file(), f"node {node} has a pid file of its own"
        recorded = json.loads(
            plants.procs_file(root, f"bottling-{node}").read_text(encoding="utf-8"))
        assert [row["role"] for row in recorded] == ["the dashboard and the API",
                                                     "the log sink"]
    assert not plants.pid_file(root, "bottling").exists(), (
        "nothing writes the plant's own pid file: stopping it would stop one node "
        "and leave the other running")


def test_each_node_writes_its_log_in_a_directory_of_its_own(a_plant_that_starts_nothing):
    root = a_plant_that_starts_nothing
    plants.start("bottling", _two_nodes(), root, echo=lambda line: None)

    assert (root / "logs" / "bottling" / "a" / "sink.log").is_file()
    assert (root / "logs" / "bottling" / "b" / "sink.log").is_file()
    assert not (root / "logs" / "bottling" / "sink.log").exists(), (
        "two nodes appending to one log is a log nobody can read a failover out of")


def test_stopping_a_two_node_plant_stops_both(a_plant_that_starts_nothing, monkeypatch):
    root = a_plant_that_starts_nothing
    plants.start("bottling", _two_nodes(), root, echo=lambda line: None)

    monkeypatch.setattr(plants, "running_pids", lambda r, name: [7001 if name.endswith("a") else 7003])
    monkeypatch.setattr(plants, "_alive", lambda pid: False)
    asked: list[int] = []
    monkeypatch.setattr(plants, "_ask_to_stop", asked.append)
    said: list[str] = []
    plants.stop("bottling", _two_nodes(), root, echo=said.append)

    assert asked == [7001, 7003], "both nodes were asked to stop, a then b"
    assert [line.strip() for line in said] == ["bottling node a stopped (1 process(es)).",
                                               "bottling node b stopped (1 process(es))."]
    assert not plants.pid_file(root, "bottling-a").exists()
    assert not plants.pid_file(root, "bottling-b").exists()


def test_starting_a_plant_whose_other_node_is_already_running_leaves_it_alone(
        a_plant_that_starts_nothing, monkeypatch):
    """The state a person is in after killing a node: one up, one to bring back."""
    root = a_plant_that_starts_nothing
    monkeypatch.setattr(plants, "running_pids",
                        lambda r, name: [4242] if name.endswith("-a") else [])
    said: list[str] = []
    plants.start("bottling", _two_nodes(), root, echo=said.append)

    assert "node a is already running" in said[0]
    assert "node b started" in said[1]
    assert {node for _pid, node in Fake.started} == {"b"}


def test_a_plant_with_one_node_starts_exactly_as_it_always_did(a_plant_that_starts_nothing):
    root = a_plant_that_starts_nothing
    said: list[str] = []
    plants.start("machining", {"simulate": False, "api_port": 8120}, root, echo=said.append)

    assert said[0].strip().startswith("machining started -> http://127.0.0.1:8120/dashboard")
    assert plants.pid_file(root, "machining").is_file()
    assert (root / "logs" / "machining" / "sink.log").is_file()


# ------------------------------------------------------- what status reports


def test_status_asks_every_node_and_states_how_many_answered(tmp_path, monkeypatch):
    """Two nodes of which one answered is a plant running on one node. The
    count is printed, because a list a person has to count themselves is
    where that gets missed."""
    answers = {
        8010: ("healthy", {"node": {"name": "a", "redundancy": True,
                                    "roles": ["reader", "floor", "housekeeping"]}}),
        8011: ("not answering yet", {}),
    }
    monkeypatch.setattr(plants, "health_report",
                        lambda cfg, timeout=4.0: answers[int(cfg["api_port"])])
    monkeypatch.setattr(plants, "supervisor", lambda root, name: "pidfile (2 proc)")

    said: list[str] = []
    out = plants.status("bottling", _two_nodes(), tmp_path, echo=said.append)

    assert "2 nodes, 1 answered" in " ".join(said)
    assert any("node a" in line and "reader, floor, housekeeping" in line for line in said)
    assert any("node b" in line and "not answering yet" in line for line in said)
    assert [one["id"] for one in out["nodes"]] == ["a", "b"]
    assert out["nodes"][1]["roles"] is None, (
        "a node that did not answer holds unknown, never nothing")
    assert plants.holding(None) == "unknown"
    assert plants.holding([]) == "nothing yet"


def test_status_of_a_one_node_plant_says_exactly_what_it_said_before(tmp_path, monkeypatch):
    monkeypatch.setattr(plants, "health_report", lambda cfg, timeout=4.0: ("healthy", {}))
    monkeypatch.setattr(plants, "supervisor", lambda root, name: "pidfile (2 proc)")
    said: list[str] = []
    out = plants.status("machining", {"label": "Machining", "api_port": 8120},
                        tmp_path, echo=said.append)

    assert len(said) == 2, said
    assert "nodes" not in out
    assert "node" in out and out["node"] == ""


# --------------------------------------------------------- what Scott sees


@pytest.fixture
def a_two_node_fleet(tmp_path, monkeypatch):
    """A fleet file naming one two-node plant and one ordinary one."""
    directory = two_node_pack(tmp_path)
    shutil.copytree(LAB / "machining", tmp_path / "machining")
    fleet = tmp_path / "fleet.toml"
    fleet.write_text('packs = ["bottling", "machining"]\n', encoding="utf-8")
    monkeypatch.setenv(plants.REGISTRY_ENV, str(fleet))
    plants.environment.cache_clear()
    yield tmp_path, directory
    plants.environment.cache_clear()


def test_the_console_shows_a_plant_once_with_its_nodes_under_it(a_two_node_fleet):
    root, _directory = a_two_node_fleet
    holders = {8010: ["reader", "floor", "housekeeping"], 8011: []}

    def health(base, **_kwargs):
        port = int(base.rsplit(":", 1)[1])
        if port not in holders:
            return observe.Answer(base, False, why="did not answer (nothing listening)")
        return observe.Answer(base, True, status=200, body={
            "status": "ok", "label": "Bottling (two nodes)",
            "node": {"name": "a" if port == 8010 else "b", "named": True,
                     "redundancy": True, "lease_seconds": 6.0,
                     "roles": holders[port]}})

    def nothing(base, **_kwargs):
        return observe.Answer(base, False, why="not asked")

    fleet = console.Console(root, health=health, pack=nothing,
                            book=lambda base, **_k: None).look()

    rows = {row["name"]: row for row in fleet["plants"]}
    assert fleet["totals"]["plants"] == 2, (
        "a plant on two nodes is one plant on this page, not two")
    assert fleet["says"] == "2 plants, 1 answered, 0 answered but empty, 1 unknown"

    bottling = rows["bottling"]
    assert [one["id"] for one in bottling["nodes"]] == ["a", "b"]
    assert bottling["nodes_answered"] == 2
    assert bottling["nodes"][0]["roles"] == ["reader", "floor", "housekeeping"]
    assert bottling["nodes"][0]["base"] == "http://127.0.0.1:8010"
    assert bottling["nodes"][1]["roles"] == [], "a standby holds nothing yet, and says so"
    assert bottling["nodes"][1]["base"] == "http://127.0.0.1:8011"

    assert rows["machining"]["nodes"] == [], (
        "the lab's one-node plant is drawn exactly as it was before nodes existed")
    assert rows["machining"]["nodes_answered"] == 0


def test_a_node_that_did_not_answer_is_unknown_and_not_a_node_holding_nothing(
        a_two_node_fleet):
    root, _directory = a_two_node_fleet

    def health(base, **_kwargs):
        if base.endswith("8010"):
            return observe.Answer(base, True, status=200, body={
                "status": "ok",
                "node": {"name": "a", "redundancy": True, "roles": ["reader"]}})
        return observe.Answer(base, False, why="did not answer (nothing listening)")

    fleet = console.Console(root, health=health,
                            pack=lambda base, **_k: observe.Answer(base, False, why="x"),
                            book=lambda base, **_k: None).look()
    bottling = next(row for row in fleet["plants"] if row["name"] == "bottling")

    assert bottling["nodes_answered"] == 1
    assert bottling["nodes"][1]["answered"] is False
    assert bottling["nodes"][1]["roles"] is None
    assert "did not answer" in bottling["nodes"][1]["why"]


def test_a_plant_whose_first_node_died_is_read_off_the_node_that_answered(
        a_two_node_fleet):
    """The one moment this page exists for, and the one it used to get wrong.

    Node a is the address the pack names and the node that was killed; node b
    is serving the floor. The row used to say `unknown` in every column - no
    label, no schema, no orders left - about a plant that was still counting,
    because it only ever asked the first address. It now reads the plant off
    the node that answered, and still names the silent node underneath.
    """
    root, _directory = a_two_node_fleet

    def health(base, **_kwargs):
        # Only node b of bottling is up: node a was killed, and the lab's
        # other plant is not running either.
        if not base.endswith("8011"):
            return observe.Answer(base, False, why="did not answer ([Errno 111] refused)")
        return observe.Answer(base, True, status=200, body={
            "status": "ok", "plant": "bottling", "label": "Bottling (two nodes)",
            "node": {"name": "b", "named": True, "redundancy": True,
                     "lease_seconds": 6.0,
                     "roles": ["floor", "housekeeping", "reader"]}})

    def pack(base, **_kwargs):
        assert base.endswith("8011"), f"a silent address was asked for its pack: {base}"
        return observe.Answer(base, True, status=200, body={
            "pack": "bottling", "schema": {"revision": "abc", "at_head": True},
            "line": {"equipment": 6, "answered": True}, "modules": {"on": ["oee"], "total": 1}})

    fleet = console.Console(root, health=health, pack=pack,
                            book=lambda base, **_k: {"open": 10}).look()
    bottling = next(row for row in fleet["plants"] if row["name"] == "bottling")

    assert bottling["state"] == "answered", (
        "a plant whose other node is serving the floor is not a plant nobody heard from")
    assert bottling["base"] == "http://127.0.0.1:8011", "the address that answered"
    assert bottling["schema_revision"] == "abc", "the columns are filled from that node"
    assert bottling["line_equipment"] == 6, "and so is the line - 6 machines, not unknown"
    assert fleet["says"] == "2 plants, 1 answered, 0 answered but empty, 1 unknown", (
        "machining is the unknown one; bottling is answered now")

    assert bottling["nodes_answered"] == 1
    assert bottling["nodes"][0]["id"] == "a"
    assert bottling["nodes"][0]["answered"] is False, "the dead node is still named as dead"
    assert bottling["nodes"][0]["roles"] is None
    assert bottling["nodes"][1]["roles"] == ["floor", "housekeeping", "reader"]


def test_a_single_node_plant_that_is_silent_is_still_unknown(a_two_node_fleet):
    """The other half of the same change: nothing to fall back to, nothing
    different. A plant on one address that does not answer reads exactly as
    it did before any of this existed."""
    root, _directory = a_two_node_fleet
    quiet = lambda base, **_k: observe.Answer(  # noqa: E731
        base, False, why="did not answer (nothing listening)")

    fleet = console.Console(root, health=quiet, pack=quiet,
                            book=lambda base, **_k: None).look()
    machining = next(row for row in fleet["plants"] if row["name"] == "machining")

    assert machining["state"] == "unknown"
    assert machining["base"].startswith("http://127.0.0.1:"), (
        "the one address it was dialled at, which is the one its pack names")
    assert machining["base"] == f"http://127.0.0.1:{packs.load(root)['machining']['api_port']}"
    assert machining["nodes"] == []


def test_fleet_json_carries_every_node_so_a_phone_can_read_it(a_two_node_fleet):
    """The console's page is drawn from this document and nothing else."""
    root, _directory = a_two_node_fleet
    fleet = console.Console(
        root,
        health=lambda base, **_k: observe.Answer(base, False, why="nothing listening"),
        pack=lambda base, **_k: observe.Answer(base, False, why="nothing listening"),
        book=lambda base, **_k: None).look()
    document = json.loads(json.dumps(fleet))  # it must survive being JSON

    bottling = next(row for row in document["plants"] if row["name"] == "bottling")
    assert [one["id"] for one in bottling["nodes"]] == ["a", "b"]
    assert [one["api_port"] if "api_port" in one else one["base"]
            for one in bottling["nodes"]] == ["http://127.0.0.1:8010",
                                              "http://127.0.0.1:8011"]


def test_the_fleet_page_draws_a_nodes_row_only_for_a_plant_that_has_them():
    """Read out of the script, because the page is what a person looks at and
    a single-node plant must render exactly as it did."""
    script = (ROOT / "src" / "fsmes" / "web" / "fleet.js").read_text(encoding="utf-8")
    assert "if (plant.nodes && plant.nodes.length)" in script
    assert "nodes_answered" in script, "the count is drawn, not inferred by the reader"
    for role, words in (("reader", "reads the machines"),
                        ("floor", "runs the floor"),
                        ("housekeeping", "prunes the history")):
        assert f"{role}: \"{words}\"" in script, (
            "each role is glossed in words a person on a floor uses")
    assert 'ROLE_WORDS[role] || role' in script, (
        "a role this build has never heard of is printed as the plant said it")


def test_the_fleet_status_line_says_both_nodes(tmp_path, monkeypatch):
    """`fsmes fleet status <plant>` on a terminal, which is the steward's view."""
    row = commands.Plant(
        name="bottling", owned=True, reason="created here", base="http://127.0.0.1:8010",
        answered=True, said={"profile": "fleet", "timezone_says": "America/Chicago"},
        nodes=({"id": "a", "api_port": 8010, "base": "http://127.0.0.1:8010",
                "health": "healthy", "answered": True, "supervisor": "pidfile (2 proc)",
                "says": "a", "roles": ["reader", "floor", "housekeeping"]},
               {"id": "b", "api_port": 8011, "base": "http://127.0.0.1:8011",
                "health": "healthy", "answered": True, "supervisor": "pidfile (2 proc)",
                "says": "b", "roles": []}))
    lines = commands.render(row)

    assert any("nodes      2, 2 answered" in line for line in lines)
    assert any("a" in line and "reader, floor, housekeeping" in line for line in lines)
    assert any(line.strip().startswith("b") and "nothing yet" in line for line in lines)


def test_the_fleet_list_names_both_nodes_and_states_their_total(tmp_path, monkeypatch):
    """`fsmes fleet list`, where the steward counts what this box runs. The
    node list states its total like every other list in this product, and a
    node nobody heard from is `unknown` rather than a node holding nothing."""
    where = commands.data_dir(tmp_path)
    where.mkdir(parents=True, exist_ok=True)
    (where / "ownership.toml").write_text(
        '[[owned]]\nname = "bottling"\npack = "/packs/bottling"\n'
        'instance_id = "abc"\n'
        f'data_dir = "{where.as_posix()}"\napi_port = 8010\n', encoding="utf-8")
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: (
        {"id": "a", "api_port": 8010, "base": "http://127.0.0.1:8010",
         "health": "healthy", "answered": True, "supervisor": "", "says": "a",
         "roles": ["reader", "floor", "housekeeping"]},
        {"id": "b", "api_port": 8011, "base": "http://127.0.0.1:8011",
         "health": "did not answer", "answered": False, "supervisor": "",
         "says": "", "roles": None}))
    quiet = lambda url, **k: observe.Answer(url, False, why="did not answer")  # noqa: E731
    said: list[str] = []

    commands.listing(root=tmp_path, echo=said.append, ask=quiet, ask_pack=quiet)

    assert any("2 nodes, 1 answered" in line for line in said)
    assert any("node a" in line and "reader, floor, housekeeping" in line for line in said)
    assert any("node b" in line and "holding unknown" in line for line in said)


def _ownership_of(where: Path, *, port: int = 8010, instance: str = "abc") -> None:
    """An ownership record for a plant created here, on this host, by this
    user - the three conditions the gate asks about before it reads a word
    the plant said."""
    host, user = ownership.here()
    where.mkdir(parents=True, exist_ok=True)
    (where / "ownership.toml").write_text(
        '[[owned]]\nname = "bottling"\npack = "/packs/bottling"\n'
        f'instance_id = "{instance}"\n'
        f'data_dir = "{where.as_posix()}"\napi_port = {port}\n'
        f'control = "{ownership.LOCAL}"\n'
        f'created_on_host = "{host}"\ncreated_by_user = "{user}"\n', encoding="utf-8")


def _answering_at(at: str, *, instance: str = "abc"):
    """`/health` from one address only: the node that is still up."""
    def health(base, **_kwargs):
        if not base.startswith(at):
            return observe.Answer(base, False, why="did not answer ([Errno 111] refused)")
        return observe.Answer(base, True, status=200, body={
            "status": "ok", "plant": "bottling", "instance_id": instance,
            "label": "Bottling (two nodes)",
            "node": {"name": "b", "redundancy": True,
                     "roles": ["floor", "housekeeping", "reader"]}})
    return health


def _two_node_rows(answered_at: int = 8011) -> tuple[dict, ...]:
    return tuple({"id": one, "api_port": port,
                  "base": f"http://127.0.0.1:{port}",
                  "health": "healthy" if port == answered_at else "did not answer",
                  "answered": port == answered_at, "supervisor": "",
                  "says": one if port == answered_at else "",
                  "roles": ["floor", "housekeeping", "reader"]
                  if port == answered_at else None}
                 for one, port in (("a", 8010), ("b", 8011)))


def test_fleet_status_reads_the_plant_off_the_node_that_answered(tmp_path, monkeypatch):
    """`fsmes fleet status bottling` with node a dead. The terminal had the
    same blind spot the page had: it asked the one address in the ownership
    record, and said `unknown` about a plant that was booking on its other
    node."""
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    monkeypatch.setattr(commands.observe, "pack",
                        lambda base, **_k: observe.Answer(base, False, why="not asked"))
    said: list[str] = []

    row = commands.status("bottling", root=tmp_path, echo=said.append,
                          ask=_answering_at("http://127.0.0.1:8011"),
                          ask_book=lambda base, **_k: None)

    assert row.answered is True
    assert row.base == "http://127.0.0.1:8011"
    assert row.owned is True, (
        "the node that answered carries the same instance id - it is the same plant, "
        "and ownership is still corroborated rather than assumed")
    assert any("answering  answered on http://127.0.0.1:8011" in line for line in said)
    assert any("nodes      2, 1 answered" in line for line in said), said


def test_fleet_status_of_a_plant_whose_every_node_is_silent_still_says_unknown(
        tmp_path, monkeypatch):
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows(0))
    said: list[str] = []

    row = commands.status("bottling", root=tmp_path, echo=said.append,
                          ask=_answering_at("http://127.0.0.1:9999"),
                          ask_book=lambda base, **_k: None)

    assert row.answered is False
    assert row.base == "http://127.0.0.1:8010", "the address it was dialled at"
    assert any("answering  unknown" in line for line in said), said


def test_a_node_answering_as_another_plant_is_refused_rather_than_believed(
        tmp_path, monkeypatch):
    """The reason the fallback goes through the ownership question instead of
    trusting a port. Something else is listening where node b should be; that
    is not this plant and no column is filled from it."""
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    said: list[str] = []

    row = commands.status("bottling", root=tmp_path, echo=said.append,
                          ask=_answering_at("http://127.0.0.1:8011",
                                            instance="a-different-plant"),
                          ask_book=lambda base, **_k: None)

    assert row.answered is False
    assert row.base == "http://127.0.0.1:8010"
    assert any("answering  unknown" in line for line in said), said


def test_fleet_list_counts_a_plant_serving_on_its_second_node_as_answered(
        tmp_path, monkeypatch):
    """The totals on `fsmes fleet list`, which is what a person reads first."""
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    said: list[str] = []

    commands.listing(root=tmp_path, echo=said.append,
                     ask=_answering_at("http://127.0.0.1:8011"),
                     ask_pack=lambda base, **_k: observe.Answer(
                         base, True, status=200,
                         body={"pack": "bottling", "line": {"machines": 6},
                               "schema": {"revision": "abc", "at_head": True},
                               "modules": {"on": ["oee"], "total": 1}}))

    assert said[0].startswith("1 plants, 1 answered"), said[0]
    assert any("2 nodes, 1 answered" in line for line in said), said


def test_stopping_a_plant_whose_first_node_died_stops_the_plant(tmp_path, monkeypatch):
    """The state a person is in one second after killing a node, and the verb
    they reach for next. `stop` asks the plant whether it is there before it
    acts, and asking only the address in the ownership record meant a plant
    running on node b refused to stop at all: *nothing in its pid file is
    alive, there may be nothing left to stop* - about two live processes."""
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    monkeypatch.setattr(commands.observe, "health",
                        _answering_at("http://127.0.0.1:8011"))
    monkeypatch.setattr(commands, "_config", lambda entry: _two_nodes())
    stopped: list[tuple[str, Path]] = []
    monkeypatch.setattr(commands.plants, "stop",
                        lambda name, cfg, root, echo=print: stopped.append((name, root)))

    ownership = commands.stop("bottling", root=tmp_path, echo=lambda _line: None)

    assert ownership.owned is True
    assert ownership.answering is True, "node b answered, so the plant is there"
    assert [name for name, _root in stopped] == ["bottling"], (
        "and `plants.stop` stops every node the pack names")


def test_a_silent_two_node_plant_counts_the_pid_file_of_every_node(tmp_path, monkeypatch):
    """What the refusal says when no node answers at all. Each node writes its
    own pid file - `bottling-a.pids`, `bottling-b.pids` - and the sentence a
    person reads before typing `--force` used to count only `bottling.pids`,
    which for a plant with nodes is a file nobody writes: it said nothing was
    left to stop while two processes were alive. Unknown is not zero, least of
    all there."""
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    (where / "bottling-b.pids").write_text(f"{os.getpid()}\n", encoding="utf-8")
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    monkeypatch.setattr(commands.observe, "health",
                        lambda base, **_k: observe.Answer(
                            base, False, why="did not answer ([Errno 111] refused)"))

    with pytest.raises(ownership.NotOwned) as refused:
        commands.stop("bottling", root=tmp_path, echo=lambda _line: None)

    said = str(refused.value)
    assert str(os.getpid()) in said, said
    assert "nothing left to stop" not in said, (
        "a plant with a live process is not a plant with nothing left to stop")


def test_a_silent_two_node_plant_with_no_live_process_says_which_files_it_read(
        tmp_path, monkeypatch):
    """And when the pid files really are all dead, the sentence says how many
    it read rather than speaking of one file the plant does not have."""
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    monkeypatch.setattr(commands.observe, "health",
                        lambda base, **_k: observe.Answer(
                            base, False, why="did not answer ([Errno 111] refused)"))

    with pytest.raises(ownership.NotOwned) as refused:
        commands.stop("bottling", root=tmp_path, echo=lambda _line: None)

    said = str(refused.value)
    assert "its 2 nodes" in said and "(a, b)" in said, said


def test_applying_a_pack_while_the_second_node_serves_the_floor_is_refused(
        tmp_path, monkeypatch):
    """The other side of the same question. A pack applied while a node is
    up migrates a database underneath a live process; asking one address
    meant the node that was still serving did not count as a plant running.
    """
    where = commands.data_dir(tmp_path)
    _ownership_of(where)
    monkeypatch.setattr(commands, "_nodes", lambda entry, root: _two_node_rows())
    monkeypatch.setattr(commands.observe, "health",
                        _answering_at("http://127.0.0.1:8011"))

    with pytest.raises(commands.Refused) as refused:
        commands.apply("bottling", root=tmp_path, echo=lambda _line: None)

    assert "still answering on http://127.0.0.1:8011" in str(refused.value), (
        "and it names the node that is answering, not the one that is not")


def test_windows_is_promised_only_what_a_windows_job_actually_runs():
    """The page says starting, answering, holding and stopping two nodes is
    proven on Windows and the kill is not. That is a claim about a CI job, so
    the job has to be there, on a Windows runner, running the script that
    makes exactly those checks."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    script = ROOT / ".github" / "scripts" / "two_nodes_on_one_box.py"

    assert "two-nodes-on-windows:" in workflow, (
        "`redundancy.md` names this job; a page promising a proof that no job "
        "runs is worse than a page that promises nothing")
    windows = workflow.split("two-nodes-on-windows:", 1)[1].split("\n  lab:", 1)[0]
    assert "runs-on: windows-latest" in windows
    assert "two_nodes_on_one_box.py" in windows
    assert script.is_file()

    source = script.read_text(encoding="utf-8")
    for command in ("pack", "check"), ("fleet", "create"), ("fleet", "start"), \
                   ("fleet", "status"), ("fleet", "stop"):
        assert f'"{command[0]}", "{command[1]}"' in source, (
            f"the Windows job is supposed to type `fsmes {' '.join(command)}`")
    page = (ROOT / "docs" / "operate" / "redundancy.md").read_text(encoding="utf-8")
    assert "two-nodes-on-windows" in page, "the page names the job it leans on"
    assert "proven on Windows is the kill" in page, (
        "the page says in one sentence what Windows does not prove")


def test_the_two_node_pack_the_docs_tell_a_person_to_write_would_check_out():
    """The example in `docs/operate/redundancy.md` is read as TOML and held to
    the rules the checker enforces, so the page cannot drift into a pack that
    `fsmes pack check` would refuse."""
    page = (ROOT / "docs" / "operate" / "redundancy.md").read_text(encoding="utf-8")
    blocks = [block.split("```", 1)[0] for block in page.split("```toml")[1:]
              if "[[serve.nodes]]" in block.split("```", 1)[0]]
    assert len(blocks) == 1, f"{len(blocks)} examples name nodes; this test wants the one"

    written = tomllib.loads(blocks[0])
    nodes = written["serve"]["nodes"]
    assert [one["id"] for one in nodes] == ["a", "b"]
    assert len({one["api_port"] for one in nodes}) == 2, "two nodes, two ports"
    assert written["serve"]["api_port"] in [one["api_port"] for one in nodes], (
        "the plant's own address has to be one a node serves on")
    assert written["serve"]["secret_key_env"], "two nodes share one signing key"
    assert written["redundancy"]["enabled"] is True


def test_the_lab_fleet_file_this_repository_ships_still_reads(tmp_path, monkeypatch):
    """Every pack in the lab fleet compiles, nodes or no nodes."""
    monkeypatch.delenv(plants.REGISTRY_ENV, raising=False)
    plants.environment.cache_clear()
    listed = tomllib.loads((LAB / "fleet.toml").read_text(encoding="utf-8"))["packs"]
    loaded = packs.load(ROOT)

    assert len(loaded) == len(listed), f"{len(listed)} packs listed, {len(loaded)} loaded"
    for name, cfg in loaded.items():
        assert cfg["nodes"] == [], f"{name} names no nodes today, and reads as it did"
