"""Lesson 1.2 rolls once the machine is watched, and shows the ledger the plant itself printed.

*Never invent production* is filmed on a scratch plant a few minutes old: its
long windows are mostly `before_first_sample`, so the hook and the shift rollup
are withheld, and its last fifteen minutes are watched enough to be shown. That
needs three things of `scripts/record-scene.py`, and this file holds it to them:

* `[plant] roll_when_watched` holds the camera, off camera, until the MES's
  own coverage of a machine over a window reaches a share — read, never written;
* a `slide` with a `command` is a terminal card: one read-only `fsmes` command,
  run on the take's own plant as it is filmed, cut only where the scene says
  and saying on the card that it was cut;
* a full lesson writes its teaser as an mp4 beside the GIF, cut the same way.

No plant and no browser: the waits are given the answers a plant gives, the
card is painted onto a page that is only a stand-in, and ffmpeg is never run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "01-02-never-invent-production.toml"

EXPLAIN = """FILL01 — Refill Station 01
  the last 15m: 2026-10-26 22:01:30 to 2026-10-26 22:16:30 UTC (0h 15m 00s  (900.0 s))

      from        to    seconds  disposition / cause
  22:01:30  22:06:57      327.3  before_first_sample
                                   rule: earlier than the first state or connection this MES ever recorded here
  22:06:57  22:16:30      572.7  observed_running
                                   rule: a recorded state interval said running
  2 interval(s)

  observed_running               0h 09m 33s  (572.7 s)
  not_observed                   0h 05m 27s  (327.3 s)
    before_first_sample          0h 05m 27s  (327.3 s)

  observed                       0h 09m 33s  (572.7 s)
  window                         0h 15m 00s  (900.0 s)
  coverage                       63.6%
  availability (run ÷ observed)  100.0%
"""


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_lesson_1_2", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scene_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "scene.toml"
    path.write_text('name = "x"\n' + body, encoding="utf-8")
    return path


PLANT = '\n[plant]\npack = "labs/multiplant/bottling"\n'


# ------------------------------------------------------------ the scene file


def test_the_lesson_is_a_full_take_of_1_2_that_runs_four_to_six_minutes(runner):
    scene = runner.load_scene(SCENE)

    assert scene["lesson"] == "1.2" and scene["take_is"] == "lesson"
    planned = runner.planned_seconds(scene)
    # Waits, page loads and the cards' own commands add to the plan; the
    # finished take is measured, and the plan leaves room for it either side.
    assert 240 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_the_lesson_walks_the_five_parts_in_order(runner):
    scene = runner.load_scene(SCENE)
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_short_enough_to_be_the_teaser(runner):
    scene = runner.load_scene(SCENE)
    hook = dict(scene, step=[step for step in scene["step"] if step["part"] == "hook"])

    # The handoff asks for a teaser of 35 s at most; the hook is the teaser.
    assert runner.planned_seconds(hook) - scene["gif_skip_seconds"] <= 30


def test_the_you_now_know_card_says_the_lessons_own_outcomes_word_for_word(runner):
    scene = runner.load_scene(SCENE)
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, "1.2")
    know = [step for step in scene["step"] if step["part"] == "know" and step["do"] == "slide"]

    assert len(know) == 1
    assert know[0]["lines"] == lesson["outcomes"]


def test_the_camera_waits_for_the_fifteen_minutes_to_be_watched_past_the_packs_floor(runner):
    scene = runner.load_scene(SCENE)
    watched = scene["plant"]["roll_when_watched"]
    import tomllib

    with (REPO / scene["plant"]["pack"] / "plant.toml").open("rb") as raw:
        floor = tomllib.load(raw)["oee"]["coverage_floor"]

    assert watched["equipment"] == "FILL01" and watched["hours"] == 0.25
    assert watched["at_least"] > floor, "the fifteen-minute figures would still be withheld"


def test_every_terminal_card_in_the_lesson_runs_only_oee_explain(runner):
    scene = runner.load_scene(SCENE)
    cards = [step for step in scene["step"] if step.get("command")]

    assert cards, "the ledger's rows are shown from the command that prints them"
    assert all(step["command"][:2] == ["oee", "explain"] for step in cards)


def test_the_ledger_card_asks_for_a_window_older_than_the_plant_can_be_when_it_is_shown(runner):
    # The camera rolls at 85% of fifteen minutes watched, so the plant is about
    # thirteen minutes old; the card comes a few minutes later. A fifteen-minute
    # window was watched end to end by then (take one's first attempt showed it)
    # and the caption about its not-observed top row would have been false.
    scene = runner.load_scene(SCENE)
    cards = [step for step in scene["step"] if step.get("command")]
    rolls_at = scene["plant"]["roll_when_watched"]["hours"] * 60

    for card in cards:
        minutes = int(card["command"][3].removesuffix("m"))
        assert minutes >= 2 * rolls_at, card["name"]


def test_no_caption_calls_the_stale_tag_or_a_young_plant_a_disconnection(runner):
    scene = runner.load_scene(SCENE)
    said = " ".join(runner.script_lines(scene)).lower()

    # No disconnection is filmed, so none is described as having happened.
    assert "disconnect" not in said
    assert "quiet" not in said


# ------------------------------------------------------- roll when watched


@pytest.mark.parametrize(
    ("watched", "complaint"),
    [
        ('roll_when_watched = { hours = 0.25, at_least = 0.8 }\n', "`equipment`"),
        ('roll_when_watched = { equipment = "FILL01", at_least = 0.8 }\n', "`hours`"),
        ('roll_when_watched = { equipment = "FILL01", hours = 0.25, at_least = 0 }\n', "above 0"),
        ('roll_when_watched = { equipment = "FILL01", hours = 0.25, at_least = 1.5 }\n', "at most 1"),
        ('roll_when_chart = "FG-BOTTLE/fill_weight"\n'
         'roll_when_watched = { equipment = "FILL01", hours = 0.25, at_least = 0.8 }\n', "one thing"),
    ],
)
def test_a_watched_wait_this_script_would_have_to_guess_at_is_refused(runner, tmp_path, watched, complaint):
    path = scene_file(tmp_path, '[[step]]\ndo = "hold"\nseconds = 1\n' + PLANT + watched)

    with pytest.raises(SystemExit) as refused:
        runner.load_scene(path)
    assert complaint in str(refused.value)


class PlantThatIsWatching:
    """Answers the machine's OEE question with the coverage it has reached, one answer a poll."""

    def __init__(self, coverages: list[float | None]) -> None:
        self.coverages = list(coverages)
        self.asked: list[str] = []

    def open(self, url: str, timeout: float = 10):
        import io

        self.asked.append(url)
        coverage = self.coverages.pop(0) if len(self.coverages) > 1 else self.coverages[0]
        return io.BytesIO(json.dumps({"equipment": "FILL01", "coverage": coverage}).encode("utf-8"))


