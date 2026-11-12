"""The wheel carries the lab packs, and carries exactly what the lab has.

A customer who runs `pip install factorysemantics-mes` has no checkout. Until
2026-10-12 the wheel had not one file under `labs/`, so every plant the lab
demonstrates lived only in a git clone. It ships them now, as package data
under `fsmes/data/packs/<name>`.

The shipped copies are **copies**, never a second source of truth, and the
copying is done by hatchling's `force-include` in `pyproject.toml` - which
honours neither `exclude` nor `.gitignore` (measured 2026-10-12: a directory
entry shipped the generated `out/` CSVs through three different spellings of
an exclusion). So the entries are written out file by file, and the thing that
can go wrong is a file added to a lab pack and never added to that list. This
is the test that fails that day, from the checkout, in a second - the
`wheel-plant` CI job proves the same thing against a real built wheel, and
costs half an hour.

Cutlery joined them on 2026-11-12, for `fsmes demo cutlery` on Scott's PC.
Its folder is also the lab's workshop - the scripts that generate the plant,
the trace driver, the scorer - and a pack carries no code (decision 0022), so
for cutlery the pack is the files a plant is made of, named in `PACK_FILES`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
#: Each shipped pack and the folder in the checkout it is copied from.
PACKS = {"bottling": "labs/multiplant/bottling",
         "machining": "labs/multiplant/machining",
         "cutlery": "labs/cutlery"}
#: For a pack whose folder holds more than the pack: the files that are the
#: pack. Everything else in that folder is the lab's tooling and stays home.
PACK_FILES = {"cutlery": ("plant.toml", "line.json", "floor.json", "tag_map.json", "masterdata")}
#: Where the packs land inside the wheel.
UNDER = "fsmes/data/packs"


def force_include() -> dict[str, str]:
    table = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = table["tool"]["hatch"]["build"]["targets"]["wheel"]
    return build["force-include"]


def lab_pack_files(name: str) -> list[Path]:
    """Every file in a lab pack, except the generated replay.

    `out/` is generated from the seed in `line.json` on first start, and the
    one thing this whole arrangement must not do is ship nine megabytes of
    CSV in a two-megabyte wheel.
    """
    pack = REPO / PACKS[name]
    wanted = PACK_FILES.get(name)
    return sorted(p for p in pack.rglob("*")
                  if p.is_file() and "out" not in p.relative_to(pack).parts
                  and (wanted is None or p.relative_to(pack).parts[0] in wanted))


@pytest.fixture(autouse=True)
def only_from_a_checkout():
    if not (REPO / "pyproject.toml").is_file():
        pytest.skip("installed package: pyproject.toml and labs/ only exist in a checkout")


@pytest.mark.parametrize("name", PACKS)
def test_every_file_in_a_lab_pack_is_one_the_wheel_will_carry(name):
    entries = force_include()
    covered = []
    for source in lab_pack_files(name):
        relative = source.relative_to(REPO).as_posix()
        named = relative in entries
        # A directory entry covers everything beneath it: `masterdata` is
        # listed as a directory because its contents are a plant's own master
        # data, which grows, and a file-by-file list of it would be the same
        # trap one level down.
        beneath = any(relative.startswith(f"{listed}/") for listed in entries)
        covered.append((relative, named or beneath))
    uncovered = [r for r, ok in covered if not ok]
    assert not uncovered, (
        f"{len(uncovered)} of {len(covered)} files in {PACKS[name]} would not "
        f"be in the wheel: {uncovered}. Add each to "
        f"[tool.hatch.build.targets.wheel.force-include] in pyproject.toml.")


@pytest.mark.parametrize("name", PACKS)
def test_a_pack_the_wheel_carries_lands_where_the_package_looks_for_it(name):
    entries = force_include()
    mine = {source: destination for source, destination in entries.items()
            if source.startswith(f"{PACKS[name]}/")}
    assert mine, f"pyproject.toml ships no part of {PACKS[name]}"
    for source, destination in mine.items():
        assert destination.startswith(f"{UNDER}/{name}/"), (
            f"{source} would land at {destination}, which is not where "
            f"fsmes.standalone looks ({UNDER}/{name})")
        assert (REPO / source).exists(), f"pyproject.toml ships {source}, which is not there"


def test_the_wheel_is_told_to_carry_no_generated_line_data():
    """`out/` is 9.7 MB for bottling and is a copy of what a seed already says."""
    shipped = [s for s in force_include() if s.startswith("labs/")]
    generated = [s for s in shipped if "/out" in s or s.endswith(".csv")]
    assert not generated, (
        f"pyproject.toml ships generated line data: {generated}. It is generated "
        f"from line.json on first start - see fsmes.standalone.generate_replay.")


def test_the_wheel_carries_the_cutlery_plant_and_none_of_the_lab_s_scripts():
    """The pack is the plant; the scripts that make it stay in the checkout."""
    shipped = [s for s in force_include() if s.startswith("labs/cutlery/")]
    assert sorted(Path(s).name for s in shipped) == sorted(PACK_FILES["cutlery"])
    assert not [s for s in shipped if s.endswith(".py")], shipped
