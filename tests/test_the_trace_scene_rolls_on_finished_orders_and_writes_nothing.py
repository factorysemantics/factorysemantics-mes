"""The traceability clip asks a recall's two questions, of orders the plant really finished.

`scripts/scenes/01-trace-the-lot.toml` films an order's genealogy — what went
in, what came out — and then asks where one of the lots that went in ended up.
Both answers have to be the plant's own: the order must have finished, with a
lot made, and drawn on the lot the clip names, before the camera rolls, or the
clip films an empty box and a caption describing a box that is full.

That wait is `[[plant.roll_when_order]]`, the order-shaped twin of the SPC
scene's `roll_when_chart`. It happens off camera, because a cold plant takes a
minute to finish its first order and the clip has forty-five seconds.

No plant, no browser here: the scene file is played onto paper, and the wait's
judgement is tested on the answers the plant gives.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "01-trace-the-lot.toml"
FRAMES_PER_SECOND = 10


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageOnPaper:
    """A caption, a clock, and the frames they make — the same as the SPC scene's test uses."""

    def __init__(self, runner) -> None:
        self.runner = runner
        self.frames: list[str] = []
        self.said: list[str] = []
        self.caption = ""
        self.marks: list[str] = []
        self.visited: list[str] = []
        self.waited: list[dict] = []
        self.scrolled: list[tuple] = []

    def _run_for(self, seconds: float) -> None:
        self.frames.extend([self.caption] * round(seconds * FRAMES_PER_SECOND))

    def goto(self, url: str, settle: float) -> None:
        self.visited.append(url)
        self._run_for(settle)

    def click(self, selector: str) -> None:
        self._run_for(self.runner.SMALL_STEP_SECONDS)

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
        self._run_for(self.runner.SMALL_STEP_SECONDS)

    def scroll_to(self, selector: str, block: str, offset: int, text: str | None = None) -> None:
        self.scrolled.append((selector, text))
        self._run_for(self.runner.SCROLL_SECONDS)


@pytest.fixture
def played(runner):
    scene = runner.load_scene(SCENE)
    stage = StageOnPaper(runner)
    runner.play(scene, stage)
    return scene, stage


# ------------------------------------------------------------------ the story


def test_the_clip_asks_what_is_in_it_and_then_where_it_went(played):
    _, stage = played
    script = " ".join(stage.said).lower()

    assert "what is in this" in script
    assert "where did it go" in script
    assert script.index("what is in this") < script.index("where did it go"), (
        "the backward question first, on the order; then the forward one, about a lot that went in"
    )
    assert stage.marks == [
        "the-genealogy-box",
        "the-where-used-answer",
        "the-next-order-on-the-same-lot",
    ], "the three stills the handoff asks for: genealogy, where-used, and the closing frame"


def test_the_lot_the_trace_page_is_asked_about_is_one_the_order_drew_on(played):
    """No lot code on camera that the plant did not make or use.

    The trace page is asked about a lot only after the order's own genealogy
    has been seen to list it, and the take rolls only once the plant says the
    order drew on it.
    """
    scene, stage = played
    asked = [url for url in stage.visited if url.startswith("/dashboard/trace?lot=")]
    assert len(asked) == 1
    lot = asked[0].split("=", 1)[1]

    seen_in_genealogy = [step for step in stage.waited
                         if step.get("selector") == "#consumed" and step.get("text") == lot]
    assert seen_in_genealogy, "the clip waits for the order's own list to name the lot before asking about it"
    first_order = scene["plant"]["roll_when_order"][0]
    assert first_order["completed"] is True
    assert first_order["consumed"] == lot


def test_the_empty_where_used_answer_is_shown_and_its_reason_said(played):
    """The bottling pack records lots, not serials, so the trace page finds no units.

    That is shown, not papered over with serials recorded for the camera, and
    the next caption says why in one line.
    """
    _, stage = played
    script = " ".join(stage.said).lower()

    assert "no serialised unit carries it" in script
    assert "by the lot" in script, "and the reason, in the same breath"


def test_the_row_a_caption_is_about_is_scrolled_into_view_by_its_words(played):
    """A finished order's list of issues runs to a hundred rows in a box that scrolls.

    The caption names the cap lot, so the camera is put on the cap lot's row
    rather than on whichever rows happen to be first.
    """
    _, stage = played
    assert stage.scrolled.count(("#consumed li", "LOT-CAP-001")) == 2, (
        "once on the finished order, once on the order still running"
    )


