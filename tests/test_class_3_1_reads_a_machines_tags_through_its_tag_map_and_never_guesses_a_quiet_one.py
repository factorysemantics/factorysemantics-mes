"""Class 3.1 reads a machine's tags through its tag map and never guesses a quiet one, on a scratch cutlery plant.

*Tags - how a machine's counters reach the MES* opens the machines tier: a
machine that counts (a card of words), the words of the class (a tag, OPC UA,
the tag map, read-only), and the fork mould's numbers followed the way the
product shows them - its tile on the Floor, every tag on its own page with its
state code turned into "running" by the tag map, the plant's whole list on
Engineering > Tags with the writable setpoint nothing writes from there, one
tag's trend, and on Ops the endpoint, the tag map file and the simulated tag
server. Then what it refuses: a quiet tag is marked stale, and a lost
connection is unknown time, not a guess. This file holds the scene file to what
the handoff asks of it. No plant and no browser: the scene is loaded and walked
the way the recorder walks it.
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
SCENE = REPO / "scripts" / "scenes" / "course" / "04-01-tags-and-opc-ua.toml"
PACK = REPO / "labs" / "cutlery"
MACHINE = "FORK1_Mold"
# The one line over the screen that is about nothing drawn there: what the MES
# does with a number it did not hear.
POINTS_AT_NOTHING_ON_SCREEN = {
    "The MES does not fill the gap with a guess. A number it did not hear is a number it does not have."
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
    spec = importlib.util.spec_from_file_location("record_scene_class_3_1", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def tag_map() -> dict:
    return json.loads((PACK / "tag_map.json").read_text(encoding="utf-8"))


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


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def clicked(scene: dict) -> list[str]:
    return [step["selector"] for step in scene["step"] if step["do"] == "click"]


def test_the_scene_is_a_full_take_of_class_3_1_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "3.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_tags_folder_of_the_machines_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "04-the-machines-talk/01-tags-and-opc-ua"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_about_a_machine_that_counts_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "a-machine-that-counts"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_a_tag_opc_ua_the_tag_map_and_read_only(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    lines = cards[0]["lines"]
    assert [line.split(":")[0] for line in lines] == ["Tag", "OPC UA", "Tag map", "Read-only"]
    assert "a name, a value and a time" in lines[0]


def test_opc_ua_and_the_tag_map_are_explained_the_first_time_they_are_said(scene):
    lines = said(scene)
    first_opc = next(line for line in lines if "OPC UA" in line)
    first_map = next(line for line in lines if "tag map" in line.lower())

    assert first_opc.startswith("OPC UA is ")
    assert first_map.startswith("The tag map is ")
    # Both are explained on the idea card, before any screen is shown.
    assert first_opc in said(scene, "idea") and first_map in said(scene, "idea")


def test_the_only_page_opened_by_address_is_the_floor_and_everything_else_is_clicked(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    screen = steps_of(scene, "screen")
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is screen[0]
    # The machine page from the mould's tile on the Floor, the tag list by the
    # Engineering menu, the trend by the tag's row, Ops by the Setup menu.
    assert path[0] == f'main .machine a.machine-code[href="/dashboard/machine/{MACHINE}"]'
    assert path.count('header nav a:text-is("Engineering")') == 2
    assert path.count('nav.subnav a:text-is("Tags")') == 2
    assert path.count('#tags-table tbody tr:has-text("primary")') == 1
    assert path.index('header nav a:text-is("Setup")') + 1 == path.index('nav.subnav a:text-is("Ops")')


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
        "a-machine-on-the-floor",
        "one-machines-tags",
        "every-tag-in-the-plant",
        "the-mes-only-reads",
        "one-tags-trend",
        "the-tag-map",
        "a-simulated-tag-server",
        "a-quiet-tag-is-marked-stale",
        "a-lost-connection-is-not-a-guess",
    ]


def test_the_state_code_the_film_reads_is_the_one_the_packs_tag_map_turns_into_running(scene):
    (mould,) = [m for m in tag_map()["machines"] if m["equipment"] == MACHINE]
    lines = " ".join(said(scene))

    assert mould["state_map"]["1"] == "running"
    assert "sends the number 1" in lines and "1 means running" in lines
    assert any("r.querySelector('td.num').textContent.trim() === '1'" in step.get("js", "") for step in scene["step"])


def test_read_only_is_true_of_the_pack_no_machine_is_given_a_tag_to_write_an_order_to(scene):
    machines = tag_map()["machines"]
    lines = " ".join(said(scene))

    assert [m["equipment"] for m in machines if m.get("order_tag")] == []
    assert "writes no tag from here" in lines
    # The one path to a PLC is pointed at, not taken.
    (ahead,) = [line for line in said(scene) if "5.3" in line]
    assert "adjustment" in ahead
    assert 'nav.subnav a:text-is("Adjustments")' not in clicked(scene)


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "No real plant's tag server has been read yet" in lines
    assert "simulated plant's own tag server" in lines
    assert "not by its OPC address" in lines
    assert "No screen shows what is inside it yet" in lines
    assert "no real plant's tag server has been read yet" in honesty
    assert "not filmed" in honesty


def test_what_it_refuses_is_a_guess_a_quiet_tag_is_stale_and_a_lost_connection_is_unknown(scene):
    refuses = steps_of(scene, "refuses")
    lines = " ".join(said(scene, "refuses"))

    assert "stale" in lines and "unknown" in lines and "guess" in lines
    # The list of stale tags states its total even when it is empty.
    assert any(step.get("selector") == "#row-count" and step.get("text") == "0 of 0" for step in refuses)
    assert "#only-stale" in [step["selector"] for step in refuses if step["do"] == "click"]


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
    visited |= {"/dashboard/tags", "/dashboard/ops", f"/dashboard/machine/{MACHINE}"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_writes_nothing(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert not scene.get("allow_writes")
    assert not [step for step in scene["step"] if step["do"] == "fill"]


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
