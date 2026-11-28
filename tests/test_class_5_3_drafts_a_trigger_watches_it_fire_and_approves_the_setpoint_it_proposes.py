"""Class 5.3 drafts a trigger, watches it fire, and approves the setpoint it proposes.

*Triggers and adjustments — the only path to a PLC* is the third class of the
running-the-plant tier: a card of words (the fork mould running hot at night,
a dial turned down, nobody knowing who), the three words of the class
(trigger, setpoint, human in the loop) and the one path a change takes to a
machine. Then the product: a trigger drafted by the lab shift supervisor, who
cannot approve it; the lab administrator putting it in force; the trigger
firing and proposing a lower setpoint; the administrator approving it; and the
setpoint read back on Tags, and the melt temperature itself coming down toward
it on the machine's trend. Then what it refuses: a setpoint outside the
bounds the tag declares. This file holds the scene file to what the handoff
asks of it. No plant and no browser: the scene is loaded and walked the way
the recorder walks it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules
from fsmes.services.capabilities import BUILTIN_ROLES

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "06-03-triggers-and-adjustments.toml"
ADJUSTMENTS = REPO / "src" / "fsmes" / "services" / "adjustments.py"
TRIGGERS_PAGE = REPO / "src" / "fsmes" / "web" / "triggers.js"
TRIGGER = "T-FORK1-HOT"

A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def everything_written(scene: dict) -> list[str]:
    written = [scene["title"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def test_the_scene_is_a_full_take_of_class_5_3_named_by_its_label_running_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "5.3" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and the wait for the trigger to fire add to the plan; the finished take is measured.
    assert 270 <= planned <= 330 and scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_triggers_folder_of_the_running_the_plant_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "06-running-the-plant/03-triggers-and-adjustments"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["3.1"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "who-turned-the-dial"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_the_three_words_and_each_is_explained_the_first_time_it_is_said(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]
    words = [line.split(":")[0] for line in cards[0]["lines"]]
    lines = said(scene)

    assert words == ["Trigger", "Setpoint", "Human in the loop"]
    for word, starts in (
        ("trigger", "Three words. A trigger is a rule"),
        ("setpoint", "A setpoint is "),
        ("human in the loop", "Human in the loop means "),
    ):
        first = next(line for line in lines if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_the_supervisor_drafts_and_only_the_administrator_approves_as_the_shipped_roles_say(scene):
    def holders(capability):
        return sorted(role for role, spec in BUILTIN_ROLES.items() if capability in spec["capabilities"])

    assert holders("triggers.approve") == ["admin"] and holders("adjustments.approve") == ["admin"]
    assert "supervisor" in holders("triggers.write") and "agent" not in holders("adjustments.approve")
    assert scene["user"] == "WALK-SUP"
    signs = [s for s in scene["step"] if s["do"] == "sign_in"]
    assert [s["user"] for s in signs] == ["ADMIN"]
    steps = scene["step"]
    draft = next(i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "#t-submit")
    approvals = [i for i, s in enumerate(steps) if s["do"] == "click" and 'button:text-is("Approve")' in s["selector"]]
    assert len(approvals) == 2 and draft < steps.index(signs[0]) < approvals[0]
    assert not [s for s in steps if s["do"] == "goto" and "login" in s["url"]]
    assert any("never approves" in line for line in said(scene, "idea"))


def test_the_trigger_typed_proposes_a_setpoint_and_never_writes_one(scene):
    typed = {s["selector"]: s["text"] for s in steps_of(scene, "screen") if s["do"] == "fill"}
    chosen = {s["selector"]: s["value"] for s in steps_of(scene, "screen") if s["do"] == "choose"}
    params = json.loads(typed["#t-params"])

    assert typed["#t-code"] == TRIGGER and typed["#t-tag"] == "MeltTemp" and typed["#t-threshold"] == "215"
    assert chosen["#t-machine"] == "FORK1_Mold" and chosen["#t-action"] == "propose_adjustment"
    assert params["setpoint"] == "MeltTempSP" and params["value"] == 211 and params["rationale"]
    # Class 3.1 is pointed at in one caption, not repeated.
    assert len([line for line in said(scene) if "three point one" in line]) == 1


def test_the_take_waits_for_the_firing_and_for_the_write_before_it_says_them(scene):
    waits = [(s.get("selector"), s.get("text")) for s in steps_of(scene, "screen") if s["do"] == "wait_for"]

    assert ("#firings-table", "ADJ-00001") in waits
    assert ("#queue-table", "approved by ADMIN") in waits
    assert ("#queue-table", "verifying in") in waits
    assert any(
        "MeltTempSP" in s.get("js", "") and "211" in s.get("js", "")
        for s in steps_of(scene, "screen")
        if s["do"] == "wait_for"
    )
    page = TRIGGERS_PAGE.read_text(encoding="utf-8")
    assert "drafted — approve it to put it in force" in page and "in force`" in page


def test_after_the_write_the_take_shows_the_melt_temperature_coming_down_on_its_own_trend_beside_the_setpoint(scene):
    screen = steps_of(scene, "screen")
    names = [s.get("name") for s in screen if s["do"] == "mark"]
    falls = names.index("the-temperature-falls")
    clicks = [s["selector"] for s in screen if s["do"] == "click"]
    chosen = {s["selector"]: s["value"] for s in screen if s["do"] == "choose"}
    waits = [s.get("js", "") for s in screen if s["do"] == "wait_for"]

    # It comes after the setpoint is read back, never before the write.
    assert names.index("approved-and-written") < names.index("the-setpoint") < falls
    # The trend is opened by clicking MeltTemp's own row on Tags, as a person would.
    assert any("MeltTemp" in c and "#tags-table" in c and "MeltTempSP" in c and ":not(" in c for c in clicks)
    assert chosen["#trend-tag-2"] == "MeltTempSP"
    # The live reading is waited for, below where the trigger fired; nothing is put there by the take.
    assert any("MeltTemp" in js and "< 213" in js for js in waits)
    assert any("#trend" in js and "MeltTempSP" in js for js in waits)
    assert any("coming down toward 211" in line for line in said(scene, "screen"))
    assert not [s for s in scene["step"] if s["do"] == "fill" and "MeltTemp" in s.get("selector", "")]


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "set close on purpose" in lines
    assert "A draft watches nothing." in lines
    assert "only as a tooltip. A gap, written up." in lines
    assert "the name leaves the row. A gap, written up." in lines
    assert "so it is said, not shown" in lines
    assert "simulated plant at ten times the clock" in lines
    assert "simulated plant" in honesty and "ten times the clock" in honesty
    assert "said, not filmed" in honesty


def test_what_it_refuses_is_a_setpoint_outside_its_bounds_in_under_twenty_five_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    toast = [s for s in refuses if s["do"] == "wait_for" and s.get("selector") == "#toast"]
    typed = {s["selector"]: s["text"] for s in refuses if s["do"] == "fill"}

    assert runner.planned_seconds(dict(scene, step=refuses)) < 25
    assert typed["#p-value"] == "250"
    assert len(toast) == 1 and toast[0]["text"] == "outside the declared bounds 200-235"
    assert "is outside the declared bounds" in ADJUSTMENTS.read_text(encoding="utf-8")


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in steps_of(scene, "know") if step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] not in ("screen", "refuses")]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # Rows and inner text are boxed, never labelled.
    assert [s for s in scene["step"] if s.get("label")] == []


def test_no_wait_leans_on_a_selector_only_playwright_understands(scene):
    # `wait_for` runs document.querySelector in the page, where :has-text and :text-is match nothing.
    waits = [s for s in scene["step"] if s["do"] == "wait_for" and s.get("selector")]

    assert [s["selector"] for s in waits if ":has-text" in s["selector"] or ":text-is" in s["selector"]] == []


def test_no_ai_chat_is_in_this_class(scene):
    assert not [s for s in scene["step"] if "AI" in s.get("selector", "") or "/dashboard/ai" in s.get("url", "")]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/adjustments", "/dashboard/tags"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_note_or_typed_text_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_at_ten_times_and_its_writes_are_said(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert scene["plant"]["speed"] == 10
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["allow_writes"] and "scratch plant" in scene["what_is_written"]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("slide") == 4 and handed.count("sign_in") == 1 and "terminal" not in handed
