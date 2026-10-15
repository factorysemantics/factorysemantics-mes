"""The wheel carries the assistant folder, and carries exactly what the repository has.

`fsmes skills unpack plant-from-your-mes ./assistant` is the first step of
building a plant shaped like your own: the folder goes onto the box that has
the MES on it, somebody runs the three commands in its `SKILL.md` by hand, and
a `plant-shape.toml` comes back. Until 2026-10-13 the folder existed only as a
zip on a release page — which is a download, which is a thing a locked-down
plant network does not do.

It ships as package data under `fsmes/data/skills/<name>`, copied at build time
by hatchling's `force-include`, which honours neither `exclude` nor
`.gitignore`. So the entries are written out file by file, and the thing that
can go wrong is a file added to the skill and never added to that list. This is
the test that fails that day, from the checkout, in a second — the
`plant-from-shape` CI job proves the same thing against a real built wheel and
costs half an hour.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from fsmes import skills

REPO = Path(__file__).resolve().parents[1]
#: Where the folders land inside the wheel.
UNDER = "fsmes/data/skills"


def force_include() -> dict[str, str]:
    table = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = table["tool"]["hatch"]["build"]["targets"]["wheel"]
    return build["force-include"]


@pytest.fixture(autouse=True)
def only_from_a_checkout():
    if not (REPO / "pyproject.toml").is_file():
        pytest.skip("installed package: pyproject.toml and skills/ only exist in a checkout")


@pytest.mark.parametrize("name", sorted(skills.SKILLS))
def test_every_file_the_skill_ships_is_one_the_wheel_will_carry(name):
    entries = force_include()
    uncovered = [rel for rel in skills.SKILLS[name]
                 if f"skills/{name}/{rel}" not in entries]
    assert not uncovered, (
        f"{len(uncovered)} of {len(skills.SKILLS[name])} files in skills/{name} would "
        f"not be in the wheel: {uncovered}. Add each to "
        f"[tool.hatch.build.targets.wheel.force-include] in pyproject.toml.")


@pytest.mark.parametrize("name", sorted(skills.SKILLS))
def test_the_folder_the_wheel_carries_lands_where_the_package_looks_for_it(name):
    entries = force_include()
    mine = {source: destination for source, destination in entries.items()
            if source.startswith(f"skills/{name}/")}
    assert mine, f"pyproject.toml ships no part of skills/{name}"
    for source, destination in mine.items():
        assert destination == f"{UNDER}/{source[len('skills/'):]}", (
            f"{source} would land at {destination}, which is not where "
            f"fsmes.skills looks ({UNDER}/{name})")
        assert (REPO / source).exists(), f"pyproject.toml ships {source}, which is not there"


@pytest.mark.parametrize("name", sorted(skills.SKILLS))
def test_the_wheel_carries_nothing_the_skill_does_not_list(name):
    """The shipping list and the folder on disk, in both directions.

    `fsmes.skills.SKILLS` is the list a release is built from, and
    `fsmes skills-zip --check` already fails when it and the folder disagree.
    This says the same thing about the wheel, so a stray file cannot reach a
    customer's machine through the other door.
    """
    missing, stray = skills.differences(name, REPO)
    assert not missing and not stray, (
        f"skills/{name}/ and fsmes.skills.SKILLS disagree — listed but not on disk: "
        f"{missing or 'none'}; on disk but not listed: {stray or 'none'}")
    shipped = {source[len(f"skills/{name}/"):] for source in force_include()
               if source.startswith(f"skills/{name}/")}
    assert shipped == set(skills.SKILLS[name]), (
        f"the wheel's list and the skill's list differ: only in the wheel "
        f"{sorted(shipped - set(skills.SKILLS[name]))}, only in the skill "
        f"{sorted(set(skills.SKILLS[name]) - shipped)}")


def test_unpacking_writes_the_folder_out_without_a_wheel_or_a_network(tmp_path):
    """A checkout has no `fsmes/data/skills` — the copies are made at build
    time — so this falls back to the repository's own folder, which is the
    thing the wheel copies. Either way `unpack` writes files and nothing else."""
    into = skills.unpack("plant-from-your-mes", tmp_path, echo=lambda _: None)
    written = sorted(p.relative_to(into).as_posix() for p in into.rglob("*") if p.is_file())
    assert written == sorted(skills.SKILLS["plant-from-your-mes"]), (
        f"unpacked {len(written)} file(s), the skill lists "
        f"{len(skills.SKILLS['plant-from-your-mes'])}")
    assert (into / "SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_unpacking_over_a_folder_somebody_has_worked_in_is_refused(tmp_path):
    """Their `plant-shape.toml` may be in there. A command that replaced it
    would be a command that lost the work the whole path exists for."""
    theirs = tmp_path / "plant-from-your-mes"
    theirs.mkdir()
    (theirs / "plant-shape.toml").write_text("shape_format = 1\n", encoding="utf-8")
    with pytest.raises(FileExistsError) as refused:
        skills.unpack("plant-from-your-mes", tmp_path, echo=lambda _: None)
    assert "already there" in str(refused.value)
    assert (theirs / "plant-shape.toml").read_text(encoding="utf-8") == "shape_format = 1\n"


def test_a_folder_nobody_ships_is_named_as_such(tmp_path):
    with pytest.raises(LookupError) as refused:
        skills.unpack("plant-from-somebody-elses-mes", tmp_path, echo=lambda _: None)
    assert "plant-from-somebody-elses-mes" in str(refused.value)


def test_unpacking_the_assistant_nests_the_other_skill_inside_it(tmp_path):
    """Stage 2 of the assistant *is* `plant-from-your-mes`, so that folder has
    to be inside the one a person downloads — made here from the one copy in
    `skills/plant-from-your-mes/`, which is why there is no second copy in git
    and no symlink for Windows to mangle."""
    into = skills.unpack("assistant", tmp_path, echo=lambda _: None)
    written = sorted(p.relative_to(into).as_posix() for p in into.rglob("*") if p.is_file())
    assert written == sorted(lands for lands, _ in skills.tree("assistant")), (
        f"unpacked {len(written)} file(s); fsmes.skills.tree('assistant') says "
        f"{len(skills.tree('assistant'))}")
    nested = [f for f in written if f.startswith("plant-from-your-mes/")]
    assert len(nested) == len(skills.SKILLS["plant-from-your-mes"]), (
        f"{len(nested)} nested file(s) of {len(skills.SKILLS['plant-from-your-mes'])}")
    assert (into / "plant-from-your-mes" / "SKILL.md").is_file()
    assert not any(p.is_symlink() for p in into.rglob("*")), (
        "a symlink in the unpacked folder: a zip of one arrives on Windows as a "
        "text file with a path in it")


def test_the_assistant_says_what_it_wrote_and_that_the_other_folder_is_in_it(tmp_path):
    """The closing lines are the only instructions a person gets before they
    hand the folder to an assistant, so they are worth a test."""
    said: list[str] = []
    skills.unpack("assistant", tmp_path, echo=said.append)
    whole = "\n".join(said)
    assert f"{len(skills.tree('assistant'))} file(s)" in whole
    assert "including plant-from-your-mes/" in whole
    assert "needs no access to this machine" in whole
