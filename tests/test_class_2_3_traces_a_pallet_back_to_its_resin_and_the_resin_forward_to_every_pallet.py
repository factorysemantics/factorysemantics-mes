"""Class 2.3 traces a pallet back to its resin and the resin forward to every pallet, on a scratch cutlery plant.

*Lots, genealogy and trace*: a supplier's recall letter, the three words (lot,
genealogy, where-used), one pallet opened on the Trace screen and read down to
the lots that went into it, the resin lot clicked and read forward to the
order that drew on it and every package that carries it, and Trace refusing a
lot it has no record of. This file holds the scene file to what the outline
asks of it, and every code it names to the pack. No plant and no browser: the
scene is loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "03-03-lots-genealogy-and-trace.toml"
PACK = REPO / "labs" / "cutlery"

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)

THE_LOT = "LOT-PP-FORK1-001"
THE_PALLET = "PL1-000000001"


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def lots_the_pack_books() -> set[str]:
    """Every lot code the cutlery pack books in at seed."""
    text = json.dumps(json.loads((PACK / "masterdata" / "lots.json").read_text(encoding="utf-8")))
    return set(re.findall(r"LOT-[A-Z0-9-]+", text))


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
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def test_the_scene_is_a_full_take_of_class_2_3_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "2.3" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_lots_folder_third_on_the_floor(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "03-the-floor/03-lots-genealogy-and-trace"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_recall_letter_naming_a_lot_the_pack_books_then_the_trace_screen_short_enough_for_the_teaser(
    runner, scene
):
    hook = [step for step in scene["step"] if step["part"] == "hook"]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "recall-letter"
    assert THE_LOT in " ".join(hook[0]["lines"]) and THE_LOT in lots_the_pack_books()
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard/trace"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_lot_genealogy_and_where_used_and_point_at_class_2_1_in_one_caption(scene):
    idea = [step for step in scene["step"] if step["part"] == "idea"]
    cards = {step["name"]: step for step in idea if step["do"] == "slide"}

    assert [line.split(":")[0] for line in cards["three-words"]["lines"]] == ["Lot", "Genealogy", "Where-used"]
    assert cards["how-a-lot-gets-in"]["title"] == "How a lot gets into a pallet"
    assert [s["line"] for s in idea if s["do"] == "say" and "class 2.1" in s["line"]] != []
    said = [s["line"] for s in scene["step"] if s["do"] == "say"]
    assert sum("class 2.1" in line for line in said) == 1


def test_it_reads_the_pallet_back_to_the_resin_lot_and_says_the_basis_is_the_orders_consumption(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    typed = [step["text"] for step in screen if step["do"] == "fill" and step["selector"] == "#q"]
    at = [step.get("at", "") for step in screen if step["do"] == "say"]
    said = " ".join(step["line"] for step in screen if step["do"] == "say")

    assert typed == [THE_PALLET]
    assert f'#s-components a:text-is("{THE_LOT}")' in at
    assert any(a.endswith("td:nth-child(4)") and THE_LOT in a for a in at), "the Basis cell is boxed"
    assert "via the order's consumption" in said
    assert "Nobody recorded resin going into each fork" in said


def test_it_clicks_the_lot_and_reads_it_forward_to_the_order_and_the_packages_to_hold(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    clicks = [step["selector"] for step in screen if step["do"] == "click"]
    at = [step.get("at", "") for step in screen if step["do"] == "say"]

    assert f'#s-components a:text-is("{THE_LOT}")' in clicks
    assert f'#l-table a:text-is("{THE_PALLET}")' in clicks
    for thing in ("#l-verdict", "#l-orders tbody tr:nth-child(1)", "#l-packages-count"):
        assert thing in at, thing


def test_no_line_says_a_figure_that_grows_while_the_plant_runs(scene):
    said = [step["line"] for step in scene["step"] if step["do"] == "say"]
    cards = [line for step in scene["step"] if step["do"] == "slide" for line in step["lines"]]

    # Units carrying the lot, packages to hold, pallets made: all different in every take.
    counted = [line for line in said + cards if re.search(r"\b\d+\s+(units?|packages?|pallets?)\b", line)]
    assert counted == []
    assert not [line for line in said + cards if re.search(r"\b(four|five|six) pallets\b", line)]


def test_it_writes_nothing_and_holding_the_pallet_is_pointed_at_not_pressed(scene):
    clicks = [step["selector"] for step in scene["step"] if step["do"] == "click"]

    assert not scene.get("allow_writes")
    assert "#st-submit" not in clicks and "#p-submit" not in clicks and "#pack-submit" not in clicks
    assert any(step.get("label") == "Hold it" for step in scene["step"] if step["do"] == "say")


def test_what_it_refuses_is_a_lot_it_has_no_record_of_and_it_runs_under_twenty_five_seconds(runner, scene):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    typed = [step["text"] for step in refuses if step["do"] == "fill" and step["selector"] == "#q"]
    waits = [step["text"] for step in refuses if step["do"] == "wait_for" and step.get("selector") == "#banner"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert len(typed) == 1 and typed[0] not in lots_the_pack_books()
    assert waits == ["no lot"]
    assert "does not guess" in said and "class 1.2" in said.lower()
    assert runner.planned_seconds(dict(scene, step=refuses)) < 25


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


def test_trace_and_the_floor_are_opened_by_address_only_in_the_hook_or_straight_after_a_card(scene):
    steps = scene["step"]
    pages = [n for n, step in enumerate(steps) if step["do"] == "goto"]
    clicks = [step["selector"] for step in steps if step["do"] == "click"]

    assert [steps[n]["url"] for n in pages] == ["/dashboard/trace", "/dashboard", "/dashboard/trace"]
    assert all(over_a_card(scene)[n - 1] for n in pages[1:]), "every page after the hook comes straight after a card"
    assert 'header nav a:text-is("Quality")' in clicks
    assert '.subnav a:text-is("Trace")' in clicks


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_note_or_typed_word_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_once_the_first_pallet_has_a_serial(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    # The palletizer's first count is starved of wraps; the first serial is its second.
    assert scene["plant"]["roll_when_order"] == [{"code": "WO-PAL1-0001", "booked": 2}]


def test_the_simulation_the_order_basis_the_empty_stacks_and_the_missing_unissued_lot_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "ten times" in honesty
    assert "via the order's consumption" in honesty
    assert "first two stacks" in honesty
    assert "never issued" in honesty


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words; a table's rows and cells, the links inside them,
    # and the product's messages are boxed without one.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    boxed_only = ("#toast", "#banner", "table", "#s-components ", "#l-orders ", "#l-table", " td")
    assert not [s["at"] for s in on_screen if s.get("label") and any(b in s["at"] for b in boxed_only)]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 5 and "terminal" not in handed
