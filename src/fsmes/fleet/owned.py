"""`ownership.toml`: what this installation created, and may therefore manage.

Decision
[0023](../../../docs/decisions/0023-the-fleet-console-observes.md) draws the
line at **owned versus not owned** rather than at read versus write: a tool
may manage the plants it created and must not touch any other. This module is
that line, in two parts - the file that records what was created, and the one
function every write path in `fsmes fleet` calls before it does anything.

A plant is owned when all three of these hold, and any one missing means
observed only:

1. **This installation created it and wrote that down** - an `[[owned]]`
   entry here, with the plant's name, its pack, where its data lives, when
   and on which host and OS user it was created, and a random `instance_id`
   the tool also wrote into the plant's own data directory.
2. **The plant corroborates the id.** When it is answering, `/health` must
   return that name and that `instance_id`; a different id, or none, means
   not owned whatever this file says. That is how a plant *revokes*
   ownership: delete the id and nothing can prove condition 2 again.
3. **A path to act exists.** Local: same host and same OS user, so this
   process can signal that plant's processes. Remote: this file names an
   environment variable holding a credential a person put there. A
   credential is never discovered, never defaulted and never reused from
   another entry - and only its *name* is ever written here.

**The `instance_id` is a continuity check, not an authentication.** It
catches the wrong-plant mistake, which is the mistake that actually happens.
It does not stop somebody copying an id into this file on purpose, and
nothing here pretends otherwise; what keeps a stranger out of a plant is that
plant's own accounts, capability roles and shadow mode.

### Where this differs from 0023 as written, and why

0023 says a plant that is silent "is not owned for as long as it is silent,
because condition 2 cannot be met". Taken literally that makes **start**
impossible: a plant that is not running cannot answer anything, and starting
it is one of the five verbs. So condition 2 is implemented as *the plant must
not contradict us*:

- **answering** - it must say this name and this id, or the verb is refused;
- **silent** - the id this tool wrote into the plant's own data directory
  must still be there and still match. That file is the other half of
  condition 2 and it is readable while the plant is down. Only the two verbs
  a stopped plant can take, `start` and `apply`, may proceed on it.
- **silent and remote** - refused. There is no data directory to read on
  another host, so nothing corroborates anything.

The gap the decision was closing stays closed: no verb reaches into a
*running* plant that has not just said who it is.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from fsmes.fleet import observe

#: The file, in the fleet's data directory. Not `fleet.toml`: that name is
#: taken, by the list of packs this machine runs
#: (`fsmes.pack.fleet`). Decision 0023 called this one `fleet.toml` before
#: M8 piece 3 renamed the registry; two files of one name, one listing packs
#: and one recording ownership, is a trap for whoever reads the next
#: traceback.
FILE = "ownership.toml"

#: What `fsmes fleet create` writes into the plant's own data directory. The
#: plant reads it at start-up and returns it on `/health`.
INSTANCE_SUFFIX = ".instance"

LOCAL = "local"
REMOTE = "remote"
CONTROL = (LOCAL, REMOTE)


class OwnershipError(Exception):
    """The ownership file cannot be used as written, and says why."""


class NotOwned(Exception):
    """A verb was asked of a plant this installation may not touch.

    Always one sentence naming the plant and the condition that failed, so
    the person at the terminal knows whether they are in the wrong directory
    or on the wrong machine.
    """


@dataclass(frozen=True)
class Entry:
    """One plant this installation created."""

    name: str
    pack: str
    instance_id: str
    data_dir: str
    api_host: str
    api_port: int
    control: str
    created_at: str
    created_on_host: str
    created_by_user: str
    product_version: str
    pack_fingerprint: str
    credential_env: str = ""
    """Remote control only: the variable a person put the credential in.
    The value never appears here and is never defaulted."""

    @property
    def base(self) -> str:
        return observe.base(self.api_host, self.api_port)

    def instance_file(self) -> Path:
        return Path(self.data_dir).expanduser() / f"{self.name}{INSTANCE_SUFFIX}"


@dataclass(frozen=True)
class Observed:
    """One plant this installation only watches. Not owned, and no verb in
    `fsmes fleet` will act on it - it is here so the console can show it."""

    name: str
    url: str
    about: str = ""


@dataclass(frozen=True)
class Ownership:
    """The gate's answer about one plant."""

    name: str
    owned: bool
    reason: str
    """One sentence, whether the answer is yes or no."""

    entry: Entry | None = None
    answering: bool = False
    said: dict | None = None
    """What `/health` returned, when it answered."""


