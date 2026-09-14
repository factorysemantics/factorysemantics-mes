"""`deploy/promote.sh` against two fake plants, with nothing real behind it.

The promote script is the one piece of this repository that is only ever run
against a live deployment, which is exactly why it had never been tested: on
2026-09-14 a promote written in its image failed twice in one minute and the
public demo was dark for seventy seconds. So here it is, run for real - a
real git checkout with real tags, a real HTTP server standing in for each
plant - with `systemctl`, `uv`, `pg_dump` and `pg_restore` replaced by
recorders that write down what they were asked and by an `fsmes` that answers
with a plan the test wrote.

What that proves is the script's own control flow: the order of stop, back
up, migrate and start; the refusals that happen before anything stops; that a
rollback puts back the backups *this run made*; that every PostgreSQL call
turns the statement timeout off; and that the health check believes the plant
and not the shell it is run from.

What it does not prove is that `fsmes pack apply` migrates a database - that
is `tests/test_pack_format.py` and the migration tests - or that systemd
starts a unit. Those have their own homes.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "deploy" / "promote.sh"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="promote.sh is bash, and prod is Linux"),
    pytest.mark.skipif(shutil.which("bash") is None, reason="no bash on this machine"),
    pytest.mark.skipif(shutil.which("git") is None, reason="no git on this machine"),
]

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

#: Every fake command writes one line here, in the order it was called. The
#: order is most of what these tests are about.
LOG = "PROMOTE_LOG"

RECORDERS = {
    "systemctl": """#!/usr/bin/env bash
printf '%s\\n' "systemctl $*" >> "$PROMOTE_LOG"
exit "${PROMOTE_SYSTEMCTL_EXIT:-0}"
""",
    # `uv` is the first thing to run after the checkout, so it is where the
    # test looks at which file the promote is being read from by then.
    "uv": """#!/usr/bin/env bash
printf '%s\\n' "uv $1 running-from ${FSMES_PROMOTE_SELF:-the-tree}" >> "$PROMOTE_LOG"
exit 0
""",
    "pg_dump": """#!/usr/bin/env bash
printf '%s\\n' "pg_dump PGOPTIONS=${PGOPTIONS:-unset} PGPASSWORD=${PGPASSWORD:-unset} ARGV $*" >> "$PROMOTE_LOG"
out=""; prev=""
for a in "$@"; do if [ "$prev" = "-f" ]; then out=$a; fi; prev=$a; done
if [ -n "$out" ]; then printf 'a dump\\n' > "$out"; fi
exit "${PROMOTE_PGDUMP_EXIT:-0}"
""",
    "pg_restore": """#!/usr/bin/env bash
printf '%s\\n' "pg_restore PGOPTIONS=${PGOPTIONS:-unset} PGPASSWORD=${PGPASSWORD:-unset} ARGV $*" >> "$PROMOTE_LOG"
exit 0
""",
}

FAKE_FSMES = """#!/usr/bin/env bash
printf '%s\\n' "fsmes $*" >> "$PROMOTE_LOG"
case "$1" in
  fleet) cat "$PROMOTE_PLAN" ;;
  pack)  if [ -n "${PROMOTE_APPLY_FAILS:-}" ]; then echo "refused" >&2; exit 1; fi ;;
  sim-generate) : ;;
