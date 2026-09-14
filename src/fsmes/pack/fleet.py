"""The fleet file: which packs this machine runs.

The registry used to be the plant. It held seventeen keys per plant, read
into a plain dict with no schema, and every one of them became an `MES_*`
variable at spawn time - which is exactly why two plants could run side by
side with no multi-tenant code path anywhere, and is worth keeping.

What changes is where the seventeen keys live. They are in the pack now, and
the file that used to hold them holds a **list of packs**:

    packs = ["bottling", "machining", "finewire"]

    [environment]
    data_dir = "labs/multiplant/.data"

Paths are relative to the fleet file, so a fleet file and its packs move
together. `[environment] data_dir` stays here and not in a pack, because
where several plants keep their databases on one machine is a fact about the
machine rather than about any one plant.

This module compiles each pack into the plain dictionary `fsmes.plant`
already knows how to run, so nothing about starting, stopping, migrating or
watching a plant changed with the format.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from fsmes import plant as plants
from fsmes.pack import format as fmt


class FleetError(Exception):
    """The fleet file cannot be used as written, and says why."""


def path(root: Path) -> Path:
    """The fleet file: `FSMES_PLANT_REGISTRY` if it is set, the lab's
    otherwise. The environment variable keeps its name - what it points at
    changed, and every deployment unit that sets it did not."""
    return plants.registry_path(root)


def load(root: Path) -> dict[str, dict]:
    """Every plant this fleet runs, compiled from its pack.

    The key is the plant's name **as its pack states it**, not as the fleet
    file spells the directory - the pack is what a plant says it is, and a
    directory somebody renamed is not a rename of the plant.
    """
    fleet = path(root)
    if not fleet.is_file():
        raise FileNotFoundError(
            f"No fleet file at {fleet}.\n"
            f"Run this from the repository, pass --root, or set {plants.REGISTRY_ENV}.")
    try:
        table = tomllib.loads(fleet.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise FleetError(f"{fleet} is not valid TOML: {exc}") from exc

    if "plants" in table:
        raise FleetError(
            f"{fleet} still describes plants directly. A plant is a pack now: one "
            "directory with a plant.toml in it. `fsmes pack migrate` writes one from "
            "each of this file's entries, and docs/operate/packs.md is the page.")

    listed = table.get("packs")
    if listed is None:
        raise FleetError(
            f"{fleet} lists no packs. A fleet file is `packs = [\"<directory>\", ...]`, "
            "each path relative to this file.")
    if not isinstance(listed, list) or not all(isinstance(p, str) for p in listed):
        raise FleetError(f"{fleet}: `packs` is a list of directory paths, as strings.")

    out: dict[str, dict] = {}
    for entry in listed:
        directory = (fleet.parent / entry).resolve()
        pack = fmt.read(directory)
        name = pack.name
        if not name:
            raise FleetError(f"{directory / fmt.PLANT_FILE} does not say which plant it is.")
        if name in out:
            raise FleetError(
                f"{fleet} lists two packs that both call themselves {name!r}: "
                f"{out[name]['pack']} and {directory}. Two plants with one name is two "
                "plants a fleet cannot tell apart.")
        out[name] = compile_pack(pack)
    return out


def compile_pack(pack: fmt.Pack) -> dict:
    """One pack as the plain configuration `fsmes.plant` runs.

    Everything the plant's processes need is in `env`, already resolved
    against the pack directory. The handful of keys beside it are what the
    *fleet* reads rather than the plant: the label on a status line, the port
    to ask for health, whether this plant simulates at all.
    """
    serve = pack.table("serve")
    storage = pack.table("storage")
    files = pack.table("files")
    return {
        "name": pack.name,
        "label": pack.label,
        "pack": pack.directory,
        "env": fmt.settings(pack),
        "api_host": serve.get("api_host", "127.0.0.1"),
        "api_port": serve.get("api_port"),
        "simulate": serve.get("simulate", True),
        "speed": serve.get("speed"),
        "accounts": pack.accounts(),
        "database_url": storage.get("database_url"),
        "database_password_file": storage.get("database_password_file"),
        # What the simulator and the scorer read directly rather than through
        # the environment: the wiring diagram, and the generated line data
        # whose sibling `line.json` is the ground truth a run is scored
        # against. Absolute, because a pack is a directory and not a path
        # relative to whatever a caller's working directory happens to be.
        "tag_map": pack.path(str(files["tag_map"])).as_posix() if files.get("tag_map") else None,
        "replay_dir": pack.path(str(files["replay_dir"])).as_posix()
        if files.get("replay_dir") else None,
    }
