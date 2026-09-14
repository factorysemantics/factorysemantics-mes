"""Several runs, read together: what recurs, cited to where it came from.

One run is an instrument reading. A finding is a thing that happened twice.
So this reads whole results directories - the notes somebody left on the
screens, every row where the MES and the script disagreed, and every question
a run could not answer - and clusters them by the screen they are about and
the measurement they belong to.

What it will not do, and the reason each rule exists:

**It never says which side is right.** A run states the truth the generator
obeyed and the answer the MES gave; putting three of those beside each other
adds no authority to either. `findings.md` is a place to look, not a verdict.
A test forbids the words.

**Every line is cited.** A cluster names its runs, its plants, its stations
and its numbers, and quotes a note verbatim with who said it and the line
second they said it at. A roll-up nobody can trace back is a rumour.

**It works with no model at all.** Clustering is by screen and by
measurement, which are facts in the files - so CI can run it, and so can a
machine with no Ollama. When the local model is there it is asked for one
thing only: a short label for a cluster somebody has to skim. It is never
asked what a cluster means.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fsmes.lab import feedback as feedback_mod
from fsmes.lab import measure

#: Words a cluster's title may not contain. The local model is asked only to
#: label, but a model asked for a label will happily offer a verdict, and a
#: roll-up that starts judging is one nobody can use as evidence again. A
#: title carrying one of these is dropped and the plain one kept.
FORBIDDEN = (
    "wrong", "right", "correct", "incorrect", "bug", "broken", "should",
    "must", "better", "worse", "fix", "fail", "failing", "failure", "invalid",
)


class ReviewError(Exception):
    """The review cannot be run as asked, and says why in one sentence."""


# ------------------------------------------------------------------ reading

def read_run(directory: Path) -> dict:
    """One results directory, as the rows a roll-up reads.

    A directory missing its scores.json is refused by name. Silently skipping
    it would make `lab review` quietly answer about fewer runs than it was
    given, which is the class of thing this whole lab exists to catch.
    """
    directory = Path(directory).expanduser().resolve()
    scores_path = directory / "scores.json"
    if not scores_path.is_file():
        raise ReviewError(
            f"{directory} is not a run: a run directory has a scores.json in it.")
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    run = directory.name
    rows = {
        "run": run,
        "path": str(directory),
        "experiment": scores.get("experiment"),
        "started_at": scores.get("started_at"),
        "product_version": scores.get("product_version"),
        "speed": scores.get("speed"),
        "plants_total": scores.get("plants_total"),
        "plants_run": scores.get("plants_run"),
        "withheld": sum(1 for p in scores.get("plants", []) if p.get("verdict_withheld")),
        "differences": [],
        "unknowns": [],
        "notes": [],
    }
    for plant in scores.get("plants", []):
        for row in measure.differences(plant):
            rows["differences"].append({**row, "run": run})
        for row in measure.unknowns(plant):
            rows["unknowns"].append({**row, "run": run})
    for conversation in feedback_mod.read(directory):
        for turn in conversation.get("turns") or []:
            if turn.get("role") != "user":
                continue        # the assistant's replies are not the evidence
            rows["notes"].append({
                "run": run,
                "plant": conversation.get("plant"),
                "screen": conversation.get("screen") or conversation.get("route") or "unnamed",
                "section": conversation.get("section"),
                "who": conversation.get("who"),
                "conversation": conversation.get("conversation"),
                "at": turn.get("moment_says"),
                "text": turn.get("text") or "",
            })
    return rows


def collect(directories: list[Path]) -> dict:
    """Every run asked for, read once."""
    runs = [read_run(directory) for directory in directories]
    if not runs:
        raise ReviewError(
            "No runs to review. Name run directories, or point --results at a "
            "directory that holds some.")
    return {
        "runs": runs,
        "differences": [row for run in runs for row in run["differences"]],
        "unknowns": [row for run in runs for row in run["unknowns"]],
        "notes": [row for run in runs for row in run["notes"]],
    }


# --------------------------------------------------------------- clustering

def _sorted_clusters(groups: dict) -> list[tuple]:
    """Clusters seen in most runs first, then most rows, then by name.

    Deterministic all the way down, because the file is committed, read and
    diffed: a roll-up whose order moved between two identical runs would make
    every diff unreadable.
    """
    return sorted(
        groups.items(),
        key=lambda item: (-len({r["run"] for r in item[1]}), -len(item[1]), str(item[0])))


def by_measurement(differences: list[dict]) -> list[tuple]:
    groups: dict[tuple, list[dict]] = {}
    for row in differences:
        groups.setdefault((row["measurement"], row["what"], row.get("station")), []).append(row)
    return _sorted_clusters(groups)


def by_unknown(unknowns: list[dict]) -> list[tuple]:
    groups: dict[tuple, list[dict]] = {}
    for row in unknowns:
        groups.setdefault((row["measurement"], row["because"]), []).append(row)
    return _sorted_clusters(groups)


def by_screen(notes: list[dict]) -> list[tuple]:
    groups: dict[str, list[dict]] = {}
    for row in notes:
        groups.setdefault(row["screen"], []).append(row)
    return _sorted_clusters(groups)


# ------------------------------------------------------------------- titles

def plain_title(kind: str, key) -> str:
    """The title that needs no model: the cluster's own key, as prose."""
    if kind == "difference":
        measurement, what, station = key
        return f"{measurement} · {what}" + (f" at {station}" if station else "")
    if kind == "unknown":
        measurement, because = key
        # The reason is printed in full under the heading; a heading that *is*
        # the reason runs off the page and stops working as a heading.
        short = str(because).split(". ")[0]
        if len(short) > 70:
            short = short[:67].rstrip() + "…"
        return f"{measurement} — {short}"
    return str(key)


