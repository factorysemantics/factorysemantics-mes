"""Find the business judgments still written into the source, and say where.

Decision [0035](../../../docs/design/config-assistance.md) divides every
setting into three tiers, and gives one test for deciding which tier a thing
is on: **ask what breaks if two plants answer differently.** If nothing
outside the plant breaks, the plant should own it. If an API field or an MQTT
topic would mean something different at the two plants, the product owns it.

That test has been applied by hand, to a handful of settings, on the design
page. This module applies the *first half* of it to the whole codebase,
mechanically: it finds the numbers, mappings and fixed lists that look like
somebody's judgment call, and reports each one with its file, its line, the
literal itself, and the comment sitting next to it. It does not decide the
tier. A person does that, reading the list.

**What it can see.** A numeric literal or a named collection sitting near a
word that names a judgment - threshold, limit, cap, floor, window, rule,
severity, warning, due, capable and the rest of `JUDGMENT_WORDS`. Python is
read with `ast`, so a literal's enclosing function and assignment are known;
the web files are read as text, because a regular expression over 64 files of
vanilla JavaScript finds a `setInterval(..., 5000)` perfectly well and an
ECMAScript parser is a dependency this product does not need.

**What it cannot see, and never will.** A judgment with no number in it. The
four Western Electric rules in `services/spc.py` are the standing example:
*which* rules a plant runs is as arguable as the Cpk bar next to it, but the
choice is expressed as four `for` loops, and no scanner looking for literals
is going to call a `for` loop a setting. Whatever this tool finds is a floor,
not a ceiling - `docs/design/config-audit-2026-09-21.md` says which entries
on the list came from a person reading the code instead.

**Why it is a command and not a report.** A report is true on the day it is
written. `fsmes config-audit` is true on the day it is run, which is the only
useful kind of true for a codebase that gains a file a week. Rerun it; diff
the JSON; the new literals are the ones somebody added since.

**The judgments a scan cannot make, carried beside it.** Two things about a
candidate are a person's call and not a scanner's: which domain owns it, and
**whose answer it is** - its *scope*. Domain is decided by `DOMAIN_RULES`
below. Scope lives in `config_audit_curated.py`, one row per candidate a
person kept, and it decides who gets asked:

- `general` - one default across every plant, so a maintainer weighs in;
- `plant` - the plant's own answer, routed to its domain's Configuration tab;
- `object` - a property of one tag, machine, material, gauge or order, set by
  the engineer who knows that object.

**Only `general` is an open question.** The other two are answered where the
thing is configured, by the person in front of it, and asking a maintainer to
pick a number for somebody else's tag is how a list of 75 becomes 75
questions nobody can honestly answer. `--scope` filters by it; `--json`
carries it beside the literal.

Deterministic. No model, no network, no database, no plant.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fsmes.sim.config_audit_curated import (
    CURATED,
    SCOPE_RULE,
    SCOPE_TITLES,
    Curated,
)

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src" / "fsmes"
WEB = SRC / "web"


# ------------------------------------------------------------------ the words

#: Words that name a judgment. A literal is a candidate when one of these
#: appears in its neighbourhood - the name it is bound to, the function it
#: sits in, the line itself, or the three lines either side of it.
#:
#: The list is deliberately broad, because a false positive costs a person one
#: line of reading and a false negative costs them a setting they never knew
#: they had. The report separates strong evidence from weak so a broad list
#: does not drown the strong ones.
JUDGMENT_WORDS: tuple[str, ...] = (
    "threshold", "thresholds", "limit", "limits", "cap", "floor", "ceiling",
    "window", "windows", "rule", "rules", "severity", "warning", "warn",
    "due", "capable", "cpk", "sigma", "tolerance", "grace", "stale",
    "timeout", "retry", "retries", "backoff", "debounce", "cooldown",
    "cadence", "refresh", "minimum", "maximum", "at least", "at most",
    "priority", "recent", "soon", "margin", "quota", "cutoff", "expiry",
    "expires", "lifetime", "retention", "prune", "shortfall", "acceptable",
    "too many", "too few", "too long", "too short", "worth",
)

#: Words that mean the author already knew this was arguable. A comment
#: carrying one of these is the strongest single signal in the scan: somebody
#: hedged in prose next to a number, which is what a judgment call sounds like
#: before anybody calls it configuration. `maintenance.py`'s *"A plant wants
#: warning, not a surprise"* is the example the audit was calibrated on.
HEDGE_WORDS: tuple[str, ...] = (
    "a plant", "plants", "for now", "arguable", "judgment", "judgement",
    "arbitrary", "magic number", "rule of thumb", "convention", "typical",
    "typically", "usually", "we picked", "we chose", "worth", "should be",
    "could be", "might want", "tune", "tuned", "tuning", "assume",
    "assumption", "heuristic", "industry", "commonly", "sensible", "taste",
    "opinion", "preference", "configurable", "hard-coded", "hardcoded",
)


# ------------------------------------------------------------- what is not it

#: Integers that are structure rather than judgment almost every time they
#: appear: an index, a count of two things, a sign. Floats are never excluded
#: this way - `1.0` as a capability bar and `1` as a list index are different
#: kinds of number, and the scan can tell them apart because Python can.
STRUCTURAL_INTS = frozenset({0, 1, 2, -1, -2})

#: A float this close to zero is a comparison epsilon or a clamp, not a
#: setting: `max(0.0, x)`, `if diff > 1e-9`. Nobody configures the width of a
#: rounding error.
EPSILON = 1e-4

#: Unit conversions and machine constants. A plant does not get an opinion
#: about how many seconds are in a day. Suppressed by value, and counted, so
#: "we looked at everything" stays a checkable claim rather than an assertion.
#: (`60` and `60.0` are the same value to Python, so the set holds each once
#: and the membership test catches both spellings.)
UNIT_CONSTANTS = frozenset({
    60, 100, 128, 256, 360, 512, 1000, 1024, 1440, 3600, 4096, 8192,
    86400, 65536, 1_000_000, 1_048_576,
})

#: Lines whose numbers belong to a protocol or to the shape of a table, not to
#: the plant: HTTP status codes, SQLAlchemy column widths, ports, versions.
NOT_A_JUDGMENT_LINE = re.compile(
    r"status_code|HTTPException|\bString\(|\bNumeric\(|\bInteger\(|\bVARCHAR"
    r"|\bColumn\(|__version__|\bport\b|\bPORT\b|sys\.version|python_requires"
    r"|\bhttpx\.|\.raise_for_status|\bstatus\s*==\s*\d|\bhttp/\d",
    re.IGNORECASE)

#: Files whose every literal is already configuration, or is not the product's
#: judgment about a plant at all. Each is counted and named in the report.
SKIPPED_PATHS: tuple[tuple[str, str], ...] = (
    ("sim/", "the simulator invents a plant; its numbers are a scenario, "
             "not a setting - and this scanner lives here, so it would "
             "otherwise find its own word list"),
    ("lab/", "the lab harness is development tooling, not the product"),
    ("migrations/", "a migration is history; changing its numbers rewrites "
                    "what already happened"),
    ("config.py", "every literal here is already a setting - it is the "
                  "settings file"),
    ("seed.py", "seed data describes an example plant"),
    ("seed_line.py", "seed data describes an example plant"),
    ("seed_kepsim.py", "seed data describes an example plant"),
    ("demo_feed.py", "the demo invents a plant"),
    ("web/vendor/", "somebody else's library; this plant does not configure it"),
    (".min.js", "minified - every line is one line, so every number in the "
                "file would look like it sits next to every word in it"),
    ("web/demo/", "the demo invents a plant"),
    ("erp/examples.py", "example payloads describe a made-up order"),
    ("erp/mock_erp.py", "a stand-in ERP for the demo, not the plant"),
)


# ---------------------------------------------------------------- the domains

#: Which of decision 0035's six domains owns a file. The mapping is itself a
#: judgment, written down here rather than implied, so it can be argued with.
#: Order matters - the first prefix that matches wins.
#:
#: There is no catch-all. A file that matches nothing is reported as
#: `unassigned` with its own total, because quietly folding it into the
#: biggest domain would be exactly the "unknown reported as zero" this
#: product's house rule 2 refuses.
DOMAIN_RULES: tuple[tuple[str, str], ...] = (
    # Quality engineering
    ("services/spc.py", "quality"),
    ("services/quality.py", "quality"),
    ("services/gauges.py", "quality"),
    ("services/coa.py", "quality"),
    ("services/serialization.py", "quality"),
    ("domain/quality.py", "quality"),
    ("api/routers/quality.py", "quality"),
    ("web/quality", "quality"),
    ("web/gauges", "quality"),
    ("web/coa", "quality"),
    # Controls engineering
    ("integrations/opc/", "controls"),
    ("integrations/inbound/", "controls"),
    ("integrations/mqtt", "controls"),
    ("services/tags.py", "controls"),
    ("services/triggers.py", "controls"),
    ("services/adjustments.py", "controls"),
    ("services/connection.py", "controls"),
    ("services/inbound.py", "controls"),
    ("services/uns.py", "controls"),
    ("kernel/tags.py", "controls"),
    ("api/routers/tags.py", "controls"),
    ("api/routers/triggers.py", "controls"),
    ("api/routers/adjustments.py", "controls"),
    ("web/machine", "controls"),
    ("web/station", "controls"),
    ("web/adjustments", "controls"),
    ("web/tags", "controls"),
    # Supply chain / ERP
    ("integrations/erp", "supply-chain"),
    ("services/erp.py", "supply-chain"),
    ("services/outbox.py", "supply-chain"),
    ("services/staging.py", "supply-chain"),
    ("api/routers/erp", "supply-chain"),
    # Plant administration
    ("services/auth.py", "administration"),
    ("services/capabilities.py", "administration"),
    ("services/audit.py", "administration"),
    ("services/retention.py", "administration"),
    ("services/review.py", "administration"),
    ("services/drafting.py", "administration"),
    ("services/walkthroughs.py", "administration"),
    ("services/assistant.py", "administration"),
    ("services/agent.py", "administration"),
    ("services/ai_status.py", "administration"),
    ("services/design", "administration"),
    ("identity.py", "administration"),
    ("modules.py", "administration"),
    ("mcp", "administration"),
    ("api/routers/admin.py", "administration"),
    ("api/routers/auth.py", "administration"),
    ("api/routers/design.py", "administration"),
    ("web/admin", "administration"),
    ("web/config", "administration"),
    ("web/common.js", "administration"),
    ("web/app.js", "administration"),
    ("web/assist", "administration"),
    ("web/design", "administration"),
    # IT
    ("db.py", "it"),
    ("storage.py", "it"),
    ("backup.py", "it"),
    ("logging.py", "it"),
    ("schema.py", "it"),
    ("shadow.py", "it"),
    ("api/app.py", "it"),
    ("pack/", "it"),
    ("fleet/", "it"),
    ("web/fleet", "it"),
    # Manufacturing / process engineering - everything the plant makes with
    ("services/maintenance.py", "process"),
    ("services/execution.py", "process"),
    ("services/scheduling.py", "process"),
    ("services/oee.py", "process"),
    ("services/workorders.py", "process"),
    ("services/line.py", "process"),
    ("services/line_clock.py", "process"),
    ("services/calendar.py", "process"),
    ("services/analysis.py", "process"),
    ("services/coverage.py", "process"),
    ("services/equipment.py", "process"),
    ("services/masterdata.py", "process"),
    ("services/documents.py", "process"),
    ("services/reasons.py", "process"),
    ("services/hold", "process"),
    ("domain/", "process"),
    ("api/routers/", "process"),
    ("web/", "process"),
)

#: The six domains of decision 0035, in the order the design page names them,
#: plus the honest seventh for a file the table above does not place.
DOMAIN_TITLES: dict[str, str] = {
    "administration": "Plant administration",
    "process": "Manufacturing / process engineering",
    "controls": "Controls engineering",
    "quality": "Quality engineering",
    "supply-chain": "Supply chain / ERP",
    "it": "IT",
    "unassigned": "Unassigned - the domain table does not place this file",
}


def domain_of(relative_path: str) -> str:
    """Which domain owns this file, or `unassigned` when nothing claims it."""
    for prefix, domain in DOMAIN_RULES:
        if prefix in relative_path:
            return domain
    return "unassigned"


# ----------------------------------------------------------------- a finding


@dataclass
class Candidate:
    """One literal that might be a plant's judgment rather than the product's.

    `strength` is the scan's own confidence, and nothing more: `strong` means
    a hedging comment sits beside it or the name it is bound to says what it
    is; `possible` means only the neighbourhood matched. Neither is a verdict.
    A person applies the two-plants test; this says where to look.
    """

    domain: str
    path: str
    line: int
    literal: str
    what: str
    """The name it is bound to, or the expression it sits in."""
    why: list[str] = field(default_factory=list)
    """Which judgment words fired, so the match can be argued with."""
    comment: str | None = None
    """The comment or docstring line beside it, if there is one."""
    strength: str = "possible"
    curated_id: str | None = None
    """The id the curated list gave this line - `C14` and the rest - when a
    person kept it. `None` means the scan found it and nobody has read it
    yet, which is most of them."""
    scope: str | None = None
    """Whose answer it is: `general`, `plant` or `object`. Only a curated
    line has one, because scope is a judgment and the scan makes none."""

    def sort_key(self) -> tuple:
        return (self.strength != "strong", self.path, self.line)


# ------------------------------------------------------- the curated judgment


@dataclass
class CuratedFinding:
    """One curated candidate, checked against the source as it stands today.

    A curated row is pinned to a fragment of its line and not to a line
    number, because a line number is true until somebody adds an import above
    it. `where` says which of three things is true now:

    - `here` - the anchor is on the line the curated list recorded;
    - `moved` - the anchor is alive, at a different line, and `line_now` says
      where (the row is still good; the page's citation is what is stale);
    - `stale` - the anchor is gone. Nobody can say whether that judgment
      still exists, so somebody has to read that row again. It is reported,
      never dropped.
    """

    entry: Curated
    line_now: int | None
    where: str
    seen_by_scan: bool
    """Whether the scan also found a literal on that line. Some of these were
    found by a person reading the file - a judgment written as a `for` loop, a
    prose house style, a `startswith` test with no number in it - and no
    scanner is going to see those. Reporting which of the two found each row
    keeps the scanner's blind spots visible instead of implied."""

    @property
    def path(self) -> str:
        """The path as the rest of this module spells it."""
        return f"src/fsmes/{self.entry.path}"


def _anchor_lines(source_root: Path, entry: Curated) -> list[int]:
    """Every line in the file that still carries this row's anchor."""
    try:
        text = (source_root / entry.path).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [i for i, line in enumerate(text.splitlines(), start=1)
            if entry.needle in line]


def locate_curated(candidates: list[Candidate],
                   source_root: Path | None = None) -> list[CuratedFinding]:
    """Check every curated row against the source, in the order it was written.

    Also the only place scope is attached to a scanned candidate: a candidate
    sitting on a curated row's line gets that row's id and scope, so
    `--json` carries the judgment beside the literal rather than in a
    document somebody has to go and find.
    """
    root = source_root or SRC
    scanned: dict[tuple[str, int], Candidate] = {
        (c.path, c.line): c for c in candidates}
    findings: list[CuratedFinding] = []
    for entry in CURATED:
        hits = _anchor_lines(root, entry)
        if not hits:
            line_now, where = None, "stale"
        elif entry.line in hits:
            line_now, where = entry.line, "here"
        else:
            line_now, where = hits[0], "moved"
        relative = f"src/fsmes/{entry.path}"
        found = scanned.get((relative, line_now)) if line_now else None
        if found is not None:
            found.curated_id = entry.id
            found.scope = entry.scope
        findings.append(CuratedFinding(entry, line_now, where, found is not None))
    return findings


# ------------------------------------------------------------- reading Python


def _context(lines: list[str], line_no: int, span: int = 3) -> str:
    """The line, and `span` lines either side of it, lowercased."""
    lo = max(0, line_no - 1 - span)
    hi = min(len(lines), line_no + span)
    return " ".join(lines[lo:hi]).lower()


def _comment_near(lines: list[str], line_no: int, span: int = 4) -> str | None:
    """The comment attached to this line: trailing on it, or the block above.

    Read upward, because a block comment explaining a number sits above it and
    a plant-facing docstring sits above the function. The first non-comment,
    non-blank line stops the walk.
    """
    own = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
    if "#" in own:
        after = own.split("#", 1)[1].strip()
        if after:
            return after
    block: list[str] = []
    for i in range(line_no - 2, max(-1, line_no - 2 - span), -1):
        if i < 0:
            break
        text = lines[i].strip()
        if text.startswith("#"):
            block.insert(0, text.lstrip("#").lstrip(":").strip())
        elif text.startswith(('"""', "'''")) and len(text) > 3:
            block.insert(0, text.strip("\"'").strip())
            break
        elif text:
            break
    joined = " ".join(p for p in block if p)
    return joined or None


#: Each judgment word, matched on word boundaries. Substring matching was the
#: first attempt and it was useless: `cap` found every `capability`, `due`
#: found every `produced`, and the scan reported four thousand candidates,
#: which is the same as reporting none. Identifiers are normalised by turning
#: `_` into a space first, so `due_soon` matches `due` and `capability` still
#: does not match `cap`.
_WORD_RE = {w: re.compile(rf"\b{re.escape(w)}\b") for w in JUDGMENT_WORDS}


def _normalise(text: str) -> str:
    return text.lower().replace("_", " ")


def _words_in(text: str) -> list[str]:
    flat = _normalise(text)
    return [w for w, rx in _WORD_RE.items() if rx.search(flat)]


def _hedges_in(text: str | None) -> list[str]:
    if not text:
        return []
    low = _normalise(text)
    return [w for w in HEDGE_WORDS if w in low]


class _Walk(ast.NodeVisitor):
    """Collect every numeric literal and named collection worth a second look.

    Two kinds of thing are collected. A **number** is reported at the line it
    sits on. A **collection** bound to a name - `RULE_WINDOW = {1: 1, 2: 3}` -
    is reported once, as itself, and its own numbers are suppressed: a mapping
    of four windows is one decision, not four, and listing it four times is
    how a report teaches people to skim it.
    """

    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.lines = source.splitlines()
        self.found: list[Candidate] = []
        self._scope: list[str] = []
        self._covered: set[int] = set()
        """Lines already claimed by a collection, so their numbers are not
        reported a second time on their own."""

    # -- scope tracking, so a literal can say which function it lives in

    def _enter(self, node) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter
    visit_ClassDef = _enter

    # -- named collections

    def visit_Assign(self, node: ast.Assign) -> None:
        self._maybe_collection(node, node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._maybe_collection(node, [node.target])
        self.generic_visit(node)

    def _maybe_collection(self, node, targets) -> None:
        value = node.value
        if not isinstance(value, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
            return
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not names:
            return
        name = names[0]
        if not _is_constant_collection(value):
            return
        lo, hi = node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno
        text = " ".join(self.lines[lo - 1:hi])
        words = sorted(set(_words_in(name.lower()) + _words_in(text.lower())))
        if not words:
            return
        comment = _comment_near(self.lines, lo)
        hedges = _hedges_in(comment)
        strong = bool(hedges) or bool(_words_in(name.lower()))
        self._covered.update(range(lo, hi + 1))
        self.found.append(Candidate(
            domain="", path=self.path, line=lo,
            literal=_shorten(ast.unparse(value)),
            what=f"{name} (a fixed {type(value).__name__.lower()})",
            why=words + [f"comment hedges: {h}" for h in hedges],
            comment=comment,
            strength="strong" if strong else "possible"))

    # -- bare numbers

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return
        line_no = node.lineno
        if line_no in self._covered:
            return
        value = node.value
        if isinstance(value, int) and value in STRUCTURAL_INTS:
            return
        if isinstance(value, float) and abs(value) < EPSILON:
            return
        if value in UNIT_CONSTANTS:
            return
        own = self.lines[line_no - 1] if line_no <= len(self.lines) else ""
        if NOT_A_JUDGMENT_LINE.search(own):
            return
        if "typer.Option" in own or "typer.Argument" in own:
            return          # a CLI default is already something you can pass
        if _ROUNDING.search(own):
            return          # how many decimals a figure prints is a format,
                            # not a policy - and it is on almost every line
                            # that computes one
        scope = " ".join(self._scope).lower()
        near = _context(self.lines, line_no)
        words = sorted(set(_words_in(scope) + _words_in(near)))
        if not words:
            return
        comment = _comment_near(self.lines, line_no)
        hedges = _hedges_in(comment)
        strong = bool(hedges) or bool(_words_in(own.lower()))
        self.found.append(Candidate(
            domain="", path=self.path, line=line_no,
            literal=repr(value),
            what=(" > ".join(self._scope) or "module level") + ": " + own.strip()[:90],
            why=words + [f"comment hedges: {h}" for h in hedges],
            comment=comment,
            strength="strong" if strong else "possible"))


def _is_constant_collection(node) -> bool:
    """True when every leaf of this collection is a literal.

    A list built from a call or a name is code with a shape, not a table
    somebody typed - and a table somebody typed is the thing worth finding.
    """
    if isinstance(node, ast.Dict):
        parts = list(node.keys) + list(node.values)
    elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        parts = list(node.elts)
    else:
        return isinstance(node, ast.Constant)
    if not parts:
        return False
    return all(p is not None and _is_constant_collection(p) for p in parts)


def _shorten(text: str, width: int = 88) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


# ---------------------------------------------------------- reading web files

#: `round(x, 2)` and friends. The precision a figure is printed to is a
#: formatting choice; it sits on most lines that compute a number, and leaving
#: it in buried the candidates that matter underneath it.
_ROUNDING = re.compile(r"\bround\s*\(")

#: A number in JavaScript or in an inline script, with what it is called.
_JS_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")


def _scan_web(path: Path, relative: str) -> list[Candidate]:
    """Numbers in the browser code, read as text.

    Vanilla JavaScript and HTML, so there is no AST here and none is wanted:
    the judgments that live in the browser are refresh cadences, how many rows
    a panel shows before it stops, and how long a message stays up, and all
    three are a number on a line whose words say what it is.
    """
    out: list[Candidate] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, own in enumerate(lines, start=1):
        low = own.lower()
        if "//" in own and own.strip().startswith("//"):
            continue                      # a comment line is evidence, not a find
        words = sorted(set(_words_in(low) + _words_in(_context(lines, i))))
        timer = "setinterval" in low or "settimeout" in low
        if not words and not timer:
            continue
        for raw in _JS_NUMBER.findall(own):
            value = float(raw) if "." in raw else int(raw)
            if isinstance(value, int) and value in STRUCTURAL_INTS:
                continue
            if isinstance(value, float) and abs(value) < EPSILON:
                continue
            if value in UNIT_CONSTANTS:
                continue
            comment = own.split("//", 1)[1].strip() if "//" in own else None
            hedges = _hedges_in(comment)
            out.append(Candidate(
                domain="", path=relative, line=i, literal=raw,
                what=own.strip()[:90],
                why=(words or ["a timer"]) + [f"comment hedges: {h}" for h in hedges],
                comment=comment,
                strength="strong" if (timer or hedges) else "possible"))
    return out


# --------------------------------------------------------------- the whole run


@dataclass
class Run:
    """One pass over the tree, and everything needed to check the claim.

    `files_scanned` and `files_skipped` are here so that "we looked at
    everything" can be checked rather than believed - house rule 2, applied to
    the audit's own list.
    """

    candidates: list[Candidate]
    files_scanned: int
    files_skipped: list[tuple[str, str]]
    unreadable: list[tuple[str, str]]
    curated: list[CuratedFinding] = field(default_factory=list)
    """The candidates a person kept, each with the scope they argued it into.
    Empty when the scan was pointed at a tree that is not this product's
    source, because a judgment about this codebase says nothing about
    somebody else's."""

    def by_domain(self) -> dict[str, list[Candidate]]:
        out: dict[str, list[Candidate]] = {k: [] for k in DOMAIN_TITLES}
        for c in self.candidates:
            out[c.domain].append(c)
        for rows in out.values():
            rows.sort(key=Candidate.sort_key)
        return out

    def totals(self) -> dict[str, int]:
        return {
            "files_scanned": self.files_scanned,
            "files_skipped": len(self.files_skipped),
            "unreadable": len(self.unreadable),
            "candidates": len(self.candidates),
            "strong": sum(1 for c in self.candidates if c.strength == "strong"),
            "possible": sum(1 for c in self.candidates if c.strength == "possible"),
            "curated": len(self.curated),
        }

    def curated_by_scope(self) -> dict[str, list[CuratedFinding]]:
        """The curated list, split by whose answer it is. Every scope is a
        key, including an empty one - a scope missing from a report is a
        scope nobody can argue with."""
        out: dict[str, list[CuratedFinding]] = {k: [] for k in SCOPE_TITLES}
        for finding in self.curated:
            out[finding.entry.scope].append(finding)
        return out

    def curated_totals(self) -> dict[str, int]:
        """How the curated list divides, and how much of it still anchors."""
        by_scope = self.curated_by_scope()
        totals = {scope: len(rows) for scope, rows in by_scope.items()}
        totals["curated"] = len(self.curated)
        totals["moved"] = sum(1 for f in self.curated if f.where == "moved")
        totals["stale"] = sum(1 for f in self.curated if f.where == "stale")
        totals["unsure"] = sum(1 for f in self.curated if f.entry.unsure)
        totals["seen_by_scan"] = sum(1 for f in self.curated if f.seen_by_scan)
        totals["seen_by_a_person"] = sum(
            1 for f in self.curated if not f.seen_by_scan)
        return totals


def _skip_reason(relative: str) -> str | None:
    for fragment, reason in SKIPPED_PATHS:
        if fragment in relative:
            return reason
    return None


def scan(root: Path | None = None) -> Run:
    """Walk the product's source and collect every configuration candidate."""
    src = (root or SRC)
    repo = src.parents[1] if src.name == "fsmes" else src
    candidates: list[Candidate] = []
    scanned = 0
    skipped: list[tuple[str, str]] = []
    unreadable: list[tuple[str, str]] = []

    paths = sorted(
        [p for p in src.rglob("*.py")]
        + [p for p in (src / "web").rglob("*.js")]
        + [p for p in (src / "web").rglob("*.html")])

    for path in paths:
        relative = str(path.relative_to(repo)) if repo in path.parents else str(path)
        relative = relative.replace("\\", "/")
        reason = _skip_reason(relative)
        if reason:
            skipped.append((relative, reason))
            continue
        try:
            if path.suffix == ".py":
                source = path.read_text(encoding="utf-8")
                walk = _Walk(relative, source)
                walk.visit(ast.parse(source))
                found = walk.found
            else:
                found = _scan_web(path, relative)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            unreadable.append((relative, f"{type(exc).__name__}: {exc}"))
            continue
        scanned += 1
        for c in found:
            c.domain = domain_of(relative)
        candidates.extend(found)

    seen: set[tuple[str, int, str]] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.path, c.line, c.literal)
        if key in seen:
            continue        # the same literal twice on one line is one decision
        seen.add(key)
        unique.append(c)

    unique.sort(key=Candidate.sort_key)
    curated = locate_curated(unique, src) if src == SRC else []
    return Run(unique, scanned, skipped, unreadable, curated)


# ------------------------------------------------------------------ reporting


def as_json(run: Run) -> str:
    """The whole run, for diffing against the next one."""
    return json.dumps({
        "totals": run.totals(),
        "domains": {
            slug: {"title": DOMAIN_TITLES[slug],
                   "total": len(rows),
                   "candidates": [asdict(c) for c in rows]}
            for slug, rows in run.by_domain().items()},
        "curated": {
            "rule": SCOPE_RULE,
            "totals": run.curated_totals(),
            "scopes": {
                scope: {"title": SCOPE_TITLES[scope],
                        "total": len(rows),
                        "candidates": [_curated_json(f) for f in rows]}
                for scope, rows in run.curated_by_scope().items()}},
        "files_skipped": [{"path": p, "why": w} for p, w in run.files_skipped],
        "unreadable": [{"path": p, "why": w} for p, w in run.unreadable],
    }, indent=2, sort_keys=False)


def _curated_json(finding: CuratedFinding) -> dict:
    entry = finding.entry
    return {
        "id": entry.id,
        "domain": entry.domain,
        "scope": entry.scope,
        "what": entry.what,
        "path": finding.path,
        "line_when_written": entry.line,
        "line_now": finding.line_now,
        "where": finding.where,
        "anchor": entry.needle,
        "why_this_scope": entry.why,
        "unsure": entry.unsure,
        "found_by": "the scan" if finding.seen_by_scan else "a person reading",
    }


def as_text(run: Run, only: str | None = None, strong_only: bool = False,
            scope: str | None = None) -> list[str]:
    """The run as lines a person reads, strongest evidence first per domain.

    With `scope`, only the curated list is printed, and only the rows whose
    answer belongs to that scope - the raw scan has no scope, because scope
    is a judgment and the scan makes none.
    """
    if scope:
        return _curated_text(run, scope=scope, only=only)
    totals = run.totals()
    out = [
        f"{totals['candidates']} configuration candidate(s) "
        f"({totals['strong']} strong, {totals['possible']} possible) "
        f"in {totals['files_scanned']} file(s) scanned; "
        f"{totals['files_skipped']} file(s) skipped by rule.",
        "",
        "A candidate is a literal that looks like a judgment a plant could "
        "make differently. It is not a verdict - apply the two-plants test.",
    ]
    for slug, rows in run.by_domain().items():
        if only and slug != only:
            continue
        shown = [r for r in rows if not strong_only or r.strength == "strong"]
        head = f"{DOMAIN_TITLES[slug]} - {len(rows)} candidate(s)"
        if strong_only and len(shown) != len(rows):
            head += f", {len(shown)} strong"
        out += ["", head, "-" * len(head)]
        if not shown:
            out.append("  none")
            continue
        for c in shown:
            tag = f"   [{c.strength}]"
            if c.curated_id:
                tag += f" [{c.curated_id}: {c.scope}]"
            out.append(f"  {c.path}:{c.line}  {c.literal}{tag}")
            out.append(f"      {c.what}")
            out.append(f"      why: {', '.join(c.why)}")
            if c.comment:
                out.append(f"      comment: {_shorten(c.comment, 100)}")
    if run.unreadable:
        out += ["", f"{len(run.unreadable)} file(s) could not be read:"]
        out += [f"  {p}: {w}" for p, w in run.unreadable]
    out += _curated_summary(run)
    return out


def _curated_summary(run: Run) -> list[str]:
    """The curated list's own totals, under the full report."""
    if not run.curated:
        return []
    totals = run.curated_totals()
    out = ["", f"The curated list - {totals['curated']} candidate(s) a person "
               "kept, by whose answer it is:", ""]
    for slug, title in SCOPE_TITLES.items():
        out.append(f"  {totals[slug]:>3}  {title}")
    out += [
        "",
        f"  {totals['seen_by_scan']} of them the scan also finds; "
        f"{totals['seen_by_a_person']} only a person reading the file does.",
        f"  {totals['moved']} have moved since the list was written; "
        f"{totals['stale']} no longer anchor and need re-reading; "
        f"{totals['unsure']} say they are unsure of their scope.",
        "",
        f"  The rule: {SCOPE_RULE}",
        "  Nothing in plant or object is an open question for a maintainer: "
        "it is routed",
        "  to the plant's Configuration tab or to the object's own row. "
        "Use --scope to list them.",
    ]
    return out


def _curated_text(run: Run, scope: str, only: str | None = None) -> list[str]:
    """One scope of the curated list, with the whole list's totals above it."""
    totals = run.curated_totals()
    rows = run.curated_by_scope().get(scope, [])
    if only:
        rows = [f for f in rows if f.entry.domain == only]
    out = [
        f"{totals['curated']} curated candidate(s): "
        + ", ".join(f"{totals[s]} {s}" for s in SCOPE_TITLES),
        "",
        SCOPE_RULE,
        "",
        f"{SCOPE_TITLES[scope]} - {len(rows)} candidate(s)"
        + (f" in {only}" if only else ""),
        "-" * 70,
    ]
    if not rows:
        out.append("  none")
        return out
    for finding in rows:
        entry = finding.entry
        where = finding.line_now if finding.line_now else "gone"
        moved = "" if finding.where == "here" else f"  ({finding.where})"
        out.append(f"  {entry.id}  {entry.what}")
        out.append(f"      {finding.path}:{where}{moved}   [{entry.domain}]")
        out.append(f"      why {entry.scope}: {entry.why}")
        if entry.unsure:
            out.append(f"      unsure: {entry.unsure}")
    return out
