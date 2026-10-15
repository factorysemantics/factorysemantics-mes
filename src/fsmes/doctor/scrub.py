"""Turn what this installation can see into shapes nobody owns.

`fsmes doctor` writes a file a person pastes into a chat window. The promise
on the first line of that file is that nothing proprietary is in it, and this
module is where that promise is kept: every value that came out of a log, a
pack, a configuration or a database URL goes through here before it is
written, and the leak check in `leak.py` then reads the finished file back and
refuses to let it be written if anything got through.

The idea is the shape skill's, and so is the notation: `WO-2026-0004711`
becomes `AA-####-#######` — a shape, never an instance
(`skills/plant-from-your-mes/scripts/profile.py`, `pattern_of`). What is
added here is the part a log needs and a database profile does not: a log line
is a *sentence* our own code wrote with the plant's values interpolated into
it, so the sentence has to survive while the values do not.

The rule that does that is one line long, and it is the only rule in this file
worth arguing about:

    a word is carried through only when it is letters, and only when it is a
    word this product itself writes (`OUR_WORDS`). Everything else becomes
    its shape.

So `Started server process [536106]` comes out as `Started server process
[<int>]`, which is the message template the handoff asked for and tells an
agent exactly what happened; and `inspected FILL01 for WO-ACME-4711` comes out
as `inspected AA####A for AA-AAAA-####`, which tells it that a characteristic
was inspected on a machine for an order, and tells it nothing about whose.

Two consequences, both deliberate, both stated in `docs/operate/doctor.md`:

* **It over-scrubs.** Numbers that are measurements, not identities, go too —
  a fill weight of 502.3 becomes `<float>`. The file is for diagnosing an
  installation, not for reading data out of it, and `fsmes shadow scorecard`
  is where figures belong.
* **It forgives our own words.** If a plant's own tag is spelled exactly like
  a word this product writes — `temperature`, `running` — that word stays.
  That is the shape skill's own accepted trade (`leakcheck.own_vocabulary`),
  and the list below is short and fixed so that a reader can check it.
"""

from __future__ import annotations

import contextlib
import json
import re
import sysconfig
from functools import lru_cache
from pathlib import Path

