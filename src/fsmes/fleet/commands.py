"""The verbs: what `fsmes fleet` does, and the gate in front of every one.

Six commands. Four of them change something and each begins with the same
call - `owned.gate(verb, name, ...)` - before it reads a pack, writes a file
or signals a process. Two of them only read.

    create <pack>   check the pack, make the plant's data directory, write it
                    an instance id, apply the pack - schema to head, master
                    data, accounts - and record the ownership
    start <name>    the existing `fsmes plant <name> start` machinery, reached
                    only through the gate
    stop <name>     the same, for stopping
    apply <name>    check the pack, then apply it to an owned, stopped plant
    status <name>   what the console shows, plus drift              (reads)
    list            owned and observed, with totals                 (reads)

The order inside `create` is the part worth reading twice. The pack is
checked before anything exists, the instance id is written **before** the
pack is applied - so a plant that half-applies is still a plant this
installation can prove it made - and the ownership entry is written last,
because an entry for a plant that was never built is a claim about a plant
that is not there. A pack whose database cannot be reached, or whose schema
the migrator will not identify, therefore refuses **before** any ownership is
recorded: the raise happens above `owned.remember`.

`create` does the whole of `fsmes plant <name> init` and not a part of it.
Until 2026-09-14 it applied the pack and stopped, and it applied it to this
process's default database rather than to the plant's, so the plant it then
recorded ownership of had no schema, no line and no account: it started,
`/health` said ok, and every sign-in returned 500.

`tests/test_fleet_ownership.py` reads this file. Every function named in
`WRITES` must call the gate before it calls anything that acts, and every
public function here must be in `WRITES` or `READS` - so a new verb that
forgets the gate fails a test rather than shipping.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fsmes import __version__
from fsmes import plant as plants
from fsmes.fleet import observe, owned
from fsmes.pack import apply as applier
from fsmes.pack import check as checker
from fsmes.pack import fleet as packs
from fsmes.pack import format as fmt

#: Every verb here that changes something. Each one calls `owned.gate` first.
WRITES = ("create", "start", "stop", "apply")

#: Every verb here that only reads. They call `owned.describe`, which is the
#: gate's question without the exception.
READS = ("status", "listing")


class Refused(Exception):
    """A verb would not run, for a reason that is not ownership: a pack that
    does not check, a plant that is still answering. One sentence."""


def data_dir(root: Path) -> Path:
    """Where this fleet keeps its plants' data - and its ownership file."""
    return plants.data_dir(root)


def _config(entry: owned.Entry) -> dict:
    """The plain configuration `fsmes.plant` runs, from the entry's pack."""
    directory = Path(entry.pack).expanduser()
    if not (directory / fmt.PLANT_FILE).is_file():
        raise Refused(
            f"{entry.name} was created from {directory}, and there is no "
            f"{fmt.PLANT_FILE} there now. Put the pack back, or create the plant "
            "again from where it lives.")
    return packs.compile_pack(fmt.read(directory))


def _refuse_unusable(directory: Path) -> fmt.Pack:
    """`fsmes pack check` first, and no further on a failure."""
    report = checker.check(directory)
    if not report.ok:
        raise Refused(
            f"{directory} has {len(report.problems)} problem(s) and nothing was done. "
            f"`fsmes pack check {directory}` lists them.")
    return fmt.read(directory)


# ------------------------------------------------------------------- writing


