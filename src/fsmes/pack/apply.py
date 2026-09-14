"""`fsmes pack apply` and `fsmes pack status`: the pack a plant actually runs.

Apply is check, then the database, then the data, then the receipt:

1. **Check first, and refuse on failure.** A pack that would not pass
   `fsmes pack check` never reaches a database.
2. **Adopt the pack's environment.** Apply is the one command that becomes
   the plant it is acting on: it puts the pack's `MES_*` values into its own
   environment and clears the settings cache, so the schema it upgrades and
   the rows it writes are that plant's and not whatever the shell happened to
   be pointed at.
3. **Pack before database**, per decision 0022. A schema migration may need a
   value the pack now carries, and a pack applied after the migration has
   already run is a value that arrived too late.
4. **Seed the master data the pack carries**, or say it carries none.
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

from fsmes import __version__
from fsmes.pack import check as checker
from fsmes.pack import format as fmt

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


def adopt(pack: fmt.Pack) -> dict[str, str]:
    """Put this pack's settings into this process's environment.

    Deliberate and narrow: only `apply` does it, and it does it because it is
    acting *as* the plant. Everything else that needs a pack's settings hands
    them to a child process (`fsmes plant <name> start`) or reads them without
    adopting them (`fsmes pack check`).
    """
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    values = fmt.settings(pack)
    os.environ.update(values)
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    return values


def apply(directory: Path, *, into: Path | None = None, echo=print) -> dict:
    """Apply the pack in this directory to the plant it describes."""
    report = checker.check(directory)
    if not report.ok:
        raise Refused(report)
    pack = fmt.read(directory)

    values = adopt(pack)
    echo(f"  {pack.name}: {len(values)} settings from the pack")

    from fsmes.db import session_scope
    from fsmes.schema import current_revision, upgrade_database
    from fsmes.services import auth

    upgrade_database(echo=lambda line: echo(f"      {line}"))

    seeded: dict[str, dict] = {}
    masterdata = pack.table("files").get("masterdata")
    if isinstance(masterdata, str) and masterdata and pack.path(masterdata).is_dir():
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
    revision: str | None
    head: str | None

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
        if self.revision is None:
            lines.append("  schema    not stamped; this database has never been migrated")
        elif self.head and self.revision != self.head:
            lines.append(f"  schema    {self.revision}, behind head {self.head}. "
                         "`fsmes init-db` brings it forward.")
        else:
            lines.append(f"  schema    {self.revision} (head)")
        return lines


def status(directory: Path, *, into: Path | None = None, ask_database: bool = True) -> Status:
    """What this plant runs, and whether it still matches its pack."""
    pack = fmt.read(directory)
    revision = head = None
    if ask_database:
        adopt(pack)
        from fsmes.schema import current_revision, head_revision

        head = head_revision()
        try:
            revision = current_revision()
        except Exception:
            revision = None
    return Status(plant=pack.name, pack=pack.directory,
                  applied=Applied.read(stamp_path(pack.name, into)),
                  fingerprint_now=fmt.fingerprint(pack), revision=revision, head=head)
