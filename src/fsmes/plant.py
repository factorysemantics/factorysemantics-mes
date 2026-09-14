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
import urllib.request
from datetime import datetime
from pathlib import Path

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
    ("ADMIN", "Lab Admin", "admin", "admin"),
    # The MCP server's own sign-in. Admin because master data (routings)
    # requires it; everything it does is audited under this name, so "what did
    # the agent do to my plant" is one audit query. The capability-role work
    # will narrow this to exactly what an agent deployment grants.
    ("AGENT", "Plant Agent", os.environ.get("FSMES_AGENT_PASSWORD", LAB_ONLY_AGENT_PASSWORD), "agent"),
]


def find_root(start: Path | None = None) -> Path:
    """Walk up until the registry is found; fall back to the current directory."""
    here = (start or Path.cwd()).resolve()
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
    url = os.path.expandvars(url)
    secret = cfg.get("database_password_file")
    if secret and "@" in url and ":" not in url.split("://", 1)[1].split("@", 1)[0]:
        password = Path(os.path.expanduser(secret)).read_text(encoding="utf-8").strip()
        scheme, rest = url.split("://", 1)
        user, host = rest.split("@", 1)
        url = f"{scheme}://{user}:{password}@{host}"
    return url


def plant_env(name: str, cfg: dict, root: Path, speed: float | None = None) -> dict[str, str]:
    """The complete MES_* contract for one plant.

    Almost all of it is the pack's, compiled once when the fleet was read.
    Three things are added here because they are facts about *running* this
    plant on this machine rather than facts about the plant: where its
    database file goes, where its log goes, and a signing key when nothing
    supplied one.
    """
    env = dict(os.environ)
    env.update(cfg.get("env") or {})
    env["MES_PLANT_NAME"] = name
    env["MES_DATABASE_URL"] = database_url(name, cfg, root)
    env["MES_LOG_DIR"] = f"logs/{name}"
    if speed is not None:
        env["MES_SIM_SPEED"] = str(speed)
    if not env.get("MES_SECRET_KEY"):
        # Stable per-plant key, so a restart does not sign everyone out and so
        # no plant can ever accept another plant's session tokens. Named so a
        # grep shows intent: a pack holds no secret, and a plant that wants a
        # real key names the variable it lives in.
        env["MES_SECRET_KEY"] = f"lab-{name}-do-not-use-in-production"
    return env


def fsmes_bin() -> str:
    """The fsmes console script belonging to the interpreter running this."""
    candidate = Path(sys.executable).parent / "fsmes"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("fsmes")
    if not found:
        raise FileNotFoundError(
            "Cannot find the fsmes executable. Install the package first:\n"
            "    uv pip install -e '.[dev]'"
        )
    return found


def pid_file(root: Path, name: str) -> Path:
    return data_dir(root) / f"{name}.pids"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def running_pids(root: Path, name: str) -> list[int]:
    f = pid_file(root, name)
    if not f.is_file():
        return []
    pids = [int(x) for x in f.read_text(encoding="utf-8").split() if x.strip().isdigit()]
    return [p for p in pids if _alive(p)]


def dashboard_url(cfg: dict) -> str:
    return f"http://{cfg.get('api_host', '127.0.0.1')}:{cfg['api_port']}/dashboard"


