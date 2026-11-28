"""Class 6.3 installs the package, starts the demo plant, and films its refusals in its own words.

*Run it on your own PC* is the one class filmed on the released package
rather than this checkout: a card of the words (pip, package, demo plant,
home) and what you need first; then the pip install and the start as they
printed, the version and the status run on camera, and the plant in a
browser, with its Plant OEE read as whatever the tile says; a card that
says two servers on PostgreSQL is the guide's next step and is not filmed;
then what it refuses — a second copy on a port already taken, and a
mistyped command. This file holds the scene file to what the handoff asks
of it. No pip, no plant and no browser: the scene is loaded and walked the
way the recorder walks it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "07-03-running-it-on-your-own-box.toml"

A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)
# The class makes no claim it did not film: no Windows run, no key.
A_CLAIM = re.compile(r"filmed on Windows|on Windows it|sk-ant-|ANTHROPIC_API_KEY=")


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def over_a_card(scene: dict) -> list[bool]:
    """For each step, whether a card the recorder painted is what is on screen."""
    card, shown = False, []
    for step in scene["step"]:
        if step["do"] == "slide":
            card = True
        elif step["do"] == "goto":
            card = False
        shown.append(card)
    return shown


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


def cards(scene: dict, part: str | None = None) -> dict[str, dict]:
    return {s["name"]: s for s in scene["step"] if s["do"] == "slide" and (part is None or s["part"] == part)}


def test_the_scene_is_a_full_take_of_class_6_3_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "6.3" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # The terminal cards' commands and the page loads add to the plan; the take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 360, planned


def test_it_films_into_the_on_your_own_pc_folder_third_in_the_last_module(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "07-asking-the-plant/03-running-it-on-your-own-box"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_then_the_plant_in_a_browser_and_is_short_enough_for_the_teaser(runner, scene):
    hook = [step for step in scene["step"] if step["part"] == "hook"]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "on-your-own-pc"
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_pip_the_package_the_demo_plant_and_its_home_and_say_what_is_needed(scene):
    idea = cards(scene, "idea")

    assert [line.split(":")[0] for line in idea["four-words"]["lines"]] == ["pip", "Package", "Demo plant", "Home"]
    assert idea["what-you-need"]["lines"][0] == "Python 3.12 or newer"


def test_the_python_it_asks_for_is_the_one_the_package_itself_requires(scene):
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.12"' in pyproject
    assert "3.12 or newer" in cards(scene)["what-you-need"]["lines"][0]


def test_it_installs_the_released_package_as_the_guide_types_it_and_starts_the_plant_with_no_speed(scene):
    guide = (REPO / "docs" / "operate" / "demo-from-your-pc.md").read_text(encoding="utf-8")

    assert scene["installed"] == {"package": "factorysemantics-mes[postgres]", "plant": "cutlery", "speed": None}
    assert '"factorysemantics-mes[postgres]"' in guide
    assert "fsmes demo cutlery" in guide


def test_the_screen_shows_the_install_the_version_the_start_and_the_status_in_that_order(scene):
    screen = [s for s in scene["step"] if s["part"] == "screen" and s["do"] == "slide"]

    assert [(s.get("shows"), s.get("command")) for s in screen if s.get("shows") or s.get("command")] == [
        ("install", None),
        (None, ["--version"]),
        ("start", None),
        ("start", None),
        ("start", None),
        (None, ["demo", "cutlery", "status"]),
    ]


def test_the_start_card_ends_on_how_to_stop_it_and_the_class_says_it(scene):
    said = " ".join(s["line"] for s in scene["step"] if s["do"] == "say")

    assert "fsmes demo cutlery stop" in said


def test_plant_oee_is_read_as_whatever_the_tile_says_and_a_dash_is_never_a_zero(scene):
    oee = [s for s in scene["step"] if s["do"] == "say" and s.get("at", "").startswith("#kpi-oee")]

    assert [s["at"] for s in oee] == ["#kpi-oee", "#kpi-oee-sub"]
    assert "a dash, never a zero" in oee[0]["line"]
    assert "not counted as zero" in oee[1]["line"]
    # No figure is said: a plant a minute old reads differently on every take.
    assert not any(re.search(r"\d+ ?%", s["line"]) for s in oee)


def test_the_earlier_screens_are_pointed_at_in_one_caption_and_not_walked_again(scene):
    pages = [s["url"] for s in scene["step"] if s["do"] == "goto"]
    pointing = [s for s in scene["step"] if s["do"] == "say" and "Class 0.1" in s["line"]]

    assert pages == ["/dashboard", "/dashboard"]
    assert not [s for s in scene["step"] if s["do"] == "click"]
    assert len(pointing) == 1 and pointing[0]["at"] == "header nav"


def test_two_servers_on_postgresql_is_a_card_pointing_at_the_guide_and_is_owned_up_to_as_not_filmed(scene):
    card = cards(scene)["two-servers"]
    honesty = " ".join(scene["honesty"]).lower()

    assert "Not filmed: this PC has no PostgreSQL server" in card["lines"]
    assert "a ten-minute demo from your own pc" in card["lines"][-1].lower()
    assert (
        "# a ten-minute demo from your own pc"
        in (REPO / "docs" / "operate" / "demo-from-your-pc.md").read_text(encoding="utf-8").lower()
    )
    assert "two servers on one postgresql" in honesty and "no postgresql" in honesty


def test_it_says_in_its_own_lines_that_it_was_filmed_on_linux_and_makes_no_windows_or_key_claim(scene):
    said = " ".join(s["line"] for s in scene["step"] if s["do"] == "say")

    assert "This was filmed on Linux." in said
    assert "that is not filmed here" in said
    assert [line for line in everything_written(scene) if A_CLAIM.search(line)] == []
    assert "AI tab" not in " ".join(s.get("url", "") for s in scene["step"])


def test_what_it_refuses_is_a_second_copy_on_a_taken_port_and_a_mistyped_command_in_under_twenty_five_seconds(
    runner, scene
):
    refuses = [s for s in scene["step"] if s["part"] == "refuses"]
    refused = [(s["command"], s.get("home", "this")) for s in refuses if s["do"] == "slide"]

    assert refused == [(["demo", "cutlery"], "another"), (["demoo"], "this")]
    assert all(s["refuses"] for s in refuses if s["do"] == "slide")
    assert runner.planned_seconds(dict(scene, step=refuses)) < 25


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [s for s in scene["step"] if s["part"] == "know" and s["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(s["url"]).path for s in scene["step"] if s["do"] == "goto"}

    assert [p for p in visited if not any(pattern.match(p) for pattern in patterns)] == []


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_its_own_plant_on_loopback_and_writes_nothing_on_camera(runner, scene):
    base = urlparse(scene["base"])

    assert base.hostname == "127.0.0.1" and base.port == 8040
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert not scene.get("allow_writes") and "plant" not in scene


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    assert not [s for s in on_screen if s.get("label") and "table" in s["at"]]


def test_the_recorder_plays_every_step_of_it_and_hands_each_terminal_card_what_it_is(runner, scene):
    handed: list[tuple[str, dict]] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append((verb, pointer))

    runner.play(scene, Stage())

    terminals = [pointer for verb, pointer in handed if verb == "terminal"]
    assert len(terminals) == 8
    assert [p.get("shows") for p in terminals].count("install") == 1
    assert [p.get("home") for p in terminals if p.get("refuses")] == ["another", None]
    assert [verb for verb, _ in handed].count("slide") == 5
