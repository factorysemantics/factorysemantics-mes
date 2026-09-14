"""A real fleet console, watching this run's plants, asked at each phase.

`fsmes fleet console` is the page somebody leaves open on a second screen. The
one thing it must never do is say a plant is fine when nobody asked it, or say
it is down when nobody could reach it - a plant that did not answer is
**unknown**, and the whole of decision 0023 rests on the difference. Nothing in
this repository tested that against plants that really start and really stop,
because a console needs several plants and a lab run has always had exactly
one at a time.

Which turns out to be the fixture. The lab runs its plants **one after
another** - two replaying at speed on one laptop compete for the same cores,
and a run whose harness fell behind is a run whose numbers are withheld - so at
any moment during a two-plant experiment exactly one plant is answering and the
other one is not. That is the console's hardest case arriving for free, on real
ports, over real HTTP.

So: a console is started before the first plant, told each plant's address the
moment that plant comes up, and asked `/fleet.json` at each phase of the run.
Three rules:

**A real console on a real port.** Not `Console.look()` in process: the thing a
person opens is a page served by a route, and a measurement that skipped the
route would not notice the day the route broke. It is the same dogfood rule the
rest of the lab keeps.

**Its own fleet, and only this run's plants in it.** The console's root is a
directory under the results directory with its own empty fleet file, so a
console started by an experiment can never pick up the plants somebody already
has running on this machine - which would make the totals meaningless and,
worse, plausible.

**It is told, never discovered.** Each plant is written into the console's
ownership file as *observed* at the moment the run first looks at it, because
that is the moment its address is known: an ephemeral plant claims its ports
when it starts. A plant the run has not reached yet is not in the file at all,
and the measurement counts it as such rather than as unknown - not yet built
and not answering are different facts.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: Where a lab console is served. Its own range, above the ephemeral plants'
#: API range (8100-8199) and clear of the product's own console default (8090),
#: because an experiment must never take the port a person left a console on.
CONSOLE_RANGE = (8200, 8249)

#: How long to wait for the console to answer after it is started.
BOOT_TIMEOUT_S = 30.0

FLEET_FILE = """# The fleet a lab run's console watches: this run's plants and nothing else.
#
# Empty on purpose. The plants are written into .data/ownership.toml as
# `[[observe]]` entries while the run plays, because an ephemeral plant claims
# its ports when it starts and nobody knows its address before then.
#
# A console started with --root pointing here can never pick up the plants
# somebody already has running on this machine, which would make its totals
# meaningless and, worse, plausible.
packs = []
"""


@dataclass
class Phase:
    """What the console said at one moment of the run, and what was true."""

    phase: str
    at: str
    #: Plants this run had started and not yet stopped when it was asked.
    running: tuple[str, ...]
    #: Plants the console had been told about at all.
    listed: tuple[str, ...]
    totals: dict = field(default_factory=dict)
    says: str = ""
    plants: dict = field(default_factory=dict)
    unknown_because: str | None = None

    def as_json(self) -> dict:
        return {"phase": self.phase, "at": self.at, "running": list(self.running),
                "listed": list(self.listed), "totals": self.totals, "says": self.says,
                "plants": self.plants, "unknown_because": self.unknown_because}


class LabConsole:
    """A `fsmes fleet console` subprocess over a fleet of this run's plants."""

    def __init__(self, directory: Path, echo=print) -> None:
        self.directory = Path(directory)
        self.echo = echo
        self.phases: list[Phase] = []
        self._proc: subprocess.Popen | None = None
        self._reservation = None
        self._watching: dict[str, str] = {}
        self._running: set[str] = set()
        self.port: int | None = None
        self.refused: list[str] = []

    # -------------------------------------------------------------- setup

    @property
    def _fleet_dir(self) -> Path:
        return self.directory / "labs" / "multiplant"

    @property
    def _data_dir(self) -> Path:
        return self._fleet_dir / ".data"

    def start(self) -> None:
        """Claim a port, lay out the fleet, and start the console."""
        from fsmes import plant as plants
        from fsmes.sim.runner import reserve_port

        self._fleet_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._fleet_dir / "fleet.toml").write_text(FLEET_FILE, encoding="utf-8")
        self._save()

        self._reservation = reserve_port(*CONSOLE_RANGE, "console port")
        self.port = self._reservation.port
        env = dict(os.environ)
        # The console must find this run's fleet and no other. A registry
        # pointing somewhere else is exactly how it would find somebody's real
        # plants instead.
        env.pop(plants.REGISTRY_ENV, None)
        self._proc = subprocess.Popen(
            [plants.fsmes_bin(), "fleet", "console", "--root", str(self.directory),
             "--host", "127.0.0.1", "--port", str(self.port)],
            env=env, stdout=(self.directory / "console.log").open("ab"),
            stderr=subprocess.STDOUT)
        self._await_answer()
        self.echo(f"  a fleet console on 127.0.0.1:{self.port}, watching this run's plants "
                  f"and nothing else")

    def _await_answer(self) -> None:
        deadline = time.time() + BOOT_TIMEOUT_S
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self._ask()
                return
            except Exception as exc:                 # still booting, or gone
                last = exc
                time.sleep(0.2)
        raise RuntimeError(f"the fleet console never answered on port {self.port}: {last}")

    # ------------------------------------------------------------- the run

    def watching(self, name: str, base: str) -> None:
        """Tell the console where this plant is, and that it is up now."""
        if self._watching.get(name) != base:
            self._watching[name] = base
            self._save()
        self._running.add(name)

    def stopped(self, name: str) -> None:
        """This plant has been torn down. It stays in the file - a console
        that forgot a plant the moment it went quiet would never have to say
        *unknown*, which is the one thing it exists to say."""
        self._running.discard(name)

    def look(self, phase: str) -> Phase:
        """Ask `/fleet.json` and write down what it said, and what was true."""
        record = Phase(phase=phase,
                       at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
                       running=tuple(sorted(self._running)),
                       listed=tuple(sorted(self._watching)))
        try:
            said = self._ask()
        except Exception as exc:                     # deliberate - a fact, not a crash
            record.unknown_because = (f"the console did not answer when it was asked: "
                                      f"{type(exc).__name__}: {exc}")
            self.refused.append(phase)
        else:
            record.totals = said.get("totals") or {}
            record.says = str(said.get("says") or "")
            record.plants = {str(p.get("name")): {"answered": p.get("answered"),
                                                  "state": p.get("state"),
                                                  "why": p.get("why"),
                                                  "owned": p.get("owned")}
                             for p in said.get("plants") or []}
        self.phases.append(record)
        return record

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        if self._reservation is not None:
            self._reservation.release()
            self._reservation = None

    # ----------------------------------------------------------- plumbing

    def _save(self) -> None:
        from fsmes.fleet import owned

        owned.save(owned.Record(
            where=owned.path(self._data_dir),
            observed=tuple(owned.Observed(name=name, url=url,
                                          about="an ephemeral plant this experiment ran")
                           for name, url in sorted(self._watching.items()))))

    def _ask(self) -> dict:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/fleet.json", timeout=10) as answer:
            return json.load(answer)

    def as_json(self) -> dict:
        return {
            "_what": "What `fsmes fleet console` said about this run's plants at each phase, "
                     "asked over HTTP from a console started for the run and watching nothing "
                     "else.",
            "port": self.port,
            "phases_total": len(self.phases),
            "looks_the_console_refused": self.refused,
            "phases": [phase.as_json() for phase in self.phases],
        }

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.as_json(), indent=2, default=str) + "\n",
                              encoding="utf-8")