def health(cfg: dict, timeout: float = 4.0) -> str:
    host = cfg.get("api_host", "127.0.0.1")
    url = f"http://{host}:{cfg['api_port']}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return "healthy" if r.status == 200 else f"HTTP {r.status}"
    except urllib.error.URLError:
        return "not answering yet"
    except OSError:
        return "not answering yet"


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
    mes = fsmes_bin()

    echo(f"  {name}: applying {cfg['pack']}...")
    applied = subprocess.run([mes, "pack", "apply", str(cfg["pack"])], cwd=root, env=env,
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
    mes = fsmes_bin()
    for account in declared:
        var = account["password_env"]
        password = env.get(var) or os.environ.get(var)
        if not password:
            raise SystemExit(f"account {account['code']}: {var} is not set; refusing to create it without a password")
        made = subprocess.run([mes, "add-user", account["code"], account["name"], password,
                               "--role", account["role"]], cwd=root, env=env, capture_output=True, text=True)
        if made.returncode == 0:
            echo(f"      account {account['code']} ({account['role']})")


def ensure_lab_users(root: Path, env: dict[str, str], echo=print) -> None:
    """Create any lab account that does not exist yet. Existing ones are left alone."""
    mes = fsmes_bin()
    for code, full, password, role in LAB_USERS:
        made = subprocess.run([mes, "add-user", code, full, password, "--role", role],
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
    done = subprocess.run([fsmes_bin(), "init-db"], cwd=root, env=env, check=True,
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
        echo(f"  {name}: migrated {env['MES_DATABASE_URL'].split('@')[-1]} to head")
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


def simulates(cfg: dict) -> bool:
    """Whether this plant runs its simulated line - the OPC replay, the agent
    and the floor - or only serves what its database already holds. A public
    demo of a plant that was measured says `simulate = false` in its pack's
    `[serve]` table and costs the machine one API process; nothing it shows
    changes, and nothing short of the pack can bring the line back."""
    return bool(cfg.get("simulate", True))



def start(name: str, cfg: dict, root: Path, speed: float | None = None, echo=print) -> None:
    if running_pids(root, name):
        echo(f"  {name} is already running. Stop it first.")
        return

    env = plant_env(name, cfg, root, speed)
    mes = fsmes_bin()
    (root / "logs" / name).mkdir(parents=True, exist_ok=True)
    # Deliberately not a context manager: this handle is inherited by the
    # three detached children and must outlive this function.
    log = open(root / "logs" / name / "plant.log", "ab")  # noqa: SIM115

    procs = []
    # Order matters: the OPC server must be listening before the agent
    # subscribes. The agent retries forever, but starting it first only buys a
    # confusing burst of connection errors in the log.
    if simulates(cfg):
        procs.append(subprocess.Popen([mes, "run-opc-sim", "--replay"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True))
        time.sleep(2.5)
        procs.append(subprocess.Popen([mes, "run-opc-agent"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True))
    procs.append(subprocess.Popen([mes, "run-api"], cwd=root, env=env,
                                  stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True))
    # The people part of the plant. It signs in over HTTP, so it waits for the
    # API rather than racing it.
    if simulates(cfg):
        procs.append(subprocess.Popen([mes, "run-operations"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True))
        # A scenario's own post-boot script: what the seeding cannot do
        # because the API did not exist yet, plus any floor activity the
        # scenario scripts beyond run-operations. **No pack can ask for
        # this** - decision 0022 says a pack carries no code, and `fsmes pack
        # check` refuses the key. It is here for a lab tool that composes a
        # configuration in code, which is code, gets reviewed, and is where
        # the two scale labs put their breadth seeding.
        if cfg.get("post_boot"):
            procs.append(subprocess.Popen([sys.executable, cfg["post_boot"]], cwd=root, env=env,
                                          stdout=log, stderr=subprocess.STDOUT,
                                          start_new_session=True))

    pid_file(root, name).write_text("\n".join(str(p.pid) for p in procs), encoding="utf-8")
    echo(f"  {name} started -> {dashboard_url(cfg)} "
         f"(PIDs {', '.join(str(p.pid) for p in procs)})")


def stop(name: str, cfg: dict, root: Path, echo=print) -> None:
    pids = running_pids(root, name)
    if not pids:
        echo(f"  {name} is not running.")
        pid_file(root, name).unlink(missing_ok=True)
        return

    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)

    deadline = time.time() + 10
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.2)
    for pid in (p for p in pids if _alive(p)):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)

    pid_file(root, name).unlink(missing_ok=True)
    echo(f"  {name} stopped ({len(pids)} process(es)).")


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


def status(name: str, cfg: dict, root: Path, echo=print) -> dict:
    # Health is asked of the port directly and never gated on knowing who
    # started the plant - the dashboard answering is the fact that matters.
    state = health(cfg)
    by = supervisor(root, name)
    echo(f"  {name:<12} {state:<18} {by:<16} {dashboard_url(cfg)}")
    echo(f"               {cfg['label']}")
    return {"plant": name, "supervisor": by, "health": state,
            "api_port": cfg["api_port"], "pack": str(cfg.get("pack", ""))}


def run(name: str, cfg: dict, root: Path, speed: float | None = None, echo=print) -> int:
    """Foreground supervisor: what the systemd unit executes.

    Owns the three child processes and takes them down with it, so `systemctl
    stop` leaves nothing orphaned holding a port.
    """
    env = plant_env(name, cfg, root, speed)
    mes = fsmes_bin()
    (root / "logs" / name).mkdir(parents=True, exist_ok=True)

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

    if simulates(cfg):
        children.append(subprocess.Popen([mes, "run-opc-sim", "--replay"], cwd=root, env=env))
        time.sleep(2.5)
        children.append(subprocess.Popen([mes, "run-opc-agent"], cwd=root, env=env))
    children.append(subprocess.Popen([mes, "run-api"], cwd=root, env=env))
    if simulates(cfg):
        children.append(subprocess.Popen([mes, "run-operations"], cwd=root, env=env))
        if cfg.get("post_boot"):
            children.append(subprocess.Popen([sys.executable, cfg["post_boot"]], cwd=root, env=env))
    echo(f"{name} running -> {dashboard_url(cfg)}" + ("" if simulates(cfg) else " (serving; nothing simulates)"))

    # If any child dies the plant is broken; take the rest down so systemd
    # restarts a whole plant rather than leaving a half-dead one answering.
    while True:
        for c in children:
            code = c.poll()
            if code is not None:
                echo(f"{name}: a component exited ({code}) — stopping the plant")
                shutdown(signal.SIGTERM, None)
        time.sleep(1.0)
