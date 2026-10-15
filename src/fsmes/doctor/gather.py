"""Everything `fsmes doctor` can see without touching anything.

This module reads. It starts nothing, stops nothing, writes nothing, and
migrates nothing - a person runs `doctor` when something is already wrong,
and a diagnostic that changes the thing it is diagnosing is worse than no
diagnostic at all. Every fact here is either read off this machine (a file,
a process table, a port) or asked of a plant that is already answering.

It is also on a clock. The handoff asks for five seconds on the lab plant,
which rules out anything that waits on a default timeout: every question to
a port or a plant carries its own, short, and silence is an answer.

What it collects, in the order the file prints it:

* the installation - version, Python, OS, how it was installed, the home;
* each plant in that home - its processes alive or gone, its ports, whether
  each answers, shadow mode and the ERP, namespace and inbound modes, and
  the tail of each process log;
* and, for the second half, how long this MES has been watching, what share
  of that window it actually observed, and the figures of any scorecard
  kept under the home.

Nothing here shapes a value. Every string that could carry a customer's word
is handed to `render` as it was read, with the same string handed to
`leak.Seen` - so the file is shaped in exactly one place and the check knows
every value that was in the room.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: One question to one plant. Two of these per plant, and a plant that is
#: not there answers immediately - a plant that is there answers in
#: milliseconds. The budget is for the plant that is half dead: a port
#: nothing listens on, or a host that drops the packet.
ASK_TIMEOUT = 0.8

#: One question to one port. Shorter again, because there are more of them.
PORT_TIMEOUT = 0.4

#: How much of each log the file carries. The handoff's number.
LOG_LINES = 40

#: How many logs, when the plant left no record of its processes and the log
#: directory is all there is to go on. Newest first, and the line above them
#: says how many there were - a stopped plant can have nine logs, and nine
#: tails of forty lines is the whole of a four-hundred-line budget.
LOGS_WITHOUT_A_RECORD = 3


# --------------------------------------------------------------- the pieces


@dataclass(frozen=True)
class Install:
    """This installation, as a support call needs it."""

    version: str
    how: str
    python: str
    interpreter: str
    executable_on_path: str
    invoked_as: str
    os_said: str
    home: Path
    home_exists: bool
    fleet_file: Path
    fleet_exists: bool
    modules: tuple[str, ...]
    skill_folder: bool


@dataclass(frozen=True)
class Process:
    """One process this plant's record names, and its log."""

    pid: int
    role: str
    alive: bool
    log: str
    log_exists: bool
    lines: tuple[str, ...] = ()
    truncated: bool = False
    dropped: int = 0


@dataclass(frozen=True)
class Port:
    """One port this plant's pack asks for, and whether anything is there."""

    what: str
    host: str
    port: int
    answers: bool


@dataclass(frozen=True)
class Shadow:
    """Shadow mode and the three modes beside it, with where each came from."""

    on: bool | None
    source: str
    erp_mode: str | None
    uns_mode: str | None
    inbound_mode: str | None
    closed: int | None = None
    total: int | None = None


@dataclass(frozen=True)
class Coverage:
    """What share of the window this MES actually watched. Aggregate only."""

    machines: int
    watching_since: datetime | None
    until: datetime | None
    window_seconds: float
    observed_seconds: float
    by_cause: dict[str, float] = field(default_factory=dict)
    why_not: str = ""

    @property
    def share(self) -> float | None:
        if self.window_seconds <= 0:
            return None
        return self.observed_seconds / self.window_seconds


@dataclass(frozen=True)
class ScorecardRow:
    """One kept scorecard, as a row in a table by date."""

    date: str
    path: str
    compared: int | None
    agree: int | None
    differ: int | None
    not_stated: int | None
    orders_compared: int | None
    operations_compared: int | None
    #: How many things that scorecard said it could not tell you. A count,
    #: not the sentences: the sentences name order codes.
    cannot_tell: int | None = None