#: Words this product writes into its own logs and screens. A match on one of
#: these is our vocabulary, not a plant's.
#:
#: Fixed and listed, not derived from the source tree. Deriving it was tried
#: first and rejected: `src/fsmes/**/*.py` yields 12,731 words in 82 ms, and
#: among them are `acme` and `scott`, which are in the source because the lab
#: packs and the lab passwords are, and which are exactly the class of word
#: this file exists to remove. A list is slower to maintain and impossible to
#: surprise.
OUR_WORDS: frozenset[str] = frozenset({
    # Where a line came from: logger names, split on their dots.
    "fsmes", "opc", "ua", "tcp", "agent", "replay", "sink", "api", "operations",
    "uvicorn", "asyncua", "structlog", "sqlalchemy", "alembic", "httpx", "urllib",
    "asyncio", "anyio", "starlette", "fastapi", "pydantic", "typer", "click",
    "server", "client", "security", "shadow", "access", "error", "root", "main",
    "mqtt", "uns", "erp", "erpnext", "frappe", "sqlite", "aiosqlite", "postgres",
    "postgresql", "psycopg", "pymssql", "pyodbc", "driver", "engine", "pool",
    # What this product calls its own processes, because `<plant>.procs` is a
    # file this product wrote itself and the file quotes those words whole:
    # "the dashboard and the API", "the simulated floor", "the reader that
    # writes the machines into the MES". A process named by what it is for is
    # the whole of what `status` gives a person, and `doctor` says the same.
    "dashboard", "reader", "writes", "answer", "simulated", "scenario",
    # The keys structlog and the standard library write on every line, and
    # the words a traceback is made of.
    "event", "level", "logger", "timestamp", "message", "msg", "logging",
    "record", "filename", "lineno", "func", "stack", "frames", "notes",
    "exc", "tb", "args", "cause", "context", "suppress",
    "syntax", "group", "id", "max", "min", "avg", "sum", "count", "p",
    "debug", "info", "warning", "warn", "critical", "exception", "traceback",
    "recent", "call", "last", "during", "handling", "above", "another",
    "occurred", "caused", "directly", "module", "frame", "locals", "self",
    # What our own events say happened.
    "start", "started", "starting", "startup", "boot", "booted", "online",
    "offline", "stop", "stopped", "stopping", "shutdown", "shutting", "down",
    "up", "connect", "connected", "disconnected", "reconnecting", "listening",
    "bind", "bound", "refused", "timeout", "timed", "retry", "retrying",
    "gave", "watched", "watching", "since", "dispatched", "handed", "counted",
    "sampled", "inspected", "rejected", "accepted", "raised", "posted",
    "staged", "transferred", "requested", "reachable", "unreachable",
    "finished", "complete", "completed", "progress", "waiting", "ticking",
    "idle", "busy", "skipped", "ignored", "dropped", "queued", "flushed",
    "wrote", "written", "write", "read", "reading", "loaded", "applied",
    "created", "cleared", "cancelled", "failed", "failure", "succeeded",
    "ready", "serves", "serving", "press", "quit", "signal", "session",
    "certificate", "credentials", "password", "key", "policy", "encrypting",
    "plaintext", "endpoints", "ingestion", "baseline", "commanded", "means",
    "readings", "batches", "backlog", "peak", "lag", "held", "settle",
    "supervise", "issue", "inspect", "every", "finishes", "keeps", "planned",
    "crew", "does", "work", "floor", "script", "gauges", "register",
    "sampling", "inspection", "walk", "job", "minutes", "seconds", "hours",
    "days", "weeks", "row", "rows", "per", "demo", "lab", "laptop", "instance",
    "application", "mode", "modes", "profile", "words", "version",
    # What this product talks about.
    "plant", "plants", "line", "lines", "area", "cell", "station", "machine",
    "machines", "equipment", "gauge", "characteristic", "measurement",
    "measured", "sample", "order", "orders", "operation", "routing",
    "component", "lot", "batch", "pallet", "shift", "person",
    "operator", "supervisor", "admin", "maintenance", "mechanic",
    "nonconformance", "defect", "defects", "scrap", "rework", "good",
    "quantity", "value", "values", "result", "reason", "rule", "note",
    "state", "status", "coverage", "availability", "oee", "spc", "chart",
    "limits", "window", "total", "share", "mean", "range", "pass", "fail",
    "fault", "seq", "source", "endpoint", "backlog_peak", "changeover",
    "unlabelled", "unassigned", "assigned", "considered", "plans", "because",
    "needs", "was", "shop",
    # Ordinary English. Carried so that a sentence our code wrote stays a
    # sentence; none of these is a thing anybody owns.
    "a", "an", "and", "are", "as", "at", "be", "before", "but", "by", "did",
    "do", "else", "for", "from", "got", "had", "has", "have", "in",
    "into", "is", "it", "its", "left", "may", "no", "not", "nothing", "now",
    "of", "off", "on", "once", "one", "only", "or", "other", "out", "over", "so",
    "than", "that", "the", "their", "them", "then", "there", "this", "those",
    "to", "too", "under", "until", "use", "used", "using", "were", "what",
    "when", "where", "which", "while", "who", "whoever", "why", "will",
    "with", "without", "you", "your", "yet", "anywhere", "available",
    "default", "instead", "fine", "well", "whole", "each", "any", "all",
    "both", "more", "less", "most", "least", "first", "next", "same",
    "still", "already", "again", "here", "how", "if", "must", "never", "new",
    "old", "own", "rest", "see", "set", "says", "said", "say", "something",
    "take", "took", "type", "way", "his", "her", "about", "after", "also",
    "been", "being", "cannot", "could", "would", "should", "made", "make",
    "making", "give", "given", "goes", "going", "gone", "keep", "kept",
    "know", "known", "look", "looking", "name", "named", "none", "number",
    "part", "point", "put", "ran", "run", "running", "second", "side",
    "sure", "tell", "thing", "things", "think", "three", "two",
    "very", "want", "watch", "went", "wrong",
    # The machine this is installed on, said in the words an installer uses.
    "linux", "windows", "darwin", "macos", "posix", "python", "cpython",
    "pip", "pipx", "venv", "virtualenv", "conda", "uv", "wheel", "sdist",
    "editable", "installer", "dist", "site", "packages", "local",
    "appdata", "localappdata", "users", "home", "program", "files",
    # The directories an operating system made, which nobody owns - and the
    # three Windows folders a person unzips into, because "they ran it from
    # Downloads" is a diagnosis.
    "usr", "etc", "srv", "opt", "var", "dev", "proc", "mnt", "media",
    "roaming", "desktop", "documents", "downloads", "onedrive",
    "programs",
    "scripts", "bin", "lib", "path", "exe", "console", "powershell", "cmd",
    "bash", "shell", "utf", "http", "https", "localhost", "loopback", "port",
    "host", "true", "false", "null", "unknown",
    # The names of this product's own files and directories, so that a path
    # it prints reads as a path. A directory a plant owns is not in here and
    # comes out as its shape.
    "jsonl", "toml", "json", "db", "env", "md", "txt", "csv", "html", "zip",
    "log", "file", "mes",
    "pids", "procs", "ownership", "fleet", "packs", "backups", "scorecards",
    "doctor", "masterdata", "certs", "cert", "pem", "tmp", "temp",
    "shape", "inbound", "config", "migrations", "static", "templates", "labs",
    "multiplant", "skills", "docs", "tests", "src", "data", "logs",
})