def _lab_accounts(echo=print) -> dict[str, str]:
    """The accounts a pack that declares none still needs, in this process.

    `fsmes plant <name> init` ends the same way, through
    `plants.ensure_lab_users`, which hands each account to a child process -
    it has to, because `plant init` is not the plant it is initialising and
    that plant's settings exist only in the environment it builds for the
    child. By the time `create` reaches here, `fsmes pack apply` has already
    put the pack's settings into *this* process, so the accounts go in from
    here and a fleet of two plants does not spawn ten interpreters to make
    ten rows.

    One list, `plants.LAB_USERS`, read by both, so an account added to it
    reaches a plant however the plant was built. Existing accounts are left
    exactly as they are: this creates, it never resets a password.
    """
    from fsmes.db import session_scope
    from fsmes.services import auth

    out: dict[str, str] = {}
    for code, full, password, role in plants.LAB_USERS:
        with session_scope() as session:
            auth.ensure_builtin_roles(session)
            try:
                auth.create_user(session, code=code, name=full, password=password,
                                 role=role, actor="fleet-create")
                out[code] = "created"
            except Exception:
                out[code] = "already there"
        echo(f"      account {code} ({role}): {out[code]}")
    return out


def create(directory: Path, *, root: Path, echo=print) -> owned.Entry:
    """Build a plant from a pack, and record that this installation made it."""
    directory = Path(directory).expanduser().resolve()
    pack = fmt.read(directory)
    where = data_dir(root)
    owned.gate("create", pack.name, data_dir=where)

    _refuse_unusable(directory)
    echo(f"  {pack.name}: pack checks out")

    where.mkdir(parents=True, exist_ok=True)
    instance = secrets.token_hex(16)
    owned.instance_path(where, pack.name).write_text(instance + "\n", encoding="utf-8")
    echo(f"  {pack.name}: instance {instance[:8]}… written to {where}")

    # Schema to head, the pack's settings, its master data and the accounts
    # it declares - `fsmes pack apply`, the same call `fsmes plant <name>
    # init` makes, and against the same database, because `apply` resolves it
    # from the pack and the fleet rather than from this process's default.
    applier.apply(directory, into=where, echo=echo)
    # The accounts a pack that declares none still needs. `plant init` ends
    # the same way, for the same reason: a plant with a schema, a line and no
    # account is a plant nobody can sign in to, and until 2026-09-14 that is
    # exactly what `fleet create` built.
    if not pack.accounts():
        _lab_accounts(echo)
    echo(f"  {pack.name}: schema at head, pack applied, accounts in place")

    host, user = owned.here()
    serve = pack.table("serve")
    entry = owned.Entry(
        name=pack.name, pack=str(directory), instance_id=instance, data_dir=str(where),
        api_host=str(serve.get("api_host", "127.0.0.1")), api_port=int(serve.get("api_port", 0)),
        control=owned.LOCAL, created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        created_on_host=host, created_by_user=user, product_version=__version__,
        pack_fingerprint=fmt.fingerprint(pack))
    file = owned.remember(where, entry)
    echo(f"  {pack.name}: owned by this installation, recorded in {file}")
    echo(f"  {pack.name}: `fsmes plant` reads the fleet file, not this one - add "
         f"{directory.name!r} to {packs.path(root)} to see it there too")
    return entry


def start(name: str, *, root: Path, speed: float | None = None, echo=print) -> owned.Ownership:
    """Start an owned plant. Nothing here starts a plant on its own."""
    where = data_dir(root)
    ownership = owned.gate("start", name, data_dir=where)
    if ownership.answering:
        echo(f"  {name} is already answering on {ownership.entry.base}.")
        return ownership
    plants.start(name, _config(ownership.entry), root, speed, echo=echo)
    return ownership


def stop(name: str, *, root: Path, force: bool = False, echo=print) -> owned.Ownership:
    """Stop an owned plant.

    `force` is for one state and says so when it refuses without it: a plant
    this installation created, whose pid file names live processes, that has
    stopped answering `/health`. It is still owned - the instance id in its
    data directory says so - and what this installation owns it may stop.
    """
    where = data_dir(root)
    ownership = owned.gate("stop", name, data_dir=where, force=force)
    if force and not ownership.answering:
        echo(f"  {name} is not answering; stopping it anyway because this installation "
             f"created it and the instance id in {ownership.entry.data_dir} still matches.")
    plants.stop(name, _config(ownership.entry), root, echo=echo)
    return ownership