def test_a_caption_on_this_clip_stays_up_long_enough_to_read(played):
    _, stage = played
    for line in stage.said:
        held = stage.frames.count(line) / FRAMES_PER_SECOND
        assert held >= len(line.split()) / 3, f"too quick to read: {line!r}"


def test_nothing_in_this_take_writes_to_the_plant(runner):
    """Reading a genealogy and asking a trace page are reads.

    So this scene does not say `allow_writes`, and a take that sent anything
    but a read would fail rather than be published.
    """
    scene = runner.load_scene(SCENE)
    assert scene["allow_writes"] is False
    assert runner.on_loopback(scene["base"])
    assert scene["plant"]["pack"] == "labs/multiplant/bottling"
    for port in runner.ports_of_pack(runner.REPO / scene["plant"]["pack"]):
        assert port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE


# --------------------------------------------------------- the wait for orders


FINISHED = {"code": "WO-1", "status": "completed"}
RUNNING = {"code": "WO-1", "status": "running"}
DREW_CAPS = {"consumed": [{"lot": "LOT-CAP-001"}, {"lot": "LOT-WATER-001"}], "produced": []}
DREW_CAPS_AND_MADE_A_LOT = {**DREW_CAPS, "produced": [{"lot": "WO-1-FG"}]}


@pytest.mark.parametrize(
    ("order", "genealogy", "wanted", "ready"),
    [
        (FINISHED, DREW_CAPS_AND_MADE_A_LOT, {"completed": True, "consumed": "LOT-CAP-001"}, True),
        (RUNNING, DREW_CAPS_AND_MADE_A_LOT, {"completed": True}, False),
        (FINISHED, DREW_CAPS, {"completed": True}, False),
        (FINISHED, DREW_CAPS_AND_MADE_A_LOT, {"completed": True, "consumed": "LOT-LABEL-001"}, False),
        (RUNNING, DREW_CAPS, {"consumed": "LOT-CAP-001"}, True),
        (None, DREW_CAPS, {"consumed": "LOT-CAP-001"}, False),
        (RUNNING, None, {"consumed": "LOT-CAP-001"}, False),
    ],
    ids=[
        "finished-with-its-lot-and-the-cap-lot-in-it",
        "still-running-is-not-finished",
        "finished-with-nothing-made-is-not-the-state-the-clip-films",
        "the-wrong-lot-is-not-the-lot-named",
        "a-running-order-that-drew-on-the-lot-is-enough-when-that-is-all-that-is-asked",
        "an-order-the-plant-did-not-answer-for-is-not-ready",
        "a-genealogy-the-plant-did-not-answer-is-not-ready",
    ],
)
def test_the_camera_rolls_only_on_an_order_in_the_state_the_scene_films(runner, order, genealogy, wanted, ready):
    assert runner.order_is_ready(order, genealogy, {"code": "WO-1", **wanted}) is ready


@pytest.mark.parametrize(
    ("plant", "complaint"),
    [
        ('[[plant.roll_when_order]]\ncompleted = true\n', "needs the `code`"),
        ('[[plant.roll_when_order]]\ncode = "WO-1"\n', "waits for nothing"),
        ('roll_when_order = "WO-1"\n', "list of tables"),
    ],
)
def test_an_order_wait_this_script_would_have_to_guess_at_is_refused(runner, tmp_path, plant, complaint):
    scene = tmp_path / "broken.toml"
    scene.write_text(
        'name = "x"\n[[step]]\ndo = "hold"\nseconds = 1\n\n[plant]\npack = "labs/multiplant/bottling"\n' + plant,
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as refused:
        runner.load_scene(scene)
    assert complaint in str(refused.value)


def test_the_folder_says_what_the_camera_waited_for(runner):
    scene = runner.load_scene(SCENE)
    page = runner.readme(scene, {
        "said": runner.script_lines(scene), "plant": "bottling", "version": "0.4.1",
        "badge": "pre-alpha · simulated plant · v0.4.1", "base": scene["base"], "commit": "abc1234",
        "scene_file": "scripts/scenes/01-trace-the-lot.toml", "command": "python scripts/record-scene.py …",
        "when": "2026-10-25 12:00 CDT", "gif_seconds": 35.0, "gif_bytes": 4 * 1024 * 1024,
        "gif_setting": "10 fps, 1100 px",
    })

    assert "`WO-ACME-4711` had finished and made its lot and drawn on `LOT-CAP-001`" in page
    assert "`WO-ACME-4712` had drawn on `LOT-CAP-001`" in page
    assert "under review" not in page, "this scene writes nothing, and its folder does not say it did"
    assert "books them and says how far over it ran" in page, "the over-run on screen is explained"
