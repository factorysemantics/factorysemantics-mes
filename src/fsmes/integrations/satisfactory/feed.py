"""The poller's files, read as polls.

The poller writes `YYYY-MM-DD.jsonl` (gzipped to `.jsonl.gz` once the day is
over), one line per endpoint per poll:

    {"ts": "2026-10-28T20:22:09-05:00", "endpoint": "getFactory", "data": [...]}

All the lines of one poll carry the same `ts`. A poll here is those lines put
back together: the buildings (`getFactory`), the power circuits (`getPower`),
the generators (`getGenerators`, written by the poller since 2026-10-30) and
the session (`getSessionInfo`), any of which may be missing - the poller
asks each endpoint separately, and the session answer is absent from about
one poll in eight of the first day. A missing answer is missing, never an
empty one.

Nothing here writes, and nothing here knows about the MES.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger("satisfactory.feed")

#: A day's file, plain while the poller is writing it, gzipped after.
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")


@dataclass
class Poll:
    """One poll of the game: what each endpoint answered at one moment."""

    ts: datetime
    """The poll's moment, as naive UTC - the MES's one convention."""
    factory: list[dict] | None = None
    power: list[dict] | None = None
    generators: list[dict] | None = None
    """None when the poll has no generator answer - every file before
    2026-10-30 - which says nothing about whether the save has any."""
    session: dict | None = None
    endpoints: set[str] = field(default_factory=set)

    @property
    def paused(self) -> bool | None:
        """Whether the game said it was paused. None when the session answer
        is missing from this poll, which is not the same as *not paused*."""
        if self.session is None:
            return None
        return bool(self.session.get("IsPaused"))


def utc(stamp: str) -> datetime:
    """An ISO-8601 moment with its offset, as naive UTC. A moment with no
    offset is refused rather than guessed at: the poller writes one, and a
    line without it is not the poller's."""
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        raise ValueError(f"{stamp!r} carries no UTC offset")
    return moment.astimezone(UTC).replace(tzinfo=None)


def day_files(folder: Path) -> list[Path]:
    """Every day's file in the folder, oldest first. A day present both
    plain and gzipped is read once, from the plain file - the poller is
    still writing it, or has just finished."""
    if not folder.is_dir():
        return []
    days: dict[str, Path] = {}
    for path in folder.iterdir():
        match = DAY_FILE.match(path.name)
        if not match or not path.is_file():
            continue
        day, gz = match.group(1), bool(match.group(2))
        if day in days and gz:
            continue
        days[day] = path
    return [days[day] for day in sorted(days)]


def _absorb(poll: Poll, line: dict) -> None:
    endpoint = line.get("endpoint")
    data = line.get("data")
    poll.endpoints.add(str(endpoint))
    if endpoint == "getFactory" and isinstance(data, list):
        poll.factory = data
    elif endpoint == "getPower" and isinstance(data, list):
        poll.power = data
    elif endpoint == "getGenerators" and isinstance(data, list):
        poll.generators = data
    elif endpoint == "getSessionInfo" and isinstance(data, dict):
        poll.session = data


class Follower:
    """Reads the folder's files as polls, and goes on reading them as the
    poller appends.

    It remembers where it got to in the file being written, so a call to
    `polls()` returns only what is new. A trailing line with no newline yet
    is the poller mid-write and is left for the next call. A poll is handed
    out once a line with a later moment arrives, or when `flush` is asked
    for - the last poll of a file has nothing after it.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.done: set[Path] = set()
        self.offset: dict[Path, int] = {}
        self.pending: Poll | None = None
        self.bad_lines = 0

    def _lines(self, path: Path) -> Iterator[dict]:
        if path.suffix == ".gz":
            # A finished day: read whole, once.
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                text = fh.read()
            self.done.add(path)
            chunks = text.splitlines()
        else:
            start = self.offset.get(path, 0)
            with path.open("rb") as fh:
                fh.seek(start)
                raw = fh.read()
            end = raw.rfind(b"\n")
            if end < 0:
                return
            self.offset[path] = start + end + 1
            chunks = raw[: end + 1].decode("utf-8", errors="replace").splitlines()
        for chunk in chunks:
            if not chunk.strip():
                continue
            try:
                line = json.loads(chunk)
                line["_ts"] = utc(str(line["ts"]))
            except (ValueError, KeyError, TypeError):
                self.bad_lines += 1
                continue
            yield line

    def polls(self) -> list[Poll]:
        """Every poll completed since the last call, oldest first."""
        out: list[Poll] = []
        for path in day_files(self.folder):
            if path in self.done:
                continue
            for line in self._lines(path):
                moment = line["_ts"]
                if self.pending is not None and moment != self.pending.ts:
                    if moment > self.pending.ts:
                        out.append(self.pending)
                        self.pending = None
                    else:
                        # Older than the poll being put together: a line out
                        # of order. Counted, never folded into the wrong poll.
                        self.bad_lines += 1
                        continue
                if self.pending is None:
                    self.pending = Poll(ts=moment)
                _absorb(self.pending, line)
            # A plain file from an earlier day is finished once a later day
            # exists; read it to its end and stop looking at it.
        return out

    def flush(self) -> list[Poll]:
        """The poll being put together, as it stands. Called when the files
        have gone quiet: the poller writes a poll's lines within a second or
        two of each other, so a poll nothing has been added to for a while
        is complete."""
        if self.pending is None:
            return []
        poll, self.pending = self.pending, None
        return [poll]


def read_all(folder: Path) -> list[Poll]:
    """Every poll in the folder, oldest first: the whole history at once."""
    follower = Follower(folder)
    return follower.polls() + follower.flush()


def read_file(path: Path) -> list[Poll]:
    """Every poll in one day's file."""
    opener = gzip.open if str(path).endswith(".gz") else open
    polls: list[Poll] = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for chunk in fh:
            if not chunk.strip():
                continue
            line = json.loads(chunk)
            moment = utc(str(line["ts"]))
            if not polls or polls[-1].ts != moment:
                polls.append(Poll(ts=moment))
            _absorb(polls[-1], line)
    return polls
