"""Class 1.3 defines a routing whose steps bring their machines' cost centers, on a scratch cutlery plant.

*Routings — the path a material takes* is the first class of the core: a
fork's road in words, the three words a routing is made of, the plant's
routings table, a new pallet routing typed into the Admin form with each
step's cost center read from its machine's line, and the two things the form
refuses. This file holds the scene file to what the outline asks of it. No
plant and no browser: the scene is loaded and walked the way the recorder
walks it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "02-03-routings.toml"
PACK = REPO / "labs" / "cutlery" / "masterdata"
# The caption lines of the take studio voiced, as that take's script file
# wrote them: the second filming says exactly these, in this order.
VOICED = REPO / "tests" / "fixtures" / "course" / "class-1-3-voiced-script.txt"
# The lines over the screen about nothing one box can hold: three machines
# that fill the Floor's width, and what the class is about to do, over a table
# the form is not yet on.
POINTS_AT_NOTHING_ON_SCREEN = {
    "Here are the three machines of that road, on the floor right now.",
    "Now the things it refuses. First, a routing with no steps at all: a code, a name, and no operation.",
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

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def pack(kind: str) -> list[dict]:
    return json.loads((PACK / f"{kind}.json").read_text(encoding="utf-8"))


def everything_written(scene: dict) -> list[str]:
    """Every sentence a viewer or a reader of the folder will see."""
    written = [scene["title"], scene["what_is_written"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def typed(scene: dict, part: str) -> list[str]:
    return [step["text"] for step in scene["step"] if step["part"] == part and step["do"] == "fill"]


def chosen(scene: dict, part: str) -> list[str]:
    return [step["value"] for step in scene["step"] if step["part"] == part and step["do"] == "choose"]


def test_the_scene_is_a_full_take_of_class_1_3_named_by_its_label_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "1.3" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    # Half a minute over the first filming's six, for the clicked path, and no more.
    assert 240 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_routings_folder_of_the_core(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "02-the-core/03-routings"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_a_forks_road_then_its_line_and_is_short_enough_for_the_teaser(runner, scene):
    hook = [step for step in scene["step"] if step["part"] == "hook"]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "a-forks-road"
    machines = [op["equipment"] for op in next(r for r in pack("routings") if r["code"] == "RT-FORK1")["operations"]]
    assert [line.rsplit(" ", 1)[-1] for line in hook[0]["lines"] if "_" in line] == machines
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard"]
    assert [(step["selector"], step["value"]) for step in hook if step["do"] == "choose"] == [("#m-line", "FORK1LINE")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_operation_sequence_and_routing_and_the_recipe_of_steps(scene):
    cards = {step["name"]: step for step in scene["step"] if step["do"] == "slide" and step["part"] == "idea"}

    assert [line.split(":")[0] for line in cards["three-words"]["lines"]] == ["Operation", "Sequence", "Routing"]
    assert cards["steps-not-ingredients"]["title"] == "The recipe of steps, not of ingredients"


def test_the_routing_typed_in_is_not_in_the_pack_and_every_code_in_it_is_the_plants_own(scene):
    codes = {r["code"] for r in pack("routings")}
    materials = {m["code"] for m in pack("materials")}
    machines = {e["code"]: e for e in pack("equipment")}
    screen_typed, screen_chosen = typed(scene, "screen"), chosen(scene, "screen")

    assert "RT-PALLET" in screen_typed and "RT-PALLET" not in codes
    assert screen_chosen == ["PALLET", "PAL1_Palletizer", "PAL1_StretchWrap"]
    assert screen_chosen[0] in materials
    assert all(code in machines for code in screen_chosen[1:])
    # The cost center the class says filled itself is the machines' line's, and
    # neither machine carries one of its own.
    assert {machines[machines[code]["parent"]]["cost_center"] for code in screen_chosen[1:]} == {"CC-PAL01"}
    assert not any(machines[code].get("cost_center") for code in screen_chosen[1:])
    assert "CC-PAL01" not in screen_typed


def test_the_routing_is_created_by_the_forms_own_button_and_seen_in_the_table_afterwards(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    create = next(i for i, step in enumerate(screen) if step["do"] == "click" and "routing-create" in step["selector"])

    assert screen[create + 1]["do"] == "wait_for" and "RT-PALLET created" in screen[create + 1]["text"]
    assert any(step["do"] == "wait_for" and step.get("text") == "RT-PALLET" for step in screen[create:])


def test_what_it_refuses_is_a_routing_with_no_operation_and_a_code_already_used(scene):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    waits = [step["text"] for step in refuses if step["do"] == "wait_for"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert waits == ["A routing needs at least one operation.", "already exists"]
    assert sum(1 for step in refuses if step["do"] == "click") == 2
    assert "machine that does not exist" in said and "list with no blank" in said


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


def test_no_caption_card_note_or_typed_word_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_never_on_one_somebody_else_runs(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    # It writes, so it must be refused anywhere but loopback.
    assert scene["allow_writes"]
    runner.refuse_to_write_off_this_box(scene)
    with pytest.raises(SystemExit):
        runner.refuse_to_write_off_this_box(dict(scene, base="http://100.64.0.1:8040"))


def test_the_existing_pallet_routing_the_lists_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "rt-pal1" in honesty
    assert "no blank" in honesty
    assert "ten times" in honesty


def test_every_caption_is_the_line_studio_voiced_word_for_word_in_the_same_order(runner, scene):
    voiced = VOICED.read_text(encoding="utf-8").splitlines()

    assert len(voiced) == 28
    assert runner.script_lines(scene) == voiced


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]
    unpointed = [s["line"] for s in on_screen if not s.get("at")]

    assert set(unpointed) == POINTS_AT_NOTHING_ON_SCREEN, (
        f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing")
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words, and the dimmed spotlight is kept for the cost
    # center the form filled in by itself.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    spotlit = [s for s in on_screen if s.get("style") == "spotlight"]
    assert len(spotlit) == 1 and spotlit[0]["line"].startswith("Cost center CC-PAL01.")


def test_the_floor_is_the_only_page_opened_by_address_once_in_the_hook_and_once_after_the_cards(scene):
    steps = scene["step"]
    pages = [number for number, step in enumerate(steps) if step["do"] == "goto"]

    # A card is the recorder's page and has no menu: the camera comes back from
    # the cards by address, and every other page is reached by a click.
    assert [steps[n]["url"] for n in pages] == ["/dashboard", "/dashboard"]
    assert steps[pages[0]]["part"] == "hook"
    assert steps[pages[1]]["part"] == "screen"
    assert steps[pages[1] - 1]["part"] == "idea", "the goto comes straight after the cards"


def test_admin_is_reached_by_its_menus_and_no_page_of_people_is_opened(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    clicks = [step["selector"] for step in screen if step["do"] == "click"]

    assert clicks[:2] == ['header nav a:text-is("Setup")', '.subnav a:text-is("Admin")']
    assert not [step for step in scene["step"] if step["do"] == "goto" and "people" in step["url"]]
    # The camera goes straight down to the routings: the next thing waited for
    # is the routings table, scrolled to its count.
    after = screen[[s.get("selector") for s in screen].index('.subnav a:text-is("Admin")') + 1]
    assert after["do"] == "wait_for" and after["scroll_to"] == "#routing-count"


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
