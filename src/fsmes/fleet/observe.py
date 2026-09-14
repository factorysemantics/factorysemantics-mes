"""The one place the fleet tooling reaches a plant over the network.

Every question this package asks a plant is a `GET`, and every endpoint it
asks is one a plant already answers without a credential: `/health` says who
the plant is and whether it is in shadow mode, `/pack` says which pack it
runs and whether it has drifted. Nothing here signs in, and nothing here
sends a body.

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
