"""Kill one of two nodes while the plant is counting, and lose nothing.

Scott, 2026-10-15, after hearing of a site that could not make its server
redundant because of the MES it ran: *"I want the next pypi release to be a
parallelizable mes. Redundancy is a priority."* The proof he asked for is
killing one of two boxes while the line runs. This is that, as small as it
goes and still real: one OPC replay standing in for the machine layer, one
PostgreSQL, and two whole nodes of the bottling plant started the way a
person starts them -

    fsmes plant bottling run --node a --api-port 8111
    fsmes plant bottling run --node b --api-port 8112

- through a throwaway fleet in `tmp_path`, so the pack's own
`[redundancy]` section, its named secret variable, the per-node log
directory and the node's omission of the simulated machine layer are all
the real ones and not a test's idea of them.

What is asserted, in the order the test does it: one holder per role at
every sample; a session cookie minted by node *a* accepted by node *b*;
the role moves within 3 lease units of the dead node's last renewal; the
survivor's dashboard answers all the way through the failover; the killed
node comes back as a standby that holds nothing and still serves;
production booked over the window equals the counters' own movement over
the same window, exactly, per machine; and no reading appears in
`tag_values` twice.

**Takeover is measured in lease units, never in seconds.** A shared runner
makes every absolute bound flaky, and a lease has its own unit: the gap
between the dead holder's last renewal and the new holder's first claim,
divided by `lease_seconds`, both read from the database clock. 3 is the
bound - one lease to expire, one renewal interval to notice, and room for
a slow runner.

This file runs only where there is a PostgreSQL to run it against, which
is the `postgres` job. Two nodes on one *Windows* box is part 2 of this
work: `Popen.terminate()` there does not go through the supervisor's
handler, so the orderly shutdown this test leans on is a different
mechanism and deserves its own test rather than a skip buried here.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text

from fsmes import plant as plants
from fsmes.schema import MIGRATION_LOCK_KEY
from fsmes.sim import runner
from fsmes.sim.generate import generate

SUITE_DATABASE = os.environ.get("MES_TEST_DATABASE_URL", "").strip()
REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not SUITE_DATABASE.startswith("postgresql"),
    reason="two nodes share one database, which a file database cannot be; "
           "set MES_TEST_DATABASE_URL to a PostgreSQL to run this",
)

# The lease, short enough that a failover fits in a test and long enough
# that a busy runner does not drop one by being busy. Every bound below is
# a multiple of this, never a number of seconds.
LEASE_SECONDS = 4.0
TAKEOVER_LEASE_UNITS = 3.0
# Line seconds per wall second. The replay wraps at the end of its file and
# a counter resets when it does, so the whole run has to fit inside one
# pass: 3,600 rows at 6x is 600 seconds of wall clock, and the run below
# takes about a third of that on this machine.
SPEED = 6.0
SECRET_VARIABLE = "FSMES_TWO_NODE_SECRET"
MACHINES = ("LD01", "RD01", "WASH01", "QI01", "FILL01", "PAL01")


# --------------------------------------------------------------- the plumbing

def _server_url(database: str) -> str:
    """The suite's own URL pointed at a different database on that server."""
    return str(make_url(SUITE_DATABASE).set(database=database))


def _make_database(name: str) -> str:
    """An empty database of its own, so this test cannot see or disturb
    what the rest of the suite put in the shared one."""
    admin = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    admin.dispose()
    return _server_url(name)


def _drop_database(name: str) -> None:
    admin = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.dispose()


def _a_fleet_with_two_nodes(tmp_path: Path, url: str, opc_port: int) -> Path:
    """A throwaway fleet holding one redundant copy of the bottling pack.

    The pack is the lab's own, patched in the four places that make it this
    test's: where the machine layer is, where the database is, that it runs
    redundantly, and the name of the variable its signing key lives in. The
    replay data is generated from the reference hour beside it rather than
    the pack's eight-hour shift, because the hour generates in a fifth of a
    second and this test only needs minutes of it.
    """
    pack = tmp_path / "bottling"
    shutil.copytree(REPO / "labs" / "multiplant" / "bottling", pack)
    shutil.copy(REPO / "labs" / "kepsim" / "line.json", pack / "line.json")
    generate(pack / "line.json", pack / "out", write_docs=False)

    toml = (pack / "plant.toml").read_text()
    toml = toml.replace('opc_endpoint = "opc.tcp://127.0.0.1:4841/fsmes/bottling"',
                        f'opc_endpoint = "opc.tcp://127.0.0.1:{opc_port}/fsmes/bottling-nodes"')
    toml = toml.replace("simulate     = true",
                        f'simulate     = true\nsecret_key_env = "{SECRET_VARIABLE}"')
    # A pack a person writes names `database_password_file` and keeps the
    # password out of the file that says what the plant is. This one splices
    # the suite's whole URL because the suite's URL is what it was given,
    # and it lives in a directory pytest deletes.
    toml += (f'\n[storage]\ndatabase_url = "{url}"\n'
             f'\n[redundancy]\nenabled = true\nlease_seconds = {LEASE_SECONDS}\n')
    (pack / "plant.toml").write_text(toml)

    fleet = tmp_path / "fleet.toml"
    fleet.write_text('packs = ["bottling"]\n\n[environment]\n'
                     f'data_dir = "{(tmp_path / "data").as_posix()}"\n')
    return fleet


