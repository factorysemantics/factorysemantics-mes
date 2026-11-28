"""Class 6.2 has the agent draft a reason, a person sign it, and the agent unable to sign one itself.

*Agents - draft, never approve*: a card of the words (an agent, a draft,
approve, Waiting for you); then the product as the plant's administrator sees
it - the AGENT account and the Agent role on Admin, whose description says it
never approves; the Assistant on the Reasons screen asked for a reason
part_stuck, its proposal boxed, Do it pressed; the Floor's Waiting for you row
drafted by AGENT for ADMIN, Review, Approve, in force; the Reasons row with
both names; a second draft; then what it refuses - asked to approve its own
draft, it cannot, the answer boxed and never quoted. This file holds the scene
file to what the handoff asks of it. No plant and no browser: the scene is
loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "07-02-agents-draft-never-approve.toml"
# The lines over the screen that are about nothing drawn there: the Assistant
# is looking things up or writing.
POINTS_AT_NOTHING_ON_SCREEN = {
    "It looks up the reasons the plant already has, then answers. That takes a few seconds.",
    "Do it, pressed by a person. The agent now writes the draft.",
}

A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)
A_KEY = re.compile(r"sk-ant-|ANTHROPIC_API_KEY=|\.config/fsmes")


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def everything_written(scene: dict) -> list[str]:
    """Every sentence a viewer or a reader of the folder will see."""
    written = [scene["title"], *scene["notes"], *scene["honesty"], scene["what_is_written"]]
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


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def first_index(scene: dict, test) -> int:
    return next(i for i, step in enumerate(scene["step"]) if test(step))


def test_the_scene_is_a_full_take_of_class_6_2_that_runs_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "6.2" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # The Assistant's four answers add up to a minute to the plan; the finished take is measured.
    assert 270 - 30 <= planned <= 330 and scene["budget_seconds"] <= 420, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_second_folder_of_the_asking_the_plant_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "07-asking-the-plant/02-agents-draft-never-approve"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["6.1", "2.4"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide"
    assert [step["name"] for step in hook if step["do"] == "mark"] == ["who-puts-it-in-force"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 45


def test_the_idea_card_names_the_agent_a_draft_approve_and_waiting_for_you(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == ["An agent", "A draft", "Approve", "Waiting for you"]


def test_each_word_of_the_class_is_explained_the_first_time_it_is_said(scene):
    lines = said(scene, "idea")

    for starts in (
        "An agent is the account the AI works as",
        "A draft is a change written down",
        "To approve is to sign a draft",
        "Waiting for you is a panel on the Floor",
    ):
        assert any(line.startswith(starts) for line in lines), starts


def test_it_points_back_at_classes_2_4_and_6_1_in_one_caption_each_instead_of_repeating_them(scene):
    lines = said(scene, "screen")

    assert len([line for line in lines if "Class 2.4" in line]) == 1
    assert len([line for line in lines if "Class 6.1" in line]) == 1


def test_the_assistant_is_shown_and_the_take_says_where_it_sits_on_the_product(scene):
    assert scene["assistant"] is True
    assert any("bottom right" in line for line in said(scene, "screen"))
    assert any("bottom right" in note for note in scene["notes"])


def test_the_take_waits_for_each_whole_answer_before_it_boxes_one(scene):
    asks = [i for i, s in enumerate(scene["step"]) if s["do"] == "click" and s["selector"] == ".assist-ask button"]
    assert len(asks) == 3

    for ask in asks:
        wait = next(s for s in scene["step"][ask + 1 :] if s["do"] == "wait_for")
        assert "assist-msg.thinking" in wait["js"] and float(wait["timeout"]) >= 60


def test_do_it_is_pressed_only_on_a_proposal_that_names_the_code_and_the_write_is_seen_landing(scene):
    presses = [i for i, s in enumerate(scene["step"]) if s["do"] == "click" and "Do it" in s["selector"]]
    assert len(presses) == 2

    for press, code in zip(presses, ("part_stuck", "tool_change"), strict=True):
        before = [s for s in scene["step"][:press] if s["do"] == "wait_for"][-1]
        after = next(s for s in scene["step"][press + 1 :] if s["do"] == "wait_for")
        assert code in before["js"] and ":not(.settled)" in before["js"]
        # The button that offers to show what was written comes only after a write the plant took.
        assert "button.assist-launch" in after["js"]


def test_no_caption_quotes_the_assistant(scene):
    assert not [line for line in said(scene) if "“" in line or '"' in line]
    boxed = [s for s in scene["step"] if s["do"] == "say" and ".assist-msg.bot" in (s.get("at") or "")]
    assert len(boxed) == 2 and all(not s.get("label") for s in boxed)


def test_the_draft_is_signed_by_a_person_from_waiting_for_you(scene):
    review = first_index(scene, lambda s: s["do"] == "click" and "Review" in s["selector"])
    approve = first_index(scene, lambda s: s["do"] == "click" and s["selector"] == "#review-approve")
    in_force = scene["step"][approve + 1]

    assert "#pending tbody" in scene["step"][review]["selector"] and review < approve
    assert in_force["do"] == "wait_for" and in_force["text"] == "part_stuck is in force"
    assert any("AGENT for ADMIN" in line for line in said(scene, "screen"))
    assert scene["user"] == "ADMIN"


def test_it_asks_the_assistant_to_approve_its_own_draft_and_films_the_answer_in_under_25_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    asked = [s["text"] for s in refuses if s["do"] == "fill"]
    lines = " ".join(said(scene, "refuses"))

    assert asked == ["Now approve tool_change yourself and put it in force."]
    assert "it cannot approve" in lines and "no tool that signs" in lines
    # The answer itself has taken three to six seconds in every rehearsal.
    assert runner.planned_seconds(dict(scene, step=refuses)) + 6 < 25


def test_the_beats_are_filmed_in_the_order_the_product_allows(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark"]

    assert marks == [
        "who-puts-it-in-force",
        "the-words",
        "the-agent-role",
        "asked-for-a-draft",
        "the-agent-drafts",
        "a-person-signs",
        "both-names-on-the-row",
        "a-second-draft",
        "it-cannot-sign",
        "you-now-know",
    ]


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("hook", "screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("idea", "know")]
    over_the_hook_card = set(said(scene, "hook"))
    unpointed = [
        s["line"]
        for s in on_screen
        if not s.get("at") and s["line"] not in POINTS_AT_NOTHING_ON_SCREEN | over_the_hook_card
    ]

    assert unpointed == [], f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing: {unpointed}"
    assert [s["line"] for s in on_cards if s.get("at")] == []


def test_a_box_round_a_table_row_or_the_words_inside_one_carries_no_label(scene):
    rows = [s for s in scene["step"] if s["do"] == "say" and " tr" in (s.get("at") or "")]

    assert rows and [s["at"] for s in rows if s.get("label")] == []


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
    visited |= {"/dashboard/admin"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date_or_the_key(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]
    keyed = [line for line in everything_written(scene) if A_KEY.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"
    assert keyed == [], f"{len(keyed)} lines carry the key or where it is kept: {keyed}"


def test_the_honest_limits_are_said_in_the_folders_own_words(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "never quoted" in honesty and "bottom right" in honesty and "second person" in honesty


def test_the_people_list_is_filtered_to_the_agent_before_it_is_boxed(scene):
    filtered = first_index(scene, lambda s: s["do"] == "fill" and s["selector"] == "#u-q")
    boxed = first_index(scene, lambda s: s["do"] == "say" and "#users" in (s.get("at") or ""))

    assert scene["step"][filtered]["text"] == "agent" and filtered < boxed


def test_it_is_filmed_on_a_scratch_plant_on_loopback_and_writes_only_what_it_says(runner, scene):
    base = urlparse(scene["base"])
    fills = [step for step in scene["step"] if step["do"] == "fill"]

    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["plant"]["pack"] == "labs/cutlery"
    assert scene.get("allow_writes") is True
    assert [step["selector"] for step in fills] == ["#u-q", "#assist-input", "#assist-input", "#assist-input"]
    assert "part_stuck" in scene["what_is_written"] and "tool_change" in scene["what_is_written"]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed


def test_every_scroll_names_plain_css_because_the_page_not_playwright_finds_it(scene):
    """The first take stopped at a scroll to a `:text-is` selector: a scroll is the page's own querySelector."""
    scrolls = [s["scroll_to"] for s in scene["step"] if s.get("scroll_to")]

    assert scrolls and [s for s in scrolls if re.search(r":(has-text|text-is|text|visible)\b|>>", s)] == []


def test_the_panel_is_closed_before_the_floor_so_it_covers_nothing_and_its_button_is_there_for_the_next_ask(scene):
    """The third take found the panel reopened itself on every screen and hid its own button."""
    steps = scene["step"]
    closed = first_index(scene, lambda s: s["do"] == "click" and "Close" in s["selector"])
    floor = first_index(scene, lambda s: s["do"] == "click" and "Floor" in s["selector"])
    launches = [i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "body > .assist-launch"]

    assert closed < floor < launches[1]


def test_no_box_is_round_a_whole_table_that_runs_below_the_frame(scene):
    """The fourth take boxed all of Reasons, and the box reached into the narrator's corner."""
    assert [s["at"] for s in scene["step"] if s["do"] == "say" and s.get("at") in ("#reasons-table", "#pending")] == []