def titled(kind: str, key, rows: list[dict], ask=None) -> str:
    """A short label for a cluster, from the local model when there is one.

    The model is given the rows and asked for a heading, nothing else. If it
    is not there, or it answers with a judgement, the plain title stands -
    which is why every test here can run without Ollama.
    """
    plain = plain_title(kind, key)
    if ask is None:
        return plain
    sample = "\n".join(
        f"- {r.get('run')}: {r.get('says') or r.get('because') or r.get('text', '')[:200]}"
        for r in rows[:8])
    reply = ask(
        "Write a heading of at most eight words for this group of observations "
        "from a manufacturing experiment. Describe what the group is about. Do "
        "not say whether anything is right or wrong, and do not recommend "
        "anything. Reply with the heading and nothing else.\n\n"
        f"Group: {plain}\n{sample}")
    if not reply:
        return plain
    line = reply.strip().splitlines()[0].strip().strip('"').strip("#").strip()
    if not line or len(line) > 90:
        return plain
    if any(word in line.lower().split() or word in line.lower() for word in FORBIDDEN):
        return plain
    return f"{line} ({plain})"


# ------------------------------------------------------------------ writing

def _cite(row: dict) -> str:
    bits = [row["run"]]
    if row.get("plant"):
        bits.append(str(row["plant"]))
    if row.get("station"):
        bits.append(str(row["station"]))
    return " · ".join(bits)


def _numbers(row: dict) -> str:
    numbers = row.get("numbers") or {}
    if not numbers:
        return ""
    return ", ".join(f"{k} {v}" for k, v in numbers.items())