def _get(url: str, cookie: str | None = None, timeout: float = 4.0):
    """(status, body) for one request, or (0, "") if nothing answered."""
    request = urllib.request.Request(url)
    if cookie:
        request.add_header("Cookie", f"mes_session={cookie}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as refused:
        return refused.code, ""
    except Exception:
        return 0, ""


def _health(port: int) -> dict:
    status, body = _get(f"http://127.0.0.1:{port}/health")
    if status != 200 or not body:
        return {}
    try:
        return json.loads(body)
    except ValueError:
        return {}


def _answers(port: int, timeout: float) -> dict:
    """Wait for a node's API to be up, and say what /health said."""
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        body = _health(port)
        if body:
            return body
        time.sleep(0.5)
    return {}


def _login(port: int) -> str:
    data = json.dumps({"code": "SCOTT", "password": "operator"}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/auth/login", data=data,
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read())["token"]


class Plant:
    """Two nodes, one replay, one database - and the few questions this
    test asks of them."""

    def __init__(self, tmp_path: Path, url: str, engine, opc_port: int, ports: dict):
        self.root = tmp_path / "run"   # where a plant keeps its own files
        self.root.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine = engine
        self.opc_port = opc_port
        self.ports = ports              # {"a": port, "b": port}
        self.nodes: dict[str, subprocess.Popen] = {}
        self.replay: subprocess.Popen | None = None
        self.logs = tmp_path / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env[SECRET_VARIABLE] = "the-same-key-on-both-nodes"
        self.env["FSMES_PLANT_REGISTRY"] = str(tmp_path / "fleet.toml")
        self.mes = plants.fsmes_bin()

    # -- starting things

    def _log(self, what: str):
        return (self.logs / f"{what}.log").open("ab")

    def start_replay(self, pack: Path) -> None:
        """The machine layer: one OPC server for the whole plant, because a
        plant has one line however many nodes read it."""
        env = dict(self.env)
        env.update(
            MES_PLANT_NAME="bottling",
            MES_OPC_ENDPOINT=f"opc.tcp://127.0.0.1:{self.opc_port}/fsmes/bottling-nodes",
            MES_REPLAY_DIR=str(pack / "out"),
            # This plant's own tag map, or the replay builds the default
            # line's address space and then looks for a station the bottling
            # line does not have.
            MES_TAG_MAP_FILE=str(pack / "tag_map.json"),
            MES_SIM_SPEED=str(SPEED),
            MES_REPLAY_HOLD_S=str(runner.REPLAY_HOLD_S),
            MES_LOG_DIR=str(self.logs),
        )
        self.replay = subprocess.Popen([self.mes, "run-opc-sim", "--replay"], cwd=REPO,
                                       env=env, stdout=self._log("replay"),
                                       stderr=subprocess.STDOUT)
        runner._await_replay(self.logs / "replay.log", timeout=60.0)

    def init(self) -> None:
        done = subprocess.run([self.mes, "plant", "bottling", "init", "--root", str(self.root)],
                              cwd=REPO, env=self.env, capture_output=True, text=True,
                              encoding="utf-8", timeout=300)
        assert done.returncode == 0, f"seeding the plant failed: {done.stderr or done.stdout}"

    def start(self, node: str) -> None:
        self.nodes[node] = subprocess.Popen(
            [self.mes, "plant", "bottling", "run", "--node", node,
             "--api-port", str(self.ports[node]), "--speed", str(SPEED),
             "--root", str(self.root)],
            cwd=REPO, env=self.env, stdout=self._log(f"node-{node}"),
            stderr=subprocess.STDOUT)

    def stop(self, node: str) -> None:
        """The way `fsmes demo <plant> stop` does it: ask, then wait."""
        process = self.nodes.pop(node, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)

    def stop_everything(self) -> None:
        for node in list(self.nodes):
            self.stop(node)
        if self.replay and self.replay.poll() is None:
            self.replay.terminate()
            try:
                self.replay.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.replay.kill()

    # -- asking the database

    def lease(self, role: str) -> tuple[str | None, datetime | None, datetime | None]:
        """(holder, acquired_at, renewed_at) for one role, from the table."""
        with self.engine.connect() as conn:
            row = conn.execute(text("select holder, acquired_at, renewed_at "
                                    "from plant_leases where role = :role"),
                               {"role": role}).first()
        return (row[0], row[1], row[2]) if row else (None, None, None)

    def snapshot(self) -> tuple[dict, dict]:
        """Production booked and counter baselines, read in one transaction.

        One snapshot of both, so the comparison is of the same instant: the
        booking and the baseline it leaves behind commit together, which is
        the whole reason a second reader cannot book the same units twice.
        """
        booked = ("select e.code, coalesce(sum(p.good_qty + p.scrap_qty), 0) "
                  "from equipment e left join production_logs p "
                  "  on p.equipment_id = e.id and p.source = 'opc' "
                  "group by e.code")
        baselines = "select equipment_code, sum(value) from counter_baselines group by 1"
        with self.engine.connect().execution_options(
                isolation_level="REPEATABLE READ") as conn, conn.begin():
            return (dict(conn.execute(text(booked)).all()),
                    dict(conn.execute(text(baselines)).all()))

    def readings(self) -> tuple[int, int]:
        with self.engine.connect() as conn:
            return conn.execute(text(
                "select count(*), count(distinct (equipment_id, tag, ts)) "
                "from tag_values")).one()


@pytest.fixture()
def two_node_plant(tmp_path):
    """One PostgreSQL, one replay, and two nodes of the bottling plant."""
    api_a = runner.reserve_port(8100, 8199, "node a")
    api_b = runner.reserve_port(8100, 8199, "node b")
    opc = runner.reserve_port(4900, 4999, "the shared machine layer")
    name = f"fsmes_two_nodes_{os.getpid()}"
    url = _make_database(name)
    engine = create_engine(url)
    fleet = _a_fleet_with_two_nodes(tmp_path, url, opc.port)
    plant = Plant(tmp_path, url, engine, opc.port,
                  {"a": api_a.port, "b": api_b.port})
    try:
        plant.init()
        plant.start_replay(fleet.parent / "bottling")
        yield plant
    finally:
        plant.stop_everything()
        engine.dispose()
        _drop_database(name)
        for held in (api_a, api_b, opc):
            held.release()


# ------------------------------------------------------------------ the proof

def test_killing_the_node_that_reads_the_machines_loses_no_production(two_node_plant, request):
    plant = two_node_plant
    plant.start("a")
    plant.start("b")

    a = _answers(plant.ports["a"], timeout=120)
    b = _answers(plant.ports["b"], timeout=120)
    assert a and b, "both nodes have to be serving before anything is proved"
    assert a["node"]["name"] == "a" and b["node"]["name"] == "b"
    assert a["node"]["redundancy"] and a["node"]["lease_seconds"] == LEASE_SECONDS

    # One holder per role, asked of the nodes themselves rather than of the
    # table: two nodes that each believe they hold the reader is the failure
    # this whole change exists to prevent, and the table alone cannot show
    # it (the role is its primary key).
    claims = _who_claims_what(plant)

    # A person signed in on one node is signed in on the other. This is the
    # pinned secret doing its work; without it node b rejects the cookie.
    token = _login(plant.ports["a"])
    for port in plant.ports.values():
        status, _ = _get(f"http://127.0.0.1:{port}/auth/me", cookie=token)
        assert status == 200, f"the cookie node a issued was refused on port {port}"

    # Let the plant count for a while, sampling who holds what as it goes.
    booked_before, baseline_before = _wait_until_every_machine_books(plant, claims)

    # ---- the reader dies
    reader = plant.lease("reader")[0]
    survivor = "b" if reader == "a" else "a"
    units, refusals = _kill_and_time_the_takeover(plant, "reader", reader, survivor)
    assert units <= TAKEOVER_LEASE_UNITS, (
        f"the reader moved {units:.2f} lease units after {reader} died; "
        f"the bound is {TAKEOVER_LEASE_UNITS}")
    assert refusals == 0, (
        f"the surviving node's dashboard refused {refusals} requests during the failover")

    # ---- the dead node comes back, holding nothing
    plant.start(reader)
    back = _answers(plant.ports[reader], timeout=120)
    assert back, "a node that holds no role still serves the dashboard"
    assert back["node"]["name"] == reader
    assert back["node"]["holders"]["reader"] == survivor, (
        "a node that comes back does not take a role the other one is holding")

    # ---- and the floor dies too
    floor = plant.lease("floor")[0]
    still_up = [node for node in plant.ports if node != floor]
    units_floor, _ = _kill_and_time_the_takeover(plant, "floor", floor, still_up[0])
    assert units_floor <= TAKEOVER_LEASE_UNITS, (
        f"the floor moved {units_floor:.2f} lease units after {floor} died")

    # ---- exactly once, measured against the counters themselves
    _settle(plant, lease_units=3)
    booked_after, baseline_after = plant.snapshot()
    for machine in MACHINES:
        booked = booked_after.get(machine, 0) - booked_before.get(machine, 0)
        counted = baseline_after.get(machine, 0) - baseline_before.get(machine, 0)
        assert counted > 0, (
            f"{machine} counted nothing across the window, so this proves nothing"
            " (a negative or empty movement means the replay wrapped and the"
            " window straddled a counter reset)")
        assert booked == counted, (
            f"{machine} booked {booked} while its counters moved {counted} "
            f"across two failovers - booking twice gives double, losing a "
            f"baseline gives a gap")

    rows, distinct = plant.readings()
    assert rows == distinct, (
        f"{rows - distinct} readings are in tag_values twice: a reader taking "
        "over was handed the moment the dead one had already stored")

    _say(request,
         f"takeover: reader {units:.2f} lease units, floor {units_floor:.2f}, "
         f"bound {TAKEOVER_LEASE_UNITS} (lease_seconds={LEASE_SECONDS}, "
         f"replay {SPEED}x, {rows} readings)")


def _say(request, line: str) -> None:
    """Put a measured number on the console even when the test passes.

    `pytest` keeps a passing test's output to itself, and a ratio nobody can
    read is a ratio nobody can check: the takeover this run measured belongs
    in the job's log, because CI's runner is the only machine the crew has to
    measure it on. The terminal reporter writes outside the capture; a runner
    that has none (`-p no:terminal`) falls back to a plain print.
    """
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(f"\n{line}")
        return
    reporter.ensure_newline()
    reporter.write_line(line)


def _who_claims_what(plant: Plant) -> dict:
    """Every role, and the one node that says it holds it."""
    claims: dict[str, str] = {}
    for node, port in plant.ports.items():
        body = _health(port)
        for role in body.get("node", {}).get("roles") or []:
            assert role not in claims, (
                f"both {claims.get(role)} and {node} say they hold {role}")
            claims[role] = node
    return claims


def _wait_until_every_machine_books(plant: Plant, claims: dict) -> tuple[dict, dict]:
    """Wait for the plant to be counting on every machine, then take the
    first snapshot. Sampling who holds what the whole way: one holder per
    role has to be true at every sample, not only at the end."""
    until = time.monotonic() + 180
    while time.monotonic() < until:
        _who_claims_what(plant)
        _booked, baselines = plant.snapshot()
        if all(baselines.get(machine, 0) > 0 for machine in MACHINES):
            # Counting, not merely baselined: the first reading of a machine
            # books nothing, so a snapshot taken at that moment would compare
            # two numbers that have not moved yet.
            time.sleep(LEASE_SECONDS * 2)
            return plant.snapshot()
        time.sleep(1.0)
    raise AssertionError(
        "the plant never counted on all six machines; "
        f"baselines were {plant.snapshot()[1]}")


def _kill_and_time_the_takeover(plant: Plant, role: str, victim: str,
                                survivor: str) -> tuple[float, int]:
    """Terminate the node holding `role` and return (lease units, refusals).

    The clock is the database's: the gap between the dead holder's last
    renewal and the new holder's claim. The survivor's dashboard is asked
    on every pass, so "it answered throughout" is a count and not an
    impression.
    """
    _, _, last_renewal = plant.lease(role)
    assert last_renewal is not None, f"nobody held {role} to kill"
    plant.stop(victim)

    refusals = 0
    until = time.monotonic() + LEASE_SECONDS * (TAKEOVER_LEASE_UNITS + 4)
    while time.monotonic() < until:
        status, _ = _get(f"http://127.0.0.1:{plant.ports[survivor]}/health")
        refusals += 0 if status == 200 else 1
        holder, acquired, _ = plant.lease(role)
        if holder and holder != victim:
            return (acquired - last_renewal).total_seconds() / LEASE_SECONDS, refusals
        time.sleep(0.25)
    raise AssertionError(
        f"{role} was still held by the dead node {victim} "
        f"{LEASE_SECONDS * (TAKEOVER_LEASE_UNITS + 4):.0f}s after it was terminated")


def _settle(plant: Plant, lease_units: float) -> None:
    """Long enough for the new holder to have booked under its own lease."""
    time.sleep(LEASE_SECONDS * lease_units)


# ------------------------------------------ two nodes starting at once

def test_two_nodes_migrating_an_empty_database_at_once_both_succeed(tmp_path):
    """Two supervisors starting together both run the migrations, and the
    database ends at one head.

    Alembic runs a transaction per revision, so a transaction-scoped lock
    would be released between them: it would hold for the first revision of
    a chain and nothing after it. The lock the product takes is
    session-scoped for that reason.

    The contention here is made rather than hoped for. This test holds the
    migration lock itself, starts both `init-db` runs against the empty
    database, and waits for each of them to say it is waiting - so the
    queue is proved, not inferred from two processes that happened to
    overlap on one machine and might not on another. Then the door opens:
    one migrates, the other finds the head, both exit 0, one row in
    `alembic_version`.
    """
    name = f"fsmes_two_init_{os.getpid()}"
    url = _make_database(name)
    # `init-db` without --pack reads this process's own default database,
    # which is exactly what a node does; MES_TEST_DATABASE_URL would send
    # the children at the suite's shared database instead.
    env = dict(os.environ, MES_DATABASE_URL=url, MES_PLANT_NAME="bottling",
               PYTHONUNBUFFERED="1")
    env.pop("MES_TEST_DATABASE_URL", None)
    mes = plants.fsmes_bin()
    logs = [tmp_path / "init-a.log", tmp_path / "init-b.log"]
    door = create_engine(url, isolation_level="AUTOCOMMIT")
    both: list[subprocess.Popen] = []
    try:
        with door.connect() as held:
            held.exec_driver_sql(f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})")
            both = [subprocess.Popen([mes, "init-db"], cwd=REPO, env=env,
                                     stdout=log.open("wb"), stderr=subprocess.STDOUT)
                    for log in logs]
            for log, process in zip(logs, both, strict=True):
                _wait_for_line(log, "waiting for it to finish", timeout=180)
                assert process.poll() is None, (
                    f"a node whose database was being migrated exited instead of "
                    f"waiting: {log.read_text(errors='replace')}")
            # Opened by hand rather than by closing the connection: an
            # advisory lock belongs to a session, and a pooled connection
            # that is "closed" goes back to the pool still holding it.
            held.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})")

        for log, process in zip(logs, both, strict=True):
            assert process.wait(timeout=300) == 0, (
                f"a node could not start against an empty database: "
                f"{log.read_text(errors='replace')}")

        engine = create_engine(url)
        with engine.connect() as conn:
            heads = conn.execute(
                text("select version_num from alembic_version")).scalars().all()
            tables = conn.execute(text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public'")).scalars().all()
        engine.dispose()
        assert len(heads) == 1, f"the database is at {len(heads)} heads: {heads}"
        for table in ("plant_leases", "counter_baselines", "production_logs"):
            assert table in tables, f"{table} is not there, so nothing migrated"
    finally:
        for process in both:
            if process.poll() is None:
                process.kill()
        door.dispose()
        _drop_database(name)


def _wait_for_line(log: Path, says: str, timeout: float) -> None:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if log.exists() and says in log.read_text(errors="replace"):
            return
        time.sleep(0.2)
    raise AssertionError(
        f"{log.name} never said {says!r}: {log.read_text(errors='replace') if log.exists() else '(no log)'}")


def test_the_test_runs_the_commands_a_person_runs():
    """A guard, not a behaviour: if the CLI stops taking `--node` or
    `--api-port`, this file's whole premise is gone and it should say so
    here rather than in a timeout."""
    helped = subprocess.run([plants.fsmes_bin(), "plant", "--help"], cwd=REPO,
                            capture_output=True, text=True, encoding="utf-8",
                            env=dict(os.environ, COLUMNS="200"), timeout=120)
    assert helped.returncode == 0
    assert "--node" in helped.stdout and "--api-port" in helped.stdout
    assert sys.executable  # the console script belongs to this interpreter
