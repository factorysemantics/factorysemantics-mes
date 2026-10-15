#!/usr/bin/env python3
"""Prove the assistant folder comes out of a built wheel, offline, on Linux and
on Windows.

This is the first line of the assistant handoff's definition of done, checked
the way a person would check it:

1. `pipx install factorysemantics-mes` on a PC that may reach the internet
   once and never again - here, a fresh virtual environment with the wheel in
   it and the network off afterwards;
2. `fsmes skills list` names `assistant`, and says the folder came out of the
   installation rather than out of a checkout lying above the working
   directory;
3. `fsmes skills unpack assistant ./x` writes the seven markdown pages **and**
   `plant-from-your-mes/` inside them, every file byte-for-byte the
   checkout's;
4. nothing in what it wrote is a symlink, and the repository has no second
   copy of the nested folder. That pair is the whole reason `INCLUDES` exists:
   one copy in git, nested at build and unpack time, and a zip of a symlink
   unpacks on Windows as a text file with a path in it.

Windows as well as Linux because the first person to install this was on
Windows, and because a path separator is the kind of thing that only breaks
there.

`plant_from_shape.py` beside this already builds the two virtual environments
and turns the network off; this imports those helpers rather than keeping a
second copy of them, for the same reason the skill folder has one copy.

Usage:  python .github/scripts/assistant_folder_from_the_wheel.py --wheel dist/<name>.whl
"""

from __future__ import annotations

import argparse
import filecmp
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plant_from_shape import (
    AS_WORDS,
    fail,
    make_venv,
    offline,
    prove_the_network_is_off,
    run,
    say,
    venv_bin,
)

#: The skill a person downloads, and the one it carries.
SKILL = "assistant"
INSIDE = "plant-from-your-mes"
#: Pages the folder is useless without, and the two files that prove the
#: nesting really happened. Named here rather than globbed so that a
#: `force-include` line somebody forgets is a red job and not a quiet gap.
MUST_CARRY = (
    "SKILL.md",
    "README.md",
    "0-how-to-help.md",
    "1-install.md",
    "2-your-plant.md",
    "3-shadow-mode.md",
    "4-migration.md",
    f"{INSIDE}/SKILL.md",
    f"{INSIDE}/scripts/profile.py",
)


def what_the_checkout_holds(repo: Path) -> list[str]:
    """Every file the unpacked folder should have, from the one copy in git."""
    wanted = []
    for name, under in ((SKILL, ""), (INSIDE, f"{INSIDE}/")):
        folder = repo / "skills" / name
        wanted += [f"{under}{p.relative_to(folder).as_posix()}"
                   for p in folder.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts]
    return sorted(wanted)


def the_repository_has_one_copy(repo: Path) -> None:
    """No `skills/assistant/plant-from-your-mes/` in git, and no symlink to it."""
    second = repo / "skills" / SKILL / INSIDE
    if second.exists() or second.is_symlink():
        fail(f"skills/{SKILL}/{INSIDE} exists in the checkout. The nested folder is "
             f"made at build time from fsmes.skills.INCLUDES, from the one copy in "
             f"skills/{INSIDE}/ — a second copy in git drifts, and a symlink unpacks "
             f"on Windows as a text file with a path in it.")
    linked = [p for p in (repo / "skills" / SKILL).rglob("*") if p.is_symlink()]
    if linked:
        fail(f"symlink(s) in skills/{SKILL}/: {[p.name for p in linked]}")
    say(f"  skills/{SKILL}/ is {len(list((repo / 'skills' / SKILL).glob('*')))} real "
        f"file(s) and no link; skills/{INSIDE}/ is the only copy of the nested folder")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, help="the built wheel to prove")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    wheel = Path(args.wheel).resolve()
    if not wheel.is_file():
        fail(f"no wheel at {wheel}")

    say("== one copy in the repository ==")
    the_repository_has_one_copy(repo)

    work = Path(tempfile.mkdtemp(prefix="fsmes-assistant-")).resolve()
    say(f"\n== a clean machine: {work} ==")
    if repo in work.parents:
        fail(f"{work} is inside the checkout at {repo}; this proves nothing")

    say("== the person's PC: a fresh venv with the wheel in it ==")
    theirs = make_venv(work / ".venv-fsmes", wheel=wheel)
    fsmes = venv_bin(theirs, "fsmes")
    python = venv_bin(theirs, "python")
    reported = subprocess.run([str(fsmes), "--version"], check=True, cwd=work,
                              capture_output=True, **AS_WORDS)
    say(f"  installed: {reported.stdout.strip()}")

    say("== the network, off, for everything from here on ==")
    environment = offline(dict(os.environ))
    prove_the_network_is_off(python, environment)

    say(f"== `fsmes skills list` names {SKILL} ==")
    listed = run([str(fsmes), "skills", "list"], cwd=work, env=environment)
    if "inside this installation" not in listed.stdout:
        fail("`fsmes skills list` did not say the folder came out of the installation; "
             "it may have found a checkout above the working directory instead")
    # One folder per indented line; the last indented line is the hint, which
    # has spaces in it and no skill name does.
    named = [line.strip() for line in listed.stdout.splitlines()
             if line.startswith("  ") and " " not in line.strip()]
    if SKILL not in named:
        fail(f"`fsmes skills list` did not name {SKILL}. It named: {named}. Check the "
             f"force-include lines in pyproject.toml.")
    say(f"  {SKILL} is one of {len(named)} folder(s) listed: {', '.join(named)}")

    say(f"== `fsmes skills unpack {SKILL}` writes the tree, offline ==")
    run([str(fsmes), "skills", "unpack", SKILL, str(work / "out")],
        cwd=work, env=environment)
    folder = work / "out" / SKILL
    written = sorted(p.relative_to(folder).as_posix()
                     for p in folder.rglob("*") if p.is_file())
    say(f"  {len(written)} file(s) written to {folder}")

    missing = [f for f in MUST_CARRY if f not in written]
    if missing:
        fail(f"the unpacked folder is missing {', '.join(missing)}. If the pages are "
             f"there and the nested folder is not, fsmes.skills.INCLUDES did not run; "
             f"if a page is missing, add its force-include line to pyproject.toml.")

    wanted = what_the_checkout_holds(repo)
    if wanted != written:
        fail(f"the unpacked folder is not the checkout's. In skills/ only: "
             f"{sorted(set(wanted) - set(written)) or 'none'}. Unpacked only: "
             f"{sorted(set(written) - set(wanted)) or 'none'}.")
    differ = [f for f in wanted
              if not filecmp.cmp(repo / "skills" / (f if f.startswith(f"{INSIDE}/")
                                                    else f"{SKILL}/{f}"),
                                 folder / f, shallow=False)]
    if differ:
        fail(f"{len(differ)} file(s) differ from the checkout: {differ}")
    say(f"  byte-for-byte the checkout's skills/{SKILL}/ and skills/{INSIDE}/")

    linked = [p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_symlink()]
    if linked:
        fail(f"the unpacked folder contains symlink(s): {linked}")
    nested = [f for f in written if f.startswith(f"{INSIDE}/")]
    say(f"  nothing in it is a symlink; {len(nested)} of {len(written)} file(s) are the "
        f"nested {INSIDE}/ folder, which is stage 2")

    say(f"\nthe assistant folder came out of the wheel on {sys.platform}, with the "
        f"network off: {len(written)} file(s), {INSIDE}/ inside it, no second copy in "
        f"git and no symlink anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
