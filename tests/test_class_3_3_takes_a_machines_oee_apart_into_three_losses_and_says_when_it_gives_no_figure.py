"""Class 3.3 takes a machine's OEE apart into three losses and says when the MES gives no figure.

*OEE and its three losses* is the second class of the machines tier: a card of
words (was it a good run?), the words of the class (OEE, availability,
performance, quality), and the fork mould's OEE the way the product shows it -
its OEE tab over fifteen watched minutes, each of the three bars, the rated
cycle performance is measured against, the replay bar that says rates are on
the line's clock, and the three multiplied; then the fork line's losses on
Engineering > Analysis, the widest one on the mould, and the unlabelled stops
behind it. Then what it refuses: the plant's list states its total, an hour it
did not watch is not downtime, and the figure a plant with a coverage floor
withholds. This file holds the scene file to what the handoff asks of it. No
plant and no browser: the scene is loaded and walked the way the recorder
walks it.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "04-03-oee-and-its-three-losses.toml"
PACK = REPO / "labs" / "cutlery"
MACHINE = "FORK1_Mold"
# The one line over the screen that is about nothing drawn there: why no
# figure beats a guessed one.
POINTS_AT_NOTHING_ON_SCREEN = {
    "No figure is better than a guessed one. A guess looks like a measurement, and someone will act on it."
}

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_class_3_3", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def the_moulds_rated_cycle() -> float:
    """The rating the pack's tag map gives the mould, in seconds a piece."""
    tag_map = json.loads((PACK / "tag_map.json").read_text(encoding="utf-8"))
    (mould,) = [m for m in tag_map["machines"] if m["equipment"] == MACHINE]
    return mould["cycle_seconds"]


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
    return written


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def clicked(scene: dict) -> list[str]:
    return [step["selector"] for step in scene["step"] if step["do"] == "click"]


def test_the_scene_is_a_full_take_of_class_3_3_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "3.3" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_oee_folder_of_the_machines_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "04-the-machines-talk/02-oee-and-its-three-losses"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["2.2", "3.1"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "was-it-a-good-run"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_oee_and_its_three_parts_each_with_its_question(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    lines = cards[0]["lines"]
    assert [line.split(":")[0] for line in lines] == ["OEE", "Availability", "Performance", "Quality"]
    assert all(line.endswith("?") for line in lines[1:])


def test_each_word_of_the_class_is_explained_the_first_time_it_is_said(scene):
    lines = said(scene)

    for word, starts in (
        ("OEE", "OEE is short for overall equipment effectiveness"),
        ("Availability", "Availability is "),
        ("Performance", "Performance is "),
        ("Quality", "Quality is "),
    ):
        first = next(line for line in lines if word.lower() in line.lower())
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_the_idea_cards_sum_is_right(scene):
    (multiplied,) = [line for line in said(scene, "idea") if line.startswith("Multiply")]

    assert "Ninety percent, three times over, is about seventy-three" in multiplied
    assert round(0.9**3, 2) == 0.73


def test_the_only_page_opened_by_address_is_the_floor_and_everything_else_is_clicked(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    screen = steps_of(scene, "screen")
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is screen[0]
    # The machine page from the mould's tile on the Floor, Analysis by the
    # Engineering menu, the mould's page again from its row on Analysis.
    assert path[0] == f'main .machine a.machine-code[href="/dashboard/machine/{MACHINE}"]'
    assert path.count('button.tab[data-tab="oee"]') == 2
    assert path.index('header nav a:text-is("Engineering")') + 1 == path.index('nav.subnav a:text-is("Analysis")')
    assert f'#oee .oee-row .who a:text-is("{MACHINE}")' in path


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] not in ("screen", "refuses")]
    unpointed = [s["line"] for s in on_screen if not s.get("at") and s["line"] not in POINTS_AT_NOTHING_ON_SCREEN]

    assert unpointed == [], f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing: {unpointed}"
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words, and only where there is room round the box: not
    # on a table row, a bar or inner text, which refused three runs of 3.1.
    labelled = [s for s in on_screen if s.get("label")]
    assert all(len(s["label"].split()) <= 3 for s in labelled)
    assert not [s["at"] for s in labelled if any(word in s["at"] for word in ("tr", ".bar", ":text"))]


def test_the_beats_are_filmed_in_the_order_the_handoff_asks(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark" and step["part"] in ("screen", "refuses")]

    assert marks == [
        "a-machines-oee-tab",
        "availability",
        "performance",
        "the-three-multiplied",
        "the-losses-on-analysis",
        "the-stops-behind-the-red",
        "every-machine-worst-first",
        "an-hour-it-did-not-watch",
        "the-figure-it-withholds",
        "no-figure-is-better-than-a-guess",
    ]


def test_the_numbers_are_true_because_the_plant_runs_on_a_clock_the_mes_is_told(scene):
    plant = scene["plant"]
    lines = " ".join(said(scene))

    # `fsmes fleet start --speed` puts MES_SIM_SPEED in the API's environment;
    # the speed on screen and in the notes is the speed the plant is started at.
    assert plant["speed"] == 10
    assert "ten times faster than the clock, and the MES is told so" in lines
    assert any("MES_SIM_SPEED=10" in note for note in scene["notes"])
    # The camera rolls on a window the MES watched in full.
    assert plant["roll_when_watched"] == {"equipment": MACHINE, "hours": 0.25, "at_least": 0.99}


def test_the_rated_cycle_the_film_waits_for_is_the_one_the_pack_gives_the_mould(scene):
    rated = the_moulds_rated_cycle()

    assert any(f"=== '{rated:g} s'" in step.get("js", "") for step in scene["step"])
    assert any(f"{rated:g} s" in note for note in scene["notes"])


def test_the_widest_loss_it_names_is_the_one_the_drawn_bar_shows(scene):
    (claim,) = [i for i, s in enumerate(scene["step"]) if s["do"] == "say" and "the red is the widest" in s["line"]]
    waits = [s for s in scene["step"][:claim] if s["do"] == "wait_for" and "b-avail" in s.get("js", "")]

    assert len(waits) == 1
    assert "w('b-avail') > w('b-perf') && w('b-avail') > w('b-qual')" in waits[0]["js"]
    # And none of the three losses is nothing.
    assert "w('b-perf') > 0 && w('b-qual') > 0" in waits[0]["js"]


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "On this simulated plant nobody gave one, so every stop is unlabelled" in lines
    assert "This plant sets no coverage floor" in lines
    assert "A plant that sets a floor shows no figure at all" in lines
    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "said, not filmed" in honesty


def test_what_it_refuses_is_a_guess_an_unwatched_hour_is_not_downtime_and_the_list_states_its_total(scene):
    refuses = steps_of(scene, "refuses")
    lines = " ".join(said(scene, "refuses"))

    assert "not count the minutes before it was watching as downtime" in lines
    assert "No figure is better than a guessed one" in lines
    assert "no rated cycle" in lines and "nothing counted" in lines
    assert any(step.get("text") == "83 machines across every line, worst OEE first" for step in refuses)
    assert any("not downtime" in step.get("js", "") for step in refuses)


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
    visited |= {"/dashboard/analysis", f"/dashboard/machine/{MACHINE}"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_writes_nothing(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert not scene.get("allow_writes")
    assert not [step for step in scene["step"] if step["do"] == "fill"]


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
