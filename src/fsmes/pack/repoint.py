"""Copy a pack and point the copy at data generated for this run.

A pack's `replay_dir` names line data somebody generated once. A run that
varies the line - an experiment, a sweep - generates its own data, and then
has to say so in a way the plant it starts will actually obey.

`MES_REPLAY_DIR` is compiled out of `plant.toml` when the pack is read, so a
caller that overrides only the compiled dictionary gets a plant that replays
the old data and a scorer that compares it against the new script. That was
`fsmes sweep`'s bug: three variants, three sets of generated data, one
replay, and a comparison that was three readings of the same hour. Rewriting
the copy's `plant.toml` and recompiling the copy makes the two agree by
construction.

Two rules, and they are the whole file:

1. **The pack on disk is never touched.** The overlay and the new
   `replay_dir` are written into a copy, so the directory a person hands to
   somebody else holds the plant that actually ran.
2. **What is written is read back.** A key the pack writer did not know would
   vanish silently, and a plant built from a pack quietly missing a setting
   is exactly the class of thing this is here to catch.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from fsmes.pack import check as checker
from fsmes.pack import format as fmt
from fsmes.pack import migrate

#: Directories inside a pack that are output rather than input. Copied nowhere:
#: a run generates its own.
NOT_INPUT = ("out", ".data", "__pycache__", ".venv")


class RepointError(Exception):
    """The copy cannot be made or cannot be used, and says why."""


def pointed_at(pack: fmt.Pack, into: Path, replay_dir: Path,
               overlay: dict | None = None) -> tuple[Path, dict]:
    """Copy the pack, point it at this run's data, apply the overlay, check it.

    The check is the same `fsmes pack check` a person would run, on the copy
    rather than on a second opinion written here - so an override that makes a
    pack unusable is refused before a plant is built from it, with the problems
    named.

    Returns the copy's directory and the overlay that was applied. Read the
    copy with `fsmes.pack.format.read` and compile it to get a configuration
    whose `replay_dir` and whose `MES_REPLAY_DIR` are the same directory.
    """
    if into.exists():
        shutil.rmtree(into)
    shutil.copytree(pack.directory, into, ignore=shutil.ignore_patterns(*NOT_INPUT))

    raw = tomllib.loads((into / fmt.PLANT_FILE).read_text(encoding="utf-8"))
    accounts = raw.pop("accounts", [])
    raw.setdefault("files", {})["replay_dir"] = Path(replay_dir).as_posix()
    applied = overlay or {}
    for table, values in applied.items():
        raw.setdefault(table, {}).update(values)

    text = migrate.render(raw, accounts)
    # The renderer writes the schema's sections in the schema's order. A key it
    # did not know would vanish silently, so the copy is read back and compared
    # before anything is built from it.
    written = tomllib.loads(text)
    written.pop("accounts", None)
    lost = {f"[{table}] {key}" for table, body in raw.items() if isinstance(body, dict)
            for key in body if key not in written.get(table, {})}
    if lost:
        raise RepointError(
            f"{pack.name}: writing the run's copy of the pack would drop {', '.join(sorted(lost))}. "
            "That is a gap in the pack writer, not in this plan - the run stopped rather than "
            "build a plant from a pack missing a setting.")
    (into / fmt.PLANT_FILE).write_text(text, encoding="utf-8")

    report = checker.check(into)
    if not report.ok:
        problems = "; ".join(f"{p.where} {p.says}" for p in report.problems)
        raise RepointError(
            f"{pack.name}: the run's copy of the pack has {len(report.problems)} problem(s) "
            f"and nothing was started - {problems}")
    return into, applied
