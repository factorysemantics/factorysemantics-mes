"""Class 0.2 places the MES between the office and the machines, on a scratch cutlery plant.

*What an MES is, and what it is not* is the second class of the course: the
whiteboard the MES replaces, the three layers and the three things it is not,
the four tiles, one order followed from the office to a machine and back up,
and the word Watched. This file holds the scene file to what the outline asks
of it. No plant and no browser: the scene is loaded and walked the way the
recorder walks it.
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
SCENE = REPO / "scripts" / "scenes" / "course" / "01-02-what-an-mes-is-and-is-not.toml"
# The caption lines of the take studio voiced, as that take's script file
# wrote them: the second filming says exactly these, in this order.
VOICED = REPO / "tests" / "fixtures" / "course" / "class-0-2-voiced-script.txt"
# The lines over the screen that are about nothing one box can hold: the page
# as a whole, a release nothing on the order names, a confirmation the outbox's
# newest rows do not hold, and every number at once.
POINTS_AT_NOTHING_ON_SCREEN = {
    "This is what replaces it. The same plant, right now, counted as it happens.",
    "The top of the floor screen. Four tiles, each one a question the plant asks every day.",
    "On this plant there is no real office system connected, so the plant's own order book released it.",
    "Then the news goes back up. When an order finishes, the MES writes a confirmation for the office:"
    " how many good, how many scrap.",
    "That is the difference from the whiteboard. Not just the number, but a line saying where it came from.",
    "A figure that says how little was seen, or a tile that says no figure, is the MES being honest, not broken.",
}


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

# The lab fleet, the public demo and the stock demo ports: a class is never
# filmed on a plant somebody else is looking at.
NOT_OURS = {8090, 8110, 8120, 8140, 9030, 5433, 5434, 4944, 4945}

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_class_0_2", RUNNER)
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
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
    return written


def test_the_scene_is_a_full_take_of_class_0_2_named_by_its_label_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "0.2" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    # Half a minute over the first filming's six, for the clicked path, and no more.
    assert 240 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_second_folder_of_the_course(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "01-before-the-software/02-what-an-mes-is-and-is-not"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_the_whiteboard_card_then_the_floor_and_is_short_enough_for_the_teaser(runner, scene):
    hook = [step for step in scene["step"] if step["part"] == "hook"]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "the-whiteboard"
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_put_the_office_above_the_machines_below_and_say_the_three_nots(scene):
    cards = {step["name"]: step for step in scene["step"] if step["do"] == "slide" and step["part"] == "idea"}

    layers = [line.split(":")[0] for line in cards["three-layers"]["lines"]]
    nots = [line.split(":")[0] for line in cards["what-it-is-not"]["lines"]]
    assert layers == ["The office system (ERP)", "The MES", "The machines (PLC / SCADA)"]
    assert nots == ["Not the ERP", "Not the historian", "Not a spreadsheet"]


def test_every_caption_is_the_line_studio_voiced_word_for_word_in_the_same_order(runner, scene):
    voiced = VOICED.read_text(encoding="utf-8").splitlines()

    assert len(voiced) == 32
    assert runner.script_lines(scene) == voiced


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]
    unpointed = [s["line"] for s in on_screen if not s.get("at")]

    assert set(unpointed) == POINTS_AT_NOTHING_ON_SCREEN, (
        f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing")
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words, and the dimmed spotlight is kept for the one refusal.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    spotlit = [s for s in on_screen if s.get("style") == "spotlight"]
    assert len(spotlit) == 1 and spotlit[0]["part"] == "refuses"


def test_the_floor_is_the_only_page_opened_by_address_after_the_whiteboard_and_after_the_cards(scene):
    steps = scene["step"]
    pages = [number for number, step in enumerate(steps) if step["do"] == "goto"]

    # A card is the recorder's page and has no menu: the camera comes back from
    # the cards by address, and every other page is reached by a click.
    assert [steps[n]["url"] for n in pages] == ["/dashboard", "/dashboard"]
    assert [s["name"] for s in steps[:pages[0]] if s["do"] == "slide"] == ["the-whiteboard"]
    assert steps[pages[1]]["part"] == "screen"
    assert steps[pages[1] - 1]["part"] == "idea", "the goto comes straight after the cards"


def test_one_order_is_followed_from_the_office_to_its_machine_and_back_up_to_the_audit_trail_by_clicks(scene):
    screen = [step for step in scene["step"] if step["part"] in ("screen", "refuses")]
    pressed = [(s["do"], s.get("selector"), s.get("text") or s.get("value"))
               for s in screen if s["do"] in ("click", "fill", "choose")]

    assert pressed == [
        ("click", 'header nav a:text-is("Orders")', None),
        ("fill", "#f-text", "WO-STACK07-0001"),
        ("click", '#order-list tbody tr:has-text("WO-STACK07-0001")', None),
        ("click", '#ops a:text-is("STACK07_Stacker")', None),
        ("click", 'header nav a:text-is("Setup")', None),
        ("click", '.subnav a:text-is("Ops")', None),
        ("fill", "#action-filter", "workorder.completed"),
        ("click", 'header nav a:text-is("Floor")', None),
        ("choose", "#m-line", "PAL1LINE"),
    ]
    assert scene["plant"]["roll_when_order"] == [{"code": "WO-STACK07-0001", "completed": True}]


def test_what_it_refuses_is_to_hide_how_little_it_watched(scene):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    said_over = [step for step in refuses if step["do"] == "say"]
    assert any(step["line"].endswith("watched.") and "coverage" in step["at"] for step in said_over)
    assert "not counted as running, and it is not counted as stopped" in said
    assert "no figure" in said


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in scene["step"] if step["part"] == "know" and step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_never_on_one_somebody_else_runs(scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in NOT_OURS
    assert not scene["allow_writes"]


def test_the_missing_office_system_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "no office system is connected" in honesty
    assert "ten times" in honesty


def test_no_screen_of_people_is_filmed(scene):
    urls = [step["url"] for step in scene["step"] if step["do"] == "goto"]

    assert not [url for url in urls if "people" in url or "/admin" in url]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 4 and "terminal" not in handed
