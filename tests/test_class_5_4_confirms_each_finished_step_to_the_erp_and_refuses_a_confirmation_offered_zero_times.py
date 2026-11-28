"""Class 5.4 confirms each finished step to the ERP and refuses a confirmation offered zero times.

*The ERP handoff* is the last class of the running-the-plant tier: a card of
words (the office's ERP books the money, the MES knows what the line made),
the words of the class (ERP, confirmation, order completion, queue), the two
ways across (a file exchange and a live connector) and one confirmation read
from the product's worked example. Then the product, signed in as the lab
admin: the Floor's ERP queue word, a fork order the simulated shift supervisor
has finished, its three steps with their good and scrap, the lots it used and
made, and the ERP delivery retries on Orders > Configuration. Then what it
refuses: a retry count of zero. No ERP is connected to the scratch plant, and
the screen that lists the queue shows file paths, so both are said in the
film's own lines. No plant and no browser: the scene is loaded and walked the
way the recorder walks it.
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
SCENE = REPO / "scripts" / "scenes" / "course" / "06-04-the-erp-handoff.toml"
ERP = REPO / "src" / "fsmes" / "services" / "erp.py"
CONTRACT = REPO / "src" / "fsmes" / "integrations" / "erp" / "contract.py"
CHECK = REPO / "src" / "fsmes" / "pack" / "check.py"
EXAMPLE = REPO / "docs" / "operate" / "confirmation-examples" / "operation-with-scrap-and-components.json"
ORDER = "WO-FORK1-0001"

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


def test_the_scene_is_a_full_take_of_class_5_4_named_by_its_label_running_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "5.4" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= 345 and scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_erp_handoff_folder_of_the_running_the_plant_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "06-running-the-plant/04-the-erp-handoff"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["1.4"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "who-tells-the-office"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_the_four_words_and_each_is_explained_the_first_time_it_is_said(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]
    words = [line.split(":")[0] for line in cards[0]["lines"]]
    idea = said(scene, "idea")

    assert words == ["ERP", "Confirmation", "Order completion", "Queue"]
    for word, starts in (("confirmation", "A confirmation is "), ("order completion", "An order completion is "),
                         ("queue", "And the queue, the outbox, is ")):
        first = next(line for line in said(scene) if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in idea
    # The ERP is first named on the hook's card and explained on the idea card.
    assert idea[0] == "Four words. The ERP is the office's system for stock, cost and money."


def test_the_two_ways_across_are_the_file_exchange_and_the_live_connector_and_no_money_crosses(scene):
    card = [step for step in steps_of(scene, "idea") if step["do"] == "slide"][1]
    erp = ERP.read_text(encoding="utf-8")

    assert [line.split(":")[0] for line in card["lines"]] == ["File exchange", "Live connector",
                                                               "Either way, no money crosses"]
    # The contract says so, and none of its worked examples carries a price, an amount or a currency.
    assert "No money." in CONTRACT.read_text(encoding="utf-8")
    keys = [key for path in sorted(EXAMPLE.parent.glob("*.json"))
            for key in json.loads(path.read_text(encoding="utf-8"))]
    assert keys and not [key for key in keys if any(word in key for word in ("price", "amount", "currency"))]
    assert "operation_confirmation" in erp and "enqueue_confirmation" in erp


def test_the_confirmation_card_reads_the_products_worked_example_and_says_whose_it_is(scene):
    card = [step for step in steps_of(scene, "idea") if step["do"] == "slide"][2]
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    assert "worked example" in card["title"]
    assert card["lines"][0] == (f"Step {example['seq']}, {example['operation']}: {example['input_qty']:.0f} put in, "
                                f"{example['good_qty']:.0f} good, {example['scrap_qty']:.0f} scrap")
    assert card["lines"][1].startswith(f"{example['machine_seconds'] / 60:.0f} minutes of machine time, "
                                       f"{example['labour_seconds'] / 60:.0f} of labour, on cost centre "
                                       f"{example['cost_center']}")
    assert card["lines"][2] == "Lots used: " + " and ".join(c["lot"] for c in example["components"])
    assert example["message_key"] == f"{example['order']}:op{example['seq']}"
    assert any("a drinks line, not this plant" in line for line in said(scene, "idea"))


def test_it_signs_in_as_the_lab_admin_who_holds_what_this_plant_asks_of_its_erp(scene):
    assert "erp.define" in BUILTIN_ROLES["admin"]["capabilities"]
    assert scene["user"] == "ADMIN"
    assert not [s for s in scene["step"] if s["do"] == "sign_in"]
    assert not [s for s in scene["step"] if s["do"] == "goto" and "login" in s["url"]]


def test_it_rolls_once_the_first_fork_order_is_finished_and_reads_its_three_steps(scene):
    screen = steps_of(scene, "screen")
    typed = {s["selector"]: s["text"] for s in screen if s["do"] == "fill"}
    finished = [s["js"] for s in screen if s["do"] == "wait_for" and "#d-status" in s.get("js", "")]

    assert scene["plant"]["roll_when_order"] == [{"code": ORDER, "completed": True}]
    assert typed == {"#f-text": ORDER}
    assert len(finished) == 1 and "'completed'" in finished[0] and "length === 3" in finished[0]
    assert any(s.get("at") == "#consumed" for s in screen) and any(s.get("at") == "#produced" for s in screen)
    # Class 1.4 is pointed at in one caption, not repeated.
    assert len([line for line in said(scene) if "one point four" in line]) == 1


def test_it_never_opens_the_ops_screen_whose_first_panel_shows_this_machines_paths(scene):
    pages = [s.get("url", "") for s in scene["step"] if s["do"] == "goto"]
    pressed = [s["selector"] for s in scene["step"] if s["do"] == "click"]

    assert pages == ["/dashboard"]
    assert not [p for p in pressed if "Ops" in p or "Setup" in p]
    assert not [s for s in scene["step"] if "erp-" in s.get("selector", "") + s.get("at", "") + s.get("js", "")]


def test_the_retries_said_are_the_products_shipped_defaults(scene):
    lines = " ".join(said(scene, "screen"))
    erp = ERP.read_text(encoding="utf-8")

    assert "MAX_ATTEMPTS = 8" in erp and "BASE_BACKOFF_SECONDS = 5" in erp and "MAX_BACKOFF_SECONDS = 3600" in erp
    assert "base * 2 ** max(0, attempts - 1)" in erp
    assert "at most eight times" in lines and "five seconds, then twice as long" in lines
    assert "never more than an hour" in lines


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "no ERP is connected to it. Nothing is being sent." in lines
    assert "It says sending even when the ERP is owed nothing. A gap, written up." in lines
    assert "opens on this computer's file paths, so it is not filmed. A gap, written up." in lines
    assert "we cannot show one sent, or one dead. Said, not filmed." in lines
    assert "but on no screen" in lines
    assert "simulated plant at ten times the clock, with no ERP connected" in lines
    assert "simulated plant" in honesty and "ten times" in honesty
    assert "said, not filmed" in honesty


def test_what_it_refuses_is_a_confirmation_offered_zero_times_in_under_twenty_five_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    typed = [(s["selector"], s["text"]) for s in refuses if s["do"] == "fill"]
    refused = [s for s in refuses if s["do"] == "wait_for" and s.get("selector") == "span.refused"]

    assert runner.planned_seconds(dict(scene, step=refuses)) < 25
    assert typed == [('input[data-setting-key="max_attempts"]', "0")]
    assert len(refused) == 1 and refused[0]["text"] == "it has to be above 0"
    assert "and it has to be {bound}: {why}." in CHECK.read_text(encoding="utf-8")
    assert "a confirmation is offered at least once" in CHECK.read_text(encoding="utf-8")


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
    visited |= {"/dashboard/orders", "/dashboard/config/supply_chain"}

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
    assert "nothing is written" in scene["what_is_written"]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("slide") == 5 and "terminal" not in handed
