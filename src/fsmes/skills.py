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
from pathlib import Path

#: Fixed date for every entry. A zip stores local modification times, so
#: building the same tree twice would otherwise give two different files.
FIXED_TIME = (2026, 1, 1, 0, 0, 0)

#: Exactly what `plant-from-your-mes.zip` holds, in the order it is written.
PLANT_FROM_YOUR_MES: tuple[str, ...] = (
    "SKILL.md",
    "FORMAT.md",
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
