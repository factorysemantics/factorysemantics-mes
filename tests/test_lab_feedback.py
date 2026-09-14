"""The feedback loop: a note left at a screen, placed in the run that caused it.

Scott's ask, on 2026-09-14: "I want to ensure that I can provide UI feedback
within each lab and that that feedback is rolled back up into the analysis."
These pin the four things that makes that trustworthy - the note is tagged to
the run without anybody typing a run id, it is placed at the second of the
line it was made at, it is printed verbatim beside the screen's own numbers,
and the roll-up across runs cites everything and judges nothing.

Nothing here starts a plant or needs a model.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fsmes.lab import feedback, review
from fsmes.lab import report as lab_report
from fsmes.lab import run as lab_run
from fsmes.lab.plan import Plan, read_plan
from fsmes.services import design

LINE = {
    "channel": "Tiny",
    "duration_s": 600,
    "events": [
        {"type": "down", "station": "Cut", "start": 300, "end": 360},
        {"type": "changeover", "start": 500, "end": 540},
    ],
}

T0 = datetime(2026, 9, 14, 12, 0, 0)


def _said(seconds_after_t0: float) -> str:
    """A design-store stamp: aware UTC, which is the other clock in this file."""
    return (T0.replace(tzinfo=UTC) + timedelta(seconds=seconds_after_t0)).isoformat()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "STORE", tmp_path / "design.db")
    yield


# --------------------------------------------------------------- the moment

def test_a_note_is_placed_at_the_line_second_it_was_made_at():
    """Ten seconds of wall clock at 10x is a hundred seconds of line time. The
    person is watching the line, so the line's clock is the one that matters."""
    placed = feedback.moment(LINE, T0, 10.0, _said(31))
    assert placed["line_second"] == 310
    assert placed["events"] == [
        {"type": "down", "station": "Cut", "start": 300, "end": 360}]
    assert "down at Cut" in feedback.says(placed)


def test_a_note_made_when_nothing_was_scripted_says_the_line_was_running_to_script():
    placed = feedback.moment(LINE, T0, 10.0, _said(10))
    assert placed["events"] == []
    assert "running to script" in feedback.says(placed)


def test_a_note_made_before_the_first_tick_says_so_rather_than_being_clamped():
    """A remark about a plant that had not started is a different remark, and
    rounding it to second zero would file it against an event it never saw."""
    placed = feedback.moment(LINE, T0, 10.0, _said(-12))
    assert placed["line_second"] < 0
    assert placed["phase"] == "before the replay's first tick"


def test_a_note_made_after_the_script_ran_out_says_that_too():
    placed = feedback.moment(LINE, T0, 10.0, _said(120))
    assert "after the scripted 600 s had finished" in feedback.says(placed)


def test_a_run_that_never_recorded_its_first_tick_makes_the_moment_unknown_not_zero():
    placed = feedback.moment(LINE, None, 10.0, _said(31))
    assert placed["line_second"] is None
    assert "unknown" in feedback.says(placed)


def test_the_two_clocks_are_reconciled_rather_than_compared_as_written():
    """The run stamps naive UTC and the design store stamps aware UTC. Taking
    them at face value shifted a whole scored window by this box's offset once
    already, so the conversion is asserted rather than assumed."""
    aware = feedback.moment(LINE, T0, 1.0, _said(60))
    naive = feedback.moment(LINE, T0, 1.0, (T0 + timedelta(seconds=60)).isoformat())
    assert aware["line_second"] == naive["line_second"] == 60


# ----------------------------------------------------------------- tagging

def test_a_conversation_is_tagged_to_the_run_and_the_screen():
    conversation = design.start(route="/dashboard/orders", plant="run.db", who="SCOTT",
                               title="the list does not say how many",
                               lab_run="2026-09-14-tiny", lab_plant="tiny",
                               screen="the work orders screen")
    design.add_turn(conversation, "user", "the list does not say how many there are")
    found = design.for_lab_run("2026-09-14-tiny")
    assert [row["id"] for row in found] == [conversation]
    assert found[0]["screen"] == "the work orders screen"
    assert found[0]["lab_plant"] == "tiny"
    assert [t["text"] for t in found[0]["turns"]] == [
        "the list does not say how many there are"]


def test_a_tag_is_never_cleared_by_leaving_it_out():
    """The panel knows the screen and the run knows the moment. Neither may
    erase what the other recorded."""
    conversation = design.start(route="/dashboard", plant=None, who="SCOTT", title="hm",
                                lab_run="r1", lab_plant="tiny", screen="the floor dashboard")
    design.tag(conversation, moment="line second 310")
    row = design.for_lab_run("r1")[0]
    assert row["screen"] == "the floor dashboard"
    assert row["moment"] == "line second 310"


def test_an_unknown_tag_is_refused_rather_than_silently_dropped():
    conversation = design.start(route="/dashboard", plant=None, who="SCOTT", title="hm",
                                lab_run="r1")
    with pytest.raises(ValueError, match="unknown tag"):
        design.tag(conversation, shift="nights")


