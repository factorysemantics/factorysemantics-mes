"""The zip that ships on a release holds exactly the files that are listed, and no others.

A skill leaves this repository as a zip somebody else unpacks next to their own
agent. Two things can go wrong quietly: a file is added to the folder and never
reaches the list, so it is missing from every release; or something that should
never ship — a `__pycache__`, a scratch database, half a plant shape with real
numbers in it — is in the folder and gets packaged because the builder globbed.

So the contents are a list, not a glob, and this is the check that the list and
the folder agree in both directions. `fsmes skills-zip --check` is the same
check from the command line, which is what CI runs.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from fsmes import skills
from fsmes.cli import app

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "plant-from-your-mes"


def test_the_folder_and_the_shipping_list_agree_in_both_directions() -> None:
    for name in skills.SKILLS:
        missing, stray = skills.differences(name, ROOT)
        assert not missing, f"listed but not on disk in skills/{name}/: {missing}"
        assert not stray, f"on disk but not listed in skills/{name}/: {stray}"


def test_the_zip_holds_the_skill_md_the_queries_and_the_three_scripts(tmp_path: Path) -> None:
    target = skills.build_zip("plant-from-your-mes", ROOT, tmp_path / "plant-from-your-mes.zip")
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
    assert names == [f"plant-from-your-mes/{p}" for p in skills.PLANT_FROM_YOUR_MES]
    assert "plant-from-your-mes/SKILL.md" in names
    assert "plant-from-your-mes/FORMAT.md" in names
    assert sum(1 for n in names if n.endswith(".sql")) == 32
    assert sorted(n for n in names if "/scripts/" in n) == [
        "plant-from-your-mes/scripts/explain.py",
        "plant-from-your-mes/scripts/leakcheck.py",
        "plant-from-your-mes/scripts/profile.py",
    ]
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


def test_the_same_tree_builds_the_same_bytes_twice(tmp_path: Path) -> None:
    """A release has to be re-makeable, so no clock goes into the archive."""
    first = skills.build_zip("plant-from-your-mes", ROOT, tmp_path / "one.zip").read_bytes()
    second = skills.build_zip("plant-from-your-mes", ROOT, tmp_path / "two.zip").read_bytes()
    assert first == second


def test_the_skill_declares_itself_so_an_agent_can_find_it() -> None:
    """The front matter is how somebody else's agent decides this folder answers
    the question it was asked."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text.split("---", 2)[1]
    assert "name: plant-from-your-mes" in front
    assert "description:" in front
    assert "without divulging" in front or "proprietary" in front
    # The question Scott's customer actually asks has to be answerable from here.
    assert "trade secrets" in text


def test_the_example_plant_shape_parses_and_reads_back_in_plain_words() -> None:
    """The example ships, so it has to be a file the tools can still read."""
    example = SKILL / "example" / "plant-shape.toml"
    shape = tomllib.loads(example.read_text(encoding="utf-8"))
    assert shape["plant"]["stations_total"] == 6
    assert shape["meta"]["leak_check"].startswith("passed: 0 of ")
    done = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "explain.py"), str(example)],
        check=False, capture_output=True, text=True, cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert "6 machines, in line order" in done.stdout


def test_nothing_in_the_skill_imports_anything_that_is_not_in_the_standard_library() -> None:
    """It runs on the customer's machine, next to their own agent, with no install.

    The promise in SKILL.md is "no install, no driver, no dependency". That is
    only true while it stays true, and the easiest way to break it is a
    well-meaning `import pandas`.
    """
    allowed = {
        "argparse", "ast", "csv", "datetime", "itertools", "json", "math", "pathlib", "re",
        "sqlite3", "statistics", "sys", "tomllib", "zipfile", "collections", "typing",
        "__future__", "leakcheck",
    }
    seen = 0
    for path in sorted(SKILL.glob("scripts/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                assert root in allowed, (
                    f"{path.name} imports {root!r}, which is not in the standard library set "
                    f"this skill promises to stay inside")
                seen += 1
    assert seen >= 12, f"only {seen} imports were examined; the scan is not reading the scripts"


def test_the_command_line_check_passes_and_lists_the_contents(tmp_path: Path) -> None:
    runner = CliRunner()
    check = runner.invoke(app, ["skills-zip", "--check", "--root", str(ROOT)])
    assert check.exit_code == 0, check.output
    assert "all listed, none stray" in check.output

    built = runner.invoke(app, ["skills-zip", "--root", str(ROOT), "--out", str(tmp_path / "s.zip")])
    assert built.exit_code == 0, built.output
    assert "plant-from-your-mes/SKILL.md" in built.output
    assert (tmp_path / "s.zip").exists()


def test_the_command_line_check_fails_when_a_stray_file_is_in_the_folder(tmp_path: Path) -> None:
    """The check has to be able to go red, or it is decoration on a release."""
    stray = SKILL / "scripts" / "scratch_for_a_test.py"
    stray.write_text("# left behind by somebody\n", encoding="utf-8")
    try:
        result = CliRunner().invoke(app, ["skills-zip", "--check", "--root", str(ROOT)])
        assert result.exit_code == 1, result.output
        assert "on disk but not listed" in result.output
        assert "scratch_for_a_test.py" in result.output
    finally:
        stray.unlink()
