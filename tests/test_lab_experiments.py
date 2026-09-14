"""`fsmes lab`: the plan, the truth, and the measurements that put them together.

Nothing here starts a plant. Every one of these questions can be asked of the
lab's own machinery with a line description, a temporary directory and a
dictionary shaped like the MES's answer - which is the point of keeping the
measurements pure: a number that can be argued with in a test before anybody
argues with it in a plant.
"""

import json
from pathlib import Path

import pytest

from fsmes.lab import build as builder
from fsmes.lab import measure
from fsmes.lab import report as lab_report
from fsmes.lab import truth as lab_truth
from fsmes.lab.plan import MEASUREMENTS, PlanError, check_names, read_plan
from fsmes.pack import format as fmt
from fsmes.sim.generate import generate

TINY_LINE = {
    "channel": "Tiny",
    "seed": 3,
    "duration_s": 120,
    "orders": [900, 901],
    "buffers": {"capacity": 10, "initial": 5},
    "stations": [
        {"name": "Cut", "rate_per_min": 60, "scrap_pct": 0.0},
        {"name": "Pack", "rate_per_min": 60, "scrap_pct": 0.0},
    ],
    "events": [
        {"type": "down", "station": "Cut", "start": 30, "end": 60},
        {"type": "changeover", "start": 80, "end": 100},
    ],
}

PLANT_TOML = """
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "tiny"
label = "A tiny plant"
timezone = "UTC"

[serve]
api_host = "127.0.0.1"
api_port = 9099
simulate = true

[files]
tag_map = "tag_map.json"
replay_dir = "out"
masterdata = "masterdata"
"""

TAG_MAP = {
    "machines": [
        {"equipment": "CUT01", "object": "Cut", "cycle_seconds": 1.0, "analog": "Value",
         "order_tag": None,
         "state_map": {"0": "idle", "1": "running", "2": "idle", "3": "idle",
                       "4": "down", "5": "setup"}},
        {"equipment": "PACK01", "object": "Pack", "cycle_seconds": 1.0, "analog": "Value",
         "order_tag": None,
         "state_map": {"0": "idle", "1": "running", "2": "idle", "3": "idle",
                       "4": "down", "5": "setup"}},
    ],
}


def _pack(directory: Path, *, masterdata: bool = True) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plant.toml").write_text(PLANT_TOML, encoding="utf-8")
    (directory / "tag_map.json").write_text(json.dumps(TAG_MAP), encoding="utf-8")
    (directory / "line.json").write_text(json.dumps(TINY_LINE), encoding="utf-8")
    if masterdata:
        data = directory / "masterdata"
        data.mkdir(exist_ok=True)
        (data / "equipment.json").write_text(json.dumps([
            {"code": "TINY", "name": "Tiny", "level": "enterprise"},
            {"code": "T1", "name": "Tiny Site", "level": "site", "parent": "TINY"},
            {"code": "TA", "name": "Tiny Area", "level": "area", "parent": "T1"},
            {"code": "TLINE", "name": "Tiny Line", "level": "work_center", "parent": "TA"},
            {"code": "CUT01", "name": "Cut 01", "level": "work_unit", "parent": "TLINE"},
            {"code": "PACK01", "name": "Pack 01", "level": "work_unit", "parent": "TLINE"},
        ]), encoding="utf-8")
    else:
        (directory / "plant.toml").write_text(
            PLANT_TOML.replace('masterdata = "masterdata"\n', ""), encoding="utf-8")
    return directory


def _plan_file(directory: Path, body: str) -> Path:
    path = directory / "experiment.toml"
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------------------------------- the plan

def test_a_plan_that_asks_for_a_measurement_this_version_does_not_take_is_refused_by_name(tmp_path):
    _pack(tmp_path / "tiny")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\nmeasure = ["booking", "latency"]\n')
    with pytest.raises(PlanError) as exc:
        read_plan(path)
    # Refused, and told what it is rather than told it does not exist: the
    # measurement is designed and unbuilt, and a plan silently losing one is
    # how a report comes to say less than it was asked for.
    assert "latency" in str(exc.value)
    assert "not measured yet" in str(exc.value)


