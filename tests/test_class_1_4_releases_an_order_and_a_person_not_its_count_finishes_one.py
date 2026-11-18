"""Class 1.4 releases a work order to the floor and a person, not its count, finishes one, on a scratch cutlery plant.

*Work orders — make this many of that, this way* is the second class of the
core: the card a bin carries, the four words of an order's life (order,
release, dispatch, finish), a new fork order typed into the Orders page and
released, the same order in the mould's queue on its station screen, the
plant's own fork order read step by step with its count the last step's, and
the thing an order refuses: it does not finish itself at its quantity
(decision 0029), so a person finishes it and the confirmation goes to the
outbox. This file holds the scene file to what the outline asks of it. No
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
SCENE = REPO / "scripts" / "scenes" / "course" / "02-04-a-work-orders-day.toml"
PACK = REPO / "labs" / "cutlery" / "masterdata"
# The caption lines of the take studio voiced, as that take's script file
# wrote them: the second filming says exactly these, in this order.
VOICED = REPO / "tests" / "fixtures" / "course" / "class-1-4-voiced-script.txt"
# The one line over the screen with nothing to box: the outbox draws its newest
# twenty messages, and by this line the confirmation has scrolled off it.
POINTS_AT_NOTHING_ON_SCREEN = {
    "It was written when a person finished the order, not when the count passed the number. "
    "So the office hears the number the line really made.",
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
    spec = importlib.util.spec_from_file_location("record_scene_class_1_4", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def fork_routing() -> dict:
    return next(r for r in pack("routings") if r["code"] == "RT-FORK1")


def test_the_scene_is_a_full_take_of_class_1_4_named_by_its_label_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "1.4" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    # Half a minute over the first filming's six, for the clicked path, and no more.
    assert 240 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_work_orders_folder_of_the_core_and_leaves_the_draft_one_scene_alone(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "02-the-core/04-a-work-orders-day"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    # The draft-1 teaser's scene stays on disk, filming into its own folder.
    old = runner.load_scene(REPO / "scripts" / "scenes" / "04-work-orders-and-routings.toml")
    assert old["take_is"] == "teaser"


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_what_a_bin_carries_in_the_fork_lines_steps_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")
    order = next(o for o in pack("work_orders") if o["code"] == "WO-FORK1-0001")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "the-card-on-the-bin"
    card = " ".join(hook[0]["lines"])
    assert all(op["name"] in card for op in fork_routing()["operations"])
    assert f"{order['quantity']:,}" in card and order["material"] in card
    assert not [step for step in hook if step["do"] == "goto"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_order_release_dispatch_and_finish(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == ["Order", "Release", "Dispatch", "Finish"]


def test_the_order_typed_in_is_not_in_the_pack_is_for_the_plants_forks_and_is_about_a_shift_of_the_line(scene):
    screen_typed, screen_chosen = typed(scene, "screen"), chosen(scene, "screen")
    # The form's code and quantity, then the plant's own order typed into the filter.
    code, quantity, found = screen_typed
    assert found == "WO-FORK1-0001"
    screen_chosen = [value for value in screen_chosen if value != "FORK1_Mold"]
    orders = {o["code"]: o for o in pack("work_orders")}
    materials = {m["code"] for m in pack("materials")}

    assert code.startswith("WO-FORK1-") and code not in orders
    assert screen_chosen == ["UT-FORK"] and "UT-FORK" in materials
    # The pack's own orders are two hours of the line; a shift is eight.
    shift = orders["WO-FORK1-0001"]["quantity"] * 4
    assert abs(int(quantity) - shift) / shift < 0.05, (quantity, shift)


def test_the_order_is_created_planned_and_released_by_its_own_button(scene):
    screen = steps_of(scene, "screen")
    clicks = [step["selector"] for step in screen if step["do"] == "click"]
    create = next(i for i, step in enumerate(screen) if step["do"] == "click" and step["selector"] == "#no-create")
    release = next(i for i, step in enumerate(screen) if step["do"] == "click" and "Release" in step["selector"])

    # The form's Release box is unticked before Create, so release is a press of its own.
    assert clicks.index("#no-release") < clicks.index("#no-create")
    assert "'planned'" in screen[create + 1]["js"]
    assert screen[release + 1] == {**screen[release + 1], "selector": "#d-status", "text": "released"}


def test_the_released_order_is_found_in_the_queue_of_the_first_machine_on_its_routing(scene):
    screen = steps_of(scene, "screen")
    first = min(fork_routing()["operations"], key=lambda op: op["seq"])["equipment"]
    code = typed(scene, "screen")[0]
    station = next(i for i, step in enumerate(screen) if step["do"] == "choose" and step["selector"] == "#machine")

    # Floor, then Station, then the first machine of the road chosen in its list.
    assert [s["selector"] for s in screen[station - 4 : station] if s["do"] == "click"] == [
        'header nav a:text-is("Floor")', '.subnav a:text-is("Station")']
    assert screen[station]["value"] == first
    assert screen[station + 1]["selector"] == "#queue" and screen[station + 1]["text"] == code


def test_the_order_finished_is_the_packs_own_fork_order_and_the_floor_is_told_not_to_finish_it(scene):
    order = next(o for o in pack("work_orders") if o["code"] == "WO-FORK1-0001")
    (wanted,) = scene["plant"]["roll_when_order"]

    assert order["release"] and order["material"] == fork_routing()["material"]
    assert wanted["code"] == order["code"] and 0 < wanted["booked"] < order["quantity"]
    assert scene["plant"]["env"] == {"MES_OPS_FINISH_ORDERS": "false"}


def test_what_it_refuses_is_finishing_at_the_count_so_a_person_finishes_every_step_and_the_outbox_has_it(scene):
    refuses = steps_of(scene, "refuses")
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")
    finished = [step["selector"] for step in refuses if step["do"] == "click" and "Finish step" in step["selector"]]

    # It opens on the count past the ordered quantity with the order still open.
    assert refuses[0]["do"] == "wait_for" and "#d-good" in refuses[0]["js"] and "#d-qty" in refuses[0]["js"]
    assert "does not finish itself" in said and "A person closes it" in said
    assert finished == [
        f'#ops tbody tr:has-text("{op["name"]}") button:has-text("Finish step")'
        for op in sorted(fork_routing()["operations"], key=lambda op: op["seq"])
    ]
    # The outbox is opened right after the last step is finished, before the
    # confirmation scrolls off its newest twenty. The page is waited for, then
    # the row, then the row is scrolled to, because a step scrolls before it waits.
    outbox = next(i for i, step in enumerate(refuses) if step.get("selector") == '.subnav a:text-is("Ops")')
    between = refuses[[s.get("selector") for s in refuses].index(finished[-1]) + 1 : outbox]
    assert [s["selector"] for s in between if s["do"] == "click"] == ['header nav a:text-is("Setup")']
    assert len(between) <= 6, "nothing slow stands between the last finish and the outbox"
    assert "/dashboard/ops" in refuses[outbox + 1]["js"]
    assert [(w["selector"], w["text"], bool(w.get("scroll_to"))) for w in refuses[outbox + 2 : outbox + 4]] == [
        ("#erp-table tbody", "WO-FORK1-0001", False),
        ("#erp-table tbody", "WO-FORK1-0001", True),
    ]
    # And the order is seen completed afterwards, its over-run beside it.
    assert any("'completed'" in step.get("js", "") for step in refuses[outbox:])


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
    assert base.port not in NOT_OURS
    # It writes, so it must be refused anywhere but loopback.
    assert scene["allow_writes"]
    runner.refuse_to_write_off_this_box(scene)
    with pytest.raises(SystemExit):
        runner.refuse_to_write_off_this_box(dict(scene, base="http://100.64.0.1:8040"))


def test_the_borrowed_order_the_floor_switch_the_outbox_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "wo-fork1-0001" in honesty
    assert "mes_ops_finish_orders=false" in honesty
    assert "newest twenty" in honesty
    assert "first routing" in honesty
    assert "ten times" in honesty


def test_no_screen_of_people_is_filmed_and_admin_is_not_opened(scene):
    urls = [step["url"] for step in scene["step"] if step["do"] == "goto"]

    assert not [url for url in urls if "people" in url or "/admin" in url]


def test_every_caption_is_the_line_studio_voiced_word_for_word_in_the_same_order(runner, scene):
    voiced = VOICED.read_text(encoding="utf-8").splitlines()

    assert len(voiced) == 29
    assert runner.script_lines(scene) == voiced


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]
    unpointed = [s["line"] for s in on_screen if not s.get("at")]

    assert set(unpointed) == POINTS_AT_NOTHING_ON_SCREEN, (
        f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing")
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words, and the dimmed spotlight is kept for the one
    # idea of the class: the order's count is its last step's.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    spotlit = [s for s in on_screen if s.get("style") == "spotlight"]
    assert len(spotlit) == 1 and spotlit[0]["line"].startswith("The order's booked count is the last step's")


def test_the_orders_page_is_the_only_page_opened_by_address_straight_after_the_cards(scene):
    steps = scene["step"]
    pages = [number for number, step in enumerate(steps) if step["do"] == "goto"]

    # A card is the recorder's page and has no menu: the camera comes back from
    # the cards by address, and every other page is reached by a click.
    assert [steps[n]["url"] for n in pages] == ["/dashboard/orders"]
    assert steps[pages[0]]["part"] == "screen" and steps[pages[0] - 1]["part"] == "idea"
    menus = [s["selector"] for s in steps if s["do"] == "click" and "nav a" in s["selector"]]
    assert menus == [
        'header nav a:text-is("Floor")', '.subnav a:text-is("Station")',
        'header nav a:text-is("Orders")',
        'header nav a:text-is("Setup")', '.subnav a:text-is("Ops")',
        'header nav a:text-is("Orders")',
    ]


def test_the_line_that_opens_what_it_refuses_is_held_long_enough_to_be_read_and_spoken(runner, scene):
    # Studio's note on the first filming: this line had one second before the next.
    line = next(s for s in scene["step"] if s["do"] == "say" and s["line"].startswith("Now what it refuses."))

    assert line["hold"] >= runner.hold_for_words(line["line"])


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