def test_a_store_written_before_the_tags_existed_is_migrated_not_replaced():
    """A design database is weeks of somebody's notes. Losing them to add a
    column would be a poor trade."""
    import sqlite3

    design.STORE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(design.STORE)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                 " started_at TEXT NOT NULL, updated_at TEXT NOT NULL, plant TEXT,"
                 " route TEXT NOT NULL, title TEXT, who TEXT)")
    conn.execute("INSERT INTO conversations (started_at, updated_at, route, title)"
                 " VALUES ('2026-09-02','2026-09-02','/dashboard','an old note')")
    conn.commit()
    conn.close()

    conversation = design.start(route="/dashboard", plant=None, who="SCOTT", title="a new one",
                                lab_run="r1")
    assert conversation == 2
    assert any(c["title"] == "an old note" for c in design.conversations())


def test_the_design_store_is_not_moved_or_emptied_by_a_run(tmp_path):
    """The run takes a copy. The notes belong to the person, not to a
    results directory."""
    conversation = design.start(route="/dashboard", plant=None, who="SCOTT",
                                title="the tiles are too big", lab_run="r1", lab_plant="tiny",
                                screen="the floor dashboard")
    design.add_turn(conversation, "user", "the tiles are too big")
    said = feedback.gather("r1", {"tiny": {"started_at": T0.isoformat(), "speed": 10.0}})
    feedback.write(tmp_path, said)
    assert (tmp_path / "feedback" / "conversations.jsonl").is_file()
    assert design.for_lab_run("r1"), "the store still holds the conversation"


# ------------------------------------------------------- what the run sets

def _plan(**kwargs) -> Plan:
    return Plan(name="tiny", path=Path("plan.toml"), packs=(), measure=("booking",),
                speed=10.0, **kwargs)


def test_a_lab_plant_has_the_design_chat_on_and_claude_off():
    """The local model is enough to take a note, and a run left going while
    somebody makes coffee should not be billing an API."""
    env = lab_run.design_env(_plan(), "2026-09-14-tiny", "tiny")
    assert env["MES_DESIGN_CHAT"] == "1"
    assert env["MES_DESIGN_CLAUDE"] == "0"
    assert env["MES_LAB_RUN"] == "2026-09-14-tiny"
    assert env["MES_LAB_PLANT"] == "tiny"


def test_a_plan_may_ask_for_claude_and_may_turn_the_panel_off():
    assert lab_run.design_env(_plan(feedback_claude=True), "r", "p")["MES_DESIGN_CLAUDE"] == "1"
    off = lab_run.design_env(_plan(feedback_chat=False), "r", "p")
    assert off == {"MES_DESIGN_CHAT": "0"}


def test_a_plan_that_misspells_a_feedback_setting_is_refused_by_name(tmp_path):
    plan = tmp_path / "plan.toml"
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "plant.toml").write_text("[plant]\nname='tiny'\n", encoding="utf-8")
    plan.write_text('packs = ["pack"]\n[feedback]\nclaud = true\n', encoding="utf-8")
    with pytest.raises(Exception, match="claud"):
        read_plan(plan)


# ------------------------------------------------------------- the report

def _scores(directory: Path) -> dict:
    return {
        "experiment": "tiny", "plan": "plan.toml", "product_version": "0.2.0",
        "started_at": T0.isoformat(), "finished_at": T0.isoformat(), "wall_seconds": 60,
        "speed": 10.0, "seed": 3, "measurements_asked_for": ["booking"],
        "plants_total": 1, "plants_run": 1, "note": "",
        "plants": [{
            "plant": "tiny", "label": "A tiny plant", "pack": "packs/tiny",
            "started_at": T0.isoformat(), "collected_at": T0.isoformat(),
            "line_json": "line/tiny.json",
            "seed": 3, "duration_line_seconds": 600, "speed": 10.0,
            "replay_overlap_line_seconds": 0, "wall_seconds_played": 68.0,
            "pipeline": {"sustained": True}, "verdict_withheld": None, "triage": {},
            "views_refused": {},
            "measurements": {"booking": {
                "question": "Did the MES book what the line made?",
                "overlap_line_seconds": 0,
                "stations_total": 1,
                "stations": [{"station": "Cut", "equipment": "CUT01", "truth_good": 100,
                              "expected_range": [100, 100], "mes_good": 97, "difference": -3,
                              "truth_scrap": 0, "mes_scrap": 0, "verdict": "3 under the range"}],
                "orders": {"why": "no order tag", "mes_orders": [], "mes_orders_total": 0,
                           "truth_orders_total": 0, "truth_good_by_order": {}},
            }},
        }],
    }


def _run_directory(tmp_path: Path, name: str, text: str, route: str) -> Path:
    directory = tmp_path / name
    (directory / "line").mkdir(parents=True)
    (directory / "line" / "tiny.json").write_text(json.dumps(LINE), encoding="utf-8")
    (directory / "scores.json").write_text(json.dumps(_scores(directory)), encoding="utf-8")
    conversation = design.start(route=route, plant=None, who="SCOTT", title=text,
                                lab_run=name, lab_plant="tiny",
                                screen="the work orders screen"
                                       if "orders" in route else "the floor dashboard")
    design.add_turn(conversation, "user", text)
    scores = json.loads((directory / "scores.json").read_text(encoding="utf-8"))
    said = feedback.gather(name, feedback.plants_of(scores, directory))
    feedback.write(directory, said)
    return directory


def test_a_note_appears_in_the_report_beside_the_screens_own_numbers(tmp_path):
    """The whole point of the loop: what he said about the work orders screen
    is rendered under the booking table, not in an appendix."""
    directory = _run_directory(tmp_path, "2026-09-14-tiny",
                               "this list does not say how many orders there are",
                               "/dashboard/orders")
    page = lab_report.write(directory).read_text(encoding="utf-8")
    assert "this list does not say how many orders there are" in page
    booking_at = page.index("Booking honesty")
    note_at = page.index("this list does not say how many orders there are")
    assert booking_at < note_at, "the note belongs under the section it is about"
    assert "SCOTT" in page


def test_a_note_about_a_screen_with_no_measurement_is_still_printed(tmp_path):
    directory = _run_directory(tmp_path, "2026-09-14-tiny",
                               "the tiles are too big to read from the aisle", "/dashboard")
    page = lab_report.write(directory).read_text(encoding="utf-8")
    assert "the tiles are too big to read from the aisle" in page
    assert "other screens" in page


def test_the_report_still_opens_on_a_machine_with_no_network(tmp_path):
    directory = _run_directory(tmp_path, "2026-09-14-tiny", "a note", "/dashboard/orders")
    page = lab_report.write(directory).read_text(encoding="utf-8")
    assert "http://" not in page and "https://" not in page


# ------------------------------------------------------------- the roll-up

def test_review_over_two_runs_cites_both_and_quotes_the_notes_verbatim(tmp_path):
    first = _run_directory(tmp_path, "2026-09-13-tiny", "the totals are not on screen",
                           "/dashboard/orders")
    second = _run_directory(tmp_path, "2026-09-14-tiny", "the totals are not on screen either",
                            "/dashboard/orders")
    out, collected = review.write([first, second], tmp_path / "findings.md")
    text = out.read_text(encoding="utf-8")

    assert "2026-09-13-tiny" in text and "2026-09-14-tiny" in text
    assert "> the totals are not on screen" in text
    assert "> the totals are not on screen either" in text
    # The same difference in both runs is one cluster naming two runs.
    assert "2 row(s) across 2 run(s)" in text
    assert len(collected["runs"]) == 2


def test_the_roll_up_never_says_which_side_is_right(tmp_path):
    """The runs state the truth and the MES's answer. Putting several of them
    beside each other adds no authority to either."""
    first = _run_directory(tmp_path, "2026-09-13-tiny", "a note", "/dashboard/orders")
    out, _ = review.write([first], tmp_path / "findings.md")
    text = out.read_text(encoding="utf-8").lower()
    for word in ("is wrong", "is incorrect", "the mes is right", "should be", "bug in"):
        assert word not in text, f"the roll-up said {word!r}"


def test_the_roll_up_needs_no_model_and_is_the_same_twice(tmp_path):
    """CI has no Ollama, and a committed file whose order moves between two
    identical runs makes every diff unreadable."""
    first = _run_directory(tmp_path, "2026-09-13-tiny", "a note", "/dashboard/orders")
    once = review.render(review.collect([first]))
    twice = review.render(review.collect([first]))
    def body(text):
        return [line for line in text.splitlines() if not line.startswith("Written ")]

    assert body(once) == body(twice)


def test_a_model_that_offers_a_verdict_instead_of_a_heading_is_ignored():
    rows = [{"run": "r1", "says": "-3 outside the range 100 to 100"}]
    key = ("booking", "units booked", "Cut")
    assert review.titled("difference", key, rows, ask=lambda p: "The MES is wrong about Cut") \
        == "booking · units booked at Cut"
    assert review.titled("difference", key, rows, ask=lambda p: "Cut books fewer than the script") \
        .startswith("Cut books fewer than the script")


def test_a_directory_that_is_not_a_run_is_refused_by_name(tmp_path):
    (tmp_path / "not-a-run").mkdir()
    with pytest.raises(review.ReviewError, match=r"scores\.json"):
        review.collect([tmp_path / "not-a-run"])


def test_every_unknown_a_run_recorded_reaches_the_roll_up(tmp_path):
    directory = _run_directory(tmp_path, "2026-09-14-tiny", "a note", "/dashboard/orders")
    scores = json.loads((directory / "scores.json").read_text(encoding="utf-8"))
    scores["plants"][0]["verdict_withheld"] = "the harness fell behind its own sample"
    (directory / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    out, collected = review.write([directory], tmp_path / "findings.md")
    assert "the harness fell behind its own sample" in out.read_text(encoding="utf-8")
    assert collected["unknowns"]
