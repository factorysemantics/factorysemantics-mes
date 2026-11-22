"""Class 2.2 names a stop from the plant's own words and leaves an unnamed one unlabelled, on a scratch cutlery plant.

*Downtime and reason codes* is the second class of the floor: a red light on
the fork mould (a card of words), the words of the class (the four states, a
reason, a planned stop, an unlabelled stop), and one stop followed from the
machine to the chart - the mould's state on its station screen, what pressing
down asks (and Never mind, because the mould is running), the stop the
replayed mould reported, found on its machine page's timeline and named
Breakdown from the plant's list, that list on the Downtime reasons page, and
the stop's seconds on the line's downtime pareto, with one line pointing ahead
to OEE. Then what it refuses: the second line's stop, left unnamed, is
"unlabelled" on its pareto, not filed under a guessed reason. This file holds
the scene file to what the handoff asks of it. No plant and no browser: the
scene is loaded and walked the way the recorder walks it.
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
SCENE = REPO / "scripts" / "scenes" / "course" / "03-02-downtime-and-reason-codes.toml"
PACK = REPO / "labs" / "cutlery"
MACHINE = "FORK1_Mold"
# The one line over the screen that is about nothing drawn there: what fixes an
# unlabelled stop is a person, not a thing on the page.
POINTS_AT_NOTHING_ON_SCREEN = {
    "An unlabelled stop is the row the pareto cannot explain."
    " The fix is a person naming it, not the software picking a word."
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
    spec = importlib.util.spec_from_file_location("record_scene_class_2_2", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def reasons() -> list[dict]:
    return json.loads((PACK / "masterdata" / "downtime_reasons.json").read_text(encoding="utf-8"))


def everything_written(scene: dict) -> list[str]:
    """Every sentence a viewer or a reader of the folder will see."""
    written = [scene["title"], scene["what_is_written"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
            if step.get("label"):
                written.append(step["label"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def chosen_in(scene: dict, part: str, selector: str) -> list[str]:
    return [step["value"] for step in steps_of(scene, part) if step["do"] == "choose" and step["selector"] == selector]


def clicked(scene: dict) -> list[str]:
    return [step["selector"] for step in scene["step"] if step["do"] == "click"]


def test_the_scene_is_a_full_take_of_class_2_2_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "2.2" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_downtime_folder_of_the_floor(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "03-the-floor/02-downtime-and-reason-codes"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_about_a_stopped_machine_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "a-red-light-on-the-mould"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_state_the_reason_the_planned_stop_and_the_unlabelled_one(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    lines = cards[0]["lines"]
    assert [line.split(":")[0] for line in lines[:2]] == ["State", "Reason"]
    assert "running, idle, down or in setup" in lines[0]
    assert "not a breakdown" in lines[2] and "unlabelled" in lines[3]


def test_the_only_page_opened_by_address_is_the_floor_and_everything_else_is_clicked(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    screen = steps_of(scene, "screen")
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is screen[0]
    # Floor's Station chip, then the machine chooser, before anything is read.
    assert [step["do"] for step in screen[:4]] == ["goto", "click", "wait_for", "choose"]
    assert screen[1]["selector"] == 'nav.subnav a:text-is("Station")'
    assert chosen_in(scene, "screen", "#machine") == [MACHINE]
    # The machine page by the station's button, the reasons by the stop's own
    # panel, the analysis by the Engineering menu and its Analysis chip.
    for selector in (
        "#machine-page",
        'button.tab[data-tab="timeline"]',
        '#block-panel a[href="/dashboard/reasons"]',
        'header nav a:text-is("Engineering")',
        'nav.subnav a:text-is("Analysis")',
    ):
        assert path.count(selector) == 1, selector
    assert path.index('header nav a:text-is("Engineering")') + 1 == path.index('nav.subnav a:text-is("Analysis")')


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] not in ("screen", "refuses")]
    unpointed = [s["line"] for s in on_screen if not s.get("at") and s["line"] not in POINTS_AT_NOTHING_ON_SCREEN]

    assert unpointed == [], f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing: {unpointed}"
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # A label is a few words, and the dimmed spotlight is kept for the refusal.
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    spotlit = [s for s in on_screen if s.get("style") == "spotlight"]
    assert len(spotlit) == 2 and all(s["part"] == "refuses" for s in spotlit)


def test_the_beats_are_filmed_in_the_order_the_handoff_asks(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark" and step["part"] in ("screen", "refuses")]

    assert marks == [
        "the-state-pill",
        "the-floor-asks-why",
        "a-stop-from-the-machine",
        "the-operator-names-the-stop",
        "the-reason-list-is-the-plants-own",
        "where-the-minutes-land",
        "pointing-ahead-to-oee",
        "the-share-nobody-explained",
        "an-unnamed-stop-stays-unlabelled",
    ]


def test_pressing_down_at_the_station_asks_why_and_is_cancelled_so_nothing_is_written_there(scene):
    path = clicked(scene)
    down = path.index("#state-buttons .state-btn.down")

    # The reason is chosen to show its description, and then Never mind.
    assert chosen_in(scene, "screen", "#down-reason-code") == ["breakdown"]
    assert path[down + 1] == "#reason-cancel"
    assert "#reason-confirm" not in path
    assert any("#reason-confirm').disabled" in step.get("js", "") for step in scene["step"])


def test_the_stop_is_named_with_a_word_on_the_packs_own_list_and_the_list_says_so(scene):
    words = {reason["code"]: reason for reason in reasons()}
    (named,) = chosen_in(scene, "screen", "#stop-reason-code")
    said = " ".join(step["line"] for step in scene["step"] if step["do"] == "say")

    assert named in words and words[named]["name"] == "Breakdown"
    assert '#stop-label button[type="submit"]' in clicked(scene)
    assert any(step.get("text") == f"1 stop named {words[named]['name']}" for step in scene["step"])
    # "Six words" is the pack's own count, and the reasons page is waited on for it.
    assert len(words) == 6 and "six words" in said.lower()
    assert any(step.get("selector") == "#kpi-approved" and step.get("text") == "6" for step in scene["step"])
    # What the description caption quotes is the pack's own sentence.
    assert any(step.get("text") == "broke down" for step in scene["step"])
    assert "broke down" in words[named]["description"]


def test_the_stops_are_the_replayed_lines_own_and_both_are_past_before_the_camera_rolls(scene):
    lines = json.loads((PACK / "line.json").read_text(encoding="utf-8"))["lines"]
    speed = scene["plant"]["speed"]
    # The first two lines of the recording each stop their mould once, a minute
    # and a half of the line apiece; the class names the first and leaves the second.
    stops = [[e for e in line["events"] if e["type"] == "down" and e["station"] == "Mold"] for line in lines[:2]]
    assert [len(s) for s in stops] == [1, 1]
    (first,), (second,) = stops
    assert (first["end"] - first["start"]) / speed == 9 == (second["end"] - second["start"]) / speed
    assert "nine seconds" in " ".join(step["line"] for step in scene["step"] if step["do"] == "say").lower()
    (roll,) = scene["plant"]["roll_when_order"]
    assert roll["code"] == "WO-FORK1-0001" and roll["booked"] >= 100
    assert speed == 10


def test_where_the_minutes_land_is_the_pareto_and_oee_is_only_pointed_at(scene):
    screen = steps_of(scene, "screen")
    said = [step["line"] for step in screen if step["do"] == "say"]
    oee = [line for line in said if "OEE" in line]

    assert any(step.get("selector") == "#pareto" and step.get("text") == "Breakdown" for step in screen)
    assert len(oee) == 1 and "3.3" in oee[0]


def test_what_it_refuses_is_a_guess_the_second_lines_unnamed_stop_is_unlabelled(scene):
    refuses = steps_of(scene, "refuses")
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert chosen_in(scene, "refuses", "#line") == ["FORK2LINE"]
    assert any(step.get("selector") == "#pareto" and step.get("text") == "unlabelled" for step in refuses)
    assert any("100% unlabelled" in step.get("js", "") for step in refuses)
    assert "does not guess" in said
    # Nothing is named in the refusal.
    assert not [step for step in refuses if step["do"] == "click"]


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
    visited |= {"/dashboard/reasons", "/dashboard/analysis", "/dashboard/machine/FORK1_Mold"}

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


def test_the_machine_stop_the_cancelled_press_the_window_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "the station did not ask why" in honesty
    assert "never mind" in honesty
    assert "15-minute window" in honesty
    assert "left unnamed on purpose" in honesty
    assert "ten times" in honesty


def test_no_screen_of_people_is_filmed_and_admin_is_not_opened(scene):
    urls = [step["url"] for step in scene["step"] if step["do"] == "goto"]
    path = " ".join(clicked(scene))

    assert not [url for url in urls if "people" in url or "/admin" in url]
    assert "Setup" not in path and "Admin" not in path


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
