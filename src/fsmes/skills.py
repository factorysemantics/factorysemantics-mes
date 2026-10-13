"""Build the skill folders that ship as zips on a release.

A skill is a folder somebody else's agent takes in: a `SKILL.md` it reads, the
queries it runs, the scripts it runs them with. It is not imported by the MES and
nothing in `src/fsmes` depends on it — this module exists only to package it, and
to give CI something to check so a release cannot ship a zip with a stray file,
a missing script or somebody's `__pycache__` in it.

The contents are listed, not globbed. A glob would quietly ship whatever happened
to be in the folder; a list means adding a file to a skill is a line in a diff,
and `fsmes skills-zip --check` fails when the folder and the list disagree in
either direction.

Deterministic: every entry is stored with the same fixed timestamp, so the same
tree produces byte-identical bytes and a release can be re-made.
"""
from __future__ import annotations

import zipfile
from importlib import resources
from pathlib import Path

#: Fixed date for every entry. A zip stores local modification times, so
#: building the same tree twice would otherwise give two different files.
FIXED_TIME = (2026, 1, 1, 0, 0, 0)

#: Exactly what `plant-from-your-mes.zip` holds, in the order it is written.
PLANT_FROM_YOUR_MES: tuple[str, ...] = (
    "SKILL.md",
    "FORMAT.md",
    "example/plant-shape-grouped.toml",
    "example/plant-shape.toml",
    "queries/mssql/01_inventory.sql",
    "queries/mssql/01b_row_count.sql",
    "queries/mssql/02_columns.sql",
    "queries/mssql/03_window.sql",
    "queries/mssql/04_column_profile.sql",
    "queries/mssql/04b_column_sample.sql",
    "queries/mssql/05_assets.sql",
    "queries/mssql/05b_tag_samples.sql",
    "queries/mssql/05c_tag_samples_nostate.sql",
    "queries/mssql/05d_routings.sql",
    "queries/mssql/06_states.sql",
    "queries/mssql/06b_production.sql",
    "queries/mssql/06c_quality.sql",
    "queries/mssql/06d_orders.sql",
    "queries/mssql/06e_shifts.sql",
    "queries/mssql/06f_documents.sql",
    "queries/mssql/07_erp.sql",
    "queries/sqlite/01_inventory.sql",
    "queries/sqlite/01b_row_count.sql",
    "queries/sqlite/02_columns.sql",
    "queries/sqlite/03_window.sql",
    "queries/sqlite/04_column_profile.sql",
    "queries/sqlite/04b_column_sample.sql",
    "queries/sqlite/05_assets.sql",
    "queries/sqlite/05b_tag_samples.sql",
    "queries/sqlite/05c_tag_samples_nostate.sql",
    "queries/sqlite/05d_routings.sql",
    "queries/sqlite/06_states.sql",
    "queries/sqlite/06b_production.sql",
    "queries/sqlite/06c_quality.sql",
    "queries/sqlite/06d_orders.sql",
    "queries/sqlite/06e_shifts.sql",
    "queries/sqlite/06f_documents.sql",
    "queries/sqlite/07_erp.sql",
    "scripts/explain.py",
    "scripts/leakcheck.py",
    "scripts/profile.py",
)

#: Every skill that ships, by folder name.
SKILLS: dict[str, tuple[str, ...]] = {
    "plant-from-your-mes": PLANT_FROM_YOUR_MES,
}

#: Files that live in a skill folder in a checkout and must never ship.
NEVER_SHIP = ("__pycache__", ".pyc", ".pyo", ".DS_Store", ".ruff_cache", ".pytest_cache")


def _on_disk(folder: Path) -> list[str]:
    """What is actually in the folder, as paths relative to it, sorted."""
    found = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        if any(part in relative for part in NEVER_SHIP):
            continue
        found.append(relative)
    return sorted(found)


def differences(name: str, root: Path) -> tuple[list[str], list[str]]:
    """(listed but not on disk, on disk but not listed)."""
    listed = set(SKILLS[name])
    folder = root / "skills" / name
    actual = set(_on_disk(folder))
    return sorted(listed - actual), sorted(actual - listed)


def build_zip(name: str, root: Path, target: Path) -> Path:
    """Write `<name>.zip` from `skills/<name>/`, refusing if the list is stale."""
    missing, stray = differences(name, root)
    if missing or stray:
        detail = []
        if missing:
            detail.append(f"listed but not on disk: {', '.join(missing)}")
        if stray:
            detail.append(f"on disk but not listed: {', '.join(stray)}")
        raise FileNotFoundError(
            f"skills/{name}/ and fsmes.skills.SKILLS['{name}'] disagree — " + "; ".join(detail)
        )
    folder = root / "skills" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in SKILLS[name]:
            info = zipfile.ZipInfo(f"{name}/{relative}", date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 0o644, or 0o755 for the scripts a person runs.
            mode = 0o755 if relative.startswith("scripts/") and relative.endswith(".py") else 0o644
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, (folder / relative).read_bytes())
    return target


