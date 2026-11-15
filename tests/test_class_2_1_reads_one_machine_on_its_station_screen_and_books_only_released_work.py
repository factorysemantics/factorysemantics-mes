"""Class 2.1 reads one machine on its station screen and books only released work, on a scratch cutlery plant.

*The station screen — one machine at arm's length* is the first class of the
floor: the operator at the press (a card of words), the four words of the
class (station, queue, book, issue), and the fork mould's station screen read
card by card - its state, its queue, 500 good and 3 scrap booked against the
order at the head of the queue, a lot of the line's resin issued to it, one
length reading recorded in spec, and what maintenance is owed. Then what it
refuses (a planned order is not offered to book against) and what it does
not (a Good figure the machine's counter contradicts is taken as typed, and
the scene says so rather than inventing a refusal). This file holds the scene
file to what the outline asks of it. No plant and no browser: the scene is
loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "03-01-the-station-screen.toml"
PACK = REPO / "labs" / "cutlery" / "masterdata"
MACHINE = "FORK1_Mold"

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
    spec = importlib.util.spec_from_file_location("record_scene_class_2_1", RUNNER)
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


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def typed_into(scene: dict, part: str, selector: str) -> list[str]:
    return [step["text"] for step in steps_of(scene, part) if step["do"] == "fill" and step["selector"] == selector]


def chosen_in(scene: dict, part: str, selector: str) -> list[str]:
    return [step["value"] for step in steps_of(scene, part) if step["do"] == "choose" and step["selector"] == selector]


def fork_routing() -> dict:
    return next(r for r in pack("routings") if r["code"] == "RT-FORK1")


def test_the_scene_is_a_full_take_of_class_2_1_named_by_its_label_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "2.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 240 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_station_screen_folder_of_the_floor(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "03-the-floor/01-the-station-screen"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_about_one_machine_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "the-operator-at-the-press"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_station_queue_book_and_issue(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == ["Station", "Queue", "Book", "Issue"]


def test_the_screen_is_the_fork_moulds_station_and_its_machine_is_the_first_step_of_the_fork_line(scene):
    first = min(fork_routing()["operations"], key=lambda op: op["seq"])["equipment"]
    stations = [urlparse(step["url"]) for step in scene["step"] if step["do"] == "goto" and "/station" in step["url"]]

    assert first == MACHINE
    assert stations and all(parse_qs(url.query)["m"] == [MACHINE] for url in stations)


def test_it_reads_the_cards_top_to_bottom_state_queue_book_issue_quality_maintenance(scene):
    marks = [step["name"] for step in steps_of(scene, "screen") if step["do"] == "mark"]

    assert marks == [
        "its-state",
        "its-queue",
        "booked-500-good-3-scrap",
        "issued-two-kilos-of-resin",
        "a-reading-in-spec",
        "what-maintenance-is-owed",
    ]


def test_it_books_500_good_and_3_scrap_against_the_released_order_at_the_head_of_the_queue(scene):
    order = next(o for o in pack("work_orders") if o["code"] == "WO-FORK1-0001")

    assert order["release"] and order["line"] == "FORK1LINE"
    assert chosen_in(scene, "screen", "#book-order") == [order["code"]]
    assert typed_into(scene, "screen", "#book-good") == ["500"]
    assert typed_into(scene, "screen", "#book-scrap") == ["3"]
    assert scene["plant"]["roll_when_order"] == [{**scene["plant"]["roll_when_order"][0], "code": order["code"]}]


def test_the_lot_issued_is_the_fork_lines_resin_and_about_what_500_forks_weigh(scene):
    (lot,) = chosen_in(scene, "screen", "#issue-lot")
    (kilos,) = typed_into(scene, "screen", "#issue-qty")
    lots = {entry["code"]: entry for entry in pack("lots")}
    order = next(o for o in pack("work_orders") if o["code"] == "WO-FORK1-0001")
    weight = next(s for s in pack("quality_specs") if s["material"] == "UT-FORK" and s["characteristic"] == "weight_g")

    assert lot in lots and lots[lot]["material"] == "RAW-PP" and lot == order["issue"]["lot"]
    assert 500 * weight["min"] / 1000 <= float(kilos) <= 500 * weight["max"] / 1000
    # What the take waits for after Issue is that lot, lighter by what was typed.
    left = lots[lot]["quantity"] - order["issue"]["quantity"] - float(kilos)
    assert any(f"{left:,.1f}" in step.get("js", "") for step in steps_of(scene, "screen"))


def test_the_reading_recorded_is_inside_the_packs_length_limits_for_forks(scene):
    (characteristic,) = chosen_in(scene, "screen", "#q-char")
    (value,) = typed_into(scene, "screen", "#q-value")
    spec = next(
        s for s in pack("quality_specs") if s["material"] == "UT-FORK" and s["characteristic"] == characteristic
    )

    assert spec["min"] < float(value) < spec["max"]
    assert any(f"Spec {spec['min']:g} to {spec['max']:g} mm." == step.get("text") for step in scene["step"])


def test_what_it_refuses_is_a_planned_order_which_the_against_list_does_not_offer(scene):
    refuses = steps_of(scene, "refuses")
    planned = next(o for o in pack("work_orders") if o["code"] == "WO-FORK1-0002")
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert not planned["release"] and planned["line"] == "FORK1LINE"
    assert "'planned'" in next(step["js"] for step in refuses if "WO-FORK1-0002" in step.get("js", ""))
    # Back at the mould, the list is checked to hold the released order alone.
    assert any("#book-order option" in step.get("js", "") for step in refuses)
    assert "not there to book against" in said
    # The floor is told not to release it while the camera rolls.
    assert scene["plant"]["env"] == {"MES_OPS_FINISH_ORDERS": "false"}


def test_a_figure_the_counter_contradicts_is_filmed_as_taken_and_owned_up_to_not_refused(scene):
    refuses = steps_of(scene, "refuses")
    (typed,) = typed_into(scene, "refuses", "#book-good")
    honesty = " ".join(scene["honesty"])
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    # The take checks the mould's count is short of the figure before typing it.
    fill = next(i for i, step in enumerate(refuses) if step["do"] == "fill")
    assert any(f"< {typed}" in step.get("js", "") for step in refuses[:fill])
    assert "took the figure as typed" in said
    assert "does not refuse a Good figure the machine's own counter contradicts" in honesty


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


def test_the_counter_the_absent_order_the_floor_switch_the_lot_list_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "added on top" in honesty
    assert "wo-fork1-0002" in honesty
    assert "mes_ops_finish_orders=false" in honesty
    assert "paper plates" in honesty
    assert "no maintenance plan" in honesty
    assert "ten times" in honesty


def test_no_screen_of_people_is_filmed_and_admin_is_not_opened(scene):
    urls = [step["url"] for step in scene["step"] if step["do"] == "goto"]

    assert not [url for url in urls if "people" in url or "/admin" in url]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed
