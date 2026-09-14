"""A fleet console asked about plants that really start and really stop.

The console's whole promise is decision 0023's: a plant that did not answer is
**unknown**, never healthy and never down. Nothing in this repository tested
that against plants that come up and go away, because a console needs several
plants and a lab run has always had exactly one at a time.

Which turns out to be the fixture. The lab runs its plants one after another,
so during a two-plant experiment exactly one is answering and the other is not —
the console's hardest case, arriving for free.

Nothing here starts a console. The measurement is a pure function of the phases
a run recorded, which is the point of keeping the two apart: the arithmetic can
be argued with in a test before anybody argues with it in a fleet.
"""


from fsmes.lab import measure


def _phase(name, running, listed, answered, unknown, states, plants=None, why=None) -> dict:
    return {
        "phase": name, "at": "2026-09-14T12:00:00",
        "running": list(running), "listed": list(listed),
        "totals": {"plants": len(listed) if plants is None else plants,
                   "answered": answered, "unknown": unknown},
        "says": f"{len(listed)} plants, {answered} answered, {unknown} unknown",
        "plants": {name: {"answered": state == "answered", "state": state,
                          "why": None, "owned": "yes"}
                   for name, state in states.items()},
        "unknown_because": why,
    }


THE_RUN = [
    _phase("before any plant had started", [], [], 0, 0, {}),
    _phase("while bottling was running", ["bottling"], ["bottling"], 1, 0,
           {"bottling": "answered"}),
    _phase("after bottling had been torn down", [], ["bottling"], 0, 1,
           {"bottling": "unknown"}),
    _phase("while machining was running", ["machining"], ["bottling", "machining"], 1, 1,
           {"bottling": "unknown", "machining": "answered"}),
    _phase("after every plant had stopped", [], ["bottling", "machining"], 0, 2,
           {"bottling": "unknown", "machining": "unknown"}),
]


def test_a_run_where_the_console_was_right_at_every_phase_says_so():
    seen = measure.console(THE_RUN, plants_total=2)
    assert seen["phases_total"] == 5
    assert seen["phases_answered"] == 5
    assert seen["phases_where_the_count_matched"] == 5
    assert seen["stopped_plants_not_read_as_unknown"] == 0
    assert seen["never_said_down"] is True


def test_the_phase_where_one_plant_is_up_and_one_is_gone_is_the_one_worth_having():
    """The lab runs its plants one after another, so this phase happens on
    every several-plant run without anybody arranging it."""
    seen = measure.console(THE_RUN, plants_total=2)
    row = next(r for r in seen["phases"] if r["phase"] == "while machining was running")
    assert row["plants_really_running"] == 1
    assert row["console_answered"] == 1
    assert row["console_unknown"] == 1
    assert row["what_it_called_them"] == {"bottling": "unknown", "machining": "answered"}
    assert row["stopped_plants_read_unknown"] is True


def test_a_stopped_plant_the_console_called_down_is_a_finding_with_the_phase():
    """Calling a plant nobody could reach *down* invents a breakdown out of a
    network. It is the changeover fault at fleet scale."""
    phases = [_phase("while machining was running", ["machining"], ["bottling", "machining"],
                     1, 0, {"bottling": "down", "machining": "answered"})]
    seen = measure.console(phases, plants_total=2)
    assert seen["stopped_plants_not_read_as_unknown"] == 1
    assert seen["never_said_down"] is False
    row = seen["phases"][0]
    assert row["stopped_plants_read_unknown"] is False
    assert row["stopped_plants_read_otherwise"] == ["bottling"]


def test_a_stopped_plant_the_console_called_healthy_is_the_same_finding():
    phases = [_phase("after bottling had been torn down", [], ["bottling"], 1, 0,
                     {"bottling": "answered"})]
    seen = measure.console(phases, plants_total=1)
    assert seen["stopped_plants_not_read_as_unknown"] == 1
    assert seen["phases_where_the_count_matched"] == 0


def test_a_console_that_did_not_answer_is_unknown_and_not_a_pass():
    phases = [_phase("while bottling was running", ["bottling"], ["bottling"], None, None, {},
                     why="the console did not answer when it was asked: URLError: refused")]
    seen = measure.console(phases, plants_total=1)
    assert seen["phases_answered"] == 0
    assert seen["phases_where_the_count_matched"] == 0
    assert seen["never_said_down"] is None
    assert "URLError" in seen["phases"][0]["unknown_because"]


def test_a_withheld_run_makes_every_phase_unknown_with_the_same_reason():
    why = "the harness fell behind its own 200 ms sample"
    seen = measure.console(THE_RUN, plants_total=2, reason=why)
    assert seen["unknown_because"] == why
    assert seen["phases_answered"] == 0
    assert all(row["unknown_because"] == why for row in seen["phases"])
    assert all(row["answered_matches"] is None for row in seen["phases"])
    assert all(row["stopped_plants_read_unknown"] is None for row in seen["phases"])


