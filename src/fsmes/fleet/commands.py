"""The verbs: what `fsmes fleet` does, and the gate in front of every one.

Six commands. Four of them change something and each begins with the same
call - `owned.gate(verb, name, ...)` - before it reads a pack, writes a file
or signals a process. Two of them only read.

    create <pack>   check the pack, make the plant's data directory, write it
                    an instance id, apply the pack, record the ownership
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
that is not there.

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

    applier.apply(directory, into=where, echo=echo)

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


def stop(name: str, *, root: Path, echo=print) -> owned.Ownership:
    """Stop an owned plant."""
    where = data_dir(root)
    ownership = owned.gate("stop", name, data_dir=where)
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
        """`answered` or `unknown`. Never `down`: a plant that did not answer
        is a plant nobody heard from, which is not the same fact."""
        return "answered" if self.answered else "unknown"


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
        modules = pack_said.get("modules") or {}
        if modules:
            lines.append(f"    modules    {len(modules.get('on', []))} on, "
                         f"{len(modules.get('off', []))} off, of "
                         f"{modules.get('total', 'unknown')}")
    if row.supervisor:
        lines.append(f"    started by {row.supervisor}")
    return lines


def listing(*, root: Path, echo=print, ask=None) -> dict:
    """Every plant this installation owns or watches, with the totals.

    The number configured is never the number seen, so both are printed and
    the difference is named: a plant that did not answer is `unknown`.
    """
    where = data_dir(root)
    ask = ask or observe.health
    record = owned.load(where)
    rows: list[Plant] = []
    for entry in sorted(record.owned, key=lambda e: e.name):
        ownership = owned.describe(entry.name, data_dir=where, ask=ask)
        rows.append(Plant(name=entry.name, owned=ownership.owned, reason=ownership.reason,
                          base=entry.base, answered=ownership.answering, said=ownership.said,
                          supervisor=plants.supervisor(root, entry.name)))
    for watched in sorted(record.observed, key=lambda o: o.name):
        answer = ask(watched.url)
        rows.append(Plant(name=watched.name, owned=False,
                          reason="watched only; this installation did not create it",
                          base=watched.url, answered=answer.answered, said=answer.body))

    answered = sum(1 for r in rows if r.answered)
    totals = {"plants": len(rows), "answered": answered, "unknown": len(rows) - answered,
              "owned": sum(1 for r in rows if r.owned)}
    echo(f"{totals['plants']} plants, {totals['answered']} answered, "
         f"{totals['unknown']} unknown; {totals['owned']} owned.")
    for row in rows:
        echo(f"  {row.name:<14} {('owned' if row.owned else 'observed'):<9} "
             f"{row.state:<9} {row.base}")
    if not rows:
        echo("  Nothing yet. `fsmes fleet create <pack>` makes the first one.")
    return {"totals": totals, "plants": rows}