esac
exit 0
"""

#: What the tag being promoted to carries as its own promote.sh: a different
#: script from the one the operator started.
BOOBY_TRAPPED = "#!/usr/bin/env bash\nexit 77\n"


class Plant:
    """One fake plant on a real port, answering the two public endpoints a
    promote reads. The test changes `health` and `pack` to make a plant lie."""

    def __init__(self, name: str):
        self.name = name
        self.health = {"status": "ok", "plant": name, "shadow": False}
        self.pack = {"plant": name, "pack": name, "drifted": False,
                     "schema": {"revision": "c8b1e40d7a92", "head": "c8b1e40d7a92",
                                "at_head": True}}
        answers = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = {"/health": answers.health, "/pack": answers.pack}.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                raw = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Prod:
    """A fake prod environment: a checkout with two tags on a real remote, a
    fleet of two plants, and a set of recorders in front of everything the
    script would otherwise do to this machine."""

    def __init__(self, root: Path):
        self.root = root
        self.checkout = root / "prod"
        self.conf = root / "conf"
        self.data = root / "data"
        self.log = root / "calls.log"
        self.plan_file = root / "plan.json"
        self.bin = root / "bin"
        self.plants: dict[str, Plant] = {}
        self.env: dict[str, str] = {}

    def git(self, *args, cwd: Path):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                              text=True, env={**os.environ, **GIT_ENV})

    def build(self):
        self.conf.mkdir()
        self.data.mkdir()
        self.bin.mkdir()
        (self.conf / "env").write_text("MES_ADMIN_PASSWORD=not-a-real-one\n", encoding="utf-8")
        self.log.touch()

        source = self.root / "source"
        (source / "deploy").mkdir(parents=True)
        self.git("init", "-q", "-b", "main", cwd=source)
        (source / "deploy" / "promote.sh").write_text(
            SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        (source / "README.md").write_text("v-old\n", encoding="utf-8")
        self.git("add", "-A", cwd=source)
        self.git("commit", "-qm", "old", cwd=source)
        self.git("tag", "-a", "v-old", "-m", "old", cwd=source)
        (source / "README.md").write_text("v-new\n", encoding="utf-8")
        (source / "deploy" / "promote.sh").write_text(BOOBY_TRAPPED, encoding="utf-8")
        self.git("add", "-A", cwd=source)
        self.git("commit", "-qm", "new", cwd=source)
        self.git("tag", "-a", "v-new", "-m", "new", cwd=source)

        remote = self.root / "remote.git"
        self.git("clone", "-q", "--bare", str(source), str(remote), cwd=self.root)
        self.git("clone", "-q", "-o", "public", str(remote), str(self.checkout), cwd=self.root)
        self.git("-c", "advice.detachedHead=false", "checkout", "-q", "v-old", cwd=self.checkout)

        venv = self.checkout / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(sys.executable)
        self.write_bin(venv / "fsmes", FAKE_FSMES)
        for name, body in RECORDERS.items():
            self.write_bin(self.bin / name, body)

        self.plants = {"bottling": Plant("bottling"), "cutlery": Plant("cutlery")}
        (self.data / "bottling.db").write_text("the old database\n", encoding="utf-8")
        (self.data / "bottling.db-wal").write_text("the old log\n", encoding="utf-8")
        self.write_plan()
        return self

    @staticmethod
    def write_bin(path: Path, body: str):
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def plan(self) -> dict:
        rows = [
            {"name": "bottling", "label": "bottling", "pack": str(self.conf / "bottling"),
             "simulate": True, "line_data": str(self.conf / "replay" / "line.json"),
             "health": f"http://127.0.0.1:{self.plants['bottling'].port}",
             "storage": {"kind": "sqlite", "backup": "copy",
                         "path": str(self.data / "bottling.db"),
                         "url": f"sqlite:///{(self.data / 'bottling.db').as_posix()}"}},
            {"name": "cutlery", "label": "cutlery", "pack": str(self.conf / "cutlery"),
             "simulate": False, "line_data": None,
             "health": f"http://127.0.0.1:{self.plants['cutlery'].port}",
             "storage": {"kind": "postgresql", "backup": "pg_dump", "host": "127.0.0.1",
                         "port": 5432, "user": "fsmes", "dbname": "fsmes_prod_cutlery",
                         "password_known": True,
                         "url": "postgresql+psycopg://fsmes:***@127.0.0.1:5432/fsmes_prod_cutlery",
                         "secret_password": "not-in-the-pack",
                         "secret_url": "postgresql+psycopg://fsmes:not-in-the-pack@"
                                       "127.0.0.1:5432/fsmes_prod_cutlery"}},
        ]
        return {"product_version": "0.2.0", "fleet": str(self.conf / "fleet.toml"),
                "data_dir": str(self.data), "count": len(rows),
                "can_back_up": sum(1 for r in rows if r["storage"]["backup"] != "none"),
                "plants": rows}

    def write_plan(self, plan: dict | None = None):
        self.plan_file.write_text(json.dumps(plan or self.plan(), indent=2), encoding="utf-8")

    def run(self, tag: str = "v-new", **extra) -> subprocess.CompletedProcess:
        env = {**os.environ, **GIT_ENV, **self.env, **extra,
               "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "FSMES_PROD_ROOT": str(self.checkout),
               "FSMES_PROD_CONF": str(self.conf),
               "FSMES_PLANT_REGISTRY": str(self.conf / "fleet.toml"),
               "FSMES_PROMOTE_WAIT": "20",
               LOG: str(self.log),
               "PROMOTE_PLAN": str(self.plan_file)}
        env.pop("FSMES_PROMOTE_SELF", None)
        return subprocess.run(
            ["bash", str(self.checkout / "deploy" / "promote.sh"), tag],
            capture_output=True, text=True, env=env, timeout=180)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()

    def at(self) -> str:
        return self.git("describe", "--tags", "--exact-match", cwd=self.checkout).stdout.strip()

    def backups(self) -> list[str]:
        return sorted(p.name for p in self.data.iterdir() if "pre-migrate" in p.name)

    def close(self):
        for p in self.plants.values():
            p.stop()


@pytest.fixture()
def prod(tmp_path):
    it = Prod(tmp_path).build()
    yield it
    it.close()


def argv(call: str) -> str:
    """What a recorder was actually given on its command line. The recorders
    write their own environment down too, which is how the statement-timeout
    test reads it - and is the one place a password may legitimately appear."""
    return call.split(" ARGV ", 1)[1] if " ARGV " in call else call


def untouched(prod) -> bool:
    """Nothing was stopped, backed up or migrated. Reading the plan does not
    count: it touches no plant."""
    return not [c for c in prod.calls()
                if c.startswith(("systemctl", "pg_dump", "pg_restore"))
                or c.startswith("fsmes pack")]


def index(calls: list[str], needle: str) -> int:
    for i, line in enumerate(calls):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} was never called:\n" + "\n".join(calls))


# ---------------------------------------------------------------- it promotes


def test_a_promote_stops_every_plant_backs_it_up_and_only_then_migrates(prod):
    result = prod.run()
    assert result.returncode == 0, result.stdout + result.stderr
    calls = prod.calls()
    assert index(calls, "systemctl --user stop fsmes-prod-plant@bottling") \
        < index(calls, "pg_dump"), "a backup of a running plant is a backup of a half-written file"
    assert index(calls, "pg_dump") < index(calls, "fsmes pack apply")
    assert index(calls, "fsmes pack apply") < index(calls, "systemctl --user start")
    assert prod.at() == "v-new"
    assert "2 plants asked, 2 answered as expected, 0 did not." in result.stdout


def test_it_backs_up_a_file_plant_with_the_log_that_belongs_to_it(prod):
    """A SQLite file restored without its write-ahead log is not the plant as
    it was; it is a database that will not open."""
    assert prod.run().returncode == 0
    made = prod.backups()
    assert len(made) == 3, made  # the file, its -wal, and the cutlery dump
    db = next(n for n in made if n.endswith(tuple("0123456789")))
    assert (prod.data / db).read_text(encoding="utf-8") == "the old database\n"
    assert (prod.data / f"{db}-wal").read_text(encoding="utf-8") == "the old log\n"


def test_it_dumps_a_postgres_plant_where_the_fleet_keeps_its_data(prod):
    assert prod.run().returncode == 0
    dumps = [n for n in prod.backups() if n.endswith(".dump")]
    assert len(dumps) == 1 and dumps[0].startswith("cutlery.pre-migrate-")


def test_it_regenerates_the_line_data_only_for_the_plant_that_simulates(prod):
    assert prod.run().returncode == 0
    generated = [c for c in prod.calls() if c.startswith("fsmes sim-generate")]
    assert len(generated) == 1 and "replay/line.json" in generated[0]


def test_every_postgres_call_turns_the_statement_timeout_off(prod):
    """The other half of the 2026-09-14 incident: a rollback cancelled by the
    database role's ten-second statement timeout. A step a timeout can cancel
    is not a rollback, and a dump it can cancel is not a backup."""
    prod.env["PROMOTE_APPLY_FAILS"] = "1"
    assert prod.run().returncode == 1
    postgres = [c for c in prod.calls() if c.startswith(("pg_dump", "pg_restore"))]
    assert len(postgres) == 2, postgres
    for call in postgres:
        assert "PGOPTIONS=-c statement_timeout=0" in call, call
        assert "PGPASSWORD=not-in-the-pack" in call, "the password reaches libpq by environment"
    assert "TEMPLATE" not in prod.log.read_text(encoding="utf-8")


def test_the_password_never_reaches_a_command_line(prod):
    """Every process on this machine can read every other process's argv. The
    plan is held in a shell variable and handed to its readers on stdin."""
    assert prod.run().returncode == 0
    for call in prod.calls():
        assert "not-in-the-pack" not in argv(call), call


def test_it_keeps_the_newest_backups_and_no_more(prod):
    for stamp in ("20260101-000001", "20260101-000002", "20260101-000003"):
        (prod.data / f"bottling.db.pre-migrate-{stamp}").write_text("old\n", encoding="utf-8")
        (prod.data / f"cutlery.pre-migrate-{stamp}.dump").write_text("old\n", encoding="utf-8")
    assert prod.run(FSMES_PROMOTE_KEEP="2").returncode == 0
    files = prod.backups()
    assert len([n for n in files if n.startswith("bottling.db.pre-migrate")
                and not n.endswith(("-wal", "-shm"))]) == 2
    assert len([n for n in files if n.endswith(".dump")]) == 2


def test_the_promote_runs_from_a_copy_and_not_from_the_tree_it_rewrites(prod):
    """A promote rewrites the tree its own script is in, and bash reads a
    script from disk as it goes rather than all at once. So the script copies
    itself once, before anything, and runs the copy.

    This asserts the mechanism, not a rescue: by the time `uv` runs, the
    promote is being read from a file outside the checkout, and that file is
    gone when it exits. It stops short of proving a corrupted run, because
    the failure it guards against cannot be provoked to order - `git
    checkout` renames a new file over the old one, which leaves a shell
    holding the old one reading fine, and a script small enough for bash's
    read buffer survives being truncated too. It is a cheap belt on the one
    script in this repository that only ever runs against a live deployment."""
    result = prod.run()
    assert result.returncode == 0, result.stdout + result.stderr
    said = prod.calls()[index(prod.calls(), "running-from")].split()[-1]
    assert said != "the-tree", "the promote was still reading the script in the checkout"
    running_from = Path(said)
    assert running_from.is_absolute() and running_from != prod.checkout / "deploy" / "promote.sh"
    assert prod.checkout not in running_from.parents, running_from
    assert not running_from.exists(), "the copy is cleaned up when the promote exits"
    assert (prod.checkout / "deploy" / "promote.sh").read_text(encoding="utf-8") == BOOBY_TRAPPED


# ---------------------------------------------------------------- it refuses


def test_a_tag_that_is_not_on_the_remote_is_refused_before_anything_stops(prod):
    result = prod.run("v-never-released")
    assert result.returncode == 2
    assert "has no tag v-never-released" in result.stdout
    assert untouched(prod), "nothing was stopped, nothing was backed up"
    assert prod.at() == "v-old"


def test_a_local_tag_that_points_somewhere_else_than_the_remote_is_refused(prod):
    """The trap this repository has already walked into: a checkout carrying
    tags from an older remote. `git fetch` will not overwrite them, so without
    this check the promote would check out the wrong commit and say the right
    tag's name while doing it."""
    prod.git("tag", "-f", "v-new", "v-old^{commit}", cwd=prod.checkout)
    result = prod.run()
    assert result.returncode == 2
    assert "shadowing" in result.stdout and "git tag -d v-new" in result.stdout
    assert untouched(prod)
    assert prod.at() == "v-old"


def test_a_remote_this_checkout_does_not_have_is_refused_by_name(prod):
    result = prod.run(FSMES_PROMOTE_REMOTE="origin")
    assert result.returncode == 2
    assert "no remote called 'origin'" in result.stdout
    assert untouched(prod)


def test_a_plant_whose_database_it_cannot_back_up_refuses_the_whole_promote(prod):
    """Unknown is not zero, and a promote it could not undo is not one to run.
    The other plant is fine; the fleet is still refused, by name."""
    plan = prod.plan()
    plan["plants"][1]["storage"] = {"kind": "mysql", "backup": "none", "url": None,
                                    "why": "this tooling copies SQLite and PostgreSQL"}
    plan["can_back_up"] = 1
    prod.write_plan(plan)
    result = prod.run()
    assert result.returncode == 2
    assert "cutlery: this tooling copies SQLite and PostgreSQL" in result.stdout
    assert untouched(prod), "it refused before it stopped anything"
    assert prod.at() == "v-old"


def test_a_dump_that_fails_leaves_the_fleet_where_it_was_and_running(prod):
    result = prod.run(PROMOTE_PGDUMP_EXIT="1")
    assert result.returncode == 2
    assert "pg_dump of cutlery" in result.stdout
    assert prod.at() == "v-old"
    assert index(prod.calls(), "systemctl --user start fsmes-prod-plant@bottling")