def test_a_plant_the_console_had_not_been_told_about_is_not_counted_as_unknown():
    """Not yet built and not answering are different facts. The console can
    only report what it was told about, and the measurement says how much of
    the plan that was."""
    seen = measure.console(THE_RUN, plants_total=2)
    row = next(r for r in seen["phases"] if r["phase"] == "while bottling was running")
    assert row["plants_in_the_plan"] == 2
    assert row["plants_the_console_knew_of"] == 1
    assert row["console_unknown"] == 0


# --------------------------------------------- what reaches the roll-up


def test_a_stopped_plant_read_as_anything_else_reaches_the_differences():
    phases = [_phase("while machining was running", ["machining"], ["bottling", "machining"],
                     1, 0, {"bottling": "down", "machining": "answered"})]
    found, missing = measure.console_findings(measure.console(phases, plants_total=2))
    assert len(found) == 1
    assert found[0]["what"] == "a plant nobody could reach, not read as unknown"
    assert "never healthy and never down" in found[0]["says"]
    assert found[0]["plant"] == "bottling"
    assert missing == []


def test_a_count_that_did_not_match_reaches_the_differences_with_both_numbers():
    phases = [_phase("while bottling was running", ["bottling"], ["bottling"], 0, 1,
                     {"bottling": "unknown"})]
    found, _ = measure.console_findings(measure.console(phases, plants_total=1))
    counts = [row for row in found if row["what"] == "the count of answering plants"]
    assert len(counts) == 1
    assert counts[0]["numbers"] == {"phase": "while bottling was running",
                                    "console_answered": 0, "really_running": 1}


def test_a_run_where_the_console_was_right_reaches_neither_list():
    found, missing = measure.console_findings(measure.console(THE_RUN, plants_total=2))
    assert found == []
    assert missing == []


def test_a_console_nobody_could_ask_reaches_the_unknowns():
    phases = [_phase("while bottling was running", ["bottling"], ["bottling"], None, None, {},
                     why="the console did not answer when it was asked: URLError: refused")]
    found, missing = measure.console_findings(measure.console(phases, plants_total=1))
    assert found == []
    assert len(missing) == 1
    assert "did not answer" in missing[0]["because"]


def test_a_withheld_run_reaches_the_unknowns_once_and_the_differences_never():
    seen = measure.console(THE_RUN, plants_total=2, reason="the harness fell behind")
    found, missing = measure.console_findings(seen)
    assert found == []
    assert [row["because"] for row in missing] == ["the harness fell behind"]


# ---------------------------------------------------------- the report


def test_the_report_names_the_phase_and_what_the_console_called_each_plant():
    from fsmes.lab import report as lab_report

    page = lab_report._console(measure.console(THE_RUN, plants_total=2))
    assert "What the fleet console counted" in page
    assert "while machining was running" in page
    assert "every stopped plant read unknown" in page
    assert "a defect at any value above zero" in page


def test_the_report_marks_a_stopped_plant_the_console_did_not_call_unknown():
    from fsmes.lab import report as lab_report

    phases = [_phase("while machining was running", ["machining"], ["bottling", "machining"],
                     1, 0, {"bottling": "down", "machining": "answered"})]
    page = lab_report._console(measure.console(phases, plants_total=2))
    assert "bottling" in page
    assert "bad" in page


# ------------------------------------------------------------- the plan


def test_a_plan_may_ask_for_the_console_now_and_is_not_refused_by_name(tmp_path):
    import json

    from fsmes.lab.plan import read_plan

    pack = tmp_path / "tiny"
    pack.mkdir()
    (pack / "plant.toml").write_text(
        '[pack]\nformat = 1\n\n[plant]\nname = "tiny"\ntimezone = "UTC"\n\n'
        '[files]\ntag_map = "tag_map.json"\nreplay_dir = "out"\nmasterdata = "md"\n',
        encoding="utf-8")
    (pack / "tag_map.json").write_text("{}", encoding="utf-8")
    (pack / "md").mkdir()
    (pack / "line.json").write_text(json.dumps({"duration_s": 60, "stations": []}),
                                    encoding="utf-8")
    (pack / "out").mkdir()
    path = tmp_path / "plan.toml"
    path.write_text('packs = ["tiny"]\nmeasure = ["console"]\n', encoding="utf-8")
    assert read_plan(path).measure == ("console",)


def test_quality_is_still_refused_by_name_and_says_what_it_is(tmp_path):
    """The one measurement still named in the design and unbuilt. Refused with
    what it is rather than told it does not exist."""
    from fsmes.lab.plan import PLANNED

    assert "quality" in PLANNED
    assert "console" not in PLANNED
    assert "latency" not in PLANNED