def test_an_overlay_may_vary_modules_and_words_and_nothing_else(tmp_path):
    _pack(tmp_path / "tiny")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\n\n[overlay.tiny.serve]\napi_port = 1234\n')
    with pytest.raises(PlanError) as exc:
        read_plan(path)
    assert "overlay.tiny.serve" in str(exc.value)


def test_a_plan_that_names_a_plant_the_experiment_does_not_run_is_refused(tmp_path):
    _pack(tmp_path / "tiny")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\n\n[overlay.bottling.modules]\ncoa = false\n')
    plan = read_plan(path)
    with pytest.raises(PlanError) as exc:
        check_names(plan, {"tiny": tmp_path / "tiny"})
    assert "does not run" in str(exc.value)


def test_an_experiment_that_measures_nothing_is_refused(tmp_path):
    _pack(tmp_path / "tiny")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\nmeasure = []\n')
    with pytest.raises(PlanError):
        read_plan(path)


# ------------------------------------------------------------------ the build

def test_shortening_the_run_past_a_scripted_event_is_refused_rather_than_half_played(tmp_path):
    pack_dir = _pack(tmp_path / "tiny")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\nduration = 50\n')
    plan = read_plan(path)
    with pytest.raises(PlanError) as exc:
        builder.script(plan, fmt.read(pack_dir), tmp_path / "line" / "tiny.json")
    assert "outside the run's 0..50 line seconds" in str(exc.value)


def test_a_pack_with_neither_master_data_nor_a_named_seeder_is_refused_before_anything_starts(tmp_path):
    pack_dir = _pack(tmp_path / "tiny", masterdata=False)
    path = _plan_file(tmp_path, 'packs = ["tiny"]\n')
    plan = read_plan(path)
    with pytest.raises(PlanError) as exc:
        builder.seeding(plan, fmt.read(pack_dir))
    # The refusal names the two ways out, because an empty plant answers every
    # question with nothing and reads as an MES that saw nothing.
    assert "masterdata" in str(exc.value)
    assert "[init.tiny]" in str(exc.value)


def test_a_plan_may_name_the_script_that_seeds_a_plant_a_pack_may_not(tmp_path):
    pack_dir = _pack(tmp_path / "tiny", masterdata=False)
    (tmp_path / "seed_it.py").write_text("print('seeded')\n", encoding="utf-8")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\n\n[init.tiny]\nscript = "seed_it.py"\n')
    plan = read_plan(path)
    assert builder.seeding(plan, fmt.read(pack_dir)) == str(tmp_path / "seed_it.py")


def test_the_runs_copy_of_the_pack_points_at_this_runs_data_and_the_pack_on_disk_is_untouched(tmp_path):
    pack_dir = _pack(tmp_path / "tiny")
    path = _plan_file(tmp_path, 'packs = ["tiny"]\n\n[overlay.tiny.modules]\ncoa = false\n')
    plan = read_plan(path)
    before = (pack_dir / "plant.toml").read_text(encoding="utf-8")
    replay = tmp_path / "results" / "replay" / "tiny"
    generate(pack_dir / "line.json", replay, write_docs=False)
    copy, applied = builder.overlaid_pack(plan, fmt.read(pack_dir),
                                          tmp_path / "results" / "packs" / "tiny", replay)
    compiled = fmt.read(copy)
    assert compiled.table("files")["replay_dir"] == replay.as_posix()
    assert compiled.table("modules") == {"coa": False}
    assert applied == {"modules": {"coa": False}}
    # The whole reason the copy exists.
    assert (pack_dir / "plant.toml").read_text(encoding="utf-8") == before


# ------------------------------------------------------------------ the truth