# ---------------------------------------------------------------- it rolls back


def test_a_failed_apply_rolls_back_to_the_previous_tag_and_the_backups_it_made(prod):
    prod.env["PROMOTE_APPLY_FAILS"] = "1"
    result = prod.run()
    assert result.returncode == 1
    assert "rolling back to v-old" in result.stdout
    assert prod.at() == "v-old"
    calls = prod.calls()
    assert index(calls, "pg_restore") > index(calls, "fsmes pack apply")
    restore = calls[index(calls, "pg_restore")]
    assert "--single-transaction" in restore and "--clean --if-exists" in restore
    made = next(n for n in prod.backups() if n.endswith(".dump"))
    assert made in restore, "it restores the backup this run made, not the newest on disk"
    assert index(calls, "systemctl --user start fsmes-prod-plant@cutlery") > index(calls, "pg_restore")


def test_a_rollback_puts_the_file_plant_and_its_log_back(prod):
    prod.env["PROMOTE_APPLY_FAILS"] = "1"
    (prod.data / "bottling.db").write_text("the old database\n", encoding="utf-8")
    assert prod.run().returncode == 1
    assert (prod.data / "bottling.db").read_text(encoding="utf-8") == "the old database\n"
    assert (prod.data / "bottling.db-wal").read_text(encoding="utf-8") == "the old log\n"