@lru_cache(maxsize=1)
def vocabulary() -> frozenset[str]:
    """Every word carried through verbatim: ours, the shape skill's, and units.

    One function so that the scrub and the leak check are given the same set.
    A word the scrub keeps and the check does not forgive would fail every
    run; a word the check forgives and the scrub removes would be a check
    that is weaker than it looks.
    """
    from fsmes.doctor import leak

    extra: frozenset[str] = frozenset()
    module = leak.leakcheck()
    if module is not None:
        extra = module.OWN_WORDS | module.UNITS_ALLOWED
    return OUR_WORDS | extra


# ------------------------------------------------------------------ the shapes

_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_NUMBER_WITH_UNIT = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s?([A-Za-z%][A-Za-z%/0-9]{0,7})$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?Z?$")
_LETTERS = re.compile(r"^[A-Za-z]+$")
_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")
_URLISH = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]{1,15}://")
_INTERPRETER_FILE = re.compile(r"^<[A-Za-z][A-Za-z0-9 ._]*>$")

#: An exception's class, possibly with the module it was defined in. Letters
#: and underscores only, in dotted segments: `TimeoutError`,
#: `asyncua.ua.uaerrors._auto.BadSessionIdInvalid`. Carried whole - see
#: `exception_type` for why, and for what it refuses.
_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z_]*(\.[A-Za-z_][A-Za-z_]*)*$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: An interpreter or one of our own executables, with the version in its name:
#: `python3.14`, `python.exe`, `pythonw3.13.exe`, `pypy3.10`. Carried whole
#: because which Python this is, exactly, is the first thing a support call
#: needs, and `aaaaaa#.##` is not an answer.
_EXECUTABLE = re.compile(r"^(python|pythonw|pypy|fsmes|uvicorn|pip)[0-9]*(\.[0-9]+)*t?(\.exe)?$",
                         re.IGNORECASE)

#: Past this many characters a character-by-character pattern is as
#: unreadable as the sentence it replaced and no safer, so the length is
#: carried instead - which is the handoff's own `<str:12>`.
#: The words this home has taught us: the names of the plants in it, which are
#: in the file on purpose and are the one documented exception to the rule
#: above. Set once, by `gather`, before anything is shaped - a plant called
#: `bottling` should read as `bottling` in a log line too, not as `aaaaaaaa`.
_ALSO: frozenset[str] = frozenset()