def test_the_truth_is_read_from_the_data_the_replay_obeyed(tmp_path):
    line = tmp_path / "line.json"
    line.write_text(json.dumps(TINY_LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate(line, out, write_docs=False)
    truth = lab_truth.read(out, TINY_LINE, 120, overlap_s=0)

    cut = truth.stations["Cut"]
    # Thirty seconds scripted down, and a twenty-second changeover the whole
    # line takes. Read off the data rather than off the script, because what
    # the MES saw was the data.
    assert cut.seconds_by_state["down"] == 30
    assert cut.changeover_seconds == 20
    assert cut.good > 0
    assert truth.last_station == "Pack"
    assert truth.line_good == truth.stations["Pack"].good
    # Two orders scripted, and the changeover is what moves between them.
    assert len(truth.good_by_order) == 2


def test_a_counter_that_resets_mid_hour_is_summed_and_not_read_off_the_last_row(tmp_path):
    """The first run of this lab accused the MES of inventing four thousand units.

    The bottling line's script snaps a counter to zero at t+3000, as a PLC does
    after a power blip. After that the last row of the CSV holds what the
    machine has made *since* the reset, and reading it as the hour's total made
    the truth smaller than what the MES had honestly booked. The MES books
    deltas; the truth is summed the same way.
    """
    script = dict(TINY_LINE, events=[
        *TINY_LINE["events"],
        {"type": "counter_reset", "station": "Cut", "at": 100},
    ])
    line = tmp_path / "line.json"
    line.write_text(json.dumps(script), encoding="utf-8")
    out = tmp_path / "out"
    generate(line, out, write_docs=False)

    truth = lab_truth.read(out, script, 120, overlap_s=0)
    import csv as _csv

    with (out / "Cut.csv").open(encoding="ascii", newline="") as handle:
        counts = [int(row["GoodCount"]) for row in _csv.DictReader(handle)]
    drop = next(i for i in range(1, len(counts)) if counts[i] < counts[i - 1])

    # Reading the last row would lose everything the machine made before the
    # blip - here, most of the hour.
    assert counts[-1] < truth.stations["Cut"].good
    # What it actually made is what it had when the counter went, plus what it
    # has made since.
    assert truth.stations["Cut"].good == counts[drop - 1] + counts[-1]


def test_the_overlap_is_what_a_second_pass_replayed_and_is_zero_when_nothing_did(tmp_path):
    line = tmp_path / "line.json"
    line.write_text(json.dumps(TINY_LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate(line, out, write_docs=False)

    assert lab_truth.overlap_seconds(120, 12.0, 10.0) == 0        # exactly one pass
    assert lab_truth.overlap_seconds(120, 13.0, 10.0) == 10       # ten line seconds more
    # A run left playing for hours wraps more than once; the number exists to
    # size a band, so it stops at one full pass rather than reconstructing a
    # replay nobody watched.
    assert lab_truth.overlap_seconds(120, 600.0, 10.0) == 120

    with_overlap = lab_truth.read(out, TINY_LINE, 120, overlap_s=20)
    assert 0 < with_overlap.stations["Cut"].overlap_good <= with_overlap.stations["Cut"].good


# ----------------------------------------------------------- the measurements

def _truth_for(tmp_path) -> lab_truth.LineTruth:
    line = tmp_path / "line.json"
    line.write_text(json.dumps(TINY_LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate(line, out, write_docs=False)
    return lab_truth.read(out, TINY_LINE, 120, overlap_s=10)


def _oee_saying(truth: lab_truth.LineTruth, *, speed: float = 1.0, **per_station) -> dict:
    """What a plant that saw exactly this line would report.

    `speed` is the replay speed to pretend the MES lived through: its run time
    is wall-clock, so at 10x it sees a tenth of the seconds the line had, which
    is the whole reason performance has to be restated before it is compared.
    """
    stations = []
    for name, station in truth.stations.items():
        code = {"Cut": "CUT01", "Pack": "PACK01"}[name]
        stations.append({
            "code": code, "good_qty": station.good, "scrap_qty": station.scrap,
            "availability": 0.5, "performance": 0.5, "quality": 1.0, "oee": 0.25,
            "runtime_seconds": round(station.running_seconds / speed, 1),
            "downtime_seconds": station.down_seconds,
            "ideal_cycle_seconds": station.ideal_cycle_seconds,
            **per_station.get(name, {}),
        })
    return {"stations": stations, "window": {"hours": 120 / 3600}, "line_oee": 0.25,
            "constraint": "CUT01"}


MAPPING = {"Cut": "CUT01", "Pack": "PACK01"}


def test_booking_under_the_truth_is_a_finding_and_booking_inside_the_overlap_is_not(tmp_path):
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    said = _oee_saying(truth, Cut={"good_qty": cut.good - 1})
    out = measure.booking(truth, said, {}, MAPPING, speed=10.0)
    row = next(r for r in out["stations"] if r["station"] == "Cut")
    # The overlap can only ever add, so one unit fewer is real at any speed.
    assert row["verdict"] == "booked 1 fewer than the line made"

    inside = _oee_saying(truth, Cut={"good_qty": cut.good + cut.overlap_good})
    out = measure.booking(truth, inside, {}, MAPPING, speed=10.0)
    row = next(r for r in out["stations"] if r["station"] == "Cut")
    assert row["verdict"] == "inside the replay's overlap band"

    over = _oee_saying(truth, Cut={"good_qty": cut.good + cut.overlap_good + 5})
    out = measure.booking(truth, over, {}, MAPPING, speed=10.0)
    row = next(r for r in out["stations"] if r["station"] == "Cut")
    assert "5 more than the line made" in row["verdict"]


def test_a_station_the_mes_never_reported_is_unknown_and_never_a_zero(tmp_path):
    truth = _truth_for(tmp_path)
    said = _oee_saying(truth)
    said["stations"] = [s for s in said["stations"] if s["code"] != "PACK01"]
    out = measure.booking(truth, said, {}, MAPPING, speed=10.0)
    row = next(r for r in out["stations"] if r["station"] == "Pack")
    assert row["mes_good"] is None
    assert row["verdict"].startswith("unknown")
    assert out["stations_answered"] == 1
    assert out["stations_total"] == 2


def test_a_tag_map_that_does_not_say_where_the_line_publishes_its_order_leaves_it_unknown(tmp_path):
    """No `line` block, no join - and the refusal names what would make one.

    The measurement is allowed to say it cannot match the two numbering
    schemes. It is not allowed to say so without saying what would.
    """
    truth = _truth_for(tmp_path)
    orders = {"items": [{"code": "WO-1", "quantity": 100, "good_qty": 101,
                         "scrap_qty": 0, "over_qty": 1}]}
    out = measure.booking(truth, _oee_saying(truth), orders, MAPPING, speed=10.0)
    assert out["orders"]["tied_to_truth"] is False
    assert "no `line` block" in out["orders"]["why"]
    assert out["orders"]["over_run_in_truth"] is None
    # The over-run the MES reports about itself owes nothing to the overlap
    # band, which is what makes it the sharp reading for the 16-against-15
    # class of fault.
    assert out["orders"]["over_run_reported"] == 1


def test_downtime_is_compared_on_the_lines_own_clock_not_the_wall_clock(tmp_path):
    truth = _truth_for(tmp_path)
    card = {"metrics": {"faults_scripted": 1, "faults_scored": 1, "breakdown_recall": 1.0,
                        "faults_recorded_late": 0, "planned_stops_scripted": 1,
                        "planned_stops_scored": 1, "planned_stop_misclassified": 0},
            "faults": [], "planned_stops": []}
    # The MES lived through three wall seconds of a thirty-line-second stop at
    # ten times speed. Comparing those two numbers unconverted is how a plant
    # comes to look as though it lost a tenth of the downtime it had.
    out = measure.downtime(truth, card, {"total_seconds": 3.0, "unlabelled_share": 1.0}, speed=10.0)
    assert out["total_down"]["mes_line_seconds"] == 30.0
    assert out["total_down"]["truth_line_seconds"] == 30
    assert out["labels"]["truth_unlabelled_share"] is None
    assert "nothing in this run labels a stop" in out["labels"]["unknown_because"]


def test_oee_performance_is_not_compared_when_the_two_sides_rate_the_machine_differently(tmp_path):
    truth = _truth_for(tmp_path)
    said = _oee_saying(truth, Cut={"ideal_cycle_seconds": 2.0})
    out = measure.oee(truth, said, MAPPING, speed=10.0)
    cut = next(r for r in out["stations"] if r["station"] == "Cut")
    pack = next(r for r in out["stations"] if r["station"] == "Pack")
    assert cut["performance_like_for_like"] is False
    assert pack["performance_like_for_like"] is True
    assert out["window"]["mismatch_share"] is not None


def test_the_mes_performance_is_put_on_the_lines_clock_before_it_is_compared(tmp_path):
    """Availability and quality are ratios of two wall-clock numbers, so the
    replay speed cancels out of both. Performance divides line seconds (the
    rated cycle) by wall-clock seconds (the run time), so it does not: a plant
    that saw exactly this line at 10x reports ten times the line's performance.
    Comparing that figure unconverted is how nine stations in two plants came
    to read 1.0 on 2026-09-14 and nobody could see what the cap was hiding."""
    truth = _truth_for(tmp_path)
    said = _oee_saying(truth, speed=10.0)
    out = measure.oee(truth, said, MAPPING, speed=10.0)

    for row in out["stations"]:
        station = truth.stations[row["station"]]
        # The same numbers the line had, restated: rating x units / run time.
        assert row["mes"]["runtime_line_seconds"] == pytest.approx(station.running_seconds, rel=0.01)
        assert row["mes"]["performance_line_clock"] == pytest.approx(row["truth"]["performance"],
                                                                      rel=0.01)
        assert row["difference"]["performance"] == pytest.approx(0.0, abs=0.01)
        assert measure.performance_significant(row) is False


def test_a_station_that_beat_its_rating_is_reported_above_one_not_capped(tmp_path):
    """Neither side caps. The script's own figure can exceed 1.0 too, and the
    row says which side said so rather than trimming either."""
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    said = _oee_saying(truth, speed=10.0,
                       Cut={"performance": 14.0,
                            "performance_note": "rating is slower than the machine",
                            # Twice the units the line made, in the same run time.
                            "good_qty": cut.good * 2})
    out = measure.oee(truth, said, MAPPING, speed=10.0)
    row = next(r for r in out["stations"] if r["station"] == "Cut")

    assert row["mes_performance_above_rated"] is True
    assert row["mes"]["performance_note"] == "rating is slower than the machine"
    assert row["mes"]["performance_line_clock"] > 1.0
    # And the difference is real rather than a floor: it is outside the band.
    assert measure.performance_significant(row) is True


def test_no_number_in_the_mes_block_is_stored_without_saying_which_clock_it_is_on(tmp_path):
    """A stored performance of 19.77 beside a difference of -0.05 is one
    object answering two ways, and the reader has no way to tell which number
    the difference came from. F12, found on 2026-09-14: the file kept the
    wall-clock figure under the plain name while the comparison used the
    line-clock one. So there is no plain name left to pick up by mistake."""
    truth = _truth_for(tmp_path)
    out = measure.oee(truth, _oee_saying(truth, speed=20.0), MAPPING, speed=20.0)
    said = out["stations"][0]["mes"]

    for ambiguous in ("performance", "oee", "runtime_seconds", "downtime_seconds"):
        assert ambiguous not in said, f"{ambiguous} does not say which clock it is on"
    assert said["performance_as_reported"] == 0.5
    assert said["performance_line_clock"] is not None
    assert said["runtime_wall_seconds"] is not None
    assert said["runtime_line_seconds"] == pytest.approx(said["runtime_wall_seconds"] * 20, rel=0.01)
    assert said["downtime_line_seconds"] == pytest.approx(said["downtime_wall_seconds"] * 20,
                                                          rel=0.01)
    # The two the replay speed cancels out of keep their plain names, and
    # there is one of each.
    assert said["availability"] == 0.5
    assert said["quality"] == 1.0


def test_the_mes_own_oee_is_restated_on_the_lines_clock_from_its_own_three_numbers(tmp_path):
    """The MES's OEE carries its wall-clock performance, so it is out by the
    replay speed the same way. Restated here from the MES's own availability,
    its own quality and its own performance on the line's clock - nothing of
    the script's is in it."""
    truth = _truth_for(tmp_path)
    out = measure.oee(truth, _oee_saying(truth, speed=20.0), MAPPING, speed=20.0)
    said = out["stations"][0]["mes"]
    assert said["oee_as_reported"] == 0.25
    assert said["oee_line_clock"] == pytest.approx(
        said["availability"] * said["performance_line_clock"] * said["quality"], rel=0.001)


# ------------------- when the MES's own two numbers do not agree

def test_more_units_than_its_own_run_time_holds_is_named_as_the_mes_against_itself(tmp_path):
    """Northgate's Deburr, 2026-09-14: the MES recorded 826 units and 1,878 s
    of run time for a machine it rates at 2.4 s a unit - which is 1,982 s of
    work inside 1,878 s of run time - while the line, priced at the same 2.4 s,
    fitted its units inside its running seconds. The rating is not the
    explanation and the script is not the other party."""
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    # Same run time as the line had; a fifth more units than fit in it.
    said = _oee_saying(truth, speed=10.0, Cut={"good_qty": int(cut.total * 1.2) - cut.scrap})
    row = next(r for r in measure.oee(truth, said, MAPPING, speed=10.0)["stations"]
               if r["station"] == "Cut")

    assert row["performance_like_for_like"] is True
    assert row["truth"]["performance"] <= 1.0
    assert row["mes"]["performance_line_clock"] > 1.0
    assert row["mes_units_outrun_its_own_runtime"] is True


def test_a_machine_that_really_beat_its_rating_on_both_sides_is_not_that_finding(tmp_path):
    """If the line itself made more units than its rating allows, the MES
    agreeing is agreement. The finding is about the MES's two numbers, not
    about the number being above one."""
    truth = _truth_for(tmp_path)
    out = measure.oee(truth, _oee_saying(truth, speed=10.0), MAPPING, speed=10.0)
    for row in out["stations"]:
        assert row["mes_units_outrun_its_own_runtime"] is False


def test_two_sides_that_rate_the_machine_differently_cannot_make_that_finding(tmp_path):
    """The rating is the shared price. Without it the arithmetic is about
    master data, which the row already says in its own column."""
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    said = _oee_saying(truth, speed=10.0,
                       Cut={"good_qty": int(cut.total * 1.2) - cut.scrap,
                            "ideal_cycle_seconds": cut.ideal_cycle_seconds * 2})
    row = next(r for r in measure.oee(truth, said, MAPPING, speed=10.0)["stations"]
               if r["station"] == "Cut")
    assert row["performance_like_for_like"] is False
    assert row["mes_units_outrun_its_own_runtime"] is None


def test_the_mes_against_itself_reaches_the_differences_above_the_ordinary_rows(tmp_path):
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    said = _oee_saying(truth, speed=10.0, Cut={"good_qty": int(cut.total * 1.2) - cut.scrap})
    plant = {"plant": "tiny",
             "measurements": {"oee": measure.oee(truth, said, MAPPING, speed=10.0)}}
    found = measure.differences(plant)
    outrun = [row for row in found if row["what"] == "more units than its own run time holds"]
    assert len(outrun) == 1
    assert "s of work, recorded inside" in outrun[0]["says"]
    assert outrun[0]["numbers"]["truth_performance"] <= 1.0
    # Ranked above the ordinary OEE differences: this one survives any
    # argument about the truth.
    assert found[0]["what"] == "more units than its own run time holds"


def test_the_report_names_the_station_whose_counts_and_run_time_disagree(tmp_path):
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    said = _oee_saying(truth, speed=10.0, Cut={"good_qty": int(cut.total * 1.2) - cut.scrap})
    page = lab_report._oee(measure.oee(truth, said, MAPPING, speed=10.0))
    assert "Counts and run time that do not agree" in page
    assert "s of run time" in page
    assert "which of the two is the wrong one" in page


def test_a_run_where_everything_agrees_prints_no_such_section(tmp_path):
    """Silent when there is nothing to say: a heading that appears every time
    and is usually empty is a heading the eye learns to skip."""
    truth = _truth_for(tmp_path)
    page = lab_report._oee(measure.oee(truth, _oee_saying(truth, speed=10.0), MAPPING, speed=10.0))
    assert "Counts and run time that do not agree" not in page


def test_a_performance_difference_inside_the_stations_own_band_is_not_a_finding(tmp_path):
    """Both sides divide by run time, and the MES drains past the end of the
    script. A difference smaller than that cannot be told apart from it."""
    truth = _truth_for(tmp_path)
    cut = truth.stations["Cut"]
    # The MES watched a tenth longer than the script ran, and made a tenth more.
    said = _oee_saying(truth, speed=10.0,
                       Cut={"runtime_seconds": round(cut.running_seconds * 1.1 / 10.0, 1),
                            "good_qty": int(cut.good * 1.05)})
    row = next(r for r in measure.oee(truth, said, MAPPING, speed=10.0)["stations"]
               if r["station"] == "Cut")

    assert row["performance_resolution"] == pytest.approx(0.1, abs=0.02)
    assert abs(row["difference"]["performance"]) < row["performance_resolution"]
    assert measure.performance_significant(row) is False


def test_a_withheld_run_makes_every_measurement_unknown_with_the_same_reason(tmp_path):
    truth = _truth_for(tmp_path)
    why = "the harness fell behind its own 50 ms sample"
    out = measure.booking(truth, _oee_saying(truth), {}, MAPPING,
                          speed=10.0, reason=why)
    assert out["unknown_because"] == why
    assert all(r["verdict"].startswith("unknown") for r in out["stations"])
    assert measure.withheld({"verdict_withheld": why}) == why
    assert measure.withheld({}) is None


# ----------------------------------------------------------------- the report

def test_the_report_prints_unknown_rather_than_a_zero_and_renders_the_notes(tmp_path):
    truth = _truth_for(tmp_path)
    scores = {
        "experiment": "tiny", "plan": "tiny.toml", "product_version": "0.0.0",
        "started_at": "2026-09-14T00:00:00", "wall_seconds": 12, "speed": 10.0,
        "measurements_asked_for": ["booking"], "plants_total": 1, "plants_run": 1,
        "note": "", "plants": [{
            "plant": "tiny", "label": "A tiny plant", "seed": 3,
            "duration_line_seconds": 120, "speed": 10.0,
            "replay_overlap_line_seconds": 10, "pipeline": {"sustained": None},
            "verdict_withheld": None, "overlay": {}, "views_refused": {},
            "measurements": {"booking": measure.booking(
                truth, _oee_saying(truth), {}, MAPPING, speed=10.0)},
        }],
    }
    (tmp_path / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (tmp_path / "notes.md").write_text(
        "# Notes\n\n## What I saw\n\nThe OEE tile went blank.\n", encoding="utf-8")
    page = lab_report.write(tmp_path).read_text(encoding="utf-8")

    assert "The OEE tile went blank." in page
    # "harness kept up" is None for this run, and None is a word, not a number.
    assert "unknown" in page
    # Self-contained: a results directory is meant to survive being copied onto
    # a memory stick and opened on a machine with no network.
    assert "http://" not in page and "https://" not in page


def test_every_measurement_the_plan_format_offers_has_a_function_behind_it():
    # A name in the plan format with nothing behind it would be accepted by
    # the reader and quietly missing from the report.
    for name in MEASUREMENTS:
        assert callable(getattr(measure, name.replace("-", "_")))
