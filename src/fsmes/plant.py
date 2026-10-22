"""Run several independent MES plants side by side on one machine.

Each plant is a completely separate MES: its own database, its own OPC UA
server, its own dashboard on its own port. Nothing is shared. There is no
multi-tenant code path anywhere in the MES, and that is the point — isolation
here is by construction, not by a WHERE clause somebody might forget.

Every difference between plants is an environment variable, so "run another
plant" needs no product code:

    MES_DATABASE_URL   which database        -> data isolation
    MES_API_PORT       which dashboard       -> several UIs at once
    MES_OPC_ENDPOINT   which OPC UA server   -> several machine layers at once
    MES_TAG_MAP_FILE   which machines exist  -> different plants entirely
    MES_REPLAY_DIR     which line data       -> different physics
    MES_SECRET_KEY     per-plant token key   -> neither accepts the other's logins

This started as the cross-platform port of a PowerShell launcher that held
the registry in its own code. The registry moved out of the script and into a
file, because a registry that lives in code is one an agent cannot edit
safely — and then, in M8 piece 3, out of that file and into a **plant pack**
per plant, because a file with no schema cannot tell a key from a typo.

What is left here is the running of a plant, not the describing of one. A
plant arrives as the plain dictionary `fsmes.pack.fleet.compile_pack` makes
out of a pack: a label, a port, whether it simulates, and `env` — every
`MES_*` value the pack states, already resolved. Nothing in this module reads
`plant.toml`, which is what keeps it below the pack format on the layering
ladder rather than beside it.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fsmes import identity, storage

REGISTRY = Path("labs/multiplant/fleet.toml")

# The fleet file *is* the environment. Set this to one outside the checkout
# and `fsmes plant` runs that environment: its own packs, ports, data
# directory and accounts, from the same code. The lab fleet in the repository
# is what runs when it is unset. The variable keeps its name — what it points
# at changed in M8, and every deployment unit that sets it did not.
REGISTRY_ENV = "FSMES_PLANT_REGISTRY"

# Accounts the seeders do not create. `fsmes seed` (the demo plant) makes these,
# but seed-kepsim and the machining seed build a plant, not a user list —
# without them the dashboard is unreachable and the plant looks broken.
# The lab agent's password, named so a grep shows intent. FSMES_AGENT_PASSWORD
# overrides it; a plant that keeps it is told so at start-up (see api/app.py).
LAB_ONLY_AGENT_PASSWORD = "agent-lab-only"

# The analysis agent's own sign-in, the same way and for the same reason.
# FSMES_ANALYST_PASSWORD overrides it; a plant that keeps it is told so at
# start-up beside the agent's. A second account rather than a second role on
# the first, because the two kinds are allowed different things and an audit
# row has to say which of them read the plant.
LAB_ONLY_ANALYST_PASSWORD = "analyst-lab-only"

LAB_USERS = [
    ("SCOTT", "Scott K", "operator", "operator"),
    # The simulated shop floor signs in as itself. Invented checks and issues
    # under a real person's name would put fiction in the audit trail.
    ("FLOOR-SIM", "Simulated shop floor", "operator", "operator"),
    # The simulated shift supervisor: the dispositions an operator may not
    # make - closing a non-conformance - are made under this account, so the
    # simulator never needs the operator role weakened to pass. Every plant
    # retired on 2026-09-06 had every non-conformance still open because the
    # floor tried to close them as an operator and was refused, silently.
    ("FLOOR-SUP", "Simulated shift supervisor", "supervisor", "supervisor"),
    # The simulated production planner. The third identity, because a plant
    # has three: in a real plant the orders come from a planner or from the
    # ERP (never from the floor), and a lab plant has no ERP, so nobody played
    # that person. Both lab plants duly ran out of orders about forty hours
    # after they were built and measured into no order from then on. This
    # account plans the next order; it may not release one - that stays the
    # supervisor's - so the sequence planner -> supervisor -> floor is the
    # real plant's and the audit trail names whichever of the three acted.
    ("FLOOR-PLAN", "Simulated production planner", "planner", "planner"),
    # The simulated maintenance crew. One account for the trades rather than
    # one per mechanic, because that is how a workshop with seven people and
    # one terminal actually signs in: the work is done under this account and
    # the row says whose work it was (`performed_by`), so the audit trail
    # names the mechanic without this lab inventing seven logins nobody
    # issued. An operator, because starting and completing a maintenance
    # order and stopping a machine for it are an operator's acts; planning the
    # work is not, and this account cannot.
    ("FLOOR-CREW", "Simulated maintenance crew", "operator", "operator"),
    ("ADMIN", "Lab Admin", "admin", "admin"),
    # The MCP server's own sign-in. Admin because master data (routings)
    # requires it; everything it does is audited under this name, so "what did
    # the agent do to my plant" is one audit query. The capability-role work
    # will narrow this to exactly what an agent deployment grants.
    ("AGENT", "Plant Agent", os.environ.get("FSMES_AGENT_PASSWORD", LAB_ONLY_AGENT_PASSWORD), "agent"),
    # The analysis agent's sign-in. The `analyst` role: `plant.read` and
    # `audit.read`, and nothing that writes - so the reads its conversations
    # make are attributable to it and gated on its own role rather than on
    # AGENT's, which may book production. Every read it makes is audited under
    # this name, so "what did the analyst look at" is one audit query, the way
    # "what did the agent do to my plant" already is.
    ("ANALYST", "Plant Analyst",
     os.environ.get("FSMES_ANALYST_PASSWORD", LAB_ONLY_ANALYST_PASSWORD), "analyst"),
]


def find_root(start: Path | None = None) -> Path:
    """Walk up until the registry is found; fall back to the current directory.

    A fleet file named outright is not a fleet file to be searched for. The
    walk below exists to find the repository's own from somewhere inside the
    checkout; when `FSMES_PLANT_REGISTRY` says where the fleet file is, the
    root is only *where this plant's logs and data go*, and a root given
    explicitly is that - whatever happens to sit in the directories above it.
    Without this, a per-user home that was made inside a checkout (which is
    where `fsmes demo <plant>` can legitimately be pointed) would have its
    logs written to the checkout instead.
    """
    here = (start or Path.cwd()).resolve()
    if start is not None and os.environ.get(REGISTRY_ENV):
        return here
    for candidate in [here, *here.parents]:
        if (candidate / REGISTRY).is_file():
            return candidate
    return here


@functools.lru_cache(maxsize=1)
def environment() -> dict:
    """The environment table of the registry named by FSMES_PLANT_REGISTRY:
    where its data lives. Empty for the lab registry in the repository."""
    override = os.environ.get(REGISTRY_ENV)
    if not override:
        return {}
    path = Path(override).expanduser()
    with path.open("rb") as fh:
        table = tomllib.load(fh).get("environment", {})
    return {"registry": path, **table}


def registry_path(root: Path) -> Path:
    override = os.environ.get(REGISTRY_ENV)
    return Path(override).expanduser() if override else root / REGISTRY


def resolve(names: list[str], plants: dict[str, dict]) -> list[str]:
    if "all" in names:
        return list(plants)
    unknown = [n for n in names if n not in plants]
    if unknown:
        raise KeyError(
            f"Unknown plant(s): {', '.join(unknown)}. "
            f"Known: {', '.join(plants)} (or 'all')."
        )
    return names


def data_dir(root: Path) -> Path:
    configured = environment().get("data_dir")
    d = Path(configured).expanduser() if configured else root / "labs" / "multiplant" / ".data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def database_url(name: str, cfg: dict, root: Path) -> str:
    """Where this plant's database is.

    The default is one SQLite file under the fleet's data directory - the
    small plant's whole storage story. A pack may instead name a
    `database_url` (PostgreSQL, the large plant's), with the password read
    from `database_password_file` rather than written into the pack, so
    the file that says what a plant is can be committed and the file that
    holds a secret cannot. Nothing in the product changes with the choice:
    the same models, the same migrations, the URL decides.
    """
    url = cfg.get("database_url")
    if not url:
        return f"sqlite:///{(data_dir(root) / f'{name}.db').as_posix()}"
    # One merge, in one place. `fsmes db-status --pack`, `fsmes pack status`
    # and the plant this starts all put the password back the same way, so a
    # status command and the plant it is about cannot be looking at two
    # different URLs.
    return storage.with_password(url, cfg.get("database_password_file"))


def plant_env(name: str, cfg: dict, root: Path, speed: float | None = None,
              node: str | None = None, api_port: int | None = None) -> dict[str, str]:
    """The complete MES_* contract for one plant.

    Almost all of it is the pack's, compiled once when the fleet was read.
    Four things are added here because they are facts about *running* this
    plant on this machine rather than facts about the plant: where its
    database file goes, where its data directory is, where its log goes, and
    a signing key when nothing supplied one.

    `node` and `api_port` are facts about running it on *this* box rather
    than on any box, which is why they are arguments and not pack keys: two
    nodes of one plant differ by name and by port and in nothing else. A node
    writes its logs in its own directory under the plant's, so two nodes on
    one box do not append to one file.
    """
    env = dict(os.environ)
    env.update(cfg.get("env") or {})
    env["MES_PLANT_NAME"] = name
    env["MES_DATABASE_URL"] = database_url(name, cfg, root)
    env["MES_LOG_DIR"] = log_dir(name, node)
    if node:
        env["MES_NODE_ID"] = node
    if api_port is not None:
        env["MES_API_PORT"] = str(api_port)
    # Where this plant's own files live: its database, the record of the pack
    # it was given, and the instance id that says which installation created
    # it. A plant started any other way does not know, and /health then
    # reports no instance id - which is the honest answer, because nothing
    # can corroborate ownership of a plant nobody can locate.
    env[identity.DATA_DIR_SETTING] = str(data_dir(root))
    if speed is not None:
        env["MES_SIM_SPEED"] = str(speed)
    if not env.get("MES_SECRET_KEY"):
        # Stable per-plant key, so a restart does not sign everyone out and so
        # no plant can ever accept another plant's session tokens. Named so a
        # grep shows intent: a pack holds no secret, and a plant that wants a
        # real key names the variable it lives in.
        env["MES_SECRET_KEY"] = identity.lab_secret_key(name)
    return env


def fsmes_bin() -> str:
    """The fsmes console script belonging to the interpreter running this.

    Beside the interpreter first, and never by assuming the directory is
    called `bin` or the file has no extension: a venv on Windows puts the
    console script in `Scripts\\fsmes.exe`, which `Path(sys.executable).parent
    / "fsmes"` does not name and `.exists()` therefore denied - so every
    plant started on Windows fell through to `shutil.which`, and found
    nothing at all unless the venv happened to be on PATH. Windows is a
    first-class target; a plant PC that installed the package and never
    activated the venv is the normal case there, not an edge one.
    """
    beside = Path(sys.executable).parent
    names = ["fsmes.exe", "fsmes"] if sys.platform == "win32" else ["fsmes"]
    for where in (beside, beside / "Scripts", beside / "bin"):
        for name in names:
            candidate = where / name
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("fsmes")
    if not found:
        raise FileNotFoundError(
            "Cannot find the fsmes executable. Install the package first:\n"
            "    uv pip install -e '.[dev]'"
        )
    return found


def fsmes_command() -> list[str]:
    """How to run fsmes as a child process, as a list to put a verb after.

    `fsmes_bin` above finds the console script, and on the machine this was
    written for there is none to find. A plain `py -m pip install
    factorysemantics-mes` on Windows puts `fsmes.exe` in a user Scripts
    folder that is not on PATH - that is why `pip` itself had to be reached
    as `py -m pip` - and nothing beside `sys.executable` is a file. The first
    person to install 0.3.0 on Windows met that on 2026-10-13 at 07:18.

    So there is a second way to run this package: `python -m fsmes`, which
    `__main__.py` sends to the same `cli.run`. A plant started that way has
    to be able to start its own processes, and this is how - the interpreter
    that is running us can always be named, PATH or no PATH. The console
    script is still preferred where it exists, because that is what every
    log line and every `ps` output has said since 0.1.0.
    """
    try:
        return [fsmes_bin()]
    except FileNotFoundError:
        return [sys.executable, "-m", "fsmes"]


# How a plant's processes are detached from the terminal that started them.
# On POSIX that is `setsid()`, which `start_new_session` asks for. On Windows
# there is no such call and `start_new_session` is accepted and silently
# ignored, so until 2026-10-12 every child of `fsmes plant <name> start` there
# stayed in the launching console's process group - and a Ctrl-C in that
# console, or closing the window, took the whole plant down with it. A new
# process group is the nearest honest equivalent: the console's control
# events no longer reach the plant.
def _detached() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _ask_to_stop(pid: int) -> None:
    """Ask this process to finish, by the nearest thing the platform has.

    POSIX gets SIGTERM, which uvicorn, the OPC server and the log sink all
    handle. Windows has no SIGTERM to deliver: `os.kill` there calls
    `TerminateProcess`, which is immediate and cannot be handled, so a plant
    stopped on Windows loses whatever its processes had buffered. That is the
    honest state of it rather than a quiet pretence - there is no portable way
    to ask a process in *another* console's group to shut down politely, and
    `GenerateConsoleCtrlEvent` only reaches the caller's own console.
    """
    os.kill(pid, signal.SIGTERM)


def _force_stop(pid: int) -> None:
    """Take this process away, for one that would not go on being asked.

    `signal.SIGKILL` does not exist on Windows - reading the attribute raises
    `AttributeError` - so `stop` used to crash there the moment a process
    outlived the ten-second deadline, leaving the pid file behind and the
    plant half down. On Windows the first ask was already a `TerminateProcess`
    and there is nothing stronger to escalate to, so this repeats it: a
    process that is gone raises, and the caller suppresses that.
    """
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def pid_file(root: Path, name: str) -> Path:
    return data_dir(root) / f"{name}.pids"


def _alive_on_windows(pid: int) -> bool:
    """Ask Windows whether this process is running, without touching it.

    `OpenProcess` with `PROCESS_QUERY_LIMITED_INFORMATION` is the least
    authority that can answer, and it fails for a pid that is gone *and* for
    one this account may not look at - which is the same answer POSIX gives
    below, where a process another user owns raises `PermissionError` and is
    reported as not ours to manage.

    One honest limitation: a process that exited with code 259 is
    indistinguishable from a running one, because 259 is `STILL_ACTIVE`. That
    is a Windows API wart, it is a value nothing here ever exits with, and
    reporting a stopped plant as running is the safe direction of the two.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _alive(pid: int) -> bool:
    """Is this process running? **Asked without touching it.**

    `os.kill(pid, 0)` is the POSIX idiom for exactly this question, and it is
    not portable in the way it looks. On Windows `signal.CTRL_C_EVENT` is
    `0`, so `os.kill(pid, 0)` does not probe anything: CPython takes it as a
    console-control signal and calls `GenerateConsoleCtrlEvent`, which sends
    **Ctrl-C to that process's console group**. Every command that only wanted
    to know whether a plant was running - `fsmes plant status`, `fsmes fleet
    list` and `fsmes fleet status` through `supervisor`, and the refusal
    `fsmes fleet stop` prints - would have interrupted the plant it was
    reporting on, and anything else sharing that console with it.

    Windows is a first-class target here: this MES is expected to run on plant
    PCs. Found on 2026-09-14, by a test that wrote its own pid into a pid file
    and watched both Windows CI cells Ctrl-C pytest half way through the run.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            return _alive_on_windows(pid)
        except OSError:
            # Same answer the POSIX branch gives when it cannot ask. A status
            # command that raises because a probe failed is worse than one
            # that reports a plant it could not see as not running.
            return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def pids_in(where: Path, name: str) -> list[int]:
    """The live processes this plant's pid file names, read from the data
    directory itself.

    `running_pids` below asks the same question of a *root*, and works out
    the data directory from the fleet file. Something holding an ownership
    entry already knows the directory and must not re-derive it: the entry
    records where that plant's data actually is, and a second guess is how a
    fleet command ends up reading another plant's pid file.
    """
    f = Path(where) / f"{name}.pids"
    if not f.is_file():
        return []
    pids = [int(x) for x in f.read_text(encoding="utf-8").split() if x.strip().isdigit()]
    return [p for p in pids if _alive(p)]


def running_pids(root: Path, name: str) -> list[int]:
    return pids_in(pid_file(root, name).parent, name)


def dashboard_url(cfg: dict) -> str:
    return f"http://{cfg.get('api_host', '127.0.0.1')}:{cfg['api_port']}/dashboard"


def health_report(cfg: dict, timeout: float = 4.0) -> tuple[str, dict]:
    """Ask the port how it is, and keep what it said.

    `health()` answers the only question most callers have - is it
    answering - and throws the body away. A redundant plant makes the body
    worth reading: it carries the node's name and the roles that node holds,
    which is the difference between "two nodes are up" and "two nodes are up
    and exactly one of them is reading the machines".
    """
    host = cfg.get("api_host", "127.0.0.1")
    url = f"http://{host}:{cfg['api_port']}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return f"HTTP {r.status}", {}
            try:
                return "healthy", json.loads(r.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError, OSError):
                # Answering is the fact that matters; a body we cannot read
                # is a thinner answer, not a sick plant.
                return "healthy", {}
    except urllib.error.URLError:
        return "not answering yet", {}
    except OSError:
        return "not answering yet", {}


def health(cfg: dict, timeout: float = 4.0) -> str:
    return health_report(cfg, timeout)[0]


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def init(name: str, cfg: dict, root: Path, echo=print) -> None:
    """Create this plant's schema and apply its pack.

    The seeding step used to be `cfg["init"]`: a Python file the registry
    named, run as a subprocess with the plant's environment. A pack carries no
    code (decision 0022), so what runs now is `fsmes pack apply`, which checks
    the pack first and seeds the data the pack carries. A plant whose master
    data is generated - the two scale labs - carries none, says so, and runs
    its own generator itself.
    """
    env = plant_env(name, cfg, root)
    mes = fsmes_command()

    echo(f"  {name}: applying {cfg['pack']}...")
    applied = subprocess.run([*mes, "pack", "apply", str(cfg["pack"])], cwd=root, env=env,
                             capture_output=True, text=True)
    for line in (applied.stdout or "").splitlines():
        echo(f"      {line}")
    if applied.returncode != 0:
        echo((applied.stderr or "").strip())
        raise SystemExit(f"{name}: `fsmes pack apply` refused (exit {applied.returncode})")

    ensure_accounts(root, env, cfg, echo)


def ensure_accounts(root: Path, env: dict[str, str], cfg: dict, echo=print) -> None:
    """The plant's accounts: the pack's `[[accounts]]` when it declares them,
    the lab list otherwise. A pack's account takes its password from the
    environment variable it names; an unset one refuses rather than creating
    an account with an empty password. Existing accounts are left alone."""
    declared = cfg.get("accounts")
    if not declared:
        ensure_lab_users(root, env, echo)
        return
    mes = fsmes_command()
    for account in declared:
        var = account["password_env"]
        password = env.get(var) or os.environ.get(var)
        if not password:
            raise SystemExit(f"account {account['code']}: {var} is not set; refusing to create it without a password")
        made = subprocess.run([*mes, "add-user", account["code"], account["name"], password,
                               "--role", account["role"]], cwd=root, env=env, capture_output=True, text=True)
        if made.returncode == 0:
            echo(f"      account {account['code']} ({account['role']})")


def ensure_lab_users(root: Path, env: dict[str, str], echo=print) -> None:
    """Create any lab account that does not exist yet. Existing ones are left alone."""
    mes = fsmes_command()
    for code, full, password, role in LAB_USERS:
        made = subprocess.run([*mes, "add-user", code, full, password, "--role", role],
                              cwd=root, env=env, capture_output=True, text=True)
        if made.returncode == 0:
            echo(f"      account {code} ({role})")


def _answers(cfg: dict, timeout: float = 1.0) -> bool:
    """Is something listening on the plant's API port?"""
    try:
        with socket.create_connection((cfg.get("api_host", "127.0.0.1"), int(cfg["api_port"])), timeout=timeout):
            return True
    except OSError:
        return False


def _run_init_db(root: Path, env: dict, echo) -> None:
    """`fsmes init-db` for one plant, with what it said passed through.

    It was discarded until 2026-09-10, which was harmless while init-db only
    ever printed "Database schema is up to date". It now says what it did to
    a database that carries no Alembic stamp - which release-era database it
    was recognised as, and what ran afterwards - and that belongs in the
    receipt rather than in a pipe to nowhere.
    """
    done = subprocess.run([*fsmes_command(), "init-db"], cwd=root, env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in done.stdout.splitlines():
        if line.strip():
            echo(f"      {line.strip()}")


def migrate(name: str, cfg: dict, root: Path, echo=print, upgrade=None) -> dict:
    """Bring one plant's database to the current schema, with a backup and a receipt.

    A merged schema change is only half done until every plant's file has
    it: the ORM would otherwise query a column the database does not have,
    and the plant is fine right up to the moment it restarts. Twice this was
    done by hand and logged as a trap; now it is a command. Back up, count
    rows, upgrade, count again, say what moved. A database already at head
    is reported as such and its backup discarded. Lab accounts are ensured
    afterwards, so an account added to LAB_USERS reaches plants initialised
    before it existed.

    Refuses a plant that is answering on its port: migrating a live SQLite
    file under a running process is how a plant ends up half-upgraded.
    """
    env = plant_env(name, cfg, root)
    if not env["MES_DATABASE_URL"].startswith("sqlite:///"):
        # A server database keeps its own backups; the migration is the
        # same command, run without the file-copy ceremony.
        for _ in range(30):
            if not _answers(cfg):
                break
            time.sleep(0.5)
        else:
            echo(f"  {name}: still answering on :{cfg['api_port']} - stop it first, then migrate")
            return {"plant": name, "migrated": False, "reason": "running"}
        _run_init_db(root, env, echo)
        ensure_accounts(root, env, cfg, echo)
        # Printed from the URL the *pack* wrote, which never holds a
        # password - not from the resolved one with `@` sliced off it, which
        # was a redaction by coincidence and one edit away from not being one.
        echo(f"  {name}: migrated {storage.redacted(str(cfg['database_url']))} to head")
        return {"plant": name, "migrated": True, "backup": None}
    db = Path(env["MES_DATABASE_URL"].removeprefix("sqlite:///"))
    if not db.exists():
        echo(f"  {name}: no database at {db} - run `fsmes plant {name} init` first")
        return {"plant": name, "migrated": False, "reason": "no database"}
    # A plant just stopped keeps its port for a few seconds while the
    # supervisor's children exit. Wait for it rather than refusing at once -
    # `stop; migrate` in a script is the whole point of the command.
    for _ in range(30):
        if not _answers(cfg):
            break
        time.sleep(0.5)
    else:
        echo(f"  {name}: still answering on :{cfg['api_port']} after 15 s - stop it first "
             f"(systemctl --user stop fsmes-plant@{name}), migrate, then start")
        return {"plant": name, "migrated": False, "reason": "running"}

    def counts() -> dict[str, int]:
        with sqlite3.connect(db) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}

    def revision() -> str | None:
        with sqlite3.connect(db) as conn:
            try:
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            except sqlite3.OperationalError:
                return None
        return row[0] if row else None

    backup = db.with_name(f"{db.name}.pre-migrate-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(db, backup)
    before, rev_before = counts(), revision()

    if upgrade is None:
        def upgrade() -> None:
            _run_init_db(root, env, echo)
    upgrade()

    after, rev_after = counts(), revision()
    changed = {t: (before.get(t), after.get(t))
               for t in sorted(set(before) | set(after)) if before.get(t) != after.get(t)}
    receipt = {"plant": name, "migrated": rev_before != rev_after, "revision_before": rev_before,
               "revision_after": rev_after, "backup": None, "rows_changed": changed}
    if rev_before == rev_after:
        backup.unlink()
        echo(f"  {name}: already at head ({rev_after}); nothing to do")
    else:
        receipt["backup"] = str(backup)
        # A database made by a wheel that shipped no migrations has no stamp
        # at all, and "None -> a3f6c81d09e2" reads like a bug rather than the
        # recognition it is. `fsmes init-db` has already printed what it
        # recognised the database as; this says which case this was.
        was = "unstamped (made before the migrations shipped)" if rev_before is None else rev_before
        echo(f"  {name}: {was} -> {rev_after}; backup {backup.name}")
        for table, (b, a) in changed.items():
            echo(f"      {table}: {b} -> {a}")
        if not changed:
            echo("      row counts unchanged in every table")
    ensure_accounts(root, env, cfg, echo)
    return receipt


def log_dir(name: str, node: str | None = None) -> str:
    """Where one plant's processes write, relative to the repository root.

    A node of its own gets a directory of its own: two nodes of one plant on
    one box would otherwise interleave two readers' lines in one file, and the
    first question a person asks of a failover is which node said what.
    """
    return f"logs/{name}/{node}" if node else f"logs/{name}"


def node_list(cfg: dict) -> list[dict]:
    """The nodes this plant's pack names, each with its own id and port.

    Empty for every plant that names none, which is almost all of them: one
    plant, one address, exactly as before. A pack that names nodes is saying
    "this plant runs as these processes on this box", and `fsmes fleet
    start/stop/status` then act on every one of them instead of on one.

    A node's id and port are facts about a box rather than about the plant -
    which is why `fsmes plant <name> run --node a --api-port 8111` takes them
    as arguments and a plant on two *boxes* needs nothing here. Naming them in
    the pack is for the other shape: two nodes on one box, which is what a
    person proving a failover on a laptop has, and what the lab has.
    """
    out: list[dict] = []
    for raw in cfg.get("nodes") or []:
        out.append({"id": str(raw["id"]), "api_port": int(raw["api_port"])})
    return out


def keyed(name: str, node: str | None = None) -> str:
    """How one node's own files are named: the plant, and the node when the
    plant has more than one.

    Two nodes of one plant on one box must not share a pid file - stopping the
    plant would stop whichever node wrote it last and leave the other running,
    which is the half-stopped plant a person then cannot start again.
    """
    return f"{name}-{node}" if node else name


def node_cfg(cfg: dict, node: dict) -> dict:
    """This plant's configuration as one of its nodes serves it: its own port.

    Everything else - the pack, the database, the machine layer - is the
    plant's and is shared, which is the whole point of two nodes.
    """
    return {**cfg, "api_port": node["api_port"]}


def redundant(cfg: dict) -> bool:
    """Whether this plant expects more than one node - `[redundancy] enabled`
    in its pack, read from the environment the pack compiled to, because that
    is the same string every one of its processes will read."""
    return str((cfg.get("env") or {}).get("MES_REDUNDANCY", "")).lower() in {"1", "true", "yes", "on"}


def simulates(cfg: dict) -> bool:
    """Whether this plant runs its simulated line - the OPC replay, the agent
    and the floor - or only serves what its database already holds. A public
    demo of a plant that was measured says `simulate = false` in its pack's
    `[serve]` table and costs the machine one API process; nothing it shows
    changes, and nothing short of the pack can bring the line back."""
    return bool(cfg.get("simulate", True))



@dataclass(frozen=True)
class Process:
    """One of the processes a plant is made of: how to start it, what it is
    for in words a person can act on, and the log it writes.

    One table, read twice. `start` below starts these in order; `processes_in`
    reads the same roles back when something wants to know what is still
    alive. Before 2026-10-14 the roles existed only in the order of the pids
    in the pid file, so the most a status command could say was "2 of 5
    processes are still alive (7964, 3940)" - which is what the first Windows
    user was told, and is not a sentence anybody can do anything with.
    """

    args: tuple[str, ...]
    role: str
    log: str
    settle: float = 0.0


# The log sink is not in the table below because it is started differently -
# it owns the file the others write down a pipe to - but it is one of the
# plant's processes and is named here so a person reading `status` is not left
# wondering what the fifth pid was.
SINK = Process((), "the log sink", "sink.log")
POST_BOOT = Process((), "the scenario's post-boot script", "plant.log")


def processes(cfg: dict) -> list[Process]:
    """What this plant's processes are, in the order they are started.

    Order matters: the OPC server must be listening before the agent
    subscribes. The agent retries forever, but starting it first only buys a
    confusing burst of connection errors in the log. The floor signs in over
    HTTP, so it waits for the API rather than racing it.
    """
    opc = [
        Process(("run-opc-sim", "--replay"), "the OPC server the machines answer on",
                "opc-replay.jsonl", settle=2.5),
        Process(("run-opc-agent",), "the reader that writes the machines into the MES",
                "opc-agent.jsonl"),
    ]
    api = Process(("run-api",), "the dashboard and the API", "api.jsonl")
    floor = Process(("run-operations",), "the simulated floor", "operations.jsonl")
    if not simulates(cfg):
        return [api]
    if redundant(cfg):
        # The OPC server is the machine layer, and the machine layer is not a
        # node: there is one of it, shared, like the database. A simulated one
        # started by a node would die with that node and take the counters
        # with it, and the reader on the surviving node would see the replay
        # restart from the beginning - which reads as a PLC reset, so nothing
        # would be booked. A redundant plant's nodes are pointed at one
        # endpoint, which is a real PLC or a replay somebody started once.
        return [opc[1], api, floor]
    return [*opc, api, floor]


#: The shared machine layer's own key, written beside the nodes' but never one
#: of them: it holds no lease, serves no browser, and there is one of it per
#: plant rather than one per node.
MACHINES = "machines"


def machine_layer(cfg: dict) -> list[Process]:
    """The processes that belong to the plant rather than to one node of it.

    One of them, and only for a *simulated* plant whose pack names nodes: the
    replay that plays this plant's line. `processes` above leaves it out of
    every node on purpose - a machine layer started by a node dies with that
    node, and the surviving reader then sees the replay restart from the
    beginning, which reads as a PLC reset and books nothing. Until 0.4.0 that
    meant nobody started it at all: after the four commands of
    `docs/operate/redundancy.md` the console said *6 of 6 disconnected* and
    nothing was booked. So the plant starts it, once, which is what a real
    plant's machine layer already is - one endpoint both nodes are pointed at.

    A plant that says `simulate = false` has none of this. Its machine layer
    is the PLC, nothing here starts it and nothing here stops it.
    """
    if not (simulates(cfg) and redundant(cfg) and node_list(cfg)):
        return []
    return [Process(("run-opc-sim", "--replay"),
                    "the OPC server this plant's machines answer on",
                    "opc-replay.jsonl", settle=2.5)]


def endpoint(cfg: dict) -> str:
    """Where this plant's machines answer, as the pack wrote it."""
    return str((cfg.get("env") or {}).get("MES_OPC_ENDPOINT", ""))


def answers(host: str, port: int, *, timeout: float = 0.6) -> bool:
    """Is something already listening there?

    The one port question in this product, and every caller asks it through
    here: `fsmes.firstrun.answers` before a start, `fsmes.doctor` when it
    writes down what was listening, `machines_answering` below, and
    `ports_are_back` on the way out of a stop.

    A connection, not a bind: the question is whether this port is somebody
    else's, and a bind test on Windows can succeed against a socket that is
    about to refuse us anyway - which is also what lets this see a socket
    that is still closing. A host of `0.0.0.0` is asked on loopback, because
    that is where a plant on this box is reached.
    """
    where = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        try:
            return probe.connect_ex((where, port)) == 0
        except OSError:
            return False


def opc_address(cfg: dict) -> tuple[str, int] | None:
    """The host and port in this plant's OPC endpoint, as something connects
    to them.

    `None` when the pack states no endpoint, when it states one with no port,
    or when it states one that does not parse: every caller here treats *we
    could not work out an address* as a reason to leave the machine layer
    alone, never as a port to act on.

    A server bound to every interface is reached on loopback; the endpoint a
    pack writes for `docker compose` says 0.0.0.0, which is not an address
    anything connects to.
    """
    url = endpoint(cfg)
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if not port:
        return None
    return ("127.0.0.1" if host in (None, "", "0.0.0.0") else host, int(port))


def machines_answering(cfg: dict, timeout: float = 0.5) -> bool:
    """Is something already listening where this plant's machines answer?

    Asked of the host and port in the endpoint and of nothing else. A second
    replay on a bound port is a process that fails to bind and dies, leaving a
    pid in a pid file that names nothing - so a machine layer somebody else
    already started (by hand, off the page this replaces, or by a run whose
    pid file went missing) is left alone rather than duplicated.

    An endpoint with no port in it, or one that does not parse, is answered
    *no*: refusing to start a machine layer that may not be there is the
    worse of the two mistakes.
    """
    where = opc_address(cfg)
    if where is None:
        return False
    return answers(*where, timeout=timeout)


def procs_file(root: Path, name: str) -> Path:
    """Where the roles of this plant's processes are recorded, beside its pid
    file and written at the same moment."""
    return data_dir(root) / f"{name}.procs"


@dataclass(frozen=True)
class Recorded:
    """One process this plant's pid file names, and whether it is still there."""

    pid: int
    alive: bool
    role: str
    log: str

    @property
    def said(self) -> str:
        """What this process is, in words, for a line a person reads."""
        return self.role or "a process this plant started (its role was not recorded)"


def processes_in(where: Path, name: str, logs: Path | None = None) -> list[Recorded]:
    """Every process this plant's pid file names - alive or gone - and what
    each one is.

    The roles come from the file `start` wrote beside the pid file, and never
    from the position of a pid in the file: a plant started by a release that
    wrote no such file reports its roles as not recorded rather than guessing
    them. Unknown is not zero, and a status line that names the wrong process
    is worse than one that admits it cannot name it.

    `pids_in` beside this answers the narrower question - which of them are
    alive - and is what every ownership decision still uses.
    """
    pid_list = Path(where) / f"{name}.pids"
    if not pid_list.is_file():
        return []
    pids = [int(x) for x in pid_list.read_text(encoding="utf-8").split() if x.strip().isdigit()]

    roles: dict[int, dict] = {}
    recorded = Path(where) / f"{name}.procs"
    if recorded.is_file():
        with contextlib.suppress(OSError, ValueError):
            for row in json.loads(recorded.read_text(encoding="utf-8")):
                roles[int(row["pid"])] = row

    out = []
    for pid in pids:
        row = roles.get(pid, {})
        log = row.get("log", "")
        out.append(Recorded(pid=pid, alive=_alive(pid), role=row.get("role", ""),
                            log=str(logs / log) if (logs and log) else log))
    return out


def start(name: str, cfg: dict, root: Path, speed: float | None = None, echo=print,
          node: str | None = None, api_port: int | None = None) -> None:
    """Start this plant, detached, and record what was started.

    A plant whose pack names nodes is started node by node: one supervisor,
    one pid file and one log directory each, and the roles that must run once
    sorted out between them by the lease in the database rather than by
    whatever order they came up in.
    """
    if node is None and node_list(cfg):
        # The machine layer first, and once: the nodes are readers of it, and
        # a reader that comes up before the machines answer spends its first
        # seconds logging connection errors.
        if machine_layer(cfg):
            start_machines(name, cfg, root, speed, echo=echo)
        for one in node_list(cfg):
            start(name, cfg, root, speed, echo=echo, node=one["id"],
                  api_port=one["api_port"])
        return

    key = keyed(name, node)
    said = f"{name} node {node}" if node else name
    if running_pids(root, key):
        echo(f"  {said} is already running. Stop it first.")
        return

    env = plant_env(name, cfg, root, speed, node=node, api_port=api_port)
    here = root / log_dir(name, node)

    post_boot = cfg["post_boot"] if simulates(cfg) and cfg.get("post_boot") else None
    launched = _launch(key, processes(cfg), env, root, here, post_boot=post_boot)
    serving = {**cfg, "api_port": api_port} if api_port is not None else cfg
    echo(f"  {said} started -> {dashboard_url(serving)} "
         f"(PIDs {', '.join(str(pid) for _spec, pid in launched)})")


def _launch(key: str, specs: list[Process], env: dict[str, str], root: Path, here: Path,
            post_boot: str | None = None) -> list[tuple[Process, int]]:
    """Start one group of a plant's processes, detached, behind one log sink,
    and record what was started.

    One group is a node of a plant, or the plant's own shared machine layer,
    or a plant that has neither - each with its own pid file, its own roles
    file and its own log directory, which is what `stop` and `status` read
    back. Nothing here decides *which* processes: that is `processes` and
    `machine_layer` above.
    """
    mes = fsmes_command()
    here.mkdir(parents=True, exist_ok=True)
    log_path = here / "plant.log"

    # THE LOG HAS A CEILING. A plant is a thing that is left running, and a
    # lab plant filled 1.1 GB in six hours on a box with 22 GB free. One
    # process owns the file and caps it at 200 MB across four; the plant's
    # processes write down its pipe. `fsmes.fleet.logsink` says why it has
    # to be a separate process rather than a rotating handler.
    #
    # Deliberately not a context manager: this handle is inherited by the
    # detached children and must outlive this function.
    #
    # THE SINK GETS A FILE, NOT OUR CONSOLE. It outlives this command by
    # design, so anything it inherits it holds for the life of the plant -
    # and a caller that reads this command's output (`subprocess.run(...,
    # capture_output=True)`, a shell pipeline) then waits for a pipe that
    # only closes when the plant stops. `fsmes demo bottling` hung for ten
    # minutes on exactly that, 2026-10-12, with the plant up and answering
    # the whole time. Its own words are few and go beside the plant log, so
    # a sink that cannot open that log still says why somewhere.
    sink_log = (here / "sink.log").open("a", encoding="utf-8")
    with contextlib.closing(sink_log):
        sink = subprocess.Popen([*mes, "run-log-sink", str(log_path)], cwd=root, env=env,
                                stdin=subprocess.PIPE, stdout=sink_log,
                                stderr=subprocess.STDOUT, **_detached())
    log = sink.stdin

    launched: list[tuple[Process, int]] = []
    for spec in specs:
        child = subprocess.Popen([*mes, *spec.args], cwd=root, env=env,
                                 stdout=log, stderr=subprocess.STDOUT, **_detached())
        launched.append((spec, child.pid))
        if spec.settle:
            time.sleep(spec.settle)

    # A scenario's own post-boot script: what the seeding cannot do because
    # the API did not exist yet, plus any floor activity the scenario scripts
    # beyond run-operations. **No pack can ask for this** - decision 0022 says
    # a pack carries no code, and `fsmes pack check` refuses the key. It is
    # here for a lab tool that composes a configuration in code, which is
    # code, gets reviewed, and is where the two scale labs put their breadth
    # seeding.
    if post_boot:
        child = subprocess.Popen([sys.executable, post_boot], cwd=root, env=env,
                                 stdout=log, stderr=subprocess.STDOUT, **_detached())
        launched.append((POST_BOOT, child.pid))

    # Let go of our own end of the pipe, so the sink sees EOF when the last
    # of the plant's processes has gone and can finish writing and exit.
    log.close()

    # The sink is last in the file: `stop` reaches it after the processes
    # that write to it, which is the order that loses nothing.
    launched.append((SINK, sink.pid))

    pids = [pid for _, pid in launched]
    pid_file(root, key).write_text("\n".join(str(p) for p in pids), encoding="utf-8")
    # What each of those pids is, written in the same breath as the pid file.
    # A pid on its own is what the first Windows user had on his screen.
    procs_file(root, key).write_text(
        json.dumps([{"pid": pid, "role": spec.role, "log": spec.log} for spec, pid in launched],
                   indent=1),
        encoding="utf-8")
    return launched


def start_machines(name: str, cfg: dict, root: Path, speed: float | None = None,
                   echo=print) -> None:
    """Start this plant's shared machine layer, once.

    Idempotent two ways, because both happen on a box where a node has been
    killed on purpose: a pid file of ours that still names live processes
    (a second `fsmes fleet start`), and something answering on the plant's
    OPC endpoint with no pid file of ours beside it. Either way the machines
    that are already playing are left playing - restarting them would restart
    the replay, and a replay that restarts reads as a PLC reset.
    """
    key = keyed(name, MACHINES)
    if running_pids(root, key):
        echo(f"  {name}: the machines are already playing.")
        return
    if machines_answering(cfg):
        echo(f"  {name}: something already answers on {endpoint(cfg)}, so the "
             "machines are left as they are.")
        return
    # The machine layer is not a node and is not given a node's name: it takes
    # no lease and serves no browser. It gets a log directory of its own for
    # the same reason each node does - one file, one writer.
    env = plant_env(name, cfg, root, speed)
    env["MES_LOG_DIR"] = log_dir(name, MACHINES)
    launched = _launch(key, machine_layer(cfg), env, root, root / log_dir(name, MACHINES))
    echo(f"  {name}: the machines started on {endpoint(cfg)} "
         f"(PIDs {', '.join(str(pid) for _spec, pid in launched)})")


#: How long `stop` will hold the command while a port it was listening on
#: finishes closing. A grace period, not a measurement: nothing asserts a
#: clock on it, and it is four times the two seconds every Windows cell
#: measured on 2026-10-21 (PR #184). A port still there when it passes is
#: said out loud rather than waited on for ever.
PORT_GRACE = 8.0


def listening_on(cfg: dict, node: str | None = None) -> list[tuple[str, str, int]]:
    """The ports the processes of this plant - or of this one node of it -
    listen on, and what each is for.

    Read from the configuration the pack compiled to, which is the only place
    a plant's ports are stated (house rule 4). Only ever ports *this*
    installation's processes bound: a plant that says `simulate = false`
    answers its API and nothing else, because its machine layer is a PLC that
    nothing here started and nothing here will wait for.

    `node=MACHINES` asks about the shared machine layer rather than about a
    node: the replay's endpoint, and only when this plant has one of its own.
    """
    if node == MACHINES:
        if not machine_layer(cfg):
            return []
        where = opc_address(cfg)
        return [("the OPC server", *where)] if where else []

    named = [one for one in node_list(cfg) if one["id"] == node]
    serving = node_cfg(cfg, named[0]) if named else cfg
    out: list[tuple[str, str, int]] = []
    if serving.get("api_port"):
        out.append(("the dashboard and the API",
                    str(serving.get("api_host") or "127.0.0.1"), int(serving["api_port"])))
    if any("run-opc-sim" in spec.args for spec in processes(serving)):
        where = opc_address(serving)
        if where:
            out.append(("the OPC server", *where))
    return out


def ports_are_back(said: str, cfg: dict, echo=print, node: str | None = None,
                   ceiling: float = PORT_GRACE) -> None:
    """Hold the command until what this plant was listening on has gone quiet.

    `stop` returning is not the same thing as the port being gone. On Windows
    a listening socket can outlive the process that owned it by about two
    seconds - every Windows cell of PR #184 measured that on 2026-10-21, and
    every Linux cell measured zero - and a start inside those two seconds
    refuses: `firstrun.in_the_way` finds the port in use and says *stop that
    program*, about a plant the person has just stopped and a process that no
    longer exists. So the wait belongs here, on the way down, where what is
    holding the port is known to have been ours. A start that waited for a
    port to close would be a start that talks itself past somebody else's
    program.

    Nothing is printed when there was nothing to wait for, which is every
    Linux stop and most Windows ones: the cost there is one connect per port.
    """
    for what, host, port in listening_on(cfg, node):
        if not answers(host, port):
            continue
        started, free = time.monotonic(), False
        while time.monotonic() - started < ceiling:
            time.sleep(0.1)
            if not answers(host, port):
                free = True
                break
        waited = time.monotonic() - started
        if free:
            echo(f"  {said}: {host}:{port} ({what}) was still closing; "
                 f"it is free after {waited:.1f} s.")
        else:
            echo(f"  {said}: {host}:{port} ({what}) is still closing after "
                 f"{waited:.0f} s. If starting it again refuses that port, nothing is "
                 "holding it but the socket: give it a moment and start it again.")


def stop(name: str, cfg: dict, root: Path, echo=print, node: str | None = None) -> None:
    """Stop this plant. Every node of it, when its pack names more than one.

    `node` stops one node and leaves the rest of the plant running. It is a
    graceful stop - the node is asked, then told - so it is not the proof
    that a plant survives a box dying; that proof is a `kill`, and
    `docs/operate/redundancy.md` says so.
    """
    if node is None and node_list(cfg):
        for one in node_list(cfg):
            stop(name, cfg, root, echo=echo, node=one["id"])
        # The machine layer last: the nodes read it, and a reader whose
        # machines go away first spends its last seconds logging that.
        if machine_layer(cfg):
            stop_machines(name, cfg, root, echo=echo)
        return

    key = keyed(name, node)
    said = f"{name} node {node}" if node else name
    pids = running_pids(root, key)
    if not pids:
        echo(f"  {said} is not running.")
        _forget(root, key)
        return

    _end(pids)
    _forget(root, key)
    echo(f"  {said} stopped ({len(pids)} process(es)).")
    # Not before the line above: the processes are gone either way, and a
    # person watching should read *stopped* first and the wait, if there is
    # one, after it.
    ports_are_back(said, cfg, echo=echo, node=node)


def stop_machines(name: str, cfg: dict, root: Path, echo=print) -> None:
    """Stop this plant's shared machine layer.

    Only what this installation started: a machine layer somebody else is
    running - a real PLC, a replay started by hand, the one `docker compose`
    owns - has no pid file of ours and is left exactly as it is.
    """
    key = keyed(name, MACHINES)
    pids = running_pids(root, key)
    if not pids:
        _forget(root, key)
        echo(f"  {name}: the machines were not started by this plant, so they are "
             "left as they are.")
        return
    _end(pids)
    _forget(root, key)
    echo(f"  {name}: the machines stopped ({len(pids)} process(es)).")
    ports_are_back(name, cfg, echo=echo, node=MACHINES)


def _end(pids: list[int]) -> None:
    """Ask every one of these to stop, wait, then tell the ones that did not."""
    for pid in pids:
        with contextlib.suppress(OSError):
            _ask_to_stop(pid)

    deadline = time.time() + 10
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.2)
    for pid in (p for p in pids if _alive(p)):
        with contextlib.suppress(OSError):
            _force_stop(pid)


def _forget(root: Path, key: str) -> None:
    """Drop the two files that named these processes."""
    pid_file(root, key).unlink(missing_ok=True)
    procs_file(root, key).unlink(missing_ok=True)


def supervisor(root: Path, name: str) -> str:
    """Who is running this plant: our pidfile, systemd, or nobody we can see.

    A plant started by `fsmes plant start` leaves a pidfile; one started by
    systemd does not. Reporting only the pidfile made a healthy systemd plant
    read as "unreachable, 0 proc" - a status command that lies is worse than
    no status command, so ask both.
    """
    pids = running_pids(root, name)
    if pids:
        return f"pidfile ({len(pids)} proc)"
    systemctl = shutil.which("systemctl")
    if systemctl:
        unit = f"fsmes-plant@{name}"
        try:
            done = subprocess.run([systemctl, "--user", "is-active", unit],
                                  capture_output=True, text=True, timeout=5)
            if done.stdout.strip() == "active":
                return "systemd"
        except (OSError, subprocess.SubprocessError):
            pass
    return "-"


def holding(roles) -> str:
    """What one node is responsible for, in words.

    Three states and not two. "nothing yet" is a standby that has not taken
    over, which is healthy; "unknown" is the lease table not answering, which
    is not the same thing as a node holding nothing.
    """
    if roles is None:
        return "unknown"
    return ", ".join(roles) if roles else "nothing yet"


def node_status(name: str, cfg: dict, root: Path, node: dict) -> dict:
    """One node of a plant: whether it answered, and what it holds."""
    serving = node_cfg(cfg, node)
    state, body = health_report(serving)
    said = body.get("node") or {}
    return {"id": node["id"], "api_port": node["api_port"],
            "base": f"http://{serving.get('api_host', '127.0.0.1')}:{node['api_port']}",
            "health": state, "answered": state == "healthy",
            "supervisor": supervisor(root, keyed(name, node["id"])),
            # What the node calls itself, which is not necessarily what the
            # pack calls it: a node started by hand with a different --node
            # answers with its own name, and a status command that printed
            # the pack's name over it would hide exactly that mistake.
            "says": said.get("name", ""), "roles": said.get("roles")}


def status(name: str, cfg: dict, root: Path, echo=print) -> dict:
    # Health is asked of the port directly and never gated on knowing who
    # started the plant - the dashboard answering is the fact that matters.
    state, body = health_report(cfg)
    by = supervisor(root, name)
    echo(f"  {name:<12} {state:<18} {by:<16} {dashboard_url(cfg)}")
    echo(f"               {cfg['label']}")
    node = body.get("node") or {}
    roles = node.get("roles")
    nodes = node_list(cfg)
    if nodes:
        # A plant whose pack names its nodes is asked node by node, and the
        # count is stated: two nodes of which one answered is a plant running
        # on one node, and nothing on this screen should let that look like
        # two. The roles are each held by exactly one of them, so the lines
        # read together or not at all.
        asked = [node_status(name, cfg, root, one) for one in nodes]
        answered = sum(1 for one in asked if one["answered"])
        echo(f"               {len(asked)} nodes, {answered} answered")
        for one in asked:
            echo(f"                 node {one['id']:<6} {one['health']:<18} "
                 f"{one['supervisor']:<16} holding {holding(one['roles'])}")
        return {"plant": name, "supervisor": by, "health": state,
                "api_port": cfg["api_port"], "pack": str(cfg.get("pack", "")),
                "node": node.get("name", ""), "roles": roles, "nodes": asked}
    if node.get("redundancy"):
        # One line a person can read during a failover: which node answered,
        # and what that node is responsible for right now.
        echo(f"               node {node.get('name', '?')}, holding {holding(roles)}")
    return {"plant": name, "supervisor": by, "health": state,
            "api_port": cfg["api_port"], "pack": str(cfg.get("pack", "")),
            "node": node.get("name", ""), "roles": roles}


def run(name: str, cfg: dict, root: Path, speed: float | None = None, echo=print,
        node: str | None = None, api_port: int | None = None) -> int:
    """Foreground supervisor: what the systemd unit executes.

    Owns the three child processes and takes them down with it, so `systemctl
    stop` leaves nothing orphaned holding a port.

    `node` names this node of the plant and `api_port` overrides the pack's
    port, so two nodes of one plant can be run on one box. Neither changes
    anything about a plant that runs on one node.
    """
    if api_port is not None:
        cfg = {**cfg, "api_port": api_port}
    env = plant_env(name, cfg, root, speed, node=node, api_port=api_port)
    mes = fsmes_command()
    (root / log_dir(name, node)).mkdir(parents=True, exist_ok=True)

    children: list[subprocess.Popen] = []

    def shutdown(signum, frame):
        for c in children:
            if c.poll() is None:
                c.terminate()
        deadline = time.time() + 10
        for c in children:
            remaining = max(0.0, deadline - time.time())
            try:
                c.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                c.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if simulates(cfg) and not redundant(cfg):
        children.append(subprocess.Popen([*mes, "run-opc-sim", "--replay"], cwd=root, env=env))
        time.sleep(2.5)
    if simulates(cfg):
        children.append(subprocess.Popen([*mes, "run-opc-agent"], cwd=root, env=env))
    children.append(subprocess.Popen([*mes, "run-api"], cwd=root, env=env))
    if simulates(cfg):
        children.append(subprocess.Popen([*mes, "run-operations"], cwd=root, env=env))
        if cfg.get("post_boot"):
            children.append(subprocess.Popen([sys.executable, cfg["post_boot"]], cwd=root, env=env))
    said = f"{name} running -> {dashboard_url(cfg)}"
    if node:
        said += f" (node {node})"
    if not simulates(cfg):
        said += " (serving; nothing simulates)"
    elif redundant(cfg):
        said += f" (reading {env.get('MES_OPC_ENDPOINT', 'the endpoint this plant was given')})"
    echo(said)

    # If any child dies the plant is broken; take the rest down so systemd
    # restarts a whole plant rather than leaving a half-dead one answering.
    while True:
        for c in children:
            code = c.poll()
            if code is not None:
                echo(f"{name}: a component exited ({code}) — stopping the plant")
                shutdown(signal.SIGTERM, None)
        time.sleep(1.0)
