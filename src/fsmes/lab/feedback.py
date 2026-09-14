"""What the person watching said, tied to the second of the run they said it.

Scott's problem, in his words on 2026-09-14: "I want to ensure that I can
provide UI feedback within each lab and that that feedback is rolled back up
into the analysis." A note typed into a terminal after a run is a memory; a
note typed into the screen while the line is stopped is evidence. The design
chat already puts the conversation on the screen it is about. What was
missing was the join to the experiment: which run, which plant, which screen,
and what the line was doing at that second.

Three rules hold this together.

**The design store is never the plant's database, and the run never moves
it.** `~/.local/share/fsmes/design.db` is one person's notes going back
weeks. A run reads the conversations tagged with its own id and writes a
*copy* into its results directory. Nothing is deleted, nothing is migrated
into a plant, and a results directory handed to somebody else carries the
notes that belong to it and no others.

**The moment is resolved here, not in the browser.** The plant does not know
when the replay's first tick was - that instant is decided by the runner,
which waits for the log line rather than guessing. So the panel tags a
conversation with the run, the plant and the screen; this module puts a
line-second and the scripted events live at that second beside every turn,
from the run's own `t0` and the script it played.

**A note is printed verbatim, with who said it and when.** Nothing here
summarises, scores or judges. A remark that turns out to be wrong is still
what somebody said while looking at the screen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

#: Where a run's copy of its conversations lands, inside the results
#: directory, one JSON object per conversation.
FEEDBACK_DIR = "feedback"
CONVERSATIONS = "conversations.jsonl"

#: Which section of the report a screen's notes belong beside. A screen with
#: no measurement behind it yet is not forced into one: its notes go in the
#: plant's own block, which is honest, rather than under a heading about
#: numbers they are not about.
SECTION_FOR_ROUTE = {
    "/dashboard/orders": "booking",
    "/dashboard/analysis": "oee",
    "/dashboard/ops": "downtime",
    # The two the latency measurement is about: the line view is the screen it
    # polls, and a note left on it while the line was stopped belongs beside
    # the figures for how long that stop took to appear there.
    "/dashboard/line": "latency",
    "/dashboard/line/3d": "latency",
}


def _when(text: str | None) -> datetime | None:
    """A stamp from either clock, as naive UTC.

    The run stamps naive UTC (`fsmes.db.utcnow`'s frame); the design store
    stamps aware UTC. Comparing the two without saying so is how a whole
    window quietly shifts by the machine's offset, so it is said here.
    """
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(UTC).replace(tzinfo=None)
    return stamp


def moment(line: dict, t0: datetime | None, speed: float, said_at: str | None) -> dict:
    """What the line was doing when this was said.

    Returns the line-second the remark lands on and every scripted event live
    at it. Outside the replay - typed before the first tick or after the run
    finished - is said in those words rather than clamped to an end, because a
    note about a plant that had stopped is a different note.
    """
    stamp = _when(said_at)
    if stamp is None or t0 is None:
        return {"line_second": None,
                "unknown_because": "the note or the run carries no readable timestamp",
                "events": []}
    wall = (stamp - t0).total_seconds()
    second = wall * float(speed)
    duration = int(line.get("duration_s") or 0)
    if second < 0:
        return {"line_second": round(second, 1), "events": [],
                "phase": "before the replay's first tick"}
    if duration and second > duration:
        return {"line_second": round(second, 1), "events": [],
                "phase": f"after the scripted {duration} s had finished"}
    live = []
    for event in line.get("events") or []:
        start = event.get("start", event.get("at"))
        end = event.get("end", event.get("at"))
        if start is None or end is None:
            continue
        if float(start) <= second <= float(end):
            live.append({"type": event.get("type"), "station": event.get("station"),
                         "start": start, "end": end})
    return {"line_second": round(second, 1), "events": live,
            "phase": "the line was running to script" if not live else None}


def says(scripted: dict) -> str:
    """One line of prose for what was live at the moment, or that nothing was."""
    if scripted.get("unknown_because"):
        return f"unknown — {scripted['unknown_because']}"
    second = scripted.get("line_second")
    where = f"line second {second:,.0f}" if second is not None else "an unknown second"
    if scripted.get("phase"):
        return f"{where}, {scripted['phase']}"
    named = ", ".join(
        f"{e['type']}"
        + (f" at {e['station']}" if e.get("station") else "")
        + f" ({e['start']} to {e['end']} s)"
        for e in scripted["events"])
    return f"{where}, during {named}"


def section_for(route: str | None) -> str | None:
    return SECTION_FOR_ROUTE.get((route or "").rstrip("/") or "/dashboard")


def gather(run_name: str, plants: dict[str, dict],
           conversations: list[dict] | None = None) -> list[dict]:
    """Every conversation tagged to this run, with a moment on every turn.

    `plants` is `{plant name: {"started_at": ..., "speed": ..., "line": path}}`
    - what the run knows and the browser does not. A conversation tagged to a
    plant this run did not have keeps its turns and says the moment is
    unknown: a note nobody can place is still a note somebody made.
    """
    if conversations is None:
        from fsmes.services import design

        conversations = design.for_lab_run(run_name)

    lines: dict[str, dict] = {}
    for name, facts in plants.items():
        path = facts.get("line")
        if path and Path(path).is_file():
            lines[name] = json.loads(Path(path).read_text(encoding="utf-8"))

    out = []
    for row in conversations:
        plant = row.get("lab_plant") or ""
        facts = plants.get(plant, {})
        t0 = _when(facts.get("started_at"))
        speed = float(facts.get("speed") or 1.0)
        line = lines.get(plant, {})
        turns = []
        for turn in row.get("turns") or []:
            scripted = moment(line, t0, speed, turn.get("ts"))
            turns.append({**turn, "moment": scripted, "moment_says": says(scripted)})
        out.append({
            "conversation": row.get("id"),
            "lab_run": row.get("lab_run"),
            "plant": plant or None,
            "route": row.get("route"),
            "screen": row.get("screen") or row.get("route"),
            "section": section_for(row.get("route")),
            "who": row.get("who"),
            "started_at": row.get("started_at"),
            "turns": turns,
        })
    return out


def write(directory: Path, conversations: list[dict]) -> Path:
    """The run's own copy of what was said while it ran."""
    folder = Path(directory) / FEEDBACK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / CONVERSATIONS
    with path.open("w", encoding="utf-8") as handle:
        for row in conversations:
            handle.write(json.dumps(row, default=str) + "\n")
    return path


def read(directory: Path) -> list[dict]:
    """Read a run's feedback back. An absent file is no notes, not an error."""
    path = Path(directory) / FEEDBACK_DIR / CONVERSATIONS
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def by_section(conversations: list[dict], plant: str) -> dict[str | None, list[dict]]:
    """This plant's conversations, grouped by the report section they belong
    beside. `None` is the group with no measurement to sit next to."""
    groups: dict[str | None, list[dict]] = {}
    for row in conversations:
        if (row.get("plant") or None) != plant:
            continue
        groups.setdefault(row.get("section"), []).append(row)
    return groups


def plants_of(scores: dict, directory: Path) -> dict[str, dict]:
    """What `gather` needs, out of a run's own scores.json."""
    facts = {}
    for plant in scores.get("plants", []):
        name = plant.get("plant")
        if not name:
            continue
        line = plant.get("line_json")
        facts[name] = {
            "started_at": plant.get("started_at"),
            "speed": plant.get("speed"),
            "line": str(Path(directory) / line) if line else None,
        }
    return facts