@dataclass(frozen=True)
class Plant:
    """One plant in this home, as this installation can see it."""

    name: str
    label: str
    owned: bool
    answering: bool
    url: str
    why_not_owned: str
    pack: Path | None
    database: str
    processes: tuple[Process, ...]
    ports: tuple[Port, ...]
    shadow: Shadow
    logs_dir: Path
    logs_total: int = 0
    coverage: Coverage | None = None
    scorecards: tuple[ScorecardRow, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def alive(self) -> tuple[Process, ...]:
        return tuple(p for p in self.processes if p.alive)

    @property
    def gone(self) -> tuple[Process, ...]:
        return tuple(p for p in self.processes if not p.alive)

    @property
    def half_dead(self) -> bool:
        return bool(self.alive) and not self.answering


@dataclass(frozen=True)
class Report:
    """Everything one run of `doctor` read."""

    install: Install
    local_clock: str
    utc_clock: str
    plants: tuple[Plant, ...]
    asked_for: str | None
    not_in_this_home: tuple[str, ...]
    seen: object
    trouble: tuple[str, ...] = ()
    #: Every plant the fleet file lists, whether `--plant` narrowed the file
    #: to one of them or not. A person who mistyped a name has to be able to
    #: see the name they meant.
    home_has: tuple[str, ...] = ()

    @property
    def in_shadow(self) -> tuple[Plant, ...]:
        return tuple(p for p in self.plants if p.shadow.on)


# ------------------------------------------------------------- installation


def _how_installed() -> str:
    """`pipx`, `pip`, `uv`, a checkout - and whether it is editable.

    Read off the installed distribution rather than guessed from the path,
    except for pipx, which is a path: pipx installs with pip and leaves
    `INSTALLER` saying `pip`, so the only evidence that pipx did it is the
    venv it did it into.
    """
    from importlib import metadata

    prefix = Path(sys.prefix)
    where = [part.lower() for part in prefix.parts]
    try:
        dist = metadata.distribution("factorysemantics-mes")
    except Exception:
        return ("not installed as a package - this is a source checkout, run with "
                "`python -m fsmes` or an editable install")
    installer = ""
    editable = False
    try:
        installer = (dist.read_text("INSTALLER") or "").strip()
        direct = dist.read_text("direct_url.json")
        if direct:
            editable = bool(json.loads(direct).get("dir_info", {}).get("editable"))
    except Exception:
        pass
    if "pipx" in where:
        said = "pipx"
    elif installer:
        said = installer
    else:
        said = "something that left no installer record"
    if editable:
        said += " (editable, from a checkout)"
    if prefix != Path(sys.base_prefix) and "pipx" not in where:
        said += ", into a virtual environment"
    return said


def _invoked_as() -> str:
    """`fsmes`, or `python -m fsmes`. The difference is the whole of the
    first Windows trip in `run-it-on-your-own-box.md`, and an agent reading
    this file needs to know which of the two this person can type."""
    first = Path(sys.argv[0] or "").name.lower()
    if first in ("__main__.py", "-m", ""):
        return "python -m fsmes"
    if first.startswith("fsmes"):
        return "fsmes"
    return "python -m fsmes"


def installation(home: Path, seen) -> Install:
    """Version, Python, OS, how, where - and nothing that names this machine.

    `platform.node()` is the host name and is never read here. The OS line
    is the system, its release and the architecture, which is what a bug
    report needs and what nobody owns.
    """
    from importlib.metadata import entry_points
    from shutil import which

    from fsmes import __version__, skills, standalone
    from fsmes.doctor import scrub

    seen.machine()
    on_path = which("fsmes")
    fleet = standalone.fleet_file(home)
    modules = tuple(sorted(ep.name for ep in entry_points(group="fsmes.modules")))
    return Install(
        version=__version__,
        how=_how_installed(),
        python=f"{platform.python_version()} ({platform.python_implementation()})",
        interpreter=scrub.shape_path(sys.executable, home=home),
        executable_on_path=(scrub.shape_path(on_path, home=home) if on_path
                            else "no - `fsmes` is not on this PATH"),
        invoked_as=_invoked_as(),
        os_said=f"{platform.system()} {platform.release()} ({platform.machine()})",
        home=home,
        home_exists=home.is_dir(),
        fleet_file=fleet,
        fleet_exists=fleet.is_file(),
        modules=modules,
        skill_folder=skills.folder("plant-from-your-mes") is not None,
    )


# -------------------------------------------------------------------- plants


def _ask(base: str):
    """The health question every fleet command asks, on doctor's clock."""
    from fsmes.fleet import observe

    return observe.health(base, timeout=ASK_TIMEOUT)


def _shadow_of(url: str, answering: bool, cfg: dict, seen) -> Shadow:
    """What is in force, asked of the plant; what was configured, otherwise.

    A running plant is the authority on its own shadow mode, because
    `MES_SHADOW` can be set in the shell that started it and no file on this
    machine would show it. A plant that is not running cannot be asked, and
    then the honest answer is what its pack sets, said to be that.
    """
    from fsmes.fleet import observe

    env = cfg.get("env") or {}
    if answering and url:
        answer = observe.ask(url, "/shadow", timeout=ASK_TIMEOUT)
        if answer.answered:
            body = answer.body or {}
            seen.structure(body)
            return Shadow(
                on=bool(body.get("shadow")),
                source="asked the plant, which is running",
                erp_mode=body.get("erp_mode"),
                uns_mode=body.get("uns_mode"),
                inbound_mode=env.get("MES_INBOUND_MQTT_MODE"),
                closed=body.get("outbound_paths_closed"),
                total=body.get("outbound_paths_total"),
            )
    said = str(env.get("MES_SHADOW", "")).strip().lower()
    return Shadow(
        on=said in ("1", "true", "yes", "on") if said else False,
        source=("the pack, which sets it" if said else
                "the pack, which sets nothing - so off, unless the shell that "
                "started this plant set it"),
        erp_mode=env.get("MES_ERP_MODE"),
        uns_mode=env.get("MES_UNS_MODE"),
        inbound_mode=env.get("MES_INBOUND_MQTT_MODE"),
    )


def _ports_of(pack: Path | None, seen) -> tuple[Port, ...]:
    """Which ports this pack asks for, and whether anything is listening.

    `firstrun.answers` is the one port question in this product - a connect,
    not a bind, registered in the shadow-mode register as a read - and this
    reuses it rather than opening a socket of its own.
    """
    from fsmes import firstrun

    if pack is None:
        return ()
    try:
        wanted = firstrun.ports_of(pack)
    except Exception:
        return ()
    out = []
    for what, host, port in wanted:
        seen.value(host)
        out.append(Port(what=what, host=host, port=int(port),
                        answers=firstrun.answers(host, int(port), timeout=PORT_TIMEOUT)))
    return tuple(out)


def _log_of(path: Path, seen) -> tuple[tuple[str, ...], bool, int]:
    """The tail of one log, scrubbed line by line.

    Two kinds of file, because this product writes two. A `.jsonl` log is
    structured and keeps its keys, its level and its message template. A
    `.log` is the console as it was painted - which is where an import-time
    traceback lands, and nothing else in it can be read as fields - so only
    the skeleton of a traceback is carried out of it and the rest is counted.
    """
    from fsmes.doctor import scrub

    raw, more = scrub.tail(path, LOG_LINES)
    for line in raw:
        seen.text(line)
    if path.suffix == ".jsonl":
        return tuple(scrub.scrub_jsonl(line) for line in raw), more, 0
    kept, dropped = scrub.traceback_skeleton(raw)
    return tuple(kept), more, dropped


def _processes_of(look, home: Path, seen) -> tuple[Process, ...]:
    """Each process the plant's own record names, alive or gone, with its log.

    The record is `<plant>.procs` beside the pid file, and the roles come
    from it rather than from the order of the pids - the same rule `status`
    follows, so `doctor` and `status` cannot name different processes.
    """
    from fsmes.doctor import scrub

    out = []
    for recorded in look.processes:
        path = Path(recorded.log) if recorded.log else None
        seen.value(recorded.log)
        lines: tuple[str, ...] = ()
        more = False
        dropped = 0
        exists = bool(path and path.is_file())
        if exists and path is not None:
            lines, more, dropped = _log_of(path, seen)
        out.append(Process(
            pid=recorded.pid,
            role=recorded.role or "",
            alive=recorded.alive,
            log=scrub.shape_path(recorded.log, home=home) if recorded.log else "",
            log_exists=exists,
            lines=lines,
            truncated=more,
            dropped=dropped,
        ))
    return tuple(out)


def _logs_without_a_record(where: Path, home: Path, seen) -> tuple[tuple[Process, ...], int]:
    """A stopped plant names no processes. Its logs are still on the disk.

    Newest first, and every one of them counted in the line above them, so a
    file that lists six of nine says it listed six of nine.
    """
    from fsmes.doctor import scrub

    if not where.is_dir():
        return (), 0
    files = sorted((p for p in where.iterdir() if p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[:LOGS_WITHOUT_A_RECORD]:
        lines, more, dropped = _log_of(path, seen)
        out.append(Process(pid=0, role="",
                           alive=False, log=scrub.shape_path(path, home=home),
                           log_exists=True, lines=lines, truncated=more, dropped=dropped))
    return tuple(out), len(files)


def _database(name: str, cfg: dict, home: Path, seen) -> str:
    """Which database this plant uses, as a kind and a shape - never a URL."""
    from fsmes import plant as plants
    from fsmes.doctor import scrub

    try:
        url = plants.database_url(name, cfg, home)
    except Exception as exc:                                    # pragma: no cover
        return f"unknown ({type(exc).__name__})"
    seen.value(url)
    if url.startswith("sqlite"):
        # A SQLite URL is a file path, and a file path is read by
        # `shape_path`, not by the URL shaper: `sqlite://<host>/...` would be
        # a true statement about nothing.
        where = url.split(":///", 1)[-1]
        return f"sqlite:///{scrub.shape_path(where, home=home)}"
    return scrub.url_shape(url)


def one_plant(name: str, cfg: dict, home: Path, seen) -> Plant:
    """One plant, read without touching it."""
    from fsmes import firstrun, standalone
    from fsmes.doctor import scrub

    seen.ours(name)
    seen.structure(cfg.get("env") or {})
    seen.value(cfg.get("label"))
    logs = standalone.logs_dir(name, home)
    look = firstrun.look(name, data_dir=standalone.data_dir(home), logs=logs, ask=_ask)
    pack = Path(cfg["pack"]) if cfg.get("pack") else None
    processes = _processes_of(look, home, seen)
    notes: list[str] = []
    logs_total = len(processes)
    if not processes:
        processes, logs_total = _logs_without_a_record(logs, home, seen)
        if processes:
            notes.append(
                f"This plant has no pid file, so nothing of it is running. Its "
                f"log directory holds {logs_total} "
                f"{'file' if logs_total == 1 else 'files'}, and the "
                f"{len(processes)} most recently written of them are below.")
    return Plant(
        name=name,
        label=scrub.scrub_text(str(cfg.get("label") or "")),
        owned=look.owned,
        answering=look.answering,
        url=scrub.url_shape(look.url) if look.url else "",
        why_not_owned="",
        pack=pack,
        database=_database(name, cfg, home, seen),
        processes=processes,
        ports=_ports_of(pack, seen),
        shadow=_shadow_of(look.url, look.answering, cfg, seen),
        logs_dir=logs,
        logs_total=logs_total,
        notes=tuple(notes),
    )


# ------------------------------------------- the second half: the long view


def coverage_of(name: str, cfg: dict, home: Path) -> Coverage:
    """What share of the window this MES actually watched, in total.

    Aggregate, deliberately. A per-machine line would name the machines, and
    a machine's code is the plant's word, not ours. The window is not
    "shadow mode so far" - nothing records when shadow mode was switched on -
    but the span this MES has been watching at all, which it does record.

    Read straight out of the plant's own database, through the same
    `coverage.totals_many` the machine page and `/metrics` use, so no figure
    here is a second opinion on a figure a screen already shows.
    """
    from fsmes import plant as plants
    from fsmes.db import make_engine
    from fsmes.services import coverage as coverage_service
    from fsmes.services import equipment as equipment_service

    try:
        from sqlalchemy.orm import Session

        engine = make_engine(plants.database_url(name, cfg, home))
        with Session(engine) as session:
            machines = equipment_service.work_units(session)
            if not machines:
                return Coverage(machines=0, watching_since=None, until=None,
                                window_seconds=0.0, observed_seconds=0.0,
                                why_not="this plant has no machines on it yet")
            ids = [m.id for m in machines]
            first = equipment_service.first_seen(session, ids)
            if not first:
                return Coverage(machines=len(machines), watching_since=None, until=None,
                                window_seconds=0.0, observed_seconds=0.0,
                                why_not="not measured: this MES has no reading from any of "
                                        "these machines yet")
            start = min(first.values())
            end = datetime.now(UTC).replace(tzinfo=None)
            if start.tzinfo is not None:
                start = start.replace(tzinfo=None)
            if end <= start:
                end = start + timedelta(seconds=1)
            accounts = coverage_service.totals_many(session, ids, start, end, first_seen=first)
            window = sum(a.window_seconds for a in accounts.values())
            observed = sum(a.observed_seconds for a in accounts.values())
            causes: dict[str, float] = {}
            for account in accounts.values():
                for cause, seconds in account.not_observed_by_cause.items():
                    causes[cause] = causes.get(cause, 0.0) + seconds
            return Coverage(machines=len(machines), watching_since=start, until=end,
                            window_seconds=window, observed_seconds=observed,
                            by_cause={k: v for k, v in causes.items() if round(v, 1) > 0})
    except Exception as exc:
        return Coverage(machines=0, watching_since=None, until=None, window_seconds=0.0,
                        observed_seconds=0.0,
                        why_not=f"not measured: this run could not read the plant's database "
                                f"({type(exc).__name__})")


#: Where `fsmes shadow scorecard --json` keeps its figures.
SCORECARDS = "scorecards"


def scorecards_in(home: Path, seen) -> tuple[ScorecardRow, ...]:
    """Every kept scorecard under the home, oldest first, as rows.

    The kept copy holds figures and no work: counts per field and the totals
    they were drawn from. The orders, the operations, the quantities and the
    sentences that name them stay in the scorecard the person ran, on their
    own machine (`scorecard.Scorecard.figures`).
    """
    from fsmes.doctor import scrub

    where = home / SCORECARDS
    if not where.is_dir():
        return ()
    out = []
    for path in sorted(where.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
            body = json.loads(text)
        except (OSError, ValueError):
            continue
        seen.text(text)
        agreement = body.get("agreement") or {}
        fields = (agreement.get("per_operation") or []) + (agreement.get("per_order") or [])
        out.append(ScorecardRow(
            date=str(body.get("written") or path.stem),
            path=scrub.shape_path(path, home=home),
            compared=agreement.get("compared"),
            agree=agreement.get("agree"),
            differ=agreement.get("differ"),
            not_stated=sum(int(f.get("not_stated") or 0) for f in fields) or 0,
            orders_compared=((body.get("orders") or {}).get("both")),
            operations_compared=((body.get("operations") or {}).get("both")),
            cannot_tell=(body.get("cannot_tell") or {}).get("total"),
        ))
    return tuple(out)


# --------------------------------------------------------------- the whole


def report(home: Path | None = None, only: str | None = None) -> Report:
    """One pass over this installation. Reads only; never longer than it says.

    The fleet file is pointed at the way `standalone.status` points at it -
    the environment variable every other command reads - so `doctor` and
    `fsmes demo <plant> status` are looking at the same fleet.
    """
    from fsmes import plant as plants
    from fsmes import standalone
    from fsmes.doctor import leak, scrub
    from fsmes.pack import fleet

    where = (home or standalone.home()).expanduser()
    seen = leak.Seen()
    trouble: list[str] = []
    local = datetime.now().astimezone()

    fleet_file = standalone.fleet_file(where)
    config: dict[str, dict] = {}
    if fleet_file.is_file():
        os.environ[plants.REGISTRY_ENV] = str(fleet_file)
        plants.environment.cache_clear()
        try:
            config = fleet.load(where)
        except Exception as exc:
            trouble.append(f"the fleet file at {scrub.shape_path(fleet_file, home=where)} "
                           f"could not be read: {type(exc).__name__}")

    # The plants' names are the one thing in this file that is a customer's
    # word on purpose: every command a person types next has one in it. They
    # are taught to the scrub and forgiven by the check in one place, here.
    # A name typed on the command line is a plant's name too, whether this
    # home has it or not: a person who asked for `machining` has to read
    # which name was not found, and it is theirs either way.
    scrub.also([*config, *( [only] if only else [] )])
    for name in config:
        seen.ours(name)
    if only:
        seen.ours(only)

    install = installation(where, seen)
    missing: list[str] = []
    wanted = list(config)
    if only:
        wanted = [name for name in config if name == only]
        if not wanted:
            missing.append(only)

    plants_seen = []
    for name in wanted:
        plants_seen.append(one_plant(name, config[name], where, seen))

    cards = scorecards_in(where, seen)
    full = []
    for plant in plants_seen:
        if plant.shadow.on:
            full.append(_with_the_long_view(plant, config[plant.name], where, cards))
        else:
            full.append(plant)

    try:
        from fsmes.config import get_settings

        seen.secrets(get_settings())
    except Exception:                                           # pragma: no cover
        pass

    return Report(
        install=install,
        local_clock=local.strftime("%Y-%m-%d %H:%M:%S %Z"),
        utc_clock=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        plants=tuple(full),
        asked_for=only,
        not_in_this_home=tuple(missing),
        home_has=tuple(config),
        seen=seen,
        trouble=tuple(trouble),
    )


def _with_the_long_view(plant: Plant, cfg: dict, home: Path,
                        cards: tuple[ScorecardRow, ...]) -> Plant:
    """The same plant with the second half's figures on it.

    Only for a plant in shadow mode: a plant that may act on its own line is
    not running beside an incumbent, and the second half has nothing to say
    about it that would not be padding.
    """
    from dataclasses import replace

    return replace(plant, coverage=coverage_of(plant.name, cfg, home), scorecards=cards)