# ------------------------------------------------------------------ the file


def path(data_dir: Path) -> Path:
    return Path(data_dir).expanduser() / FILE


def instance_path(data_dir: Path, name: str) -> Path:
    return Path(data_dir).expanduser() / f"{name}{INSTANCE_SUFFIX}"


def read_instance(data_dir: Path, name: str) -> str | None:
    """The id in the plant's own data directory, or None when it is gone."""
    where = instance_path(data_dir, name)
    if not where.is_file():
        return None
    return where.read_text(encoding="utf-8").strip() or None


@dataclass(frozen=True)
class Record:
    """The whole file: what is owned, what is only watched, and where it is."""

    where: Path
    owned: tuple[Entry, ...] = ()
    observed: tuple[Observed, ...] = ()

    def entry(self, name: str) -> Entry | None:
        return next((e for e in self.owned if e.name == name), None)

    def watching(self, name: str) -> Observed | None:
        return next((o for o in self.observed if o.name == name), None)


_REQUIRED = ("name", "pack", "instance_id", "data_dir", "api_port")


def load(data_dir: Path) -> Record:
    """Read the ownership file. A missing file is an empty fleet, not an error.

    Two plants with one name, or two entries with one `instance_id`, are
    refused rather than resolved: a directory copied to make a second plant
    carries the first one's id until something regenerates it, and a fleet
    tool that picked one of them would act on the wrong plant. This is where
    that is caught, because a pack carries no id and `fsmes pack check`
    therefore cannot see it.
    """
    where = path(data_dir)
    if not where.is_file():
        return Record(where=where)
    try:
        table = tomllib.loads(where.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise OwnershipError(f"{where} is not valid TOML: {exc}") from exc

    owned: list[Entry] = []
    for raw in table.get("owned", []):
        missing = [k for k in _REQUIRED if not raw.get(k)]
        if missing:
            raise OwnershipError(
                f"{where}: an [[owned]] entry is missing {', '.join(missing)}. "
                "Every entry says which plant, which pack, which instance and where "
                "its data lives; one that does not cannot be checked against a plant.")
        control = str(raw.get("control", LOCAL))
        if control not in CONTROL:
            raise OwnershipError(
                f"{where}: {raw['name']} says control = {control!r}; it is "
                f"{' or '.join(CONTROL)}.")
        if control == REMOTE and not raw.get("credential_env"):
            raise OwnershipError(
                f"{where}: {raw['name']} is controlled remotely and names no "
                "credential_env. A credential is never defaulted; name the variable "
                "it lives in.")
        owned.append(Entry(
            name=str(raw["name"]), pack=str(raw["pack"]),
            instance_id=str(raw["instance_id"]), data_dir=str(raw["data_dir"]),
            api_host=str(raw.get("api_host", "127.0.0.1")), api_port=int(raw["api_port"]),
            control=control, created_at=str(raw.get("created_at", "")),
            created_on_host=str(raw.get("created_on_host", "")),
            created_by_user=str(raw.get("created_by_user", "")),
            product_version=str(raw.get("product_version", "")),
            pack_fingerprint=str(raw.get("pack_fingerprint", "")),
            credential_env=str(raw.get("credential_env", "")),
        ))

    names = [e.name for e in owned]
    duplicate = next((n for n in names if names.count(n) > 1), None)
    if duplicate:
        raise OwnershipError(
            f"{where} records {duplicate!r} twice. Two entries for one plant is two "
            "plants a fleet cannot tell apart.")
    ids = [e.instance_id for e in owned]
    shared = next((i for i in ids if ids.count(i) > 1), None)
    if shared:
        both = [e.name for e in owned if e.instance_id == shared]
        raise OwnershipError(
            f"{where}: {' and '.join(both)} claim the same instance id. A plant "
            "directory copied to make a second plant carries the first one's id; "
            "delete the id from the copy's data directory and create it again.")

    observed = tuple(
        Observed(name=str(raw["name"]), url=str(raw["url"]), about=str(raw.get("about", "")))
        for raw in table.get("observe", []) if raw.get("name") and raw.get("url"))
    watched = [o.name for o in observed]
    clash = next((n for n in watched if n in names or watched.count(n) > 1), None)
    if clash:
        raise OwnershipError(
            f"{where}: {clash!r} is listed twice. A plant is either one this "
            "installation created or one it watches, and never both.")
    return Record(where=where, owned=tuple(owned), observed=observed)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


HEADER = """\
# What this installation created, and may therefore manage.
#
# Written by `fsmes fleet create`; read by every `fsmes fleet` verb before it
# acts, and by the console before it says a plant is owned. Ownership is the
# three conditions in docs/operate/fleet.md: this entry, the same instance id
# coming back from the plant, and a path to act on it.
#
# No password is ever written here - `credential_env` names the variable one
# lives in. Deleting an entry makes that plant observed like any other; the
# plant itself keeps running.
"""


def save(record: Record) -> Path:
    """Write the file. Whole-file, sorted by name, so two runs make one diff."""
    lines = [HEADER]
    for entry in sorted(record.owned, key=lambda e: e.name):
        lines.append("[[owned]]")
        for field, value in entry.__dict__.items():
            if field == "credential_env" and not value:
                continue
            lines.append(f"{field} = {_toml_value(value)}")
        lines.append("")
    for watched in sorted(record.observed, key=lambda o: o.name):
        lines.append("[[observe]]")
        for field, value in watched.__dict__.items():
            if field == "about" and not value:
                continue
            lines.append(f"{field} = {_toml_value(value)}")
        lines.append("")
    record.where.parent.mkdir(parents=True, exist_ok=True)
    record.where.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return record.where


def remember(data_dir: Path, entry: Entry) -> Path:
    """Record one created plant, replacing any entry of the same name."""
    record = load(data_dir)
    kept = tuple(e for e in record.owned if e.name != entry.name)
    return save(replace(record, owned=(*kept, entry)))


# ------------------------------------------------------------------- the gate

#: The verbs the gate knows. `fsmes fleet` has no other write path, and a
#: verb that is not here cannot be gated, so the ratchet in
#: `tests/test_fleet_ownership.py` refuses one.
WRITE_VERBS = ("create", "start", "stop", "apply")

#: Verbs a plant that is not answering may still take: it has to be stopped
#: for both of them. Every other verb needs the plant's own corroboration,
#: live, at the moment it is asked.
WHILE_SILENT = ("start", "apply")

#: Verbs that need this process to be able to signal the plant's own
#: processes. There is no remote start in this product and this decision
#: adds none.
LOCAL_ONLY = ("start", "stop")


def here() -> tuple[str, str]:
    """This host and this OS user, as an entry records them."""
    return socket.gethostname(), getpass.getuser()


def agrees(entry: Entry, said: dict | None) -> tuple[bool, str]:
    """Condition 2, as a pure function: does what this plant said corroborate
    the entry that claims it?

    One definition, called by the gate and by the console, so the command
    and the page can never disagree about which plants are owned. `said` is
    what `/health` returned; `None` means the plant did not answer, which is
    neither corroboration nor contradiction.
    """
    if said is None:
        return False, (f"{entry.name} did not answer, and a plant that is not saying "
                       "anything cannot corroborate that this is the plant this "
                       "installation created. Unknown is not owned.")
    answered_as = said.get("plant") or "a plant that will not say its name"
    if answered_as != entry.name:
        return False, (f"{entry.base} answers as {answered_as!r}, not as {entry.name}. "
                       "Something else is on that port, and this installation did not "
                       "create it.")
    heard = said.get("instance_id")
    if not heard:
        return False, (f"{entry.name} answers with no instance id, so nothing corroborates "
                       "that this is the plant this installation created. That is how a "
                       "plant gives ownership back; it is observed now, not owned.")
    if heard != entry.instance_id:
        return False, (f"{entry.name} answers with a different instance id from the one "
                       "this installation recorded. This is not the plant that entry was "
                       "written for, and no verb will touch it.")
    return True, (f"created here, and {entry.name} answers with the instance id this "
                  "installation gave it")


def gate(verb: str, name: str, *, data_dir: Path, ask=None) -> Ownership:
    """Refuse unless this installation owns this plant. Called first, always.

    Every write path in `fsmes.fleet.commands` begins with this call, and a
    test reads the source to hold it there. It raises `NotOwned` with one
    sentence naming the condition that failed, and returns an `Ownership`
    saying what the plant said about itself when it did.
    """
    if verb not in WRITE_VERBS:
        raise ValueError(f"{verb!r} is not a write verb; the gate knows {WRITE_VERBS}.")
    # Resolved here rather than in the signature so that a test - and only a
    # test - can put a fake plant in front of it by patching the module.
    ask = ask or observe.health
    record = load(data_dir)
    entry = record.entry(name)

    if verb == "create":
        if entry is not None:
            raise NotOwned(
                f"{name} is already recorded in {record.where}, created "
                f"{entry.created_at or 'at an unrecorded time'} from {entry.pack}. "
                "Creating it again would abandon the plant that entry points at.")
        if record.watching(name):
            raise NotOwned(
                f"{name} is listed in {record.where} as a plant this installation "
                "only watches. Creating a plant of that name here would give two "
                "different plants one name.")
        return Ownership(name=name, owned=False,
                         reason="not created yet; this is the command that creates it")

    if entry is None:
        raise NotOwned(
            f"this installation has no record of creating {name}, so it does not own it "
            f"and will not {verb} it. {record.where} is the record; "
            "`fsmes fleet create <pack>` is what writes one.")

    # Condition 3: is there a path to act on it at all?
    host, user = here()
    if entry.control == LOCAL:
        if entry.created_on_host != host or entry.created_by_user != user:
            raise NotOwned(
                f"{name} was created on {entry.created_on_host or 'an unrecorded host'} by "
                f"{entry.created_by_user or 'an unrecorded user'}; this is {host} as {user}. "
                "A local plant is managed from the machine and the account that made it.")
    elif verb in LOCAL_ONLY:
        raise NotOwned(
            f"{name} is controlled remotely and this product has no remote {verb}. "
            "Start and stop are local process control; run them on that host.")
    elif not os.environ.get(entry.credential_env):
        raise NotOwned(
            f"{name} is controlled remotely through {entry.credential_env}, which is not "
            "set on this machine. A credential is never defaulted.")

    # Condition 2: the plant must not contradict us.
    answer = ask(entry.base)
    if answer.answered:
        said = answer.body or {}
        agreed, why = agrees(entry, said)
        if not agreed:
            raise NotOwned(why)
        return Ownership(name=name, owned=True, entry=entry, answering=True, said=said,
                         reason=why)

    if entry.control == REMOTE:
        raise NotOwned(
            f"{name} did not answer ({answer.why}), and it is on another host, so nothing "
            "here can corroborate that it is the plant this installation created. Unknown "
            "is not owned.")
    if verb not in WHILE_SILENT:
        raise NotOwned(
            f"{name} did not answer ({answer.why}). `{verb}` acts on a running plant and "
            "this one is not saying anything; a plant is never managed through a gap in "
            "which nobody can see it.")
    on_disk = read_instance(Path(entry.data_dir), name)
    if on_disk is None:
        raise NotOwned(
            f"{name} is not answering and the instance id is gone from "
            f"{instance_path(Path(entry.data_dir), name)}. That is how a plant revokes "
            "ownership; it is observed now, not owned.")
    if on_disk != entry.instance_id:
        raise NotOwned(
            f"{name} is not answering and the id in its data directory is not the one "
            f"{record.where} recorded. Two plants are claiming one entry.")
    return Ownership(name=name, owned=True, entry=entry, answering=False,
                     reason=f"created here, not running, and the instance id is still in "
                            f"{entry.data_dir}")


def describe(name: str, *, data_dir: Path, ask=None) -> Ownership:
    """The gate's answer without the exception: what `status` and `list` show.

    Reads only. It asks the same question every verb asks - *would this be
    refused, and why* - so a person can see the reason before they hit it.
    """
    entry = load(data_dir).entry(name)
    # `apply` is the one verb a plant may take whether it is answering or
    # not, and whether it is local or remote - so asking the gate about it is
    # asking about ownership itself rather than about one verb's own rules.
    try:
        return gate("apply", name, data_dir=data_dir, ask=ask)
    except NotOwned as exc:
        return Ownership(name=name, owned=False, reason=str(exc), entry=entry)