def test_a_plant_that_does_not_say_its_schema_is_at_head_rolls_the_promote_back(prod):
    """The check that has to read the plant. A CLI in the promote's own shell
    can be pointed at a different database than the plant serves - that is
    what rolled back a good promote on 2026-09-14 - so the question is asked
    of the plant, over the endpoint the plant answers."""
    prod.plants["cutlery"].pack["schema"] = {"revision": "older", "head": "c8b1e40d7a92",
                                             "at_head": False}
    result = prod.run()
    assert result.returncode == 1
    assert "cutlery: /pack says schema 'older', head is 'c8b1e40d7a92'" in result.stdout
    assert "2 plants asked, 1 answered as expected, 1 did not." in result.stdout
    assert prod.at() == "v-old"


def test_a_plant_whose_schema_is_unknown_is_not_read_as_fine(prod):
    """`at_head: null` is what a plant says when its database did not answer.
    Null is not "no drift" and it is not "at head"."""
    prod.plants["bottling"].pack["schema"] = {"revision": None, "head": None, "at_head": None}
    result = prod.run()
    assert result.returncode == 1 and "rolling back" in result.stdout


def test_a_plant_answering_on_another_plants_port_is_caught(prod):
    """Two plants on one machine, one port wrong in the fleet file: without
    asking each plant its own name, the promote would read one plant's health
    as both plants' and call the fleet green."""
    prod.plants["cutlery"].health = {"status": "ok", "plant": "bottling", "shadow": False}
    result = prod.run()
    assert result.returncode == 1
    assert "calls itself 'bottling'" in result.stdout
    assert prod.at() == "v-old"


def test_a_plant_that_never_answers_rolls_the_promote_back(prod):
    prod.plants["bottling"].stop()
    result = prod.run(FSMES_PROMOTE_WAIT="1")
    assert result.returncode == 1
    assert "never answered" in result.stdout
    assert prod.at() == "v-old"


def test_a_free_port_is_a_free_port():
    """Guard for the fixture itself: the fake plants bind port 0 and are told
    which port they got, so two tests running side by side never collide."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        assert s.getsockname()[1] != 0