def also(words) -> None:
    """Teach this run the names of the plants in this home.

    Also forgets what the last run carried whole: `carried` below is a record
    of this run and nothing else.
    """
    global _ALSO
    _ALSO = frozenset(str(w).strip().lower() for w in words if str(w).strip())
    _CARRIED.clear()


def taught() -> frozenset[str]:
    """What `also` was told, for the leak check to forgive the same words."""
    return _ALSO


#: Every exception class this run decided to carry whole. Kept so that the
#: leak check forgives exactly the names the scrub actually wrote and no
#: others: `TimeoutError` is in the file because `exception_type` put it
#: there, and a check that could not tell that from a tag would refuse every
#: file with a traceback in it.
_CARRIED: set[str] = set()


def carried() -> frozenset[str]:
    """The exception classes this run carried whole, for the leak check."""
    return frozenset(_CARRIED)


#: The addresses that identify nobody: they are the same four numbers on
#: every machine on earth, this file prints them on purpose (a plant on
#: loopback is the whole of the evaluation story), and shaping them to
#: `###.#.#.#` would make the ports table useless.
LOOPBACK = frozenset({"127.0.0.1", "0.0.0.0", "localhost", "::1", "::", "[::1]",
                      "0:0:0:0:0:0:0:1"})

PATTERN_LIMIT = 24

#: A list longer than this is carried as its length. Shorter than this and the
#: shapes of its items are the useful part (six machine codes, say).
LIST_LIMIT = 6


def pattern_of(value: str) -> str:
    """`WO-2026-0004711` -> `AA-####-#######`. The shape skill's own function.

    Copied rather than imported because the skill is a standalone script by
    design - it has to run on a plant PC with nothing installed - and four
    lines of character classes is not a second source of truth worth a loader.
    The skill's spelling is in `scripts/profile.py`;
    `test_the_shape_of_a_value_is_the_shape_the_shape_skill_writes` loads that
    script and holds the two spellings to the same answers.
    """
    out: list[str] = []
    for char in str(value):
        if char.isdigit():
            out.append("#")
        elif char.isalpha():
            out.append("A" if char.isupper() else "a")
        else:
            out.append(char)
    return "".join(out)


def url_shape(url: str) -> str:
    """A URL as its scheme and its port, and nothing else.

    The handoff allows exactly that much of an OPC endpoint - "the OPC
    endpoint beyond its scheme and port" is what must not be carried - and a
    port is the thing a person needs on screen when nothing is answering.
    `storage.redacted()` runs first, so a password is gone before anything
    here sees it; what this adds is the host, the user and the path, which
    `redacted` keeps on purpose and a plant owns.
    """
    from fsmes import storage

    said = storage.redacted(str(url))
    match = _URLISH.match(said)
    if not match:
        return shape_token(said)
    scheme = said.split("://", 1)[0]
    rest = said.split("://", 1)[1]
    port = ""
    authority = rest.split("/", 1)[0]
    if ":" in authority:
        maybe = authority.rsplit(":", 1)[1]
        if maybe.isdigit():
            port = maybe
    loopback = authority.split(":", 1)[0] in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
    where = "127.0.0.1" if loopback else "<host>"
    tail = "/..." if "/" in rest and rest.split("/", 1)[1] else ""
    return f"{scheme}://{where}{':' + port if port else ''}{tail}"


