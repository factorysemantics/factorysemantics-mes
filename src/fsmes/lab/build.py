"""Turning a pack and a plan into a plant this run can start.

Three rules, and they are the whole file:

1. **The pack on disk is never touched.** An overlay is written into a copy in
   the results directory. That copy is what the run builds from, so the
   directory a person hands to somebody else holds the plant it actually ran
   and not a reference to one.
2. **The line data is generated for the run**, into the results directory, from
   the line description the plan produced. A pack's `replay_dir` normally
   points at CSVs somebody generated once; an experiment that reused those
   could not honour its own seed, and would score against a script it had not
   played.
3. **The copy's `plant.toml` is rewritten, not just its compiled settings.**
   `MES_REPLAY_DIR` is compiled from the pack, so a caller that overrides only
   the dictionary key gets a plant that replays the old data and a scorer that
   compares it against the new script. Rewriting the file makes the two agree
   by construction. That rule is not the lab's alone - `fsmes sweep` needs it
   too - so it lives in `fsmes.pack.repoint` and this module translates its
   refusals into the plan's own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fsmes.lab.plan import Plan, PlanError
from fsmes.pack import fleet as packs
from fsmes.pack import format as fmt
from fsmes.pack import repoint


@dataclass(frozen=True)
class Built:
    """One plant, ready to run."""

    name: str
    cfg: dict
    pack_dir: Path        # the copy, with the overlay applied
    line_json: Path       # the script this plant plays
    replay_dir: Path      # the data generated from it
    duration_s: int
    seed: int | None
    overlaid: dict


def line_description(pack: fmt.Pack) -> Path:
    """The line description a pack's simulated plant is scored against.

    A pack names generated data; the description that produced it is its
    sibling, which is the convention `fsmes score` already reads.
    """
    compiled = packs.compile_pack(pack)
    replay = compiled.get("replay_dir")
    if not replay:
        raise PlanError(
            f"{pack.name} names no replay_dir, so it has no line to script. The lab runs "
            "plants that simulate a line; a plant that only serves a database cannot be "
            "given a scenario.")
    line = Path(replay).parent / "line.json"
    if not line.is_file():
        raise PlanError(
            f"{pack.name}: no line description at {line}. The lab generates a run's data "
            "from the description, so the description is what it needs - not the CSVs.")
    return line


def script(plan: Plan, pack: fmt.Pack, into: Path) -> tuple[Path, int, int | None]:
    """Write the line description this plant will play, and say what it says.

    The pack's own description is the starting point. The plan may replace its
    events, its duration and its seed - and nothing else, because everything
    else about the line (its stations, its rates, its buffers) is the plant,
    and varying the plant is what a second pack is for.
    """
    source = json.loads(line_description(pack).read_text(encoding="utf-8"))
    if "lines" in source:
        raise PlanError(
            f"{pack.name} describes a factory of several lines. The lab scripts one line "
            "per plant; run each line as its own pack.")

    if plan.duration_s is not None:
        source["duration_s"] = plan.duration_s
    if plan.seed is not None:
        source["seed"] = plan.seed
    if pack.name in plan.scenario:
        source["events"] = plan.scenario[pack.name]

    duration = int(source.get("duration_s") or 0)
    if duration <= 0:
        raise PlanError(f"{pack.name}: its line declares no duration_s, so a run has no length.")

    from fsmes.sim.generate import EVENT_TYPES, OBSERVER_TYPES

    stations = {s.get("name") for s in source.get("stations", [])}
    for event in source.get("events", []):
        kind = event.get("type")
        where = event.get("station")
        if kind not in EVENT_TYPES:
            # The generator refuses this too, but it does it by exiting the
            # process from inside a library call, several steps later, after
            # the run has made a directory. A plan is refused before anything
            # is built, with the vocabulary printed.
            raise PlanError(
                f"{pack.name}: a scripted event has type {kind!r}, which is not something a "
                f"line can be told to do. The vocabulary is:\n" + "\n".join(
                    f"    {name:<14} {says}" for name, says in sorted(EVENT_TYPES.items())))
        # A changeover is the whole line and a disconnect is the whole
        # endpoint. Asking which machine the network outage happened to is
        # not a question either of them has an answer to.
        if kind not in ("changeover", *OBSERVER_TYPES) and where not in stations:
            raise PlanError(
                f"{pack.name}: a scripted {kind!r} names station {where!r}, and this line has "
                f"{', '.join(sorted(str(s) for s in stations)) or 'none'}.")
        for key in ("start", "end", "at"):
            if key in event and not 0 <= int(event[key]) <= duration:
                raise PlanError(
                    f"{pack.name}: a scripted {kind!r} has {key}={event[key]}, outside the run's "
                    f"0..{duration} line seconds. Lengthen `duration` or move the event.")

    into.parent.mkdir(parents=True, exist_ok=True)
    into.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    return into, duration, source.get("seed")


def overlaid_pack(plan: Plan, pack: fmt.Pack, into: Path, replay_dir: Path) -> tuple[Path, dict]:
    """Copy the pack, point it at this run's data, apply the overlay, check it.

    `fsmes.pack.repoint` does the work, because a sweep needs the same copy for
    the same reason. What is the lab's own is the overlay the plan carries and
    the error a plan is refused with.
    """
    try:
        return repoint.pointed_at(pack, into, replay_dir, plan.overlay.get(pack.name, {}))
    except repoint.RepointError as exc:
        raise PlanError(str(exc)) from exc


def seeding(plan: Plan, pack: fmt.Pack) -> str | None:
    """What will put machines in this plant's empty database - or a refusal.

    A pack normally carries its plant as data under `masterdata/`, and
    `fsmes pack apply` seeds from it. The scale labs do not: their master data
    is generated rather than written, so they carry none and seed themselves.
    Decision 0022 keeps a script out of a *pack*; `fsmes.sim.runner` still
    takes one from a lab tool, and an experiment plan is a lab tool. So a plan
    may name it, and a plant with neither is refused before anything is
    started - an ephemeral plant with no equipment rows answers every question
    with nothing, which reads as an MES that saw nothing rather than as a
    plant that was never built.

    Bottling was one of the packs that carried none, until 2026-09-14. Its
    line is the product's own reference line and the argument was that a copy
    could drift from what it copied; what the absence actually did was leave
    `fsmes fleet create`, `fsmes plant bottling init` and `fsmes score
    bottling` all building a plant with no machines, and only a plan could
    name the script that fixed it. The refusal below is the same instinct as
    this one, reached from the other side - and the pack carries its line now.
    """
    named = plan.init.get(pack.name)
    if named:
        return str(named)
    masterdata = pack.table("files").get("masterdata")
    if isinstance(masterdata, str) and masterdata and pack.path(masterdata).is_dir():
        return None
    raise PlanError(
        f"{pack.name} carries no master data, so a plant built from this pack alone has no "
        f"machines for the line's tags to attach to - every measurement would come back empty. "
        f"Give the pack a `[files] masterdata` directory, or name the script that seeds it in "
        f"the plan:\n\n    [init.{pack.name}]\n    script = \"<path to a .py file>\"")


def build(plan: Plan, pack_dir: Path, results: Path, echo=print) -> Built:
    """One pack plus the plan, as a plant ready for `scored_run`."""
    pack = fmt.read(pack_dir)
    name = pack.name
    init = seeding(plan, pack)
    line_path = results / "line" / f"{name}.json"
    replay_dir = results / "replay" / name
    line_path, duration, seed = script(plan, pack, line_path)

    from fsmes.sim.generate import generate

    generate(line_path, replay_dir, write_docs=True)
    echo(f"  {name}: {duration}s of line time generated from {line_path.name} (seed {seed})")

    copy, applied = overlaid_pack(plan, pack, results / "packs" / name, replay_dir)
    if applied:
        echo(f"  {name}: overlay applied to {', '.join(sorted(applied))}; the pack on disk is unchanged")
    cfg = packs.compile_pack(fmt.read(copy))
    if init:
        # The one key `fsmes.sim.runner` reads instead of applying the pack.
        cfg["init"] = init
        echo(f"  {name}: seeded by {Path(init).name}, which the plan names and the pack does not")
    return Built(name=name, cfg=cfg, pack_dir=copy, line_json=line_path,
                 replay_dir=replay_dir, duration_s=duration, seed=seed, overlaid=applied)