def test_the_camera_rolls_when_the_mes_has_watched_enough_and_asks_nothing_else(runner, monkeypatch):
    plant = PlantThatIsWatching([None, 0.41, 0.79, 0.86])
    monkeypatch.setattr(runner, "a_signed_in_reader", lambda base, user, password: plant)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    spec = runner.plant_defaults({"pack": "labs/multiplant/bottling",
                                  "roll_when_watched": {"equipment": "FILL01", "hours": 0.25, "at_least": 0.8}})

    seen = runner.wait_for_the_watching("http://127.0.0.1:8010", spec, "ADMIN", "admin")

    assert seen["coverage"] == 0.86
    assert len(plant.asked) == 4, "it rolls on the first answer at or past the share, not before"
    assert set(plant.asked) == {"http://127.0.0.1:8010/equipment/FILL01/oee?hours=0.25"}


def test_a_machine_that_is_never_watched_enough_films_nothing(runner, monkeypatch):
    plant = PlantThatIsWatching([0.5])
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(runner, "a_signed_in_reader", lambda base, user, password: plant)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(runner.time, "monotonic", lambda: float(next(clock)))
    spec = runner.plant_defaults({"pack": "labs/multiplant/bottling", "roll_when_timeout_seconds": 60,
                                  "roll_when_watched": {"equipment": "FILL01", "hours": 0.25, "at_least": 0.8}})

    with pytest.raises(SystemExit) as refused:
        runner.wait_for_the_watching("http://127.0.0.1:8010", spec, "ADMIN", "admin")
    assert "Nothing was filmed" in str(refused.value)


def test_the_folder_says_the_camera_waited_for_the_machine_to_be_watched(runner):
    scene = runner.load_scene(SCENE)
    page = runner.readme(scene, {
        "said": runner.script_lines(scene), "plant": "bottling", "version": "0.4.2",
        "badge": "pre-alpha · simulated plant · v0.4.2", "base": scene["base"], "commit": "abc1234",
        "scene_file": SCENE.relative_to(REPO).as_posix(), "command": "python scripts/record-scene.py …",
        "when": "2026-10-26 17:00 CDT", "gif_seconds": 30.0, "gif_bytes": 4_000_000,
        "gif_setting": "10 fps, 1100 wide",
    })

    assert "until the MES had watched `FILL01` for at least 85% of the last 0.25 h" in page
    assert "**the terminal cards** are the output of the `fsmes` command" in page


# ------------------------------------------------------------ terminal card


@pytest.mark.parametrize(
    ("card", "complaint"),
    [
        ('command = ["fleet", "stop", "bottling"]\nhold = 3\n', "not a command a card may run"),
        ('command = "oee explain FILL01"\nhold = 3\n', "list of strings"),
        ('command = ["oee", "explain", "FILL01"]\n', "needs a `hold`"),
        ('command = ["oee", "explain", "FILL01"]\nhold = 3\nlines = ["typed by hand"]\n', "not `lines`"),
    ],
)
def test_a_terminal_card_that_could_write_or_would_guess_is_refused(runner, tmp_path, card, complaint):
    path = scene_file(tmp_path, '[[step]]\ndo = "slide"\nname = "t"\ntitle = "T"\n' + card + PLANT)

    with pytest.raises(SystemExit) as refused:
        runner.load_scene(path)
    assert complaint in str(refused.value)


