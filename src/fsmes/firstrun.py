"""What a person running one plant on their own PC is told when it is not up.

On 2026-10-13 the first person to install 0.3.0 on Windows hit three things in
three hours, and the product had no words for any of them:

* 07:18, nothing to run: `py -m pip install` put `fsmes.exe` in a Scripts
  folder that was not on PATH. That one is fixed in `__main__.py`.
* 07:50, "I think it got shut down. How to check status and why it's not
  running?" - `status` relayed the fleet's three states and stopped there,
  with no next command on screen.
* 07:56, "Says 2 PID are alive, 7964 and 3940. But it can't talk to them." A
  plant that had half died, reported as two bare numbers, and the easy stop
  refused to touch it: `--force` lives on `fsmes fleet stop`, five words and
  a `--root` away from where he was standing.

This module is the words for the last two. It knows nothing about managing a
plant - `fsmes.fleet` does that, and its gate and its `--force` are exactly as
they were. This is the part that looks at one plant and says, in a line or
two, what is true and what to type next.

Every sentence here is written for somebody reading it on a phone with a
PowerShell window open beside it, which is where all three of those questions
came from.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from fsmes import plant as plants
from fsmes.fleet import owned


@dataclass(frozen=True)
class Look:
    """One plant, as this installation can see it without touching it."""

    name: str
    owned: bool
    local: bool
    answering: bool
    url: str
    processes: tuple[plants.Recorded, ...] = ()

    @property
    def alive(self) -> tuple[plants.Recorded, ...]:
        return tuple(p for p in self.processes if p.alive)

    @property
    def gone(self) -> tuple[plants.Recorded, ...]:
        return tuple(p for p in self.processes if not p.alive)

    @property
    def half_dead(self) -> bool:
        """Alive but silent: the state that cost a first-time user two hours.

        Only ever said of a plant this installation made on this machine. A
        remote plant that is not answering is a plant nobody here can see,
        which is a different sentence and `fsmes.fleet.owned` says it.
        """
        return self.owned and self.local and not self.answering and bool(self.alive)


def look(name: str, *, data_dir: Path, logs: Path | None = None, ask=None) -> Look:
    """Read one plant: the ownership record, one question to it, its processes.

    `owned.describe` is the read-only form of the gate every write verb goes
    through, so what is said here cannot drift from what the next command will
    allow. The processes come from the data directory the ownership entry
    records, never from a second guess at where that is.
    """
    seen = owned.describe(name, data_dir=data_dir, ask=ask)
    entry = seen.entry
    if entry is None:
        return Look(name=name, owned=False, local=False, answering=False, url="")
    return Look(
        name=name,
        owned=seen.owned,
        local=entry.control == owned.LOCAL,
        answering=seen.answering,
        url=entry.base,
        processes=tuple(plants.processes_in(Path(entry.data_dir).expanduser(), name, logs=logs)),
    )


def _by_role(seen: Look) -> list[str]:
    """Every process the plant started, alive or gone, one line each.

    Alive first, because the question a person has in front of a half-dead
    plant is *what is still holding my ports*; then what is gone, each with
    the log that will say why it went. Every line is counted in the sentence
    above it: a list that does not state its total invites the reader to
    assume the rest is fine.
    """
    lines = []
    for p in seen.alive:
        lines.append(f"      still alive: {p.said} (pid {p.pid})")
    for p in seen.gone:
        where = f" - its log is {p.log}" if p.log else ""
        lines.append(f"      gone: {p.said} (pid {p.pid}){where}")
    return lines


def what_status_should_say(seen: Look) -> list[str]:
    """`fsmes demo <plant> status`, after the fleet has said owned and answering.

    Three endings, because there are three states and only two of them were
    ever acted on. The last line is always a command, because at 07:50 the
    question was not *what state is it in*, it was *what do I do*.
    """
    if not seen.owned and not seen.processes:
        return []
    if seen.answering:
        return [f"  {seen.name} is up: {seen.url}/dashboard"]
    if seen.half_dead:
        return [
            f"  {seen.name} is not answering on {seen.url}, and {len(seen.alive)} of its "
            f"{len(seen.processes)} processes are still alive:",
            *_by_role(seen),
            "  that is a plant that is running and cannot be talked to. This is the way out:",
            f"      fsmes demo {seen.name} stop      (stops what is left and clears the record)",
            f"      fsmes demo {seen.name}           (starts it again)",
        ]
    if seen.processes:
        return [
            f"  {seen.name} is not running: none of the {len(seen.processes)} processes it "
            "started last time is alive.",
            *_by_role(seen),
            f"  start it again with: fsmes demo {seen.name}",
        ]
    return [f"  {seen.name} is not running. Start it with: fsmes demo {seen.name}"]


def what_stop_found(seen: Look) -> list[str]:
    """What `fsmes demo <plant> stop` says it found, before it acts on it.

    Said first, because afterwards the pids are gone and nobody can check the
    sentence. `fsmes fleet stop` then prints what it did in its own words.
    """
    if seen.answering or not (seen.owned and seen.local):
        return []
    if seen.alive:
        pids = ", ".join(str(p.pid) for p in seen.alive)
        return [f"  {seen.name} was not answering; {len(seen.alive)} of its "
                f"{len(seen.processes)} processes were still alive ({pids}); stopping them."]
    if seen.processes:
        return [f"  {seen.name} was not answering and none of the {len(seen.processes)} "
                "processes it started is alive; clearing what is left of it."]
    return []


def what_stop_leaves(seen: Look) -> list[str]:
    """The line after a successful stop, when there was something to recover."""
    if seen.answering or not seen.processes or not (seen.owned and seen.local):
        return []
    return [f"  `fsmes demo {seen.name}` starts it again."]


# ----------------------------------------------------------------- before a start


def ports_of(pack: Path) -> list[tuple[str, str, int]]:
    """The ports this pack's plant will try to listen on, and what each is for.

    Read from the pack, which is the only place that says: a plant's ports are
    configuration at the plant boundary, not a constant in this file.
    """
    from fsmes.pack import format as fmt

    serve = fmt.read(pack).table("serve")
    wanted = [("the dashboard and the API", serve.get("api_host") or "127.0.0.1",
               int(serve["api_port"]))]
    endpoint = urllib.parse.urlsplit(str(serve.get("opc_endpoint") or ""))
    if endpoint.hostname and endpoint.port:
        wanted.append(("the OPC server", endpoint.hostname, int(endpoint.port)))
    return wanted


def answers(host: str, port: int, *, timeout: float = 0.6) -> bool:
    """Is something already listening there?

    A connection, not a bind: the question is whether this port is somebody
    else's, and a bind test on Windows can succeed against a socket that is
    about to refuse us anyway. A host of `0.0.0.0` is asked on loopback,
    because that is where a plant on this box is reached.

    The connect itself is `fsmes.plant.answers`, because the other side of
    this - a stop waiting for its own port to close - asks the same question
    from below this module, and two spellings of it would be two spellings
    that drift. The name stays here: it is what `fsmes.doctor` and this
    module's own start check call, and what the egress register names.
    """
    return plants.answers(host, port, timeout=timeout)


def _who_else(port: int, *, name: str, data_dir: Path) -> str:
    """Another plant of ours on that port, if the record names one."""
    for entry in owned.load(data_dir).owned:
        if entry.name != name and int(entry.api_port) == port:
            return entry.name
    return ""


def _is_its_own(port: int, *, name: str, data_dir: Path) -> bool:
    """Is that this plant's own recorded port?

    Said apart from `_who_else` because it is a different sentence. Nothing
    of this plant is running - `look` above has already found no live
    processes - so a port that is still answering on the address this
    installation recorded for it is its socket finishing closing, which on
    Windows takes about two seconds. `fsmes.plant.stop` waits for exactly
    that before it returns; this is what is left to say when the wait's
    grace period was not enough.
    """
    return any(entry.name == name and int(entry.api_port) == port
               for entry in owned.load(data_dir).owned)


def in_the_way(name: str, pack: Path, *, data_dir: Path, logs: Path | None = None,
               ask=None) -> list[str]:
    """What will stop this plant coming up, said before anything is launched.

    A plant is five processes started one after another, and a port that is
    taken does not stop the start - it kills the process that wanted it, a
    second or two later, in a log file. A plausible way to end up where the
    first Windows user ended up is exactly that: one plant's API dies, the
    person starts the plant again, and the new processes fail at bind one by
    one while the first plant's survivors keep its ports. Nobody has
    confirmed that is what happened on his PC; his logs are on his machine.

    Empty means nothing is in the way. Anything else is the reason, and the
    caller does not start.
    """
    seen = look(name, data_dir=data_dir, logs=logs, ask=ask)
    if seen.answering:
        # Already up. `fsmes fleet start` says so itself, in its own words.
        return []
    if seen.half_dead:
        return [
            f"  not starting {name}: it is not answering, but {len(seen.alive)} of its "
            f"{len(seen.processes)} processes from the last start are still alive:",
            *_by_role(seen),
            "  they are holding its ports, so starting it again would launch processes "
            "that die one by one. This is the way out:",
            f"      fsmes demo {name} stop      (stops what is left and clears the record)",
            f"      fsmes demo {name}           (starts it again)",
        ]
    try:
        wanted = ports_of(pack)
    except (OSError, KeyError, ValueError):
        # A pack we cannot read is `fsmes fleet create`'s refusal to make, in
        # its own words. Not ours to pre-empt with a guess at its ports.
        return []
    for what, host, port in wanted:
        if not answers(host, port):
            continue
        mine = _who_else(port, name=name, data_dir=data_dir)
        if mine:
            held = (f"{mine}, another plant this installation made and is still running. "
                    f"Stop it with `fsmes demo {mine} stop`, or give this plant another port "
                    f"in its pack.")
        elif _is_its_own(port, name=name, data_dir=data_dir):
            held = (f"{name}'s own socket, still closing. None of its processes is alive, "
                    "so there is nothing to stop: wait a moment and start it again.")
        else:
            held = ("another program on this PC - nothing this installation made is "
                    "recorded on that port. Stop that program, or give this plant another "
                    "port in its pack.")
        return [
            f"  not starting {name}: {host}:{port} is already in use, and that is the port "
            f"{what} needs.",
            f"  what is on it: {held}",
        ]
    return []
