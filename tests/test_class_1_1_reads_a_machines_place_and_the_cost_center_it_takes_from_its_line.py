"""Class 1.1 reads a machine's place in the tree and the cost center it takes from its line, on a scratch cutlery plant.

*Equipment — the plant as a tree, and cost centers* is the first class of the
core: a mould's page, the three words (equipment, work center, cost center),
the cost center set once on a line and read up the tree by every machine
under it, a line and a machine added in Master data with nothing typed on the
machine, and then what the form refuses and the one thing it does not. This
file holds the scene file to what the outline asks of it. No plant and no
browser: the scene is loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "02-01-equipment-and-cost-centers.toml"
PACK = REPO / "labs" / "cutlery" / "masterdata"

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def equipment() -> dict[str, dict]:
    rows = json.loads((PACK / "equipment.json").read_text(encoding="utf-8"))
    return {row["code"]: row for row in rows}


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
    written = [scene["title"], scene["what_is_written"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def adds(scene: dict) -> list[dict]:
    """Each press of Add, with what the form held when it was pressed."""
    form: dict[str, str] = {}
    pressed = []
    for step in scene["step"]:
        if step["do"] in ("fill", "choose") and step["selector"].startswith("#eq-") and step["selector"] != "#eq-q":
            form[step["selector"]] = step.get("text", step.get("value"))
        if step["do"] == "click" and step["selector"] == "#eq-submit":
            pressed.append(dict(form))
        if step["do"] == "wait_for" and step.get("selector") == "#toast" and step["text"].endswith("added"):
            # The product empties the form after a save it accepted.
            form = {}
    return pressed


def test_the_scene_is_a_full_take_of_class_1_1_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "1.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_equipment_folder_at_the_head_of_the_core(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "02-the-core/01-equipment-and-cost-centers"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_one_mould_then_its_own_page_and_is_short_enough_for_the_teaser(runner, scene):
    hook = [step for step in scene["step"] if step["part"] == "hook"]
    plant = equipment()
    line = plant[plant["FORK1_Mold"]["parent"]]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "one-mould"
    assert line["cost_center"] in hook[0]["lines"][-1]
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard/machine/FORK1_Mold"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_equipment_work_center_and_cost_center_and_set_it_once_on_the_line(scene):
    cards = {step["name"]: step for step in scene["step"] if step["do"] == "slide" and step["part"] == "idea"}

    assert [line.split(":")[0] for line in cards["three-words"]["lines"]] == ["Equipment", "Work center", "Cost center"]
    assert cards["set-once"]["title"] == "Set once, on the line"


def test_every_line_of_the_pack_carries_a_cost_center_and_not_one_machine_does_as_the_class_says(scene):
    plant = equipment().values()
    machines = [e for e in plant if e["level"] == "work_unit"]
    lines = [e for e in plant if e["level"] == "work_center"]
    said = " ".join(step["line"] for step in scene["step"] if step["do"] == "say")

    assert all(e.get("cost_center") for e in lines), f"{len(lines)} lines"
    assert not [e for e in machines if e.get("cost_center")], f"{len(machines)} machines"
    assert len(machines) == 83 and "eighty-three machines" in said
    # 150 nodes and, since 2026-11-27, the nine moulds' 45 parts (bands and sensors).
    assert len(plant) == 195 and "A hundred and ninety-five places" in said


def test_the_line_and_machine_it_adds_are_new_codes_and_only_the_line_is_given_a_cost_center(scene):
    plant = equipment()
    pressed = adds(scene)
    line, machine = pressed[0], pressed[1]

    assert (line["#eq-code"], line["#eq-level"], line["#eq-parent"]) == ("FORK4LINE", "work_center", "MOULD-AREA")
    assert line["#eq-parent"] in plant and plant[line["#eq-parent"]]["level"] == "area"
    assert line["#eq-cc"] == "CC-MOULD10"
    assert "CC-MOULD10" not in {e.get("cost_center") for e in plant.values()}
    assert (machine["#eq-code"], machine["#eq-level"]) == ("FORK4_MOLD", "work_unit")
    assert machine["#eq-parent"] == "FORK4LINE"
    assert "#eq-cc" not in machine, "nothing is typed in the machine's cost center box"
    assert not {line["#eq-code"], machine["#eq-code"]} & set(plant)


def test_the_new_machines_page_shows_the_lines_cost_center_in_the_one_spotlight(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    opened = next(i for i, s in enumerate(screen) if s["do"] == "click" and "FORK4_MOLD" in s["selector"])
    spotlit = [s for s in scene["step"] if s.get("style") == "spotlight"]

    assert screen[opened + 1] == {**screen[opened + 1], "do": "wait_for", "selector": "#m-cc", "text": "CC-MOULD10"}
    assert len(spotlit) == 1 and spotlit[0]["at"] == "#m-cc" and "CC-MOULD10" in spotlit[0]["line"]


def test_what_it_refuses_is_a_code_in_use_and_what_it_takes_is_a_machine_with_no_parent(scene):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    waits = [step["text"] for step in refuses if step["do"] == "wait_for" and step.get("selector") == "#toast"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")
    loose, again = adds(scene)[2:]

    assert waits == ["LOOSE_PRESS added", "already exists"]
    assert loose["#eq-code"] == again["#eq-code"] == "LOOSE_PRESS"
    # The form is fresh, so its parent is the page's own "no parent"; nothing chose one.
    assert "#eq-parent" not in loose
    assert "does not refuse a machine with no place" in said
    assert "machine under a machine cannot even be asked" in said
    assert "will not pick a budget" in said


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


def test_only_the_moulds_page_and_the_floor_are_opened_by_address_and_the_rest_by_menus_and_links(scene):
    steps = scene["step"]
    pages = [n for n, step in enumerate(steps) if step["do"] == "goto"]
    clicks = [step["selector"] for step in steps if step["do"] == "click"]

    assert [steps[n]["url"] for n in pages] == ["/dashboard/machine/FORK1_Mold", "/dashboard"]
    assert steps[pages[1] - 1]["part"] == "idea", "the Floor comes straight after the cards"
    assert 'header nav a:text-is("Engineering")' in clicks
    assert '.subnav a:text-is("Master data")' in clicks


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


def test_the_parentless_machine_the_missing_edit_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "no parent" in honesty
    assert "no way to change a place" in honesty
    assert "ten times" in honesty


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words; the product's message after a save sits over the
    # equipment table, so it is boxed without one.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    assert not [s for s in on_screen if s["at"] == "#toast" and s.get("label")]


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
