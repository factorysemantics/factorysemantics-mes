"""Class 6.1 asks the AI tab a plain question, checks it on the screen, and reads who asked.

*The AI tab - ask a plain question, and the record of who asked* is the first
class of the last tier: a card of the words (the AI tab, the agent, the key,
the trace); then the AI tab as a supervisor sees it - the Explore line that
says a model is answering, the panel's read-only promise, one plain question
about the fork line's last fifteen minutes, its answer boxed and never
quoted, and the same figure on the fork mould's own OEE tab; then what it
refuses - asked to stop the mould, it only reads - and the Conversations tab,
where every turn is kept with who asked it. This file holds the scene file to
what the handoff asks of it. No plant and no browser: the scene is loaded and
walked the way the recorder walks it.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "07-01-the-chat-on-the-ai-tab.toml"
MACHINE = "FORK1_Mold"
# The one line over the screen that is about nothing drawn there: the agent
# is still looking things up.
POINTS_AT_NOTHING_ON_SCREEN = {
    "The agent looks things up in the plant's records on its own. That takes a few seconds.",
    "It looks again. The reply is filmed as it came.",
}

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)

# What the take may never show or say: the key itself, or where it is kept.
A_KEY = re.compile(r"sk-ant-|ANTHROPIC_API_KEY=|\.config/fsmes")


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_class_6_1", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def everything_written(scene: dict) -> list[str]:
    """Every sentence a viewer or a reader of the folder will see."""
    written = [scene["title"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
            if step.get("label"):
                written.append(step["label"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def first_index(scene: dict, test) -> int:
    return next(i for i, step in enumerate(scene["step"]) if test(step))


def test_the_scene_is_a_full_take_of_class_6_1_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "6.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and the agent's two answers add to the plan; the finished take is measured.
    assert 240 <= planned <= scene["budget_seconds"] <= 420, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_first_folder_of_the_asking_the_plant_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "07-asking-the-plant/01-the-chat-on-the-ai-tab"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["3.3"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide"
    assert [step["name"] for step in hook if step["do"] == "mark"] == ["which-machine-stood-still"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 45


def test_the_idea_card_names_the_ai_tab_the_agent_the_key_and_the_trace(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == ["The AI tab", "The agent", "The key", "The trace"]


def test_each_word_of_the_class_is_explained_the_first_time_it_is_said(scene):
    lines = said(scene, "idea")

    for starts in (
        "The AI tab is one place in the MES",
        "The analysis agent answers. It is a language model, a program",
        "The model runs at an AI company. The plant's owner buys a key",
        "The trace is the record of every conversation",
    ):
        assert any(line.startswith(starts) for line in lines), starts


def test_the_tab_is_shown_answering_before_anything_is_asked(scene):
    answering = first_index(scene, lambda s: s["do"] == "wait_for" and s.get("text") == "is answering")
    asked = first_index(scene, lambda s: s["do"] == "click" and s["selector"] == "#explore-send")

    assert scene["step"][answering]["selector"] == "#explore-state"
    assert answering < asked
    assert any("says the agent is off, and why" in line for line in said(scene, "screen"))


def test_the_take_waits_for_each_whole_answer_before_it_boxes_one(scene):
    sends = [i for i, s in enumerate(scene["step"]) if s["do"] == "click" and s["selector"] == "#explore-send"]
    assert len(sends) == 2

    for send in sends:
        after = scene["step"][send + 1 :]
        wait = next(s for s in after if s["do"] == "wait_for")
        boxed = next(s for s in after if s["do"] == "say" and s.get("at"))
        # The suggestions under an answer are drawn last: waiting on them is
        # waiting on the answer, not on the box that will hold it.
        assert "explore-next" in wait["js"] and float(wait["timeout"]) >= 60
        assert ".explore-msg.bot" in boxed["at"]


def test_no_caption_quotes_the_agent_and_the_one_that_names_the_machine_is_checked_first(scene):
    names = first_index(scene, lambda s: s["do"] == "say" and s["line"].startswith("It names the fork mould"))
    guard = scene["step"][names - 1]

    assert guard["do"] == "wait_for" and float(guard["timeout"]) <= 5
    assert MACHINE in guard["js"] and ".explore-msg.bot" in guard["js"]
    assert not [line for line in said(scene) if "“" in line or '"' in line]


def test_the_answer_is_checked_on_the_mould_s_own_oee_tab_over_the_same_fifteen_minutes(scene):
    asked = next(s["text"] for s in scene["step"] if s["do"] == "fill" and s["selector"] == "#explore-input")
    window = [s for s in scene["step"] if s["do"] == "choose" and s["selector"] == 'select[data-span="kind"]']

    assert "last fifteen minutes" in asked and "FORK1LINE" in asked
    assert [s["value"] for s in window] == ["h:0.25"]
    assert any(
        s.get("at") == "#oee-facts" and "Trust the screen" in s["line"] for s in scene["step"] if s["do"] == "say"
    )


def test_it_asks_the_agent_to_stop_a_machine_and_films_the_refusal_as_it_comes(scene):
    refuses = steps_of(scene, "refuses")
    asked = [s["text"] for s in refuses if s["do"] == "fill"]
    lines = " ".join(said(scene, "refuses"))

    assert asked == ["Stop the fork mould for ten minutes, please."]
    assert "It only reads" in lines and "Nothing on the plant changed" in lines


def test_the_record_names_who_asked_and_holds_every_turn(scene):
    tab = 'button.tab[data-tab="conversations"]'
    record = first_index(scene, lambda s: s["do"] == "click" and s["selector"] == tab)
    after = scene["step"][record:]

    assert any(s["do"] == "wait_for" and "WALK-SUP" in s["js"] for s in after)
    assert any(s["do"] == "click" and "Read it" in s["selector"] for s in after)
    assert any(s["do"] == "wait_for" and "#turns .turn" in s["js"] and "Stop the fork mould" in s["js"] for s in after)
    assert scene["user"] == "WALK-SUP"


def test_the_beats_are_filmed_in_the_order_the_product_allows(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark"]

    assert marks == [
        "which-machine-stood-still",
        "the-words",
        "the-ai-tab",
        "a-plain-question",
        "the-answer",
        "the-same-figure-on-the-screen",
        "it-changes-nothing",
        "the-record-of-who-asked",
        "who-may-read-it",
        "you-now-know",
    ]


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("hook", "screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("idea", "know")]
    over_the_hook_card = set(said(scene, "hook"))
    unpointed = [
        s["line"]
        for s in on_screen
        if not s.get("at") and s["line"] not in POINTS_AT_NOTHING_ON_SCREEN | over_the_hook_card
    ]

    assert unpointed == [], f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing: {unpointed}"
    assert [s["line"] for s in on_cards if s.get("at")] == []


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in steps_of(scene, "know") if step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/ai", f"/dashboard/machine/{MACHINE}"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date_or_the_key(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]
    keyed = [line for line in everything_written(scene) if A_KEY.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"
    assert keyed == [], f"{len(keyed)} lines carry the key or where it is kept: {keyed}"


def test_the_honest_limits_are_said_in_the_folders_own_words(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "never quoted" in honesty and "defines no shifts" in honesty


def test_it_is_filmed_on_a_scratch_plant_on_loopback_and_writes_only_its_two_questions(runner, scene):
    base = urlparse(scene["base"])
    fills = [step for step in scene["step"] if step["do"] == "fill"]

    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["plant"]["pack"] == "labs/cutlery"
    assert scene.get("allow_writes") is True
    assert [step["selector"] for step in fills] == ["#explore-input", "#explore-input"]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed
