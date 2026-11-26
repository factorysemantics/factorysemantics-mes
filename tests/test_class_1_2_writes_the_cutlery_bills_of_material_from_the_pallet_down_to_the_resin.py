"""Class 1.2 writes the cutlery bills of material from the pallet down to the resin, on a scratch cutlery plant.

*Materials and bills of material*: a card of one pallet, the four words (raw,
intermediate, finished, bill of material), the plant's eight materials read by
type, the bills the pack does not ship written top down on Master data, the
pallet's bill read again to show it did not move, and the Floor refusing a lot
of a material the plant has never heard of. This file holds the scene file to
what the outline asks of it, and every figure in it to the pack. No plant and
no browser: the scene is loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "02-02-materials-and-bills-of-material.toml"
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


def pack(name: str) -> list[dict]:
    return json.loads((PACK / f"{name}.json").read_text(encoding="utf-8"))


def materials() -> dict[str, dict]:
    return {row["code"]: row for row in pack("materials")}


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


def bill_lines(scene: dict) -> list[tuple[str, str, float, int]]:
    """Each press of Add component: (the material picked, the component, quantity, operation)."""
    picked, form, pressed = None, {}, []
    for step in scene["step"]:
        if step["do"] == "click" and step["selector"].startswith("#mat-table td"):
            picked = re.search(r'"([^"]+)"', step["selector"]).group(1)
        if step["do"] in ("fill", "choose") and step["selector"].startswith("#bom-"):
            form[step["selector"]] = step.get("text", step.get("value"))
        if step["do"] == "click" and step["selector"] == "#bom-submit":
            pressed.append((picked, form["#bom-component"], float(form["#bom-qty"]), int(form["#bom-seq"])))
            form = {}
    return pressed


def test_the_scene_is_a_full_take_of_class_1_2_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "1.2" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_materials_folder_second_in_the_core(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "02-the-core/02-materials-and-bills-of-material"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_one_pallet_then_the_materials_list_and_is_short_enough_for_the_teaser(runner, scene):
    hook = [step for step in scene["step"] if step["part"] == "hook"]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "one-pallet"
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard/masterdata"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_raw_intermediate_finished_and_the_bill_and_say_a_bill_is_one_level(scene):
    cards = {step["name"]: step for step in scene["step"] if step["do"] == "slide" and step["part"] == "idea"}

    assert [line.split(":")[0] for line in cards["four-words"]["lines"]] == [
        "Raw",
        "Intermediate",
        "Finished",
        "Bill of material",
    ]
    assert cards["one-level"]["title"] == "One level at a time"


def test_the_materials_it_reads_by_type_are_the_packs_eight_two_raw_four_intermediate_two_finished(scene):
    by_type: dict[str, list[str]] = {}
    for code, row in materials().items():
        by_type.setdefault(row["type"], []).append(code)
    counts = [
        step["text"]
        for step in scene["step"]
        if step["part"] == "screen" and step["do"] == "wait_for" and step.get("selector") == "#mat-count"
    ]

    assert len(materials()) == 8 and counts[0] == "8 of 8"
    assert {kind: len(codes) for kind, codes in by_type.items()} == {"raw": 2, "intermediate": 4, "finished": 2}
    assert counts[1:4] == [
        f"{len(by_type[k])} of {len(by_type[k])} matching, 8 in the plant" for k in ("raw", "intermediate", "finished")
    ]


def test_the_pack_ships_no_bill_of_material_so_the_class_writes_its_own_as_it_says(scene):
    said = " ".join(step["line"] for step in scene["step"] if step["do"] == "say")

    assert not (PACK / "bom.json").exists()
    assert "came with no bills at all" in said
    assert "ships no bill of material" in " ".join(scene["honesty"])


def test_the_bills_it_writes_run_from_the_pallet_down_to_the_resin_and_the_plate_on_the_packs_own_figures(scene):
    known = materials()
    routings = {r["material"]: r for r in pack("routings")}
    weight = next(s for s in pack("quality_specs") if s["material"] == "UT-FORK" and s["characteristic"] == "weight_g")
    readme = (REPO / "labs" / "cutlery" / "README.md").read_text(encoding="utf-8")

    lines = bill_lines(scene)

    assert lines == [
        ("PALLET", "WRAP", 240.0, 10),
        ("WRAP", "STACK-3", 1.0, 10),
        ("WRAP", "RAW-PLATE", 1.0, 10),
        ("STACK-3", "UT-FORK", 1.0, 10),
        ("STACK-3", "UT-SPOON", 1.0, 10),
        ("STACK-3", "UT-KNIFE", 1.0, 10),
        ("UT-FORK", "RAW-PP", 0.0039, 10),
    ]
    assert "240 wraps become a pallet" in readme
    # The resin line is the middle of the fork's weight spec, in the resin's own unit.
    assert weight["min"] < 3.9 < weight["max"] and known["RAW-PP"]["unit"] == "kg"
    for parent, component, _, seq in lines:
        assert parent in known and component in known
        assert seq in {op["seq"] for op in routings[parent]["operations"]}, (parent, seq)


def test_the_resin_is_read_as_zero_by_the_table_and_the_class_says_what_is_behind_it(scene):
    zero = next(step for step in scene["step"] if step.get("line", "").startswith("The quantity reads 0."))

    assert "0.0039" in zero["line"] and "cannot show it" in zero["line"]
    assert zero["at"] == "#bom-table tbody tr:nth-child(1) td:nth-child(2)"
    assert any("rounds to two decimal places" in line for line in scene["honesty"])


def test_the_pallets_bill_is_read_again_after_the_wraps_and_has_not_moved(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    opened = [
        i for i, s in enumerate(screen) if s["do"] == "click" and s["selector"] == '#mat-table td:text-is("PALLET")'
    ]
    after = " ".join(s["line"] for s in screen[opened[-1] :] if s["do"] == "say")

    assert len(opened) == 2
    assert "Still one line: 240 wraps" in after
    assert "cannot change or remove one" in after


def test_what_it_refuses_is_a_lot_of_a_material_the_plant_has_never_heard_of_and_it_runs_under_twenty_five_seconds(
    runner, scene
):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    typed = {step["selector"]: step["text"] for step in refuses if step["do"] == "fill"}
    waits = [step["text"] for step in refuses if step["do"] == "wait_for" and step.get("selector") == "#toast"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert typed['#form-lot input[name="material"]'] not in materials()
    assert waits == ["not found"]
    assert "typo, not a delivery" in said
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


def test_only_master_data_and_the_floor_are_opened_by_address_and_the_floor_only_straight_after_a_card(scene):
    steps = scene["step"]
    pages = [n for n, step in enumerate(steps) if step["do"] == "goto"]
    clicks = [step["selector"] for step in steps if step["do"] == "click"]

    assert [steps[n]["url"] for n in pages] == ["/dashboard/masterdata", "/dashboard", "/dashboard"]
    assert all(over_a_card(scene)[n - 1] for n in pages[1:]), "the Floor comes straight after a card"
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


def test_the_missing_bills_the_rounding_the_missing_edit_the_loose_form_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "ships no bill of material" in honesty
    assert "rounds to two decimal places" in honesty
    assert "cannot be changed or removed" in honesty
    assert "a material inside itself" in honesty
    assert "ten times" in honesty


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words; a table's rows and cells and the product's message
    # after a save are boxed without one.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    assert not [s for s in on_screen if s.get("label") and (s["at"] == "#toast" or "table" in s["at"])]


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
