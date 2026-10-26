"""The work-orders clip: an order released, the line counting, and a person finishing the step.

Topic 4 of the GIF series is decision 0029 on camera: a machine counting to the
ordered quantity does not finish the step it counted against, a person does.
The scene file is `scripts/scenes/04-work-orders-and-routings.toml`, and these
tests read it the way the runner does — through a stage that holds a caption
and a clock and no browser — so what is checked is the story and the words, on
any machine, without a plant.

Two things the runner gained for this scene are tested here as well, because
they are what keep the take honest:

  * `[plant] env` sets the simulated floor's own switches and nothing else. On
    the bottling plant the simulated shift supervisor finishes an order the
    moment the line has made its number; left alone, he would finish the step
    the clip is waiting for a person to finish, sometimes on camera and
    sometimes a second before the click. `MES_OPS_FINISH_ORDERS=false` is the
    switch `fsmes lab` already uses for a scripted over-run, and the scene
    says so in its folder.
  * `[[plant.roll_when_order]]` gains `booked`: it holds the camera, off
    camera, until the order has booked most of its quantity, so the count
    arrives at the number while the camera is on it.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "04-work-orders-and-routings.toml"
FRAMES_PER_SECOND = 10


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_04", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageOnPaper:
    """A stage with no browser: what was said, clicked and waited for, and a strip of frames."""

    def __init__(self, runner) -> None:
        self.runner = runner
        self.frames: list[str] = []
        self.said: list[str] = []
        self.caption = ""
        self.marks: list[str] = []
        self.clicked: list[str] = []
        self.waited: list[dict] = []
        self.visited: list[str] = []
        # Everything that happened, in order, so the tests can ask what came first.
        self.happened: list[tuple[str, str]] = []

    def _run_for(self, seconds: float) -> None:
        self.frames.extend([self.caption] * round(seconds * FRAMES_PER_SECOND))

    def goto(self, url: str, settle: float) -> None:
        self.visited.append(url)
        self.happened.append(("goto", url))
        self._run_for(settle)

    def click(self, selector: str) -> None:
        self.clicked.append(selector)
        self.happened.append(("click", selector))
        self._run_for(self.runner.SMALL_STEP_SECONDS)

    def wait_for(self, step: dict, timeout: float) -> None:
        self.waited.append(step)
        self.happened.append(("wait_for", step.get("js") or step.get("text") or ""))

    def say(self, line: str, hold: float) -> None:
        self.caption = line
        self.said.append(line)
        self.happened.append(("say", line))
        self._run_for(hold)

    def hold(self, seconds: float) -> None:
        self._run_for(seconds)

    def mark(self, name: str) -> None:
        self.marks.append(name)
        self.happened.append(("mark", name))
        self._run_for(self.runner.SMALL_STEP_SECONDS)

    def scroll_to(self, selector: str, block: str, offset: int, text: str | None = None) -> None:
        self._run_for(self.runner.SCROLL_SECONDS)


@pytest.fixture
def played(runner):
    scene = runner.load_scene(SCENE)
    stage = StageOnPaper(runner)
    runner.play(scene, stage)
    return scene, stage


def the_pack_order_book(runner, scene) -> dict[str, dict]:
    book = REPO / scene["plant"]["pack"] / "masterdata" / "work_orders.json"
    return {order["code"]: order for order in json.loads(book.read_text(encoding="utf-8"))}


# ------------------------------------------------------------------ the story


def test_the_clip_releases_a_planned_order_and_then_a_person_finishes_a_step(runner, played):
    """In this order: a planned order and its route, Release, the count at the number, a person.

    The release comes before the finish because the finish is what moves the
    loader onto the order that was released — the two clicks are one story.
    """
    _, stage = played
    clicks = [what for verb, what in stage.happened if verb == "click"]
    release = next(i for i, c in enumerate(clicks) if "Release" in c)
    finish = next(i for i, c in enumerate(clicks) if "Finish step" in c)

    assert release < finish, "released first, finished after"
    assert stage.marks == [
        "the-route-before-release",
        "the-count-at-the-ordered-quantity",
        "the-step-finished-by-a-person",
    ], "the three stills the handoff asks for, in the order the story has them"
    assert len(stage.visited) == 1, "one screen, the orders screen, the whole way through"
    assert stage.visited[0].startswith("/dashboard/orders")


def test_the_count_reaches_the_quantity_on_camera_and_nothing_finishes_it(runner, played):
    """The event is a number arriving and nothing happening — then a person.

    The wait for the count is the one wait the clip pays for, so it has less
    patience than the usual, not more: past it the take stops rather than runs
    long. The caption that says nothing finished it comes after that wait and
    before the click, which is the only order in which it is true.
    """
    _, stage = played
    happened = stage.happened
    the_count = next(i for i, (verb, what) in enumerate(happened)
                     if verb == "wait_for" and "#d-good" in what)
    the_click = next(i for i, (verb, what) in enumerate(happened)
                     if verb == "click" and "Finish step" in what)
    said_between = " ".join(what for verb, what in happened[the_count:the_click] if verb == "say").lower()

    assert "still running" in said_between, "the step is still open at the number, and the overlay says so"
    assert "a person does" in said_between, "and says who finishes it"
    filmed = next(step for step in stage.waited if "#d-good" in (step.get("js") or ""))
    assert filmed["timeout"] < runner.WAIT_TIMEOUT_SECONDS


def test_no_line_says_the_mes_decides_what_runs_next(played):
    """Scheduling here is advisory, and the clip must not imply otherwise.

    What keeps WO-ACME-4712 waiting on camera is that nothing has been counted
    against it — the loader is still booking to the order released before it —
    and not that the MES has sequenced the line. The overlay says the first and
    never the second.
    """
    _, stage = played
    script = " ".join(stage.said).lower()

    for claim in ("schedul", "sequence", "the mes decides", "the mes starts", "enforce",
                  "not allowed", "must wait", "blocks", "automatically"):
        assert claim not in script, f"the overlay implies the MES runs the line: {claim!r}"


def test_the_orders_the_scene_names_are_the_ones_the_pack_puts_in_the_book(runner, played):
    """WO-ACME-4711 starts released and WO-ACME-4712 starts planned — the pack's facts, not the scene's.

    And the quantity the overlay names is the one the pack ordered: a caption
    that said 4,000 over an order for some other number would be the recording
    making up production.
    """
    scene, stage = played
    book = the_pack_order_book(runner, scene)
    script = " ".join(stage.said)

    assert book["WO-ACME-4711"]["release"] is True
    assert book["WO-ACME-4712"]["release"] is False
    for number in re.findall(r"\d{1,3}(?:,\d{3})+", script):
        assert float(number.replace(",", "")) == book["WO-ACME-4711"]["quantity"], number


def test_every_caption_stays_up_long_enough_to_read(played):
    _, stage = played

    for line in stage.said:
        seconds = stage.frames.count(line) / FRAMES_PER_SECOND
        assert seconds >= len(line.split()) / 3, f"too quick to read: {line!r}"


# ------------------------------------------------------------------ the plant


def test_the_camera_rolls_short_of_the_quantity_so_the_count_arrives_on_camera(runner, played):
    scene, _ = played
    plant = scene["plant"]
    book = the_pack_order_book(runner, scene)
    [wanted] = plant["roll_when_order"]
    order = book[wanted["code"]]

    assert runner.on_loopback(scene["base"]) and scene["allow_writes"] is True
    assert not (wanted.get("completed") or wanted.get("consumed")), "an open order, still counting"
    assert 0 < wanted["booked"] < order["quantity"], (
        "short of the number, or the count is at it before the camera arrives"
    )
    assert plant["roll_when_chart"] is None, "an order story rolls on the order"


def test_the_simulated_supervisor_leaves_finishing_the_order_to_the_person_on_camera(runner, played):
    """The bottling floor's shift supervisor finishes an order at its number; in this take he is told not to.

    Left alone he finishes every running step within twenty seconds of the line
    making the number — the very step the clip waits for a person to finish.
    The switch is the floor's own, the one `fsmes lab` hands an over-run
    experiment, and it is named on the folder's page.
    """
    scene, _ = played

    assert scene["plant"]["env"] == {"MES_OPS_FINISH_ORDERS": "false"}
    assert "MES_OPS_FINISH_ORDERS" in (REPO / "src" / "fsmes" / "cli.py").read_text(encoding="utf-8"), (
        "the switch is one the product's simulated floor reads, not one invented for the camera"
    )


@pytest.mark.parametrize(
    ("order", "ready"),
    [
        ({"status": "running", "good_qty": 3400, "quantity": 4000}, True),
        ({"status": "running", "good_qty": 3399, "quantity": 4000}, False),
        ({"status": "released", "good_qty": 0, "quantity": 4000}, False),
        ({"status": "completed", "good_qty": 4000, "quantity": 4000}, False),
        (None, False),
    ],
    ids=[
        "running-and-past-the-count-rolls",
        "one-short-waits",
        "released-and-nothing-booked-waits",
        "finished-already-is-not-the-story-and-does-not-roll",
        "an-order-the-plant-did-not-answer-for-does-not-roll",
    ],
)
def test_the_camera_rolls_on_an_open_order_that_has_booked_the_count(runner, order, ready):
    """`booked` asks only the order — no genealogy — and only of an order still open."""
    assert runner.order_is_ready(order, None, {"code": "WO-1", "booked": 3400}) is ready


@pytest.mark.parametrize(
    ("plant", "complaint"),
    [
        ('env = { MES_DATABASE_URL = "sqlite://" }', "only the simulated floor's switches"),
        ('env = { MES_OPS_FINISH_ORDERS = false }', "values are strings"),
        ('env = "MES_OPS_FINISH_ORDERS=false"', "is a table"),
        ('roll_when_chart = "FG-BOTTLE/fill_weight"\n[[plant.roll_when_order]]\ncode = "WO-1"\nbooked = 1',
         "not on more than one"),
    ],
)
def test_a_scene_may_change_the_simulated_floor_and_nothing_of_the_mes(runner, tmp_path, plant, complaint):
    """A take of a product configured differently from the one anybody installs is not a take of the product."""
    scene = tmp_path / "scene.toml"
    scene.write_text(
        f'name = "x"\n\n[plant]\npack = "labs/multiplant/bottling"\n{plant}\n\n[[step]]\ndo = "hold"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as refused:
        runner.load_scene(scene)

    assert complaint in str(refused.value)


def test_the_folder_says_what_this_take_wrote_and_what_the_floor_was_told(runner, played):
    """The page beside the GIF names the two clicks, the floor's switch and where the camera rolled.

    It used to say, for every scene that writes, that the operator took a hold
    under review. This scene takes no hold; its page says what it did.
    """
    scene, _ = played
    page = runner.readme(scene, {
        "said": ["the first sentence"],
        "plant": "bottling",
        "version": "0.4.1",
        "badge": "pre-alpha · simulated plant · v0.4.1",
        "base": "http://127.0.0.1:8010",
        "commit": "2e5077fd",
        "scene_file": "scripts/scenes/04-work-orders-and-routings.toml",
        "command": "python scripts/record-scene.py scripts/scenes/04-work-orders-and-routings.toml",
        "when": "2026-10-25 13:00 CDT",
        "gif_seconds": 30.0,
        "gif_bytes": 6_000_000,
        "gif_setting": "10 fps, 1100 wide",
    })

    assert "hold under review" not in page, "that is the SPC scene's write, not this one's"
    assert "released" in page and "finished" in page and "loopback" in page
    assert "MES_OPS_FINISH_ORDERS=false" in page
    assert "WO-ACME-4711" in page, "and which order the camera waited on"
    assert "caption bar" in page and "badge" in page