def apply(name: str, *, root: Path, directory: Path | None = None, echo=print) -> owned.Entry:
    """Apply a pack to an owned, stopped plant, and record the new fingerprint."""
    where = data_dir(root)
    ownership = owned.gate("apply", name, data_dir=where)
    entry = ownership.entry
    if ownership.answering:
        raise Refused(
            f"{name} is still answering on {entry.base}. Stop it first: applying a pack "
            "to a plant that is running upgrades a database underneath a live process.")

    pack_dir = Path(directory).expanduser().resolve() if directory else Path(entry.pack)
    pack = _refuse_unusable(pack_dir)
    if pack.name != name:
        raise Refused(
            f"{pack_dir} describes {pack.name}, not {name}. A pack is a plant's identity; "
            "applying one plant's pack to another would rename a running plant's data.")
    echo(f"  {name}: pack checks out")
    applier.apply(pack_dir, into=where, echo=echo)

    from dataclasses import replace

    updated = replace(entry, pack=str(pack_dir), pack_fingerprint=fmt.fingerprint(pack))
    owned.remember(where, updated)
    echo(f"  {name}: applied, and {owned.path(where)} records the new fingerprint")
    return updated


# ------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Plant:
    """One row of `fsmes fleet list` and of the console: what is known about
    one plant, and what is not. Every unknown is `None` and says so on the
    way out; nothing here guesses."""

    name: str
    owned: bool
    reason: str
    base: str
    answered: bool
    said: dict | None = None
    pack_said: dict | None = None
    supervisor: str | None = None
    label: str = ""

    @property
    def state(self) -> str:
        """`answered`, `empty` or `unknown`. Never `down`: a plant that did
        not answer is a plant nobody heard from, which is not the same fact.

        `empty` is the third state, added 2026-09-14. It is a plant that
        answered and told us it has no schema or no machines - not a
        failure, and not something a person should have to notice by reading
        a dashboard with nothing on it. It comes from what the plant said and
        from nothing else: a plant too old to answer `/pack`, or one whose
        database did not answer, stays `answered`, because *we did not ask
        successfully* is not *there is nothing there*.
        """
        if not self.answered:
            return "unknown"
        return "empty" if observe.is_empty(self.pack_said) else "answered"


def status(name: str, *, root: Path, echo=print, ask=None) -> Plant:
    """One plant: whether it is owned, whether it answered, and its drift."""
    where = data_dir(root)
    ownership = owned.describe(name, data_dir=where, ask=ask)
    entry = ownership.entry
    base = entry.base if entry else ""
    row = Plant(name=name, owned=ownership.owned, reason=ownership.reason, base=base,
                answered=ownership.answering, said=ownership.said,
                pack_said=(observe.pack(base).body if ownership.answering else None),
                supervisor=(plants.supervisor(root, name) if entry else None),
                label=str((ownership.said or {}).get("label", "")))
    for line in render(row):
        echo(line)
    return row


