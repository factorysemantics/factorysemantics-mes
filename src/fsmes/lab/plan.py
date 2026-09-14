"""The experiment, as a file: what to run, what to do to it, what to measure.

A plan is TOML because a plan is configuration, and the whole point of the lab
is that trying a different plant is an edit rather than a patch. Nothing here
runs anything; it reads a file, complains about it in sentences, and hands back
a `Plan` the runner can obey.

The shape, in full:

    name    = "one-line-bad-hour"
    packs   = ["labs/multiplant/bottling"]
    duration = 3600          # line seconds; omitted keeps each line's own
    speed    = 10            # 10 replays a scripted hour in six minutes
    seed     = 42            # omitted keeps each line's own
    measure  = ["booking", "downtime", "oee"]

    # Vary a pack without editing it. The copy the run builds from lands in
    # the results directory; the pack on disk is never touched.
    [overlay.bottling.modules]
    maintenance = false

    # A plant named here plays this script instead of its pack's own.
    [scenario.bottling]
    events = [ { type = "down", station = "LD", start = 600, end = 612 } ]

    # The on-screen design chat, on for every lab plant so a note can be left
    # while the line is stopped. The local model is enough to take a note, so
    # Claude stays off unless a plan says otherwise.
    [feedback]
    chat   = true
    claude = false

    # How often the run looks at the plant while the hour plays, in WALL
    # seconds. It is the resolution of every latency figure, and it is also
    # requests competing with the agent for one machine.
    [watch]
    every = 1.0

    # A plant whose pack carries no master data needs something to seed it -
    # the scale labs, whose master data is generated rather than written.
    # A pack may never name a script (decision 0022); a lab plan is not a
    # pack, and `fsmes.sim.runner` already takes one from a lab tool.
    [init.megafactory]
    script = "../megafactory/init.py"

Paths are relative to the plan file, so a plan and the packs it names travel
together the way a fleet file and its packs do.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: Measurements this version takes. Each one is a number with the truth beside
#: it, or the word *unknown* and the reason.
MEASUREMENTS = {
    "booking": "units booked against units the line made, per station and in total",
    "downtime": "scripted stops detected and how late, and planned stops kept out of downtime",
    "oee": "availability, performance and quality per station against the script",
    "latency": "how long after the line each screen said it, watched while the hour played",
}

#: Named in the design and not built yet. A plan that asks for one of these is
#: refused by name rather than ignored: silently dropping a measurement is how
#: a report comes to say less than its plan asked for without anybody noticing.
PLANNED = {
    "console": "`fsmes fleet console` counting plants, answers and unknowns",
    "quality": "a scrap burst tripping an SPC rule and holding the lot",
    "agent-eval": "the agent evaluation over the run, with its cost stated",
}

DEFAULT_MEASUREMENTS = ("booking", "downtime", "oee")

#: How often a run looks at the plant while the hour plays, in wall seconds,
#: when a plan does not choose. Wall rather than line seconds because it is the
#: cost that matters - these are HTTP requests competing with the agent for the
#: same machine - and a plan that wants a sharper figure asks for it and pays
#: for it in the run's own honesty about whether the harness kept up.
DEFAULT_WATCH_EVERY_S = 1.0

#: Replay speed when the plan does not choose one. Slow enough that the
#: overlap band `measure.booking` has to state stays small - see the page.
DEFAULT_SPEED = 10.0


class PlanError(Exception):
    """The plan cannot be run as written, and says why in one sentence."""


@dataclass(frozen=True)
class Plan:
    """One experiment, read and checked."""

    name: str
    path: Path
    packs: tuple[Path, ...]
    measure: tuple[str, ...]
    speed: float
    duration_s: int | None = None
    seed: int | None = None
    overlay: dict[str, dict] = field(default_factory=dict)
    scenario: dict[str, list[dict]] = field(default_factory=dict)
    init: dict[str, Path] = field(default_factory=dict)
    note: str = ""
    feedback_chat: bool = True
    feedback_claude: bool = False
    watch_every_s: float = DEFAULT_WATCH_EVERY_S

    @property
    def directory(self) -> Path:
        return self.path.parent


def _table(raw: dict, key: str, label: str) -> dict:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise PlanError(f"{label}: `{key}` is a table, not {type(value).__name__}.")
    return value


def read_plan(path: Path) -> Plan:
    """Read an experiment plan, or refuse it with a sentence."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise PlanError(f"No experiment plan at {path}.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PlanError(f"{path} is not valid TOML: {exc}") from exc

    listed = raw.get("packs")
    if not isinstance(listed, list) or not listed or not all(isinstance(p, str) for p in listed):
        raise PlanError(
            f"{path} lists no packs. An experiment is `packs = [\"<directory>\", ...]`, "
            "each path relative to this file.")
    packs = tuple((path.parent / entry).resolve() for entry in listed)
    for directory in packs:
        if not (directory / "plant.toml").is_file():
            raise PlanError(
                f"{path} names {directory}, and there is no plant.toml there. "
                "A pack is one directory with a plant.toml in it.")

    name = str(raw.get("name") or path.stem)

    measure = raw.get("measure", list(DEFAULT_MEASUREMENTS))
    if not isinstance(measure, list) or not all(isinstance(m, str) for m in measure):
        raise PlanError(f"{path}: `measure` is a list of measurement names.")
    if not measure:
        raise PlanError(
            f"{path}: `measure` is empty. A run that measures nothing writes a report "
            f"with nothing in it; ask for at least one of {', '.join(sorted(MEASUREMENTS))}.")
    for asked in measure:
        if asked in MEASUREMENTS:
            continue
        if asked in PLANNED:
            raise PlanError(
                f"{path}: `{asked}` is not measured yet - {PLANNED[asked]}. This version "
                f"measures {', '.join(sorted(MEASUREMENTS))}.")
        raise PlanError(
            f"{path}: `{asked}` is not a measurement. This version takes "
            f"{', '.join(sorted(MEASUREMENTS))}.")

    speed = raw.get("speed", DEFAULT_SPEED)
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or speed <= 0:
        raise PlanError(f"{path}: `speed` is a positive number - 10 replays a scripted hour in six minutes.")

    duration = raw.get("duration")
    if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0):
        raise PlanError(f"{path}: `duration` is a whole number of line seconds.")

    seed = raw.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise PlanError(f"{path}: `seed` is a whole number. The same seed twice is the same run.")

    overlay = _table(raw, "overlay", str(path))
    for plant, tables in overlay.items():
        if not isinstance(tables, dict):
            raise PlanError(f"{path}: `[overlay.{plant}]` is a table of tables - `[overlay.{plant}.modules]`.")
        for table in tables:
            if table not in ("modules", "words"):
                raise PlanError(
                    f"{path}: `[overlay.{plant}.{table}]` - an overlay varies `modules` or `words` "
                    "and nothing else. Anything more is a different pack, and belongs in one.")

    scenario_raw = _table(raw, "scenario", str(path))
    scenario: dict[str, list[dict]] = {}
    for plant, body in scenario_raw.items():
        if not isinstance(body, dict) or "events" not in body:
            raise PlanError(f"{path}: `[scenario.{plant}]` needs an `events` list.")
        events = body["events"]
        if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
            raise PlanError(f"{path}: `[scenario.{plant}] events` is a list of tables, one per event.")
        scenario[plant] = [dict(e) for e in events]

    feedback = _table(raw, "feedback", str(path))
    for key in feedback:
        if key not in ("chat", "claude"):
            raise PlanError(
                f"{path}: `[feedback] {key}` is not a setting. A plan chooses `chat` "
                "(the on-screen design panel) and `claude` (whether design questions "
                "leave this machine).")
    for key in ("chat", "claude"):
        if key in feedback and not isinstance(feedback[key], bool):
            raise PlanError(f"{path}: `[feedback] {key}` is true or false.")

    watch = _table(raw, "watch", str(path))
    for key in watch:
        if key != "every":
            raise PlanError(
                f"{path}: `[watch] {key}` is not a setting. A plan chooses `every` - how often, "
                f"in wall seconds, the run looks at the plant while the hour plays.")
    every = watch.get("every", DEFAULT_WATCH_EVERY_S)
    if not isinstance(every, (int, float)) or isinstance(every, bool) or every <= 0:
        raise PlanError(f"{path}: `[watch] every` is a positive number of wall seconds.")
    if "latency" in measure and float(every) * float(speed) > 120:
        raise PlanError(
            f"{path}: `latency` is asked for and the run would look every {every:g}s of wall "
            f"clock, which at {speed:g}x is {float(every) * float(speed):.0f}s of line time. "
            f"Every lag it could measure would be smaller than its own resolution, so every "
            f"figure would read `within resolution` and the measurement would say nothing. "
            f"Look more often, or replay more slowly.")

    init_raw = _table(raw, "init", str(path))
    init: dict[str, Path] = {}
    for plant, body in init_raw.items():
        if not isinstance(body, dict) or not isinstance(body.get("script"), str):
            raise PlanError(f"{path}: `[init.{plant}]` needs `script = \"<path to a .py file>\"`.")
        script = (path.parent / body["script"]).resolve()
        if script.suffix != ".py" or not script.is_file():
            raise PlanError(
                f"{path}: `[init.{plant}] script` names {script}, which is not a Python file "
                "that exists.")
        init[str(plant)] = script

    return Plan(
        name=name,
        path=path,
        packs=packs,
        measure=tuple(measure),
        speed=float(speed),
        duration_s=duration,
        seed=seed,
        overlay={str(k): dict(v) for k, v in overlay.items()},
        scenario=scenario,
        init=init,
        note=str(raw.get("note") or ""),
        feedback_chat=bool(feedback.get("chat", True)),
        feedback_claude=bool(feedback.get("claude", False)),
        watch_every_s=float(every),
    )


def check_names(plan: Plan, known: dict[str, Path]) -> None:
    """Every plant an overlay or a scenario names is a plant this plan runs.

    Checked against the *pack's* name rather than the directory's, because a
    pack is what a plant says it is - and a plan that quietly overlays nothing
    because somebody renamed a folder is worse than one that refuses.
    """
    for label, table in (("overlay", plan.overlay), ("scenario", plan.scenario),
                         ("init", plan.init)):
        for plant in table:
            if plant not in known:
                raise PlanError(
                    f"{plan.path}: `[{label}.{plant}]` names a plant this experiment does not "
                    f"run. It runs {len(known)}: {', '.join(sorted(known)) or 'none'}.")
