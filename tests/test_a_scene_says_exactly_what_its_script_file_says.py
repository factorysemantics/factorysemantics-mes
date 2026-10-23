"""The script beside a recording is what the recording said, frame for frame.

`scripts/record-scene.py` films a plant and writes, beside the GIF, a plain
`<name>.script.txt` — the overlay lines in the order they were said, one per
line and nothing else — because somebody has to read them aloud over the
picture. If that file and the picture ever disagree, the narrator says one
thing while the screen says another, and nobody watching knows which to
believe.

So this runs the real scene file — `scripts/scenes/02-spc-on-the-line.toml`,
the one the published recording was made from — through a stage that is not a
browser: it holds a caption and a clock, and writes down what was on screen in
every tenth of a second, the way the finished GIF does. Then it compares the
captions in that strip of frames against the script file the same run wrote.
No plant, no browser, no ffmpeg: the thing being tested is whether the words
line up, and that does not need a picture.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "02-spc-on-the-line.toml"

#: The published GIF's frame rate, the fastest the ladder in the runner will
#: use. A caption held for four seconds is forty frames of it.
FRAMES_PER_SECOND = 10


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageOnPaper:
    """A stage with no browser: a caption, a clock, and a strip of frames.

    Every step that takes time advances the clock and stamps the caption that
    was on screen into `frames`, one entry per tenth of a second. What comes out
    is the same thing the GIF is: a sequence of pictures, each with whatever the
    overlay was saying at the time.
    """

    def __init__(self, runner) -> None:
        self.clock = runner  # the runner's own idea of how long a small step takes
        self.frames: list[str] = []
        self.said: list[str] = []
        self.caption = ""
        self.marks: list[str] = []
        self.visited: list[str] = []
        self.waited: list[dict] = []

    def _run_for(self, seconds: float) -> None:
        self.frames.extend([self.caption] * round(seconds * FRAMES_PER_SECOND))

    def goto(self, url: str, settle: float) -> None:
        self.visited.append(url)
        self._run_for(settle)

    def click(self, selector: str) -> None:
        self._run_for(self.clock.SMALL_STEP_SECONDS)

    def wait_for(self, step: dict, timeout: float) -> None:
        self.waited.append(step)

    def say(self, line: str, hold: float) -> None:
        self.caption = line
        self.said.append(line)
        self._run_for(hold)

    def hold(self, seconds: float) -> None:
        self._run_for(seconds)

    def mark(self, name: str) -> None:
        self.marks.append(name)
        self._run_for(self.clock.SMALL_STEP_SECONDS)

    def scroll_to(self, selector: str, block: str, offset: int) -> None:
        self._run_for(self.clock.SCROLL_SECONDS)


def captions_in_frame_order(frames: list[str]) -> list[str]:
    """What a person reading the frames one by one would see, with repeats collapsed."""
    seen: list[str] = []
    for caption in frames:
        if caption and (not seen or seen[-1] != caption):
            seen.append(caption)
    return seen


@pytest.fixture
def played(runner):
    scene = runner.load_scene(SCENE)
    stage = StageOnPaper(runner)
    runner.play(scene, stage)
    return scene, stage


def test_the_script_file_holds_the_overlay_lines_in_order_and_nothing_else(runner, played, tmp_path):
    scene, stage = played

    path = runner.write_script_file(tmp_path, scene["name"], stage.said)

    assert path.name == "spc-on-the-line.script.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == stage.said
    assert all(line.strip() for line in lines), "no blank lines: the file is read aloud, not laid out"
    assert not any(line.lstrip().startswith(("#", "-", "*", "1.")) for line in lines), (
        "no numbering, no bullets, no comments - the say lines and nothing else"
    )


def test_every_frame_of_the_take_says_what_the_script_file_says_at_that_point(runner, played, tmp_path):
    scene, stage = played
    path = runner.write_script_file(tmp_path, scene["name"], stage.said)

    assert captions_in_frame_order(stage.frames) == path.read_text(encoding="utf-8").splitlines()


def test_a_caption_stays_up_long_enough_for_somebody_to_read_it(played):
    _, stage = played

    counted = {line: stage.frames.count(line) for line in stage.said}
    for line, frames in counted.items():
        words = len(line.split())
        # Three words a second is a comfortable read on a caption, and a reader
        # has to find the sentence before reading it. Anything tighter is a
        # sentence that is on screen without being read.
        assert frames / FRAMES_PER_SECOND >= words / 3, f"too quick to read: {line!r}"


def test_the_script_file_is_a_record_of_the_take_and_not_a_copy_of_the_scene(runner, tmp_path):
    """What was said is what is written, even when it is not what was planned.

    A take that lost a line has a script file one line short, and the run says
    so and refuses; the file never quietly reads back the plan.
    """
    path = runner.write_script_file(tmp_path, "whatever", ["the first thing", "the second thing"])

    assert path.read_text(encoding="utf-8") == "the first thing\nthe second thing\n"


def test_the_scene_filmed_is_the_spc_story_the_handoff_asked_for(runner, played):
    """The words, in order: the chart, the rule, withheld capability, the hold, what is next."""
    scene, stage = played
    script = " ".join(stage.said).lower()

    assert stage.said == runner.script_lines(scene)
    assert "fill weight" in script
    assert "out of control" in script
    assert "withheld" in script, "capability is withheld on an unstable process, and the overlay says why"
    assert "hold" in script
    assert "review" in script and "decide" in script, "what the operator does next"
    assert stage.marks == ["the-chart", "the-reading", "the-hold"]
    assert len(stage.visited) == 2, "the chart page and the quality page"


def test_a_scene_waits_for_the_state_it_is_about_to_film(runner, played):
    """House rule: never wait for the element that will hold the state.

    A caption written over an element that is still empty is the recording
    telling a lie about the product, and on loopback an empty one looks full.
    """
    _, stage = played

    assert stage.waited, "a scene that waits for nothing films whatever happens to be there"
    for step in stage.waited:
        assert step.get("text") or step.get("attribute") or step.get("js"), step


@pytest.mark.parametrize(
    ("broken", "complaint"),
    [
        ('name = "x"\n[[step]]\ndo = "wave"\n', "not one of"),
        ('name = "x"\n[[step]]\ndo = "wait_for"\nselector = "#chart"\n', "films an empty one"),
        ('name = "x"\n[[step]]\ndo = "say"\nhold = 3\n', "needs a `line`"),
        ('name = "x"\n[[step]]\ndo = "goto"\n', "needs a `url`"),
        ('name = "x"\n', "at least one"),
        ('[[step]]\ndo = "hold"\n', "needs a `name`"),
    ],
)
def test_a_scene_this_script_would_have_to_guess_at_is_refused(runner, tmp_path, broken, complaint):
    scene = tmp_path / "broken.toml"
    scene.write_text(broken, encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        runner.load_scene(scene)

    assert complaint in str(refused.value)