def test_a_terminal_card_needs_the_takes_own_plant_to_run_on(runner, tmp_path):
    path = scene_file(tmp_path, '[[step]]\ndo = "slide"\nname = "t"\ntitle = "T"\n'
                                'command = ["oee", "explain", "FILL01"]\nhold = 3\n')

    with pytest.raises(SystemExit) as refused:
        runner.load_scene(path)
    assert "has no `[plant]`" in str(refused.value)


def test_a_card_cut_short_shows_from_its_first_piece_to_its_last_and_counts_the_lines(runner):
    shown, first, total = runner.trim_output(EXPLAIN, None, "interval(s)")

    assert shown[0].startswith("FILL01")
    assert shown[-1].strip() == "2 interval(s)"
    assert first == 1 and total == len(EXPLAIN.strip("\n").splitlines())

    shown, first, _ = runner.trim_output(EXPLAIN, "interval(s)", "availability")
    assert shown[0].strip() == "2 interval(s)" and shown[-1].lstrip().startswith("availability")
    assert first == 9


def test_a_piece_that_is_not_in_the_output_fails_the_take_rather_than_showing_something_else(runner):
    with pytest.raises(SystemExit) as refused:
        runner.trim_output(EXPLAIN, None, "disconnected")
    assert "no line containing 'disconnected'" in str(refused.value)


def test_a_card_that_shows_part_of_the_output_says_so_on_the_card(runner):
    shown, first, total = runner.trim_output(EXPLAIN, None, "interval(s)")
    card = runner.terminal_html("Every second", ["oee", "explain", "FILL01", "15m"], shown, first, total)
    whole = runner.terminal_html("Every second", ["oee", "explain", "FILL01", "15m"],
                                 EXPLAIN.strip("\n").splitlines(), 1, total)

    assert "$ fsmes oee explain FILL01 15m" in card
    assert f"lines 1 to {len(shown)} of the {total} it printed" in card
    assert "it printed" not in whole, "a card showing everything does not say it was cut"
    assert "id='fs-slide-body'" in card, "the corner check reads the card's writing from this box"


class PageOnPaper:
    """Enough of a Playwright page for a card: where it went, and an empty corner."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 1280, "height": 720}
        self.visited: list[str] = []

    def goto(self, url: str, wait_until: str = "load") -> None:
        self.visited.append(url)

    def evaluate(self, script: str, args=None):
        return []

    def wait_for_timeout(self, ms: int) -> None:
        pass


def test_a_terminal_card_runs_its_command_as_filmed_and_keeps_the_whole_output(runner, tmp_path):
    ran: list[list[str]] = []
    stage = runner.BrowserStage(PageOnPaper(), tmp_path, 0.0, base="http://127.0.0.1:8010",
                                run_fsmes=lambda words: ran.append(words) or EXPLAIN)

    stage.terminal("oee explain", "Every second", ["oee", "explain", "FILL01", "15m"], None, "interval(s)", 2.0)

    assert ran == [["oee", "explain", "FILL01", "15m"]]
    mark = stage.marks[-1]
    assert mark["kind"] == "slide" and mark["command"] == "fsmes oee explain FILL01 15m"
    assert mark["lines_shown"] == [1, 9] and mark["lines_of"] == len(EXPLAIN.strip("\n").splitlines())
    kept = (tmp_path / mark["output"]).read_text(encoding="utf-8")
    assert kept.startswith("$ fsmes oee explain FILL01 15m\n") and "availability" in kept
    assert "before_first_sample" in stage.slides["1"]


def test_a_terminal_card_on_a_plant_somebody_else_started_is_refused(runner, tmp_path):
    stage = runner.BrowserStage(PageOnPaper(), tmp_path, 0.0)

    with pytest.raises(SystemExit) as refused:
        stage.terminal("t", "T", ["oee", "explain", "FILL01"], None, None, 1.0)
    assert "the take's own plant" in str(refused.value)


def test_the_scene_hands_a_command_card_to_the_terminal_and_any_other_to_the_slide(runner):
    scene = runner.load_scene(SCENE)
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args: handed.append(verb)

    runner.play(scene, Stage())

    slides = [step for step in scene["step"] if step["do"] == "slide"]
    assert handed.count("terminal") == sum(1 for step in slides if step.get("command"))
    assert handed.count("slide") == sum(1 for step in slides if not step.get("command"))


# -------------------------------------------------------------- teaser mp4


def test_the_teaser_mp4_is_cut_where_the_teaser_gif_is(runner, monkeypatch, tmp_path):
    commands: list[list[str]] = []
    monkeypatch.setattr(runner, "run", lambda command: commands.append(command))
    raw = tmp_path / "raw.webm"

    runner.make_teaser_mp4(raw, tmp_path / "x.teaser.mp4", 2.0, 27.5)
    gif_cut = ["-ss", "2.0", "-t", "27.50", "-i", str(raw)]
    command = commands[-1]

    assert command[command.index("-ss"):command.index("-i") + 2] == gif_cut
    assert command[-1].endswith(".teaser.mp4")
