"""Class 0.1 follows a fork from the mould to the pallet, on a scratch cutlery plant.

*What a factory does all day* is the first class of the course: the five words
a plant runs on, the plant as a tree, one fork's road through four lines, and
the rule that the MES knows nothing a machine or a person has not reported.
This file holds the scene file to what the outline asks of it. No plant and no
browser: the scene is loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "01-01-what-a-factory-does-all-day.toml"

# The lab fleet, the public demo and the stock demo ports: a class is never
# filmed on a plant somebody else is looking at.
NOT_OURS = {8090, 8110, 8120, 8140, 9030, 5433, 5434}


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_class_0_1", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def test_the_scene_is_a_full_take_of_class_0_1_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "0.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 240 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_first_folder_of_the_course(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "01-before-the-software/01-what-a-factory-does-all-day"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_opens_on_the_3d_line_view_then_the_line_view_and_is_short_enough_for_the_teaser(runner, scene):
    hook = [step for step in scene["step"] if step["part"] == "hook"]
    visited = [step["url"] for step in hook if step["do"] == "goto"]

    assert visited == ["/dashboard/line/3d?line=FORK1LINE", "/dashboard/line?line=FORK1LINE"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_the_five_words_and_the_five_levels_of_the_tree(scene):
    cards = {step["name"]: step for step in scene["step"] if step["do"] == "slide" and step["part"] == "idea"}

    words = [line.split(":")[0] for line in cards["five-words"]["lines"]]
    levels = [line.split(":")[0] for line in cards["the-tree"]["lines"]]
    assert words == ["Material", "Machine", "Step", "Order", "Shift"]
    assert levels == ["Enterprise", "Site", "Area", "Line", "Machine"]


def test_the_fork_is_followed_through_the_four_lines_in_the_order_it_travels(scene):
    screen = [step["url"] for step in scene["step"] if step["part"] == "screen" and step["do"] == "goto"]
    lines = []
    for url in screen:
        for line in ("FORK1", "STACK07", "WRAP03", "PAL1"):
            if line in url and line not in lines:
                lines.append(line)

    assert screen[0] == "/dashboard/machines", "the tree comes before the road"
    assert lines == ["FORK1", "STACK07", "WRAP03", "PAL1"]


def test_what_it_refuses_is_the_time_nobody_reported(scene):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert any(step.get("text") == "before first sample" for step in refuses if step["do"] == "wait_for")
    assert "Nothing is known until a machine or a person reports it" in said


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in scene["step"] if step["part"] == "know" and step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_never_on_one_somebody_else_runs(scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in NOT_OURS
    assert scene["plant"]["roll_when_order"] == [{"code": "WO-PAL1-0001", "booked": 1}]


def test_the_grey_boxes_and_the_replay_banner_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "grey box" in honesty
    assert "ten times" in honesty


def test_no_screen_of_people_is_filmed(scene):
    urls = [step["url"] for step in scene["step"] if step["do"] == "goto"]

    assert not [url for url in urls if "people" in url or "/admin" in url]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args: handed.append(verb)

    runner.play(scene, Stage())

    assert handed.count("goto") == sum(1 for step in scene["step"] if step["do"] == "goto")
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed
