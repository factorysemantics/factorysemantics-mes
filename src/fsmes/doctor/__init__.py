"""`fsmes doctor`: one file a person can paste to their agent, or send to us.

Four steps, in one place, in this order:

1. `gather` reads this installation and its plants. It touches nothing.
2. `render` writes the Markdown, with every value already shaped by `scrub`.
3. `leak` runs the shape skill's own check over the finished text, against
   the list of values this run actually read.
4. Only then is the file written.

Step 3 is not advisory. If the check finds one of those values in the file,
nothing is written and the command says which keys were involved - because
the promise this file makes is the whole reason anybody would paste it into a
chat window, and a diagnostic that leaks a tag name once is a diagnostic
nobody can be told to run again.

The file is written where the person asked, in UTF-8 with the newline
Python's text mode gives their platform, and the path is printed. Nothing
sends it anywhere: there is no command in this product that does, and there
must not be one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fsmes.doctor import gather, leak, render
from fsmes.doctor.leak import Leak

__all__ = ["DEFAULT_OUT", "Leak", "Written", "write"]

#: Where the file goes when nobody says: the working directory, named for
#: what it is, so `doctor.md` in a chat window needs no explaining.
DEFAULT_OUT = "doctor.md"

#: What the verdict line says before the check has run. It is in the text the
#: check reads, so that the check reads the whole of the finished file -
#: heading, clock and all - rather than the body alone.
BEFORE = "leak check: not yet run"


@dataclass(frozen=True)
class Written:
    """What one run of `doctor` wrote, for the command to print."""

    path: Path
    lines: int
    verdict: str
    plants: int
    values_checked: int


def write(out: Path | str | None = None, *, plant: str | None = None,
          home: Path | None = None) -> Written:
    """Gather, render, check, and write. Raises `Leak` instead of writing.

    `plant` narrows the file to one plant in this home - the home's name for
    it, which is the name `fsmes demo <plant>` takes, not a name out of a
    fleet file somewhere else. `home` is for the tests and for a person with
    more than one home; everything else uses the one `FSMES_HOME` or the
    platform decides.
    """
    report = gather.report(home=home, only=plant)
    draft = render.whole(report, BEFORE)
    verdict = leak.verdict(draft, report.seen)
    text = render.whole(report, verdict)
    path = Path(out or DEFAULT_OUT)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return Written(
        path=path,
        lines=len(text.splitlines()),
        verdict=verdict,
        plants=len(report.plants),
        values_checked=len(report.seen.values),
    )
