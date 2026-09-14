"""Which database a command is looking at, and what that database says.

On 2026-09-14, promoting a plant to 0.2.0, `fsmes pack apply` migrated a
plant's PostgreSQL to head and said so. The next line, `fsmes db-status` in
the same shell, said *"There is no database yet"* — about the process's
default SQLite, which nobody had asked about. The script promoting the plant
read that as failure and rolled a healthy plant back. Afterwards `fsmes pack
status` said the same plant's schema had *never been migrated* while `GET
/pack` on the running plant said it was at head. Two commands whose whole
purpose is to state the truth about a database were stating it about a
different one, and neither said which.

So this module holds the two answers all of them now share:

* **Which database.** The password merge a pack needs - it names the file
  holding the password and never the password itself (decision 0022) - and
  this process's own setting, with the phrase saying it is only a default.
  A pack's and a plant's are read where packs and fleets are read
  (`fsmes.pack.format.database_url`, `fsmes.pack.fleet.database_url`); both
  land back here, and both raise `Unknown` rather than quietly answering
  about a default when they cannot tell.
* **What that database says.** One `Reading`, taken by one function, rendered
  by `fsmes db-status`, `fsmes pack status`, `GET /pack` and the fleet's
  status line. A database that cannot be reached is reported as unreachable
  and never as empty or unstamped — "nobody answered" and "never migrated"
  are different facts, and the whole incident above was the two being merged.

Nothing here connects until it is asked to, and nothing here writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The phrase `db-status` and `init-db` print when nothing named a database.
#: It says which database *and* that it is only a default, because the one
#: line that would have caught the incident is the one naming both.
PROCESS_DEFAULT = ("the process default; pass --pack or --plant for a plant's database")


class Unknown(Exception):
    """Nothing here can say which database is meant. Carries the sentence why."""


# ------------------------------------------------------------ which database


def redacted(url: str) -> str:
    """A database URL with the password taken out, safe to print or write."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}" if ":" in creds else url


def password_in(url: str) -> str | None:
    """The password inside a URL, so a message can be scrubbed of it."""
    if "@" not in url or "://" not in url:
        return None
    creds = url.split("://", 1)[1].rsplit("@", 1)[0]
    return creds.split(":", 1)[1] if ":" in creds else None


def with_password(url: str, password_file: str | os.PathLike | None) -> str:
    """A URL the pack wrote, with the password the pack deliberately does not
    carry put back in from the file it names.

    A pack should be safe to paste into an issue (decision 0022), so it names
    the file and never the secret. A URL that already carries its own password
    is left exactly as it is.
    """
    url = os.path.expandvars(url)
    if not password_file or "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    user, host = rest.split("@", 1)
    if ":" in user:
        return url
    password = Path(os.path.expanduser(str(password_file))).read_text(encoding="utf-8").strip()
    return f"{scheme}://{user}:{password}@{host}"



def of_process() -> tuple[str, str]:
    """The database this process is configured to use, and whether anything
    but the product's own default said so.

    The second half is the whole point. "sqlite:///./fsmes.db" tells a reader
    nothing; "sqlite:///./fsmes.db (the process default)" tells them the
    command was never pointed at their plant.
    """
    from fsmes.config import get_settings

    url = get_settings().database_url
    if os.environ.get("MES_DATABASE_URL"):
        return url, "from MES_DATABASE_URL"
    return url, PROCESS_DEFAULT

# ------------------------------------------------ what that database says


@dataclass(frozen=True)
class Reading:
    """What one database says about its schema, and which database it was.

    Every field is separate on purpose. `why` is the reason the database did
    not answer at all, and while it is set `revision` and `empty` mean
    nothing - a caller that renders them anyway would be inventing the state
    of a database nobody reached.
    """

    url: str
    source: str
    head: str | None
    revision: str | None = None
    empty: bool = False
    why: str | None = None

    @property
    def answered(self) -> bool:
        return self.why is None

    @property
    def at_head(self) -> bool | None:
        """True, False, or None for a database that did not answer. None is
        not "behind"."""
        if not self.answered or self.head is None:
            return None
        return self.revision == self.head

    def looking_at(self) -> str:
        """The first line, printed every time, before anything else is said."""
        return f"Looking at {redacted(self.url)} ({self.source})."

    def sentence(self) -> str:
        """The one sentence about this database's schema, shared by every
        command that says anything about one."""
        if not self.answered:
            return (f"This database did not answer, so nothing here can say what schema it "
                    f"is at: {self.why}")
        if self.at_head:
            return "The database is at the current schema."
        if self.revision is None and self.empty:
            return "There is no database here yet. Create one with `fsmes init-db`."
        if self.revision is None:
            return ("This database has tables but no Alembic stamp - it was created before "
                    "the migrations shipped. `fsmes init-db` will recognise it and stamp it.")
        return "The database is behind. Bring it up with `fsmes init-db`."

    def short(self) -> str:
        """The same answer on one line, for a status block that indents it."""
        if not self.answered:
            return f"unknown - this database did not answer: {self.why}"
        if self.revision is None and self.empty:
            return "no database here yet; `fsmes init-db` creates one"
        if self.revision is None:
            return "not stamped; this database has tables from before the migrations shipped"
        if self.at_head:
            return f"{self.revision} (head)"
        return f"{self.revision}, behind head {self.head}. `fsmes init-db` brings it forward."

    def payload(self) -> dict:
        """The schema object `GET /pack` returns, and the fleet renders.

        `at_head` is tri-state: `None` is a database that did not answer, and
        is not "behind". The *reason* it did not answer is deliberately not
        here - that endpoint is public and a driver's message quotes the host
        and the user it tried. `answered` says which of the two nulls this is,
        and the reason goes in the payload's `unknown` block as a sentence
        saying where to look.
        """
        return {"revision": self.revision, "head": self.head,
                "at_head": self.at_head, "answered": self.answered}


def look(url: str, source: str) -> Reading:
    """Read one database's schema revision. Never raises; silence is an answer.

    The one function `fsmes db-status`, `fsmes pack status`, `GET /pack` and
    `fsmes fleet status` all take their schema answer from, so the four cannot
    disagree about a database they were all pointed at.
    """
    from fsmes import schema

    head: str | None
    try:
        head = schema.head_revision()
    except schema.SchemaError:
        head = None
    try:
        revision = schema.current_revision(url)
        empty = not schema.database_shape(url)
    except Exception as exc:  # every driver has its own; none of them is an answer
        return Reading(url=url, source=source, head=head, why=_scrubbed(exc, url))
    return Reading(url=url, source=source, head=head, revision=revision, empty=empty)


def _scrubbed(exc: Exception, url: str) -> str:
    """Why a database did not answer, with the password taken out of it.

    A driver's message quotes the connection it was given, so it can carry the
    password that the pack went to some trouble not to write down.
    """
    said = " ".join(str(exc).split())
    password = password_in(url)
    if password:
        said = said.replace(password, "***")
    return said or exc.__class__.__name__