def render(row: Plant) -> list[str]:
    """What one plant looks like on a terminal. Unknown is printed, not hidden."""
    said = row.said or {}
    lines = [f"  {row.name}",
             f"    owned      {'yes' if row.owned else 'no'} - {row.reason}",
             f"    answering  {row.state}" + (f" on {row.base}" if row.base else "")]
    if row.answered:
        lines.append(f"    identity   profile {said.get('profile') or 'unknown'}, "
                     f"clock {said.get('timezone_says') or 'unknown'}")
        lines.append(f"    shadow     {'on' if said.get('shadow') else 'off'}")
    pack_said = row.pack_said or {}
    if not row.answered:
        lines.append("    pack       unknown - the plant was not there to ask")
    elif not pack_said:
        lines.append("    pack       unknown - this plant does not answer /pack")
    else:
        drifted = pack_said.get("drifted")
        lines.append(f"    pack       {pack_said.get('pack') or 'unknown'}, "
                     + ("never applied" if drifted is None
                        else "drifted" if drifted else "no drift"))
        schema = pack_said.get("schema") or {}
        # Three states, not two. A plant whose database did not answer is
        # `unknown`, never "behind head": rendering the null as behind is how
        # a healthy plant gets rolled back by a script reading this line.
        if schema.get("answered") is False:
            lines.append("    schema     unknown - this plant's database did not answer")
        elif schema.get("at_head"):
            lines.append(f"    schema     {schema.get('revision')} (head)")
        elif schema.get("revision") is None:
            lines.append("    schema     not stamped; this database has never been migrated")
        else:
            lines.append(f"    schema     {schema.get('revision')}, behind head "
                         f"{schema.get('head') or 'unknown'}")
        line = pack_said.get("line") or {}
        if not line:
            lines.append("    line       unknown - this plant does not say how big its line is")
        elif not line.get("answered"):
            lines.append("    line       unknown - this plant could not count its equipment")
        elif line.get("equipment") == 0:
            lines.append("    line       0 machines - answered, but empty")
        else:
            lines.append(f"    line       {line.get('equipment')} machines")
        modules = pack_said.get("modules") or {}
        if modules:
            lines.append(f"    modules    {len(modules.get('on', []))} on, "
                         f"{len(modules.get('off', []))} off, of "
                         f"{modules.get('total', 'unknown')}")
    if row.supervisor:
        lines.append(f"    started by {row.supervisor}")
    return lines


def listing(*, root: Path, echo=print, ask=None, ask_pack=None) -> dict:
    """Every plant this installation owns or watches, with the totals.

    The number configured is never the number seen, so both are printed and
    the difference is named: a plant that did not answer is `unknown`, and a
    plant that answered and said it has no schema or no machines is `empty`.

    A plant that answered is asked `/pack` as well as `/health`, because
    `empty` is a fact only `/pack` carries. That is one more GET per plant
    per listing, against a plant that has just proved it is there.
    """
    where = data_dir(root)
    ask = ask or observe.health
    ask_pack = ask_pack or observe.pack
    record = owned.load(where)
    rows: list[Plant] = []
    for entry in sorted(record.owned, key=lambda e: e.name):
        ownership = owned.describe(entry.name, data_dir=where, ask=ask)
        rows.append(Plant(name=entry.name, owned=ownership.owned, reason=ownership.reason,
                          base=entry.base, answered=ownership.answering, said=ownership.said,
                          pack_said=(ask_pack(entry.base).body if ownership.answering else None),
                          supervisor=plants.supervisor(root, entry.name)))
    for watched in sorted(record.observed, key=lambda o: o.name):
        answer = ask(watched.url)
        rows.append(Plant(name=watched.name, owned=False,
                          reason="watched only; this installation did not create it",
                          base=watched.url, answered=answer.answered, said=answer.body,
                          pack_said=(ask_pack(watched.url).body if answer.answered else None)))

    answered = sum(1 for r in rows if r.state == "answered")
    empty = sum(1 for r in rows if r.state == "empty")
    totals = {"plants": len(rows), "answered": answered, "empty": empty,
              "unknown": len(rows) - answered - empty,
              "owned": sum(1 for r in rows if r.owned)}
    echo(f"{totals['plants']} plants, {totals['answered']} answered, "
         f"{totals['empty']} answered but empty, {totals['unknown']} unknown; "
         f"{totals['owned']} owned.")
    for row in rows:
        echo(f"  {row.name:<14} {('owned' if row.owned else 'observed'):<9} "
             f"{row.state:<9} {row.base}{'   ' + observe.empty_because(row.pack_said) if row.state == 'empty' else ''}")
    if not rows:
        echo("  Nothing yet. `fsmes fleet create <pack>` makes the first one.")
    return {"totals": totals, "plants": rows}