def shape_path(path, *, home: Path | None = None, keep: frozenset[str] | None = None) -> str:
    """A path a person can act on, with nobody's name left in it.

    Three anchors, in order: the `fsmes` home, this user's home directory, and
    the Python this is running in - each replaced by what it is rather than by
    where it is, because `C:\\Users\\<a person>\\AppData\\Local\\fsmes` names a
    person and `~/AppData/Local/fsmes` does not.

    Below the anchor, a part is carried when it is one of this product's own
    words or one of `keep` - the plants in this home, whose names are in the
    file on purpose and are the one documented exception - and shaped
    otherwise. Printed with forward slashes on both platforms: a reader on
    Windows can still read it, and an agent is not asked to guess whether a
    backslash is an escape.
    """
    import sys

    spelt = str(path)
    drive = _DRIVE.match(spelt)
    text = Path(spelt.replace("\\", "/")) if drive else Path(spelt)
    allowed = keep or frozenset()
    anchors: list[tuple[Path, str]] = []
    if home is not None:
        anchors.append((Path(home), "<home>"))
    with contextlib.suppress(OSError, RuntimeError):
        anchors.append((Path.home(), "~"))
    anchors.append((Path(sys.prefix), "<python>"))
    if sys.prefix != sys.base_prefix:
        anchors.append((Path(sys.base_prefix), "<python>"))

    prefix = ""
    rest = text
    if text.is_absolute():
        # Only an absolute path is resolved. `Path("C:/Users/...")` is a
        # relative path on Linux, and resolving it would paste this machine's
        # working directory on the front of somebody else's Windows path -
        # which then matches the `~` anchor and reads as though it were ours.
        for root, label in anchors:
            try:
                rest = text.resolve().relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            prefix = label
            break
        else:
            prefix = "/"
            rest = Path(*text.parts[1:])
    elif drive:
        # A Windows path read on Linux - a log line from somebody else's
        # machine. The drive letter is not anybody's name; what follows it is.
        prefix = text.parts[0].rstrip("/\\").upper()
        rest = Path(*text.parts[1:])

    said = []
    for part in rest.parts:
        if (part.lower() in allowed or carried_whole(part, extra=allowed)
                or _EXECUTABLE.match(part)):
            said.append(part)
        else:
            said.append(shape_token(part))
    joined = "/".join(said)
    if prefix in ("", "/"):
        return prefix + joined
    if prefix.endswith(":"):
        return f"{prefix}/{joined}" if joined else prefix
    return f"{prefix}/{joined}" if joined else prefix


def carried_whole(token: str, *, extra: frozenset[str] = frozenset()) -> bool:
    """Is this token made only of words this product writes?

    `extra` adds words this home has taught us - the names of the plants in
    it, which are in the file on purpose - so that `bottling.procs` is carried
    whole for the same reason `bottling` is.

    Split on the four characters our own names are joined with, so
    `opc-agent.jsonl`, `uvicorn.error`, `read-only` and `seconds_per_row` are
    each judged by their pieces and each survives, while `fill_weight`,
    `PAL-700123` and `WO-ACME-4711` do not: a piece with a digit in it, or a
    piece that is not one of our words, fails the whole token.

    This is the one rule that decides what is carried verbatim, and it is
    deliberately the same function the leak check is given
    (`leak.vocabulary`), because a token the scrub keeps and the check does
    not forgive would fail every run, and a token the check forgives and the
    scrub removes would be a check weaker than it looks.
    """
    if str(token).strip().lower() in LOOPBACK:
        return True
    pieces = [piece for piece in re.split(r"[.\-_/]", str(token)) if piece]
    if not pieces:
        return False
    known = vocabulary()
    return all(_LETTERS.match(piece)
               and (piece.lower() in known or piece.lower() in extra or piece.lower() in _ALSO)
               for piece in pieces)


def shape_token(token: str) -> str:
    """One word, as a shape. The whole of the rule this module exists for."""
    text = str(token)
    if not text:
        return text
    if _URLISH.match(text):
        return url_shape(text)
    if _TIMESTAMP.match(text):
        return "<ts>"
    if _DATE.match(text):
        return "<date>"
    if _TIME.match(text):
        return "<time>"
    if _INT.match(text):
        return "<int>"
    if _FLOAT.match(text):
        return "<float>"
    unit = _NUMBER_WITH_UNIT.match(text)
    if unit and unit.group(2).lower() in vocabulary():
        return f"<num>{unit.group(2)}"
    if carried_whole(text):
        return text
    if len(text) <= PATTERN_LIMIT:
        return pattern_of(text)
    return f"<str:{len(text)}>"


def _edges(word: str) -> tuple[str, str, str]:
    """A word split into its leading punctuation, its core, and its trailing.

    `[536106]` is a number in brackets, and the brackets are uvicorn's
    sentence rather than anybody's data.
    """
    start = 0
    while start < len(word) and not (word[start].isalnum() or word[start] in "+-"):
        start += 1
    end = len(word)
    while end > start and not word[end - 1].isalnum():
        end -= 1
    return word[:start], word[start:end], word[end:]


def scrub_text(text: str) -> str:
    """A sentence, word by word: ours kept, everything else shaped."""
    out: list[str] = []
    for piece in re.split(r"(\s+)", str(text)):
        if not piece or piece.isspace():
            out.append(piece)
            continue
        lead, core, trail = _edges(piece)
        out.append(lead + (shape_token(core) if core else "") + trail)
    return "".join(out)


def scrub_name(name: str) -> str:
    """A key or a dotted logger name, part by part.

    `uvicorn.error` and `opc.agent` are this product's and its libraries'
    names for themselves and stay; a logger or a key somebody else's
    integration bound is shaped. Split on dots only - `fill_weight` is one
    token and is shaped, because `fill` and `weight` being our words does not
    make a plant's characteristic ours.
    """
    parts = str(name).split(".")
    said = [part if carried_whole(part)
            else pattern_of(part) if len(part) <= PATTERN_LIMIT else f"<str:{len(part)}>"
            for part in parts]
    return ".".join(said)


def shape_value(value, *, depth: int = 0):
    """One JSON value as its shape, keeping the kinds a reader needs.

    Booleans and nulls are carried as themselves: `shadow: false` is the
    single most useful thing in a plant's first log line and nobody owns a
    boolean.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return "<int>"
    if isinstance(value, float):
        return "<float>"
    if isinstance(value, str):
        return scrub_text(value) if " " in value else shape_token(value)
    if isinstance(value, list):
        if depth >= 2:
            return f"<list:{len(value)}>"
        if len(value) > LIST_LIMIT:
            return f"<list:{len(value)}>"
        return [shape_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if depth >= 2:
            return f"<dict:{len(value)} keys>"
        return {scrub_name(k): shape_value(v, depth=depth + 1) for k, v in value.items()}
    return shape_token(str(value))


# ------------------------------------------------------------ a line of a log

#: Keys carried through with their own values untouched, because each is this
#: product's or a library's own fixed word and a reader needs all three to
#: make sense of anything else on the line.
AS_WRITTEN = ("level", "timestamp")


@lru_cache(maxsize=1)
def _roots() -> tuple[Path, ...]:
    """Directories a traceback's filenames are allowed to be reported inside."""
    import fsmes

    found: list[Path] = []
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        where = paths.get(key)
        if where:
            found.append(Path(where))
    # The directory the package sits in, so `src/fsmes/opc/agent.py` in a
    # checkout comes out with `src/` on the front and is recognisable.
    found.append(Path(fsmes.__file__).resolve().parent.parent)
    return tuple(found)


def shape_source_file(filename: str) -> str:
    """A traceback's filename, kept only where it is a file this machine ships.

    A line number inside a module this installation ships is a fact about our
    code, not about anybody's plant, and it is the whole reason a traceback is
    worth carrying at all - so `src/fsmes/opc/agent.py` survives and so does
    its line number. A file anywhere else is somebody's own script in
    somebody's own directory and is shaped away.
    """
    text = str(filename)
    if _INTERPRETER_FILE.match(text):
        return text
    try:
        path = Path(text).resolve()
    except (OSError, ValueError):                               # pragma: no cover
        return "<file>"
    for root in _roots():
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return "<file>"


def shape_frames(frames) -> tuple[list[dict], int]:
    """A traceback's frames, and how many locals were dropped.

    `locals` is never carried in any shape: structlog's `dict_tracebacks`
    puts every variable in scope at the failure into it, and a variable in
    scope at a failure in this product is an order, a tag or a row.
    """
    out: list[dict] = []
    dropped = 0
    for frame in frames if isinstance(frames, list) else []:
        if not isinstance(frame, dict):
            continue
        dropped += len(frame.get("locals") or {})
        where = shape_source_file(frame.get("filename", ""))
        said: dict = {"filename": where, "name": scrub_name(str(frame.get("name", "")))}
        lineno = frame.get("lineno")
        if where != "<file>" and isinstance(lineno, int):
            said["lineno"] = lineno
        out.append(said)
    return out, dropped