# ------------------------------------------- the copy that ships inside the wheel

#: Where the wheel keeps its copy of each skill folder, under `fsmes/data/`.
#: `pyproject.toml`'s force-include puts it there, file by file, from the list
#: above; `tests/test_the_wheel_ships_the_assistant_folder.py` fails when the
#: two disagree.
SKILLS_IN_PACKAGE = ("data", "skills")


def _in_a_checkout(name: str, start: Path | None = None) -> Path | None:
    """`skills/<name>/` above here, when this is a checkout rather than a wheel.

    An editable install has no `fsmes/data/skills` - the copies are made at
    build time - and the folder a developer means is the one in the repository,
    which is the source of truth the wheel copies from. So `fsmes skills
    unpack` works in both worlds and neither is a second copy.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        folder = candidate / "skills" / name
        if (folder / "SKILL.md").is_file():
            return folder
    return None


def _in_the_package(name: str) -> bool:
    try:
        root = resources.files("fsmes").joinpath(*SKILLS_IN_PACKAGE, name)
        return root.joinpath("SKILL.md").is_file()
    except (OSError, FileNotFoundError, NotADirectoryError, ModuleNotFoundError):
        return False


def shipped() -> list[str]:
    """The skill folders this installation carries, by name, sorted.

    Read from the package rather than from `SKILLS`, because `SKILLS` says what
    a checkout would package and this says what is actually here. An install
    from a wheel built before the folders shipped gets an empty list and a
    command that says so.
    """
    try:
        root = resources.files("fsmes").joinpath(*SKILLS_IN_PACKAGE)
        return sorted(entry.name for entry in root.iterdir()
                      if entry.joinpath("SKILL.md").is_file())
    except (OSError, FileNotFoundError, NotADirectoryError, ModuleNotFoundError):
        return []


def available() -> list[str]:
    """What `fsmes skills unpack` can write out here: the package's copies, or
    a checkout's own `skills/` when this is a checkout."""
    here = shipped()
    if here:
        return here
    return sorted(name for name in SKILLS if _in_a_checkout(name) is not None)


def unpack(name: str, target: Path, *, echo=print) -> Path:
    """Write a shipped skill folder into `target/<name>/`, and say what landed.

    No network, no zip, no checkout: the files are already on this machine
    inside the installed package. This is how the folder gets onto a plant
    network that cannot reach a release page.

    Refuses to overwrite: a folder somebody has already unpacked may have
    their notes or their `plant-shape.toml` in it, and a command that silently
    replaced it would be a command that lost work.
    """
    here = available()
    if name not in here:
        total = f"{len(here)} skill folder(s)" if here else "no skill folders"
        known = f": {', '.join(here)}" if here else (
            " — this release ships none, and the folder is on its Release page as a zip")
        raise LookupError(f"This installation carries {total}{known}. There is no {name!r}.")

    into = Path(target).expanduser().resolve() / name
    if into.exists() and any(into.iterdir()):
        raise FileExistsError(
            f"{into} is already there and is not empty. Unpack somewhere else, or "
            f"move that folder aside first — anything you have written in it, "
            f"a plant-shape.toml included, stays yours.")

    if _in_the_package(name):
        with resources.as_file(
                resources.files("fsmes").joinpath(*SKILLS_IN_PACKAGE, name)) as source:
            written = _copy_out(Path(source), into)
    else:
        folder = _in_a_checkout(name)
        if folder is None:                                      # pragma: no cover
            raise LookupError(f"no {name} here")
        written = _copy_out(folder, into)
    echo(f"{into} — {written} file(s)")
    echo(f"  read {into / 'SKILL.md'} next. It is written for an assistant, and a "
         f"person can follow it: three commands against your own MES, by hand, and a "
         f"plant-shape.toml comes out.")
    echo("  the scripts need nothing on that box but a python 3.11 or newer (they read "
         "TOML with the standard library's own tomllib) — no fsmes, no driver, no "
         "internet, no AI.")
    return into


def _copy_out(source: Path, into: Path) -> int:
    """Every file under `source` into `into`, keeping the shape of the tree.

    Not `shutil.copytree`: a developer who has run the scripts in place has a
    `__pycache__` beside them, and a folder carried onto a plant network should
    be the files the release lists and nothing else.
    """
    written = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in relative.as_posix() for part in NEVER_SHIP):
            continue
        destination = into / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
        written += 1
    return written