def render(collected: dict, ask=None) -> str:
    """`findings.md`: what recurred, where it came from, and what was said."""
    runs = collected["runs"]
    when = sorted(str(r.get("started_at") or "") for r in runs)
    out: list[str] = []
    out.append(f"# Findings — {len(runs)} run(s), "
               f"{when[0][:10] or 'an unknown day'} to {when[-1][:10] or 'an unknown day'}")
    out.append("")
    out.append(
        "Rolled up by `fsmes lab review` from the runs below. Every row is cited to the run it "
        "came from and every note is quoted verbatim. Nothing here says which side is right: a "
        "run states the truth the generator obeyed and the answer the MES gave, and putting "
        "several of those beside each other adds no authority to either.")
    out.append("")
    out.append("Written "
               f"{datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat()} UTC.")
    out.append("")

    out.append("## The runs")
    out.append("")
    out.append("| Run | Experiment | Started | Version | Plants | Verdicts withheld |")
    out.append("|---|---|---|---|---|---|")
    for run in runs:
        out.append(f"| `{run['run']}` | {run['experiment']} | {run['started_at']} | "
                   f"{run['product_version']} | {run['plants_run']} of {run['plants_total']} | "
                   f"{run['withheld']} |")
    out.append("")

    out.append("## Where the MES and the script differed")
    out.append("")
    clusters = by_measurement(collected["differences"])
    if not clusters:
        out.append("No run put a number outside a band it could vouch for. That is not the same "
                   "as everything matching — read the unknowns below.")
        out.append("")
    for key, rows in clusters:
        seen = sorted({r["run"] for r in rows})
        out.append(f"### {titled('difference', key, rows, ask)}")
        out.append("")
        out.append(f"{len(rows)} row(s) across {len(seen)} run(s): "
                   + ", ".join(f"`{r}`" for r in seen))
        out.append("")
        out.append("| Run | Plant | Station | Difference | Numbers |")
        out.append("|---|---|---|---|---|")
        for row in rows:
            out.append(f"| `{row['run']}` | {row.get('plant') or ''} | "
                       f"{row.get('station') or ''} | {row['says']} | {_numbers(row)} |")
        out.append("")

    out.append("## What the runs could not answer")
    out.append("")
    unknown_clusters = by_unknown(collected["unknowns"])
    if not unknown_clusters:
        out.append("Every question these runs asked came back with a number.")
        out.append("")
    for key, rows in unknown_clusters:
        seen = sorted({r["run"] for r in rows})
        out.append(f"### {titled('unknown', key, rows, ask)}")
        out.append("")
        out.append(f"{len(rows)} time(s) across {len(seen)} run(s): "
                   + ", ".join(f"`{r}`" for r in seen))
        out.append("")
        for row in rows:
            out.append(f"- {_cite(row)} — {row['because']}")
        out.append("")

    out.append("## What was said at the screens")
    out.append("")
    note_clusters = by_screen(collected["notes"])
    if not note_clusters:
        out.append("No notes were left on these runs. The design panel is on for every lab "
                   "plant and `fsmes lab note` writes one from the terminal.")
        out.append("")
    for screen, rows in note_clusters:
        seen = sorted({r["run"] for r in rows})
        out.append(f"### {screen} — {len(rows)} note(s) across {len(seen)} run(s)")
        out.append("")
        for row in rows:
            for line in str(row["text"]).splitlines() or [""]:
                out.append(f"> {line}")
            out.append(">")
            out.append(f"> — {row.get('who') or 'somebody'} · `{row['run']}`"
                       + (f" · {row['plant']}" if row.get("plant") else "")
                       + (f" · {row['at']}" if row.get("at") else ""))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def write(directories: list[Path], out: Path, ask=None) -> tuple[Path, dict]:
    """Roll the runs up and write `findings.md`. Returns the path and the rows."""
    collected = collect(list(directories))
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(collected, ask=ask), encoding="utf-8")
    return out, collected


def local_ask():
    """The on-device model, if it answers. None when there is nothing there.

    `design_triage`'s machinery: the same local model, asked the same kind of
    small bounded question the nightly rollup asks it. A machine with no
    Ollama gets the deterministic roll-up, which is the whole file minus the
    headings' extra words.
    """
    from fsmes.services import design

    if design.local_generate("Reply with the word READY.", timeout=20.0) is None:
        return None
    return lambda prompt: design.local_generate(prompt, timeout=120.0)
