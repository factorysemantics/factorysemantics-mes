"""One experiment, end to end, into one directory.

The order matters and is the whole design:

    read the plan -> build each plant from its pack and the scenario ->
    run each one through its scripted line and ask it what it recorded ->
    put that beside what the line actually did -> write the directory

Nothing here runs a plant itself. `fsmes.sim.runner.scored_run` does that, the
same way `fsmes score` and `fsmes sweep` do: an ephemeral plant on loopback,
its own database, its own ports out of a reserved range, torn down when the
questions have been asked. What the lab adds is that the questions, the answers
and the truth all land in one directory somebody can read next week.

Plants run one after another rather than together. Two plants replaying at
speed on one laptop compete for the same cores, and a run whose harness fell
behind is a run whose headline numbers are withheld - so the lab would be
buying wall-clock time with the thing it exists to measure.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from fsmes import __version__
from fsmes.integrations.opc.tag_map import load_line_map
from fsmes.lab import build as builder
from fsmes.lab import console as lab_console
from fsmes.lab import feedback, measure, observe, report
from fsmes.lab import truth as truth_reader
from fsmes.lab.plan import Plan, PlanError, check_names, read_plan
from fsmes.pack import format as fmt
from fsmes.sim.truth import station_to_equipment

#: Where runs land when nothing says otherwise. One directory per run, named
#: by the day and the plan, because a person looking for "the bad hour I ran on
#: Tuesday" looks for exactly that.
DEFAULT_RESULTS = "lab-results"

NOTES_TEMPLATE = """# Notes — {name}, {when}

What no number in this run captured. Written by whoever watched it run; the
report renders this file at the end, so what you saw travels with what was
measured.

## What I saw on the screens

## What was wrong

## What I want next
"""


@dataclass
class PlantResult:
    name: str
    card: dict
    scores: dict
    built: builder.Built


def _slot(results_root: Path, plan: Plan, when: datetime) -> Path:
    """This run's own directory: `<day>-<plan>`, and `-2`, `-3` after that."""
    stem = f"{when:%Y-%m-%d}-{plan.name}"
    candidate = results_root / stem
    attempt = 1
    while candidate.exists():
        attempt += 1
        candidate = results_root / f"{stem}-{attempt}"
    return candidate


