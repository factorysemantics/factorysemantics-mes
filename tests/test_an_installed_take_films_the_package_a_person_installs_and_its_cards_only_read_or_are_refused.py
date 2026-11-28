"""An installed take films the package a person installs, and its cards only read or are refused.

Class 6.3 is filmed on the released package rather than this checkout:
`[installed]` makes `scripts/record-scene.py` pip-install the package into a
virtual environment of the take's own, start the demo plant with that
installation's own `fsmes demo <plant>`, and film terminal cards of three
kinds: what the install and the start printed before the roll (`shows`), a
read run on camera (`fsmes --version`, `fsmes demo <plant> status`), and a
refusal run on camera (`refuses = true`) — a command the CLI does not have,
or a second copy of the demo in a home of its own (`home = "another"`),
refused at the port check before it writes anything. This file holds the
scene checks to that. No pip, no plant, no browser: the installation's runs
are stood in for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HEAD = """name = "mini"
title = "Mini"
base = "http://127.0.0.1:8040"
badge = "pre-alpha · simulated plant · v{version}"
budget_seconds = 45
width = 1280
height = 720
"""

INSTALLED = """
[installed]
package = "factorysemantics-mes[postgres]"
plant = "cutlery"
"""

PLANT = """
[plant]
pack = "labs/cutlery"
"""


def scene_with(tmp_path: Path, card: str, top: str = INSTALLED) -> Path:
    path = tmp_path / "scene.toml"
    path.write_text(
        HEAD
        + top
        + '\n[[step]]\ndo = "slide"\nname = "card"\ntitle = "A card"\n'
        + card
        + '\n[[step]]\ndo = "say"\nline = "A line."\n',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "card",
    [
        'command = ["--version"]\nhold = 1.0',
        'command = ["demo", "cutlery", "status"]\nhold = 1.0',
        'shows = "install"\nstop_after = "Collecting"\nhold = 1.0',
        'shows = "start"\nstart_at = "cutlery started"\nhold = 1.0',
        'command = ["demoo"]\nrefuses = true\nhold = 0.5',
        'command = ["demo", "cutlery"]\nrefuses = true\nhome = "another"\nhold = 0.5',
    ],
)
def test_a_read_a_kept_print_or_a_refusal_is_a_card_an_installed_take_may_film(runner, tmp_path, card):
    scene = runner.load_scene(scene_with(tmp_path, card))

    assert scene["installed"] == {"package": "factorysemantics-mes[postgres]", "plant": "cutlery", "speed": None}


@pytest.mark.parametrize(
    ("card", "said"),
    [
        ('command = ["demo", "cutlery", "stop"]\nhold = 1.0', "not a command an installed take's card may run"),
        ('command = ["demo", "cutlery"]\nhold = 1.0', "not a command an installed take's card may run"),
        ('command = ["init-db"]\nrefuses = true\nhold = 1.0', "may not be filmed refusing"),
        ('command = ["demo", "cutlery", "status"]\nhome = "another"\nhold = 1.0', "a second copy being refused"),
        ('command = ["demoo"]\nrefuses = true\nhome = "next-door"\nhold = 1.0', "`home` is one of"),
        ('shows = "the-log"\nhold = 1.0', "`shows` is one of"),
        ('shows = "start"\ncommand = ["--version"]\nhold = 1.0', "runs nothing"),
        ('command = ["--version"]', "needs a `hold`"),
        ('command = ["--version"]\nlines = ["a line"]\nhold = 1.0', "not `lines`"),
    ],
)
def test_a_card_that_could_write_or_that_the_recorder_would_guess_at_is_refused_before_anything_runs(
    runner, tmp_path, card, said
):
    with pytest.raises(SystemExit) as refused:
        runner.load_scene(scene_with(tmp_path, card))

    assert said in str(refused.value)


def test_a_card_of_an_installed_take_is_not_a_card_of_a_scratch_plant_and_a_scene_is_one_or_the_other(runner, tmp_path):
    with pytest.raises(SystemExit):
        runner.load_scene(scene_with(tmp_path, 'shows = "install"\nhold = 1.0', top=PLANT))
    with pytest.raises(SystemExit):
        runner.load_scene(scene_with(tmp_path, 'command = ["--version"]\nhold = 1.0', top=INSTALLED + PLANT))


@pytest.mark.parametrize(
    ("installed", "said"),
    [
        ('package = "some-other-package"\nplant = "cutlery"', "factorysemantics-mes"),
        ('package = "factorysemantics-mes"\nplant = "Cutlery Plant"', "plant"),
        ('package = "factorysemantics-mes"\nplant = "cutlery"\nindex = "http://x"', "index"),
    ],
)
def test_it_installs_only_this_product_and_names_its_plant_plainly(runner, tmp_path, installed, said):
    with pytest.raises(SystemExit) as refused:
        runner.load_scene(
            scene_with(tmp_path, 'command = ["--version"]\nhold = 1.0', top="\n[installed]\n" + installed + "\n")
        )

    assert said in str(refused.value)


def test_a_card_shows_the_line_as_it_was_typed_and_a_scratch_card_still_types_fsmes(runner):
    installed = runner.terminal_html(
        "One line installs it",
        'pip install "factorysemantics-mes[postgres]"',
        ["Collecting factorysemantics-mes[postgres]"],
        1,
        146,
    )
    scratch = runner.terminal_html("Every second", ["oee", "explain", "FILL01", "15m"], ["a line"], 1, 1)

    assert (
        "$ pip install &quot;factorysemantics-mes[postgres]&quot;" in installed
        or '$ pip install "factorysemantics-mes[postgres]"' in installed
    )
    assert "lines 1 to 1 of the 146 it printed" in installed
    assert "$ fsmes oee explain FILL01 15m" in scratch


class Ran:
    """What `subprocess.run` hands back, for a stand-in installation."""

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


def an_installation(runner, monkeypatch, answers: dict[tuple[str, ...], Ran]):
    demo = runner.InstalledDemo({"package": "factorysemantics-mes", "plant": "cutlery", "speed": None})
    ran: list[tuple[tuple[str, ...], str]] = []

    def run(argv, home, timeout):
        words = tuple(argv[1:])
        ran.append((words, home))
        return answers.get(words, Ran(0, ""))

    monkeypatch.setattr(demo, "_run", run)
    return demo, ran


def test_a_refusal_that_does_not_refuse_fails_the_take_and_its_second_copy_is_still_stopped(runner, monkeypatch):
    demo, ran = an_installation(runner, monkeypatch, {("demo", "cutlery"): Ran(0, "cutlery started")})

    with pytest.raises(SystemExit) as failed:
        demo.card(["demo", "cutlery"], refuses=True, home="another")
    demo.stop()

    assert "did not" in str(failed.value)
    assert (("demo", "cutlery", "stop"), "another") in ran
    assert not demo.root.exists()


def test_a_read_that_fails_fails_the_take_and_a_kept_print_runs_nothing(runner, monkeypatch):
    demo, ran = an_installation(runner, monkeypatch, {("--version",): Ran(1, "broken")})
    demo.kept["install"] = ('pip install "factorysemantics-mes"', "Collecting factorysemantics-mes\n")

    assert demo.card(None, shows="install") == (
        'pip install "factorysemantics-mes"',
        "Collecting factorysemantics-mes\n",
    )
    assert ran == []
    with pytest.raises(SystemExit):
        demo.card(["--version"])
    demo.stop()


def test_its_runs_read_their_output_as_utf_8_whatever_the_console_says(runner, monkeypatch):
    demo = runner.InstalledDemo({"package": "factorysemantics-mes", "plant": "cutlery", "speed": None})
    seen: dict = {}

    def run(argv, **how):
        seen.update(how)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", run)
    demo._run(["fsmes", "--version"], "this", 5.0)
    demo.stop()

    assert seen["encoding"] == "utf-8"
    assert seen["env"]["FSMES_HOME"].endswith("home") and "ANTHROPIC_API_KEY" not in seen["env"]


def test_a_printed_line_longer_than_a_terminal_wraps_on_the_card_as_a_terminal_wraps_it(runner):
    said = (
        "what is on it: another program on this PC - nothing this installation made is recorded on that port."
        " Stop that program, or give this plant another port in its pack."
    )
    wrapped = runner.terminal_wrap("  " + said)

    assert len(wrapped) == 2 and all(len(line) <= runner.TERMINAL_COLUMNS for line in wrapped)
    assert " ".join(line.strip() for line in wrapped) == said, "wrapping drops no word"
    assert wrapped[1].startswith("    "), "a wrapped line is indented under the line it continues"
    assert runner.terminal_wrap("a short line") == ["a short line"]
    card = runner.terminal_html("Refused", ["demo", "cutlery"], ["  " + said], 1, 1)
    assert wrapped[1] in card
