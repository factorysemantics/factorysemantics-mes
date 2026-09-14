"""`fsmes pack apply` and `fsmes pack status`: the pack a plant actually runs.

Apply is check, then the database, then the data, then the receipt:

1. **Check first, and refuse on failure.** A pack that would not pass
   `fsmes pack check` never reaches a database.
2. **Adopt the pack's environment.** Apply is the one command that becomes
   the plant it is acting on: it puts the pack's `MES_*` values into its own
   environment and clears the settings cache, so the schema it upgrades and
   the rows it writes are that plant's and not whatever the shell happened to
   be pointed at. **Which database that is, is named on a line of its own
   before anything changes** - and a pack that states no database gets the
   file its fleet gives it rather than this process's default, which is the
   whole of `database_for` and the bug it was written for.
3. **Pack before database**, per decision 0022. A schema migration may need a
   value the pack now carries, and a pack applied after the migration has
   already run is a value that arrived too late.
4. **Seed the master data the pack carries**, or say it carries none -
   and refuse rather than seed when the database did not reach head, because
   rows written through the ORM into a half-migrated file make a database no
   release of this software can recognise afterwards.
5. **Record what was applied** in the plant's data directory: the pack's
   name, its format, the product version, the fingerprint of every file a
   person wrote in it, and the schema revision the database reached.

Idempotent. Applying the same pack twice makes nothing twice and says so.

`fsmes pack status` reads that record back and answers four questions: which
pack this plant runs, what version it was, whether the files on disk still
make the same fingerprint - **drift** - and what schema revision the database
is at. A plant that has never been applied says so rather than answering
`none`, because "no pack" and "not asked" are different facts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from fsmes import __version__, storage
from fsmes.pack import check as checker
from fsmes.pack import format as fmt

log = structlog.get_logger("pack.apply")

STAMP_SUFFIX = ".pack.json"

#: Where the record goes when nothing says otherwise. The fleet's own data
#: directory, beside the database and the pid file - the plant's data
#: directory in the sense decision 0022 means, which is per plant and not per
#: checkout.
DATA_DIR_ENV = "FSMES_DATA_DIR"


class Refused(Exception):
    """`apply` would not run. Carries the check report that says why."""

    def __init__(self, report: checker.Report):
        self.report = report
        super().__init__(f"{report.directory} has {len(report.problems)} problem(s).")


def data_dir(explicit: Path | None = None) -> Path:
    from fsmes import plant as plants

    if explicit is not None:
        return Path(explicit).expanduser()
    named = os.environ.get(DATA_DIR_ENV)
    if named:
        return Path(named).expanduser()
    return plants.data_dir(plants.find_root())


def stamp_path(name: str, directory: Path | None = None) -> Path:
    return data_dir(directory) / f"{name}{STAMP_SUFFIX}"


@dataclass(frozen=True)
class Database:
    """Which database a pack means: the URL to connect with, where that came
    from, and the one form of it that may be printed.

    `shown` is built separately rather than by redacting `url`, and that is
    the point of it being its own field. `fsmes.plant.migrate` learned the
    same lesson on 2026-09-14 and says it in one line: print the URL the
    *pack* wrote, which never holds a password (decision 0022) - not the
    resolved one with its `@` sliced off, which is a redaction by coincidence
    and one edit away from not being one.
    """

    url: str
    source: str
    shown: str

    def sentence(self) -> str:
        """The line `fsmes pack apply` prints before it changes anything."""
        return f"{self.shown} ({self.source})"


def database_for(pack: fmt.Pack, into: Path | None = None) -> Database:
    """Which database this pack's plant keeps its data in, and who said so.

    One function, because the alternative was a command that guessed. Until
    2026-09-14 `apply` set `MES_DATABASE_URL` only when the pack named one,
    so a pack that named none was applied to **whatever database this process
    already had** - the product default `./fsmes.db` when nothing had said
    otherwise. `fsmes fleet create` calls `apply` in-process, so it migrated
    and seeded a stray file beside the working directory while the plant it
    had just recorded ownership of was never created at all: `/health` said
    ok, `/pack` said `revision: null`, and every sign-in returned 500.

    Three answers, in this order, and never a fourth:

    1. **The pack's own `[storage] database_url`.** A plant that states where
       its data lives is the authority on it.
    2. **`MES_DATABASE_URL`, when the invoker set it.** That is the variable
       a deployment points at real data, and `fsmes plant <name> init` sets
       it to the file the fleet gives the plant before it spawns this.
    3. **The file its fleet gives it** - `<data dir>/<plant>.db`, the same
       path `fsmes.plant.database_url` builds, so a plant applied here and
       the plant started later are the same file.

    Never the process default. A database nobody named is not this plant's.
    """

    from fsmes import storage

    written = str(pack.table("storage").get("database_url") or "").strip()
    if written:
        url, source = fmt.database_url(pack)
        # From `written`, never from `url`. The password is in `url` because
        # something has to connect with it; nothing here needs it to print a
        # line, so nothing here derives a printed line from it.
        return Database(url=url, source=source,
                        shown=storage.redacted(os.path.expandvars(written)))
    named = os.environ.get("MES_DATABASE_URL")
    if named:
        return Database(url=named, source="from MES_DATABASE_URL",
                        shown=storage.redacted(named))
    where = data_dir(into)
    url = f"sqlite:///{(where / f'{pack.name}.db').as_posix()}"
    return Database(url=url, shown=url,
                    source=f"the file this fleet gives {pack.name}, in {where}")


@dataclass(frozen=True)
class Applied:
    """What was recorded the last time a pack was applied to this plant."""

    plant: str
    pack: str
    format: int
    fingerprint: str
    product_version: str
    applied_at: str
    revision: str | None
    files: int

    @classmethod
    def read(cls, path: Path) -> Applied | None:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(plant=raw["plant"], pack=raw["pack"], format=raw["format"],
                   fingerprint=raw["fingerprint"], product_version=raw["product_version"],
                   applied_at=raw["applied_at"], revision=raw.get("revision"),
                   files=raw.get("files", 0))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")


def adopt(pack: fmt.Pack, into: Path | None = None) -> tuple[dict[str, str], str]:
    """Put this pack's settings into this process's environment.

    Deliberate and narrow: only `apply` does it, and it does it because it is
    acting *as* the plant. Everything else that needs a pack's settings hands
    them to a child process (`fsmes plant <name> start`) or reads them without
    adopting them (`fsmes pack check`).

    Returns the settings and the sentence saying which database they point at,
    because `apply` prints that before it changes anything - the same rule
    `fsmes db-status` already keeps. The sentence is `Database.shown`, which
    is never derived from the URL holding the password.
    """
    from fsmes import storage
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    values = fmt.settings(pack)
    # The pack names its database and names the file holding the password,
    # and never the password (decision 0022). Everything downstream of here
    # gets one URL it can actually connect with - the same one `fsmes pack
    # status` and `fsmes db-status --pack` read - and a pack that names no
    # database gets the file its fleet gives it rather than this process's
    # default. See `database_for`.
    try:
        database = database_for(pack, into)
    except storage.Unknown as exc:
        raise Refused(checker.Report(
            directory=pack.directory,
            problems=(checker.Problem("[storage] database_url", str(exc)),),
            unknowns=(), checked=0)) from None
    values["MES_DATABASE_URL"] = database.url
    os.environ.update(values)
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    return values, database.sentence()


def refuse_unless_at_head(pack: fmt.Pack) -> str:
    """Refuse to seed master data into a database that is not at head.

    The ORM will happily write a row into a database the migrations have
    never touched: SQLAlchemy creates nothing, but the tables the mappers
    reach exist as soon as an earlier revision made them, and the ones a
    later revision would have added do not. The result is a file with some
    of the product's tables and no Alembic stamp - which the migrator then
    disowns outright, because a schema it cannot identify is one it will not
    guess at. That happened on 2026-09-14: a pack was seeded into an
    unstamped database, and `fsmes plant machining init` afterwards refused
    with *"the database is missing the table routing_operations"*, leaving a
    half-made plant no command could take forward.

    So the seed asks first. `upgrade_database` runs immediately above this
    and normally leaves nothing to say; this is the assertion that it did,
    and it is here rather than in a comment because the cost of being wrong
    is a database a person has to delete.
    """
    from fsmes import storage
    from fsmes.schema import head_revision

    url, source = storage.of_process()
    reading = storage.look(url, source)
    head = head_revision()
    if reading.revision == head:
        return head
    if not reading.answered:
        why = (f"its database did not answer ({reading.why or 'no reason given'}), so "
               "nothing here can say what schema it is at")
    elif reading.revision is None:
        why = ("its database carries no schema revision, so the migrations have never "
               "run against it")
    else:
        why = (f"its database is at {reading.revision} and the migrations end at "
               f"{head}")
    raise Refused(checker.Report(
        directory=pack.directory,
        problems=(checker.Problem(
            f"{pack.name} master data",
            f"{why}. Seeding master data through the ORM would leave tables no "
            f"release of this software made. Nothing was seeded. The database is "
            f"{storage.redacted(url)} ({source}); `fsmes db-status` on it says more."),),
        unknowns=(), checked=0))


def apply(directory: Path, *, into: Path | None = None, echo=print) -> dict:
    """Apply the pack in this directory to the plant it describes."""
    report = checker.check(directory)
    if not report.ok:
        raise Refused(report)
    pack = fmt.read(directory)

    values, database = adopt(pack, into)
    echo(f"  {pack.name}: {len(values)} settings from the pack")
    # Which database, before anything is changed. `fsmes db-status` has said
    # this on its first line since 2026-09-14 for the same reason: a command
    # that migrates and seeds without naming its target is one that can do
    # both to the wrong file and report success.
    echo(f"  {pack.name}: database {database}")

    from fsmes.db import session_scope
    from fsmes.schema import current_revision, upgrade_database
    from fsmes.services import auth

    upgrade_database(echo=lambda line: echo(f"      {line}"))

    seeded: dict[str, dict] = {}
    masterdata = pack.table("files").get("masterdata")
    if isinstance(masterdata, str) and masterdata and pack.path(masterdata).is_dir():
        refuse_unless_at_head(pack)
        from fsmes.integrations.opc.tag_map import load_tag_map
        from fsmes.pack import masterdata as data

        cycles: dict[str, float] = {}
        tag_map = pack.table("files").get("tag_map")
        if isinstance(tag_map, str) and tag_map and pack.path(tag_map).is_file():
            cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(pack.path(tag_map))}
        with session_scope() as session:
            seeded = data.seed(session, pack.path(masterdata), cycles)
        for kind, counts in sorted(seeded.items()):
            echo(f"      {kind}: {counts['made']} made, {counts['present']} already there")
    else:
        echo("      master data: this pack carries none, so nothing was seeded")

    accounts = _accounts(pack, auth, session_scope, echo)

    revision = current_revision()
    stamp = Applied(plant=pack.name, pack=str(pack.directory), format=pack.format or fmt.FORMAT,
                    fingerprint=fmt.fingerprint(pack), product_version=__version__,
                    applied_at=datetime.now(UTC).isoformat(timespec="seconds"),
                    revision=revision, files=len(fmt._hashable(pack)))
    where = stamp_path(pack.name, into)
    stamp.write(where)
    echo(f"  {pack.name}: applied, recorded in {where}")
    return {"plant": pack.name, "settings": values, "seeded": seeded,
            "accounts": accounts, "revision": revision, "stamp": str(where)}


def _accounts(pack: fmt.Pack, auth, session_scope, echo) -> dict[str, str]:
    """Create the accounts the pack declares, from passwords the environment
    holds. A variable that is not set refuses rather than making an account
    with no password - the rule the registry already had, kept."""
    out: dict[str, str] = {}
    declared = pack.accounts()
    if not declared:
        return out
    for account in declared:
        password = os.environ.get(account["password_env"])
        if not password:
            raise Refused(checker.Report(
                directory=pack.directory,
                problems=(checker.Problem(f"account {account['code']}", (
                    f"{account['password_env']} is not set on this machine; refusing to "
                    "create an account with no password.")),),
                unknowns=(), checked=0))
        with session_scope() as session:
            auth.ensure_builtin_roles(session)
            try:
                auth.create_user(session, code=account["code"], name=account["name"],
                                 password=password, role=account["role"], actor="pack-apply")
                out[account["code"]] = "created"
            except Exception:
                out[account["code"]] = "already there"
        echo(f"      account {account['code']} ({account['role']}): {out[account['code']]}")
    return out


# -------------------------------------------------------------------- status


@dataclass(frozen=True)
class Status:
    plant: str
    pack: Path
    applied: Applied | None
    fingerprint_now: str
    #: What the pack's own database said, or `None` when nothing asked it.
    reading: storage.Reading | None = None
    #: Why nothing here can say which database this pack means. Set instead
    #: of `reading`, never beside it.
    storage_unknown: str | None = None

    @property
    def revision(self) -> str | None:
        return self.reading.revision if self.reading else None

    @property
    def head(self) -> str | None:
        return self.reading.head if self.reading else None

    @property
    def at_head(self) -> bool | None:
        """True, False, or None when nothing here reached the database."""
        return self.reading.at_head if self.reading else None

    @property
    def drifted(self) -> bool | None:
        """True when the files on disk no longer make the fingerprint that was
        applied; None when this plant has never been applied, because that is
        not the same as "no drift"."""
        if self.applied is None:
            return None
        return self.applied.fingerprint != self.fingerprint_now

    def render(self) -> list[str]:
        lines = [f"  plant     {self.plant}",
                 f"  pack      {self.pack}"]
        if self.applied is None:
            lines.append("  applied   never, as far as this machine can see. Nothing here "
                         "says what this plant is running; `fsmes pack apply` writes it.")
        else:
            lines.append(f"  applied   {self.applied.applied_at} by {self.applied.product_version}, "
                         f"format {self.applied.format}, {self.applied.files} files")
            lines.append("  drift     " + (
                "yes - the files in the pack have changed since it was applied. "
                "`fsmes pack apply` again to bring the plant to them."
                if self.drifted else "no - the files on disk still make the fingerprint "
                                     "that was applied"))
        if self.storage_unknown is not None:
            lines.append(f"  database  unknown - {self.storage_unknown}")
            lines.append("  schema    unknown - nothing here knows which database to ask")
        elif self.reading is None:
            lines.append("  database  not asked")
            lines.append("  schema    not asked")
        else:
            lines.append(f"  database  {storage.redacted(self.reading.url)} "
                         f"({self.reading.source})")
            lines.append(f"  schema    {self.reading.short()}")
        return lines


def told_data_dir() -> Path | None:
    """The plant's own data directory, when something told this process where
    it is - and `None` when nothing did.

    Deliberately not the guess `data_dir()` makes. A running plant answering
    `/pack` is being asked what it was given, and the honest answer for a
    plant nobody told is *unknown*: a guess that walked up from the working
    directory would answer for a different plant's record, or create a
    directory on a read.
    """
    named = os.environ.get(DATA_DIR_ENV)
    return Path(named).expanduser() if named else None


def line_here() -> dict:
    """How many machines this plant has, asked of the plant itself.

    A plant with a schema at head, an account that signs in and **no
    equipment at all** answers `/health` with `ok` and looks healthy from
    every angle a fleet console had until 2026-09-14. It is not broken - a
    pack that carries no master data is allowed, and the bottling lab plant
    was one on purpose - but *empty* and *running* are different facts and a
    console that shows only the second is no help to the person who just
    built the fleet.

    Work units, not rows in `equipment`: the table is one tree and counting
    all of it would count the enterprise, the site and the area as machines.
    `services.equipment.work_units` is the same predicate every screen that
    says "machines" already uses.

    `answered` is False and `equipment` None when the database could not be
    asked - a plant whose schema never ran has no equipment table to count,
    and reporting that as zero would be inventing an empty line where there
    is no line at all.
    """
    from sqlalchemy import func, select

    from fsmes.db import session_scope
    from fsmes.domain import Equipment, EquipmentLevel

    try:
        with session_scope() as session:
            count = session.scalar(select(func.count(Equipment.id)).where(
                Equipment.level == EquipmentLevel.WORK_UNIT))
        return {"equipment": int(count or 0), "answered": True}
    except Exception as exc:
        # Same rule as the schema reading above: the driver's words name the
        # host and the account, and a public endpoint is owed neither.
        log.warning("line could not be counted", error=str(exc))
        return {"equipment": None, "answered": False}


def what_this_plant_runs() -> dict:
    """What a plant can say about its own pack, from inside the plant.

    `fsmes pack status` reads a pack directory and a record beside it. This
    is the same four facts asked of a *running* plant by something outside
    it, which is what a fleet console needs - and every one of them is
    reported as unknown rather than guessed when this plant cannot know it.

    Four separate answers, never merged into one light: which pack, when it
    was applied and by which product version, whether the files have drifted
    since, and what schema revision the database is at. Plus the modules
    this plant serves, because "the same codebase with two modules off" is
    only visible from outside if a plant will say which - and how many
    machines it has, because a plant at head with an empty line answers every
    other question here like a healthy one.
    """
    from fsmes import identity
    from fsmes.config import get_settings

    settings = get_settings()
    name = identity.plant_name(settings)
    unknown: dict[str, str] = {}

    where = told_data_dir()
    applied = None
    if where is None:
        unknown["pack"] = ("this plant was not told where its data directory is, so it "
                           "cannot say which pack it was given")
    else:
        applied = Applied.read(stamp_path(name, where))
        if applied is None:
            unknown["pack"] = ("no pack has been applied to this plant, as far as its "
                               "data directory can see")

    drifted = None
    pack_name = None
    if applied is not None:
        directory = Path(applied.pack)
        pack_name = directory.name
        if (directory / fmt.PLANT_FILE).is_file():
            try:
                drifted = fmt.fingerprint(fmt.read(directory)) != applied.fingerprint
            except fmt.PackError as exc:
                # The reason stays out of the payload: this endpoint is public,
                # and a parser's message can carry a path or a library's words.
                # The log has it; `fsmes pack check` on this machine says why.
                log.warning("pack unreadable", pack=pack_name, error=str(exc))
                unknown["drift"] = ("the pack this plant was given cannot be read here; run "
                                    "`fsmes pack check` on this machine to see why")
        else:
            unknown["drift"] = ("the pack this plant was given is not on this machine any "
                                "more, so nothing here can tell whether it has changed")

    url, source = storage.of_process()
    reading = storage.look(url, source)
    line = line_here()
    if not line["answered"]:
        unknown["line"] = ("this plant's equipment could not be counted, so nothing here "
                           "can say how big its line is; `fsmes db-status` on that machine "
                           "says whether the schema is there at all")
    if not reading.answered:
        # Same rule as the drift reason above: the driver's words name the
        # host and the account it tried, and a public endpoint is owed
        # neither. The log has them.
        log.warning("database did not answer", url=storage.redacted(url), error=reading.why)
        unknown["schema"] = ("this plant's database did not answer, so nothing here can say "
                             "what schema it is at; `fsmes db-status` on that machine says why")

    return {
        "plant": name,
        "pack": pack_name,
        "applied_at": applied.applied_at if applied else None,
        "product_version": applied.product_version if applied else None,
        "format": applied.format if applied else None,
        "files": applied.files if applied else None,
        # Tri-state on purpose. None is *never applied*, or a pack this
        # machine cannot read - neither of which is "no drift".
        "drifted": drifted,
        # One function took this, the same one `fsmes pack status` and
        # `fsmes db-status` take theirs from, so the CLI and this endpoint
        # cannot disagree about a database they were both pointed at.
        "schema": reading.payload(),
        # How much plant there is. Separate from `schema` because a plant can
        # be at head and empty, and those are two different things to fix.
        "line": line,
        "modules": {
            "on": [m.name for m in settings.enabled_modules()],
            "off": [m.name for m in settings.disabled_modules()],
            "total": len(settings.enabled_modules()) + len(settings.disabled_modules()),
        },
        "unknown": unknown,
    }


def status(directory: Path, *, into: Path | None = None, ask_database: bool = True) -> Status:
    """What this plant runs, and whether it still matches its pack.

    The database asked is **the pack's**, not this process's. Reading a
    status command's own default and reporting it as the plant's is what
    made `fsmes pack status` say a migrated plant had never been migrated;
    the URL comes from `[storage] database_url` with the password put back
    from the file the pack names, and a pack that does not say which database
    it uses is reported as unknown rather than answered about.

    It reads. Unlike `apply`, it does not adopt the pack's environment: a
    command that says what a plant is should not become it.

    Deliberately **not** `database_for`, which is what `apply` resolves with.
    That function's third answer is *the file this fleet gives the plant*,
    and finding it means resolving a data directory - which, with no
    `--data-dir`, walks up from the working directory and creates one. A
    command that reads must not make a directory in order to have something
    to report, and a path no plant was ever started against is not an answer
    worth printing. So a pack that names no database still says unknown here
    and still names `--plant` as the way to ask.
    """
    pack = fmt.read(directory)
    reading = None
    unknown = None
    if ask_database:
        try:
            url, source = fmt.database_url(pack)
        except storage.Unknown as exc:
            unknown = str(exc)
        else:
            reading = storage.look(url, source)
    return Status(plant=pack.name, pack=pack.directory,
                  applied=Applied.read(stamp_path(pack.name, into)),
                  fingerprint_now=fmt.fingerprint(pack),
                  reading=reading, storage_unknown=unknown)