def exception_type(name: str) -> str:
    """An exception's class, carried whole where it can only be ours.

    The handoff asks for this one by name: *a log line with a Traceback still
    shows the exception type and the module*. Without it a traceback says
    "something went wrong" and an agent reading the file can say nothing an
    error count could not.

    The rule is letters, underscores and dots. An exception class is defined
    in Python's standard library, in a package this installation has, or in
    this product - never in a plant's masterdata - and the one way a plant's
    word could reach here is somebody defining a class named after their
    line. A name with a digit, a dash or a space in it is not that class
    anyway and is shaped, as is a name longer than any class has, as is a
    name shouted in capitals: `SECRET_TAG_XYZ` is letters and underscores
    too, and a tag is far more likely to be spelt that way than a class is.
    """
    text = str(name).strip()
    if (len(text) <= 80 and _EXCEPTION_TYPE.match(text)
            and any(c.islower() for c in text)):
        _CARRIED.add(text)
        return text
    return shape_token(text)


def shape_exception(blocks) -> list[dict]:
    """What `dict_tracebacks` wrote, with the type and the frames and nothing else.

    The exception type and the module are what the handoff asks to survive,
    because they are what an agent needs to say *this is the OPC client
    losing its session* rather than *something went wrong*. The message is
    scrubbed word by word like any other sentence: it is where an order
    number ends up.
    """
    said: list[dict] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        frames, dropped = shape_frames(block.get("frames"))
        one: dict = {
            "exc_type": exception_type(str(block.get("exc_type", ""))),
            "exc_value": scrub_text(str(block.get("exc_value", ""))),
            "frames": frames,
        }
        if dropped:
            one["locals_dropped"] = dropped
        said.append(one)
    return said


def _unused(name: str, said: dict) -> str:
    """A key that is not already on this line.

    Two keys a plant wrote can shape to one shape, and a dict would then keep
    only the second - which would be a line that silently lost a field.
    """
    if name not in said:
        return name
    n = 2
    while f"{name}~{n}" in said:
        n += 1
    return f"{name}~{n}"


def scrub_jsonl(line: str) -> str:
    """One line of a `.jsonl` log, scrubbed, as one line of JSON.

    The keys stay, the level and the timestamp stay, the logger name stays
    part by part, and every other value becomes its shape. A line that is not
    JSON at all - a crash that reached the file before logging was set up -
    is scrubbed as a sentence, which is the safe direction.
    """
    text = line.strip()
    if not text:
        return ""
    try:
        record = json.loads(text)
    except (ValueError, TypeError):
        return scrub_text(_ANSI.sub("", text))
    if not isinstance(record, dict):
        return scrub_text(_ANSI.sub("", text))
    said: dict = {}
    for key, value in record.items():
        name = _unused(scrub_name(key), said)
        if key in AS_WRITTEN and isinstance(value, str):
            said[name] = value
        elif key == "logger" and isinstance(value, str):
            said[name] = scrub_name(value)
        elif key == "exception":
            said[name] = shape_exception(value)
        else:
            said[name] = shape_value(value)
    return json.dumps(said, ensure_ascii=False, separators=(", ", ": "))


# ------------------------------------------------- a log that is not structured

_TRACEBACK_START = "Traceback (most recent call last):"
_FRAME = re.compile(r'^\s*File "(?P<file>.*)", line (?P<line>\d+), in (?P<name>.*)$')
_EXC_LINE = re.compile(r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt|Warning))"
                       r"(?::\s*(?P<message>.*))?$")


