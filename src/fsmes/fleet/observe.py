"""The one place the fleet tooling reaches a plant over the network.

Every question this package asks a plant is a `GET`, and every endpoint it
asks is one a plant already answers without a credential: `/health` says who
the plant is and whether it is in shadow mode, `/pack` says which pack it
runs and whether it has drifted, and `/metrics` is the Prometheus text an
orchestrator already scrapes - which is where the order book's depth comes
from. Nothing here signs in, and nothing here sends a body.

That is deliberate and it is the property a reviewer should check first. A
fleet console is a long-running process on a port, and whatever credential it
holds, whoever reaches that port holds too. This one holds none: the read is
public, so there is nothing to steal from it and nothing it could be
persuaded to do.

**A plant that does not answer is not down.** It is `unknown`, and an
`Answer` says which of the two it is rather than collapsing them. Everything
above this module is written to keep that distinction: a silent plant is
never rendered as healthy, never as down, and - because ownership needs the
plant's own corroboration - never quietly managed on faith.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

#: Long enough for a plant that is busy, short enough that a console polling
#: a dozen of them does not hang on the one that is off.
TIMEOUT = 3.0


@dataclass(frozen=True)
class Answer:
    """What one plant said, or the fact that it said nothing.

    `answered` is the only thing a caller may treat as a yes/no. `body` is
    `None` whenever it is False, and `why` then carries the one phrase a
    person reads: not an exception type, and never the word "down".
    """

    url: str
    answered: bool
    body: dict | None = None
    status: int | None = None
    why: str | None = None

    def get(self, key: str, default=None):
        """One field of the answer, or the default when there was no answer."""
        if not self.answered or not isinstance(self.body, dict):
            return default
        return self.body.get(key, default)


def is_empty(pack_said: dict | None) -> bool:
    """Did this plant say it has nothing in it?

    One definition, read by `fsmes fleet list`, by `fsmes fleet status` and
    by the console, so the page and the command can never disagree about
    which plants are empty. It lives here, with the rest of *what a plant
    said*, rather than in `commands`, because the console may not import a
    verb.

    True only when the plant actually said so:

    * its schema answered and carries no revision - the migrations have never
      run against it, so there is not even a table to be empty; or
    * its line answered and holds no machines.

    False for everything else, **silence included**. A plant that does not
    answer `/pack` at all is not empty, it is unasked, and that is the whole
    reason this is three states and not a boolean.
    """
    if not pack_said:
        return False
    schema = pack_said.get("schema") or {}
    if schema.get("answered") and schema.get("revision") is None:
        return True
    line = pack_said.get("line") or {}
    return bool(line.get("answered")) and line.get("equipment") == 0


def empty_because(pack_said: dict | None) -> str:
    """Which kind of empty this is, as the half-sentence a person reads.

    Empty string when the plant is not empty, or did not say enough to tell.
    """
    schema = (pack_said or {}).get("schema") or {}
    if schema.get("answered") and schema.get("revision") is None:
        return "no schema: this plant's database has never been migrated"
    line = (pack_said or {}).get("line") or {}
    if line.get("answered") and line.get("equipment") == 0:
        return "no line: this plant has no machines on it"
    return ""


def base(host: str, port: int | str) -> str:
    """Where a plant answers, from what a pack said about serving it."""
    return f"http://{host or '127.0.0.1'}:{port}"


def ask(where: str, path: str, *, timeout: float = TIMEOUT) -> Answer:
    """Ask one plant one question. Never raises; silence is an answer.

    `where` is the base (`http://host:port`), `path` the endpoint. A plant
    that refuses, that answers something other than JSON, or that is not
    there at all all come back as `answered=False` with a phrase saying
    which - because "the port is closed" and "the plant answered 403" are
    different facts about a fleet and a console that merged them would be
    inventing one.
    """
    url = f"{where.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        return Answer(url, False, status=exc.code, why=f"answered HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return Answer(url, False, why=f"did not answer ({exc.reason})")
    except (TimeoutError, OSError) as exc:
        return Answer(url, False, why=f"did not answer ({exc})")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Answer(url, False, status=status, why="answered something that is not JSON")
    if not isinstance(body, dict):
        return Answer(url, False, status=status, why="answered JSON that is not an object")
    return Answer(url, True, body=body, status=status)


def health(where: str, *, timeout: float = TIMEOUT) -> Answer:
    """Who this plant says it is, and whether it may act on itself."""
    return ask(where, "/health", timeout=timeout)


def pack(where: str, *, timeout: float = TIMEOUT) -> Answer:
    """Which pack this plant runs, and whether it has drifted from it."""
    return ask(where, "/pack", timeout=timeout)


#: The counts a book is made of, in the order a person reads them.
_BOOK_STATUSES = ("planned", "released", "running")


def book(where: str, *, timeout: float = TIMEOUT) -> dict | None:
    """How many orders this plant still has to run, or None if it did not say.

    Read off `/metrics`, which already carries `mes_work_orders` per status
    and which a plant has answered without a credential since it had metrics
    at all. Nothing new is exposed by asking: this module could not have
    reached an authenticated endpoint anyway, and the reason is the console -
    a long-running process on a port holds no credential here, so there is
    nothing on it to steal.

    `None` is *did not say* and is never rendered as an empty book. A plant
    that is up with nothing left to run and a plant that could not be asked
    are different facts, and the line that made this worth adding - a lab
    plant running one order seventy times over because nothing released a
    second - is exactly the kind a merged answer would hide again.
    """
    url = f"{where.rstrip('/')}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            text = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.startswith("mes_work_orders{"):
            continue
        labels, _, value = line.partition("} ")
        for status in _BOOK_STATUSES:
            if f'status="{status}"' in labels:
                try:
                    counts[status] = int(float(value))
                except ValueError:
                    return None
    if not counts:
        return None
    counts["open"] = sum(counts.get(status, 0) for status in _BOOK_STATUSES)
    return counts
