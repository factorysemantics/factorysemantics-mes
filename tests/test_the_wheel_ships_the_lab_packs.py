"""The wheel carries the two lab packs, and carries exactly what the lab has.

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
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKS = ("bottling", "machining")
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
    pack = REPO / "labs" / "multiplant" / name
    return sorted(p for p in pack.rglob("*")
                  if p.is_file() and "out" not in p.relative_to(pack).parts)


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
        f"{len(uncovered)} of {len(covered)} files in labs/multiplant/{name} would not "
        f"be in the wheel: {uncovered}. Add each to "
        f"[tool.hatch.build.targets.wheel.force-include] in pyproject.toml.")


@pytest.mark.parametrize("name", PACKS)
def test_a_pack_the_wheel_carries_lands_where_the_package_looks_for_it(name):
    entries = force_include()
    mine = {source: destination for source, destination in entries.items()
            if source.startswith(f"labs/multiplant/{name}/")}
    assert mine, f"pyproject.toml ships no part of labs/multiplant/{name}"
    for source, destination in mine.items():
        assert destination.startswith(f"{UNDER}/{name}/"), (
            f"{source} would land at {destination}, which is not where "
            f"fsmes.standalone looks ({UNDER}/{name})")
        assert (REPO / source).exists(), f"pyproject.toml ships {source}, which is not there"


def test_the_wheel_is_told_to_carry_no_generated_line_data():
    """`out/` is 9.7 MB for bottling and is a copy of what a seed already says."""
    shipped = [s for s in force_include() if s.startswith("labs/multiplant/")]
    generated = [s for s in shipped if "/out" in s or s.endswith(".csv")]
    assert not generated, (
        f"pyproject.toml ships generated line data: {generated}. It is generated "
        f"from line.json on first start - see fsmes.standalone.generate_replay.")