def traceback_skeleton(lines: list[str]) -> tuple[list[str], int]:
    """The tracebacks out of console text, and how many lines were dropped.

    `plant.log` and `sink.log` are the rendered console, not JSON: they hold
    whatever a child process wrote to its own stderr, which is where an
    import-time crash lands and never reaches the `.jsonl`. So they are worth
    reading and cannot be scrubbed key by key.

    What is carried is the skeleton: the `Traceback` line, each frame's file
    and line and function where the file is one this machine ships, and the
    exception type with its message scrubbed. Everything else is counted and
    dropped - a count rather than a silence, because a reader has to know
    there was more.
    """
    kept: list[str] = []
    dropped = 0
    inside = False
    for raw in lines:
        line = _ANSI.sub("", raw.rstrip("\n")).rstrip()
        if not line.strip():
            continue
        if _TRACEBACK_START in line:
            kept.append(_TRACEBACK_START)
            inside = True
            continue
        frame = _FRAME.match(line)
        if inside and frame:
            where = shape_source_file(frame.group("file"))
            name = scrub_name(frame.group("name"))
            if where == "<file>":
                kept.append('  File "<file>", line <int>, in ' + name)
            else:
                kept.append(f'  File "{where}", line {frame.group("line")}, in {name}')
            continue
        exc = _EXC_LINE.match(line.strip())
        if inside and exc:
            message = exc.group("message")
            said = exception_type(exc.group("type"))
            if message:
                said = f"{said}: {scrub_text(message)}"
            kept.append(said)
            inside = False
            continue
        if inside and line.startswith("    "):
            # The source line the frame points at. It is this product's own
            # source where the frame was ours, but it can equally be a line
            # of somebody's script, so it is counted rather than carried.
            dropped += 1
            continue
        inside = False
        dropped += 1
    return kept, dropped


def scrub_jsonl_lines(lines: list[str]) -> tuple[list[str], int]:
    """A `.jsonl` log's last lines, keeping a crash that never reached JSON.

    A process's `.jsonl` is also where its own stderr lands: anything raised
    before logging was set up, or by the interpreter itself, is written there
    as the console would have painted it - several plain lines in the middle
    of the JSON. A run of plain lines that holds a `Traceback` is read the way
    `plant.log` is read, so the exception type and the frames survive instead
    of being shaped word by word into nothing; any other plain line is
    scrubbed as a sentence, which is what it is. Returns the lines and how
    many were dropped, because a reader has to know there was more.
    """
    said: list[str] = []
    dropped = 0
    console: list[str] = []

    def flush() -> None:
        nonlocal dropped
        if not console:
            return
        if any(_TRACEBACK_START in line for line in console):
            kept, gone = traceback_skeleton(console)
            said.extend(kept)
            dropped += gone
        else:
            said.extend(scrub_text(_ANSI.sub("", line.rstrip("\n")).rstrip())
                        for line in console)
        console.clear()

    for line in lines:
        if not line.strip():
            continue
        if line.lstrip().startswith("{"):
            flush()
            said.append(scrub_jsonl(line))
            continue
        console.append(line)
    flush()
    return said, dropped


# ------------------------------------------------------------------ reading them


def tail(path: Path, lines: int, *, max_bytes: int = 256_000) -> tuple[list[str], bool]:
    """The last `lines` lines of a file, read from its end.

    From the end because a plant's log rotates at five megabytes and `doctor`
    has five seconds for the whole file. The flag says whether there was more
    than this read - a reader who is told "the last 40 of 12,004 lines" knows
    what they are looking at, and a reader who is not, does not.
    """
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - max_bytes)
        handle.seek(start)
        blob = handle.read()
    text = blob.decode("utf-8", errors="replace")
    found = text.splitlines()
    if start > 0 and found:
        # The first line of the window is almost certainly half a line.
        found = found[1:]
    return found[-lines:], (start > 0 or len(found) > lines)


def collapse(lines: list[str]) -> list[tuple[str, int]]:
    """Runs of identical lines, each with how many times it repeated.

    A plant's OPC agent writes one heartbeat every eight seconds, so the last
    forty lines of its log are the same sentence forty times - which is a
    true and useless forty lines of a file that has four hundred.
    """
    out: list[tuple[str, int]] = []
    for line in lines:
        if out and out[-1][0] == line:
            out[-1] = (line, out[-1][1] + 1)
        else:
            out.append((line, 1))
    return out