def collector(codes: list[str], hours: float):
    """What to ask the plant before it is torn down.

    Read-only, through the HTTP API, exactly as a browser would - so a view the
    lab cannot measure is a view the product does not serve, which is the
    dogfood rule the simulator already keeps.
    """
    from fsmes.sim.runner import TIMELINE_PAGE, _get

    def collect(base: str, token: str) -> dict:
        out: dict = {"collected_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                     "asked": [], "refused": {}}

        def ask(key: str, path: str) -> None:
            try:
                out[key] = _get(base, path, token)
                out["asked"].append(key)
            except Exception as exc:
                # A view that did not answer is a fact about the run worth
                # keeping. Losing the whole collection because one endpoint
                # was unhappy would throw away the rest of the evidence.
                out[key] = None
                out["refused"][key] = f"{type(exc).__name__}: {exc}"

        ask("oee", f"/analysis/oee?hours={hours:.4f}")
        ask("downtime", f"/analysis/downtime?hours={hours:.4f}")
        ask("production", f"/analysis/production?hours={hours:.4f}")
        ask("workorders", "/workorders?limit=200")
        # Units a machine counted with no order open to book them against.
        # Asked for one row: the totals in the envelope are for the whole
        # selection, and the rows themselves are a list of counter deltas
        # nobody would read. Without it a run can say the MES booked fewer
        # units than the line made and cannot say whether the rest was
        # dropped or kept, which are very different faults.
        ask("unassigned", "/execution/unassigned?limit=1")
        ask("health", "/health")
        if codes:
            page = codes[:TIMELINE_PAGE]
            ask("timeline", f"/analysis/timeline?hours={hours:.4f}"
                            f"&limit={TIMELINE_PAGE}&equipment={','.join(page)}")
            out["timeline_machines_asked_for"] = len(page)
            out["timeline_machines_total"] = len(codes)
        return out

    return collect


def measure_plant(plan: Plan, built: builder.Built, card: dict, echo=print,
                  watched: dict | None = None) -> dict:
    """Every measurement the plan asked for, with the truth beside it."""
    recorded = card.get("recorded") or {}
    reason = measure.withheld(card)

    played = _played_wall_seconds(card, recorded)
    overlap = truth_reader.overlap_seconds(built.duration_s, played, plan.speed) if played else 0
    line = json.loads(built.line_json.read_text(encoding="utf-8"))
    truth = truth_reader.read(built.replay_dir, line, built.duration_s, overlap)
    mapping = station_to_equipment(Path(built.cfg["tag_map"]))
    # Where the line publishes the order it is running, and how that value
    # names an order in this MES. None when the map does not say, and the
    # orders block then states that rather than guessing a join.
    line_map = load_line_map(Path(built.cfg["tag_map"]))

    out: dict = {
        "plant": built.name,
        "label": built.cfg.get("label"),
        "pack": str(built.pack_dir),
        # The replay's first tick, and the moment the MES was asked. Kept
        # because a note left during the run is placed against them, and
        # because `fsmes lab open` has to be able to place one that arrived
        # after the run without re-running anything.
        "started_at": card.get("started_at"),
        "collected_at": recorded.get("collected_at"),
        "line_json": f"line/{built.name}.json",
        "overlay": built.overlaid,
        "seed": built.seed,
        "duration_line_seconds": built.duration_s,
        "speed": plan.speed,
        "replay_overlap_line_seconds": overlap,
        "wall_seconds_played": round(played, 1) if played else None,
        "pipeline": card.get("pipeline"),
        "verdict_withheld": reason,
        "triage": card.get("triage"),
        # The second, typed pass over the same log. Recorded beside the
        # first; no measurement on this page is computed from it.
        "jev": card.get("jev"),
        "views_refused": recorded.get("refused") or {},
        "measurements": {},
    }
    if reason:
        echo(f"  {built.name}: every measurement is unknown - {reason}")

    if "booking" in plan.measure:
        out["measurements"]["booking"] = measure.booking(
            truth, recorded.get("oee") or {}, recorded.get("workorders") or {},
            mapping, plan.speed, reason, line_map=line_map,
            unassigned=recorded.get("unassigned") or {})
    if "downtime" in plan.measure:
        out["measurements"]["downtime"] = measure.downtime(
            truth, card, recorded.get("downtime") or {}, plan.speed, reason)
    if "latency" in plan.measure:
        out["measurements"]["latency"] = measure.latency(
            card, {**(card.get("observed_during_run") or {}), **(watched or {})},
            truth, mapping, plan.speed, reason)
    if "oee" in plan.measure:
        out["measurements"]["oee"] = measure.oee(
            truth, recorded.get("oee") or {}, mapping, plan.speed, reason)
    if "connection" in plan.measure:
        # Read from the watch, not from the plant at the end: by then the link
        # is back and every screen says so. What they said while it was down
        # only exists in the looks.
        out["measurements"]["connection"] = measure.connection(
            card, {**(card.get("observed_during_run") or {}), **(watched or {})},
            recorded.get("oee") or {}, mapping, plan.speed, reason)
    out["truth"] = truth.as_json()
    return out


def _played_wall_seconds(card: dict, recorded: dict) -> float:
    """From the replay's first tick to the moment the MES was asked.

    Measured rather than assumed: it is what sizes the overlap band, and a
    band computed from a settle time the run did not actually take would be a
    tolerance nobody could check.
    """
    started, collected = card.get("started_at"), recorded.get("collected_at")
    if not started or not collected:
        return 0.0
    try:
        return (datetime.fromisoformat(collected) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return 0.0


def run(plan_path: Path, results_root: Path | None = None, root: Path | None = None,
        speed: float | None = None, keep_evidence: bool = False, echo=print) -> Path:
    """Run one experiment and write its directory. Returns the directory."""
    from fsmes import plant as plants
    from fsmes.sim.runner import scored_run

    plan = read_plan(Path(plan_path))
    if speed is not None:
        plan = replace(plan, speed=float(speed))
    where = plants.find_root(root)
    results_root = Path(results_root or where / DEFAULT_RESULTS).expanduser().resolve()

    known = {fmt.read(directory).name: directory for directory in plan.packs}
    if len(known) != len(plan.packs):
        raise PlanError(
            f"{plan.path} lists {len(plan.packs)} packs that are only {len(known)} plants. "
            "Two plants with one name is two plants an experiment cannot tell apart.")
    check_names(plan, known)

    # Everything that can be refused is refused before a directory exists. A
    # half-built results directory is worse than none: `fsmes lab list` reads
    # what is there, and a run that stopped on its second plant would sit in
    # the list looking like a run.
    for directory in plan.packs:
        pack = fmt.read(directory)
        builder.seeding(plan, pack)
        builder.line_description(pack)

    started = datetime.now(UTC).replace(tzinfo=None)
    results = _slot(results_root, plan, started)
    results.mkdir(parents=True)
    shutil.copyfile(plan.path, results / "plan.toml")
    echo(f"experiment {plan.name}: {len(plan.packs)} plant(s) at {plan.speed}x "
         f"-> {results}")

    outcomes: list[dict] = []
    truths: dict[str, dict] = {}
    (results / "recorded").mkdir()
    # A console is started before the first plant and asked at each phase, so
    # the phase where a plant this run built is no longer there is one it
    # actually lived through rather than one somebody arranged.
    console = lab_console.LabConsole(results / "fleet", echo=echo) \
        if "console" in plan.measure else None
    if console is not None:
        console.start()
        console.look("before any plant had started")
    try:
        for directory in plan.packs:
            built = builder.build(plan, directory, results, echo=echo)
            hours = max(0.05, (built.duration_s / plan.speed + 8.0) / 3600.0)
            codes = [code for code in
                     station_to_equipment(Path(built.cfg["tag_map"])).values() if code]
            # Only when something needs it. A watcher is HTTP requests against
            # the same API the run is scored through, and a run that measures
            # neither latency nor the console should not pay for looks nobody
            # reads. The console needs one because a plant's address is not
            # known until it is up, and a look is the first thing that has it.
            # `connection` is here for the same reason `latency` is: the
            # thing it measures only exists while the hour is playing.
            watcher = (observe.Watch(on_look=_telling(console, built.name))
                       if {"latency", "console", "connection"} & set(plan.measure) else None)
            card = scored_run(built.name, built.cfg, where, plan.speed,
                              line_json=built.line_json, echo=echo,
                              keep_evidence=keep_evidence,
                              collect=collector(codes, hours),
                              extra_env=design_env(plan, results.name, built.name),
                              observe=watcher, observe_every_s=plan.watch_every_s)
            (results / "recorded" / f"{built.name}.json").write_text(
                json.dumps(card.get("recorded") or {}, indent=2, default=str), encoding="utf-8")
            if console is not None:
                console.stopped(built.name)
                console.look(f"after {built.name} had been torn down")
            watched = None
            if watcher is not None:
                # Kept beside the run whatever the measurement made of them: a
                # version of the measurement that asked the wrong question is
                # worth re-running against an hour somebody already paid for.
                (results / "watched").mkdir(exist_ok=True)
                watcher.write(results / "watched" / f"{built.name}.json")
                watched = watcher.as_json()
            scored = measure_plant(plan, built, card, echo=echo, watched=watched)
            truths[built.name] = scored.pop("truth")
            outcomes.append(scored)
            card.pop("recorded", None)
            (results / "recorded" / f"{built.name}-scorecard.json").write_text(
                json.dumps(card, indent=2, default=str), encoding="utf-8")
            _echo_plant(scored, echo)
        if console is not None:
            console.look("after every plant had stopped")
    finally:
        # Stop what we start, whatever happened. A console left listening on a
        # claimed port outlives the run that wanted it.
        if console is not None:
            console.stop()
    run_wide: dict = {}
    if console is not None:
        console.write(results / "fleet" / "phases.json")
        # Run-wide, not per-plant, and kept where it belongs. A console counts
        # the whole fleet; copying one answer into each plant's block would put
        # the same object in the file twice and invite a reader to treat it as
        # two readings.
        run_wide["console"] = measure.console(console.as_json()["phases"], len(plan.packs))
        _echo_console(run_wide["console"], echo)

    finished = datetime.now(UTC).replace(tzinfo=None)
    scores = {
        "experiment": plan.name,
        "plan": str(plan.path),
        "product_version": __version__,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "wall_seconds": round((finished - started).total_seconds(), 1),
        "speed": plan.speed,
        "seed": plan.seed,
        "measurements_asked_for": list(plan.measure),
        "plants_total": len(plan.packs),
        "plants_run": len(outcomes),
        # Measurements about the run rather than about any one plant.
        "measurements": run_wide,
        "note": plan.note,
        "plants": outcomes,
    }
    (results / "scores.json").write_text(json.dumps(scores, indent=2, default=str), encoding="utf-8")
    said = export_feedback(results, scores, echo=echo)
    scores["feedback_conversations"] = len(said)
    (results / "scores.json").write_text(json.dumps(scores, indent=2, default=str), encoding="utf-8")
    (results / "truth.json").write_text(json.dumps(truths, indent=2, default=str), encoding="utf-8")
    notes = results / "notes.md"
    if not notes.exists():
        notes.write_text(NOTES_TEMPLATE.format(name=plan.name, when=f"{started:%Y-%m-%d %H:%M} UTC"),
                         encoding="utf-8")
    report.write(results)
    echo("")
    echo(f"  {results}")
    echo(f"  report {results / 'report.html'}   notes {notes}")
    return results


def design_env(plan: Plan, run_name: str, plant: str) -> dict[str, str]:
    """What tells a lab plant it is part of an experiment.

    The design chat is on for every lab plant, because the whole reason a
    person watches a run is to notice something, and the cheapest place to
    say it is the screen it is about. Claude is off unless the plan asks for
    it: taking a note is work the on-device model does for nothing, and a
    run left going while somebody makes coffee should not be billing an API.
    """
    if not plan.feedback_chat:
        return {"MES_DESIGN_CHAT": "0"}
    return {
        "MES_DESIGN_CHAT": "1",
        "MES_DESIGN_CLAUDE": "1" if plan.feedback_claude else "0",
        "MES_LAB_RUN": run_name,
        "MES_LAB_PLANT": plant,
    }


def export_feedback(results: Path, scores: dict, echo=print) -> list[dict]:
    """Copy this run's conversations into the run, with a moment on each turn.

    A copy, always: the design store is one person's notes going back weeks
    and belongs to them, not to a results directory. Reading it is allowed to
    fail - a machine with no store has no notes, which is not an error - and
    a run must not be lost because its notes could not be read.
    """
    try:
        said = feedback.gather(results.name, feedback.plants_of(scores, results))
    except Exception as exc:                         # deliberate - see the docstring
        echo(f"  feedback: the design store could not be read ({type(exc).__name__}: {exc}); "
             f"the run's numbers are unaffected")
        said = []
    feedback.write(results, said)
    notes = sum(1 for c in said for t in c["turns"] if t.get("role") == "user")
    if said:
        echo(f"  feedback: {notes} note(s) in {len(said)} conversation(s) tagged to this run")
    return said


def _telling(console, plant: str):
    """Tell the console where this plant is, and ask it while the plant is up.

    Called after each look. The first one is what puts the plant in the
    console's fleet at all - an ephemeral plant claims its ports when it
    starts, so nobody knows its address before then - and one look in thirty
    keeps the phases from being a page of near-identical rows.
    """
    if console is None:
        return None

    def told(base: str, token: str, line_second: float, looks: int) -> None:
        if looks == 1:
            console.watching(plant, base)
            console.look(f"while {plant} was running")
        elif looks % 30 == 0:
            console.look(f"while {plant} was running")

    return told


def _echo_console(seen: dict, echo) -> None:
    matched = seen["phases_where_the_count_matched"]
    echo("")
    echo(f"  console   : the count matched at {matched}/{seen['phases_answered']} phase(s); "
         f"{seen['stopped_plants_not_read_as_unknown']} stopped plant(s) read as anything "
         f"other than unknown")


def _echo_plant(scored: dict, echo) -> None:
    echo("")
    echo(f"  {scored['plant']}: seed {scored['seed']}, {scored['duration_line_seconds']}s of line "
         f"time at {scored['speed']}x, overlap band {scored['replay_overlap_line_seconds']}s")
    booking = scored["measurements"].get("booking")
    if booking:
        unknown = [r for r in booking["stations"] if r["verdict"].startswith("unknown")]
        off = [r for r in booking["stations"] if not r["verdict"].startswith("unknown")
               and r["verdict"] not in ("matched", "inside the replay's overlap band")]
        inside = booking["stations_total"] - len(off) - len(unknown)
        echo(f"    booking   : {inside}/{booking['stations_total']} stations inside the band the "
             f"replay allows, {len(off)} outside it, {len(unknown)} unknown")
        for row in off:
            echo(f"                {row['station']}: {row['verdict']}")
    down = scored["measurements"].get("downtime")
    if down:
        recall = down["breakdowns"]["recall"]
        mis = down["planned_stops"]["misclassified_as_downtime"]
        echo(f"    downtime  : breakdowns detected "
             f"{'unknown' if recall is None else f'{recall:.0%}'} "
             f"({down['breakdowns']['scored']}/{down['breakdowns']['scripted']} scored), "
             f"planned stops misclassified {'unknown' if mis is None else mis}")
    late = scored["measurements"].get("latency")
    if late:
        answered, total = late["events_answered"], late["events_total"]
        behind = (late.get("production") or {}).get("worst_behind_units")
        echo(f"    latency   : {answered}/{total} scripted event(s) caught by a screen while "
             f"the hour played, resolution "
             f"{late['watched']['resolution_line_seconds']}s of line time"
             + (f"; the line view was {behind:,.0f} unit(s) behind at worst"
                if behind is not None else ""))
    oee_out = scored["measurements"].get("oee")
    if oee_out:
        mismatch = oee_out["window"]["mismatch_share"]
        loud = [r for r in oee_out["stations"] if measure.significant(r, mismatch)]
        slow = [r for r in oee_out["stations"] if measure.performance_significant(r)]
        echo(f"    oee       : {oee_out['stations_answered']}/{oee_out['stations_total']} stations "
             f"answered; {len(loud)} availability difference(s) bigger than the window mismatch, "
             f"{len(slow)} performance difference(s) bigger than the station's own band")


def listing(results_root: Path) -> list[dict]:
    """Every run in a results directory, newest first, with what it said."""
    results_root = Path(results_root).expanduser()
    if not results_root.is_dir():
        return []
    out = []
    for directory in sorted(results_root.iterdir(), reverse=True):
        scores = directory / "scores.json"
        if not scores.is_file():
            continue
        try:
            body = json.loads(scores.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({
            "run": directory.name,
            "path": directory,
            "experiment": body.get("experiment"),
            "started_at": body.get("started_at"),
            "plants_total": body.get("plants_total"),
            "plants_run": body.get("plants_run"),
            "withheld": sum(1 for p in body.get("plants", []) if p.get("verdict_withheld")),
        })
    return out
