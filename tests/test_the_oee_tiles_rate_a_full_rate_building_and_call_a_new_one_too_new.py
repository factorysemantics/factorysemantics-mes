"""The OEE tiles above the parts mix, at the window's two edges.

Two warts the "last played" window showed on 2026-10-30, both on screens
Scott reads as the MES doubting him:

* Four constructors that ran at full rate for the whole 41 minutes were
  flagged "counts outrun run time". The cause was the reader, not the window:
  a building's running sum carried the part of a unit made before the game was
  closed across the eight-hour gap, and the first poll of the next stretch
  booked it. Concrete `CONSTRUCTORMK1-2146851399` made 14.94 at 01:37, booked
  14, and the 0.94 was booked at 09:42 - 617 units in a window whose 2,467 s of
  running at 15 a minute hold 616.75. The fraction now stops at a break.
* Six constructors placed in the last two minutes read "Watched 2 %" and a
  dash. They are withheld by the same 80 % floor, and now say "placed 05:21 -
  too new to rate", listed under the rated ones and counted apart.
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fsmes.config import get_settings
from fsmes.integrations.satisfactory import reader
from fsmes.services import analysis, coverage
from fsmes.services import equipment as equipment_service

FIXTURES = Path(__file__).parent / "fixtures" / "satisfactory"
DAYS = ("2026-10-28", "2026-10-29", "2026-10-30")
CONCRETE = "CONSTRUCTORMK1-2146851399"

T0 = datetime(2026, 10, 30, 12, 0, 0)


def _minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


def _maker(ident: str, rate: float, *, most: float = 15.0) -> dict:
    """One constructor making concrete at `rate` a minute, rated `most`."""
    return {"ID": f"Build_ConstructorMk1_C_{ident}", "Name": "Constructor",
            "ClassName": "Build_ConstructorMk1_C", "Recipe": "Concrete", "IsConfigured": True,
            "IsProducing": rate > 0, "IsPaused": False, "ManuSpeed": 100,
            "PowerInfo": {"CircuitID": 1, "FuseTriggered": False,
                          "PowerConsumed": 4.0, "MaxPowerConsumed": 4.0},
            "production": [{"Name": "Concrete", "ClassName": "Desc_Cement_C", "Amount": 1,
                            "MaxAmount": 500, "CurrentProd": rate, "MaxProd": most}],
            "ingredients": [], "InputInventory": [], "OutputInventory": []}


def _poll(ts: datetime, buildings: list[dict]) -> list[dict]:
    stamp = ts.isoformat() + "+00:00"
    return [{"ts": stamp, "endpoint": "getFactory", "data": buildings},
            {"ts": stamp, "endpoint": "getSessionInfo",
             "data": {"SessionName": "S", "IsPaused": False}}]


def _write(folder: Path, polls: list[list[dict]]) -> None:
    with (folder / "2026-10-30.jsonl").open("w", encoding="utf-8") as fh:
        for poll in polls:
            for line in poll:
                fh.write(json.dumps(line) + "\n")


def _station(oee: dict, ident: str) -> dict:
    return next(s for s in oee["stations"] if s["code"] == f"CONSTRUCTORMK1-{ident}")


@pytest.fixture
def floor_of(monkeypatch):
    """This plant's pack floor for one test, the way a pack sets it."""
    def set_it(value: str) -> None:
        monkeypatch.setenv("MES_OEE_COVERAGE_FLOOR", value)
        get_settings.cache_clear()
    yield set_it
    get_settings.cache_clear()


# ------------------------------------------------------------------ the full-rate building


def test_a_building_at_exactly_its_recipe_rate_across_a_window_that_starts_mid_interval_reads_1_00_and_no_flag(
        tmp_path, session, scope, monkeypatch):
    _write(tmp_path, [_poll(_minutes(i), [_maker("1", 15.0)]) for i in range(31)])
    reader.replay(tmp_path, scope, now=_minutes(30.5))
    # A quarter hour ending half a minute after the last poll: it starts
    # half way through an interval, whose booking it counts in full.
    monkeypatch.setattr(analysis, "utcnow", lambda: _minutes(30.5))
    row = _station(analysis.oee_breakdown(session, line_code="CONSTRUCTORS", hours=0.25), "1")
    assert row["counts_outrun_run_time"] is False
    assert row["performance"] == pytest.approx(1.0)


def test_part_of_a_unit_made_before_the_game_was_closed_is_not_booked_after_it(
        tmp_path, session, scope, monkeypatch):
    # 14.9 a minute for two intervals (29.8 made, 29 booked), the game closed
    # for an hour, then fifteen minutes at the same rate.
    before = [_poll(_minutes(i), [_maker("1", 14.9)]) for i in range(3)]
    after = [_poll(_minutes(60 + i), [_maker("1", 14.9)]) for i in range(16)]
    _write(tmp_path, before + after)
    reader.replay(tmp_path, scope, now=_minutes(75))
    monkeypatch.setattr(analysis, "utcnow", lambda: _minutes(75))
    row = _station(analysis.oee_breakdown(session, line_code="CONSTRUCTORS", shift="last_play"), "1")
    # 15 minutes at 14.9 is 223.5: 223 whole units, not 224 with the 0.8 the
    # first stretch left over.
    assert row["good_qty"] == 223
    assert row["counts_outrun_run_time"] is False


def test_a_building_whose_count_jumps_past_its_run_time_still_shows_the_flag(
        tmp_path, session, scope, monkeypatch):
    # The game says 15 a minute; one poll it says 600, a counter's jump.
    polls = [_poll(_minutes(i), [_maker("1", 600.0 if i == 5 else 15.0)]) for i in range(11)]
    _write(tmp_path, polls)
    reader.replay(tmp_path, scope, now=_minutes(10))
    monkeypatch.setattr(analysis, "utcnow", lambda: _minutes(10))
    row = _station(analysis.oee_breakdown(session, line_code="CONSTRUCTORS", shift="last_play"), "1")
    assert row["counts_outrun_run_time"] is True
    assert row["performance"] is None


# ------------------------------------------------------------------ the building placed just now


def test_a_station_first_seen_two_minutes_before_the_window_ends_reads_too_new_and_is_not_among_the_rated(
        tmp_path, session, scope, monkeypatch, floor_of):
    floor_of("0.8")
    polls = [_poll(_minutes(i), [_maker("1", 15.0)] + ([_maker("2", 15.0)] if i >= 18 else []))
             for i in range(21)]
    _write(tmp_path, polls)
    reader.replay(tmp_path, scope, now=_minutes(20))
    monkeypatch.setattr(analysis, "utcnow", lambda: _minutes(20))
    oee = analysis.oee_breakdown(session, line_code="CONSTRUCTORS", shift="last_play")
    new = _station(oee, "2")
    assert new["too_new"] is True
    assert new["first_seen"] == _minutes(18)
    assert new["oee"] is None
    assert new["coverage_note"].startswith("too new to rate: first seen at 2026-10-30 12:18 UTC")
    # Under the rated one, not among it; counted apart.
    assert [s["code"] for s in oee["stations"]][-1] == new["code"]
    assert oee["stations_rated"] == 1
    assert oee["stations_too_new"] == 1
    assert oee["stations_withheld"] == 1
    assert _station(oee, "1")["too_new"] is False


def test_a_station_watched_from_before_the_window_but_under_the_floor_is_not_called_too_new(
        tmp_path, session, scope, monkeypatch, floor_of):
    floor_of("0.8")
    # Seen at the start, then gone from the list for most of the window (and
    # not torn down, so it stays on the floor).
    polls = [_poll(_minutes(i), [_maker("1", 15.0)] + ([_maker("2", 15.0)] if i < 3 else []))
             for i in range(21)]
    _write(tmp_path, polls)
    reader.replay(tmp_path, scope, now=_minutes(20), torn_down_polls=1000)
    monkeypatch.setattr(analysis, "utcnow", lambda: _minutes(20))
    old = _station(analysis.oee_breakdown(session, line_code="CONSTRUCTORS", shift="last_play"), "2")
    assert old["coverage_note"] and old["too_new"] is False


def test_the_machine_oee_tab_says_too_new_the_same_way(
        tmp_path, session, scope, monkeypatch, floor_of):
    floor_of("0.8")
    polls = [_poll(_minutes(i), [_maker("1", 15.0)] + ([_maker("2", 15.0)] if i >= 18 else []))
             for i in range(21)]
    _write(tmp_path, polls)
    reader.replay(tmp_path, scope, now=_minutes(20))
    monkeypatch.setattr(equipment_service, "utcnow", lambda: _minutes(20))
    tab = equipment_service.oee(session, equipment_code="CONSTRUCTORMK1-2", hours=0.25)
    assert tab["too_new"] is True
    assert tab["first_seen"] == _minutes(18)
    assert tab["coverage_note"].startswith("too new to rate")


def test_a_withheld_machine_seen_before_the_window_is_never_too_new():
    start = datetime(2026, 10, 30, 9, 0)
    assert coverage.too_new(start - timedelta(minutes=1), start, "withheld: ...") is None
    assert coverage.too_new(start + timedelta(minutes=1), start, None) is None
    assert coverage.too_new(start + timedelta(minutes=1), start, "withheld: ...") == (
        start + timedelta(minutes=1))


# ------------------------------------------------------------------ the evening itself


def test_the_10_30_evening_on_the_last_played_window_rates_9_of_15_with_6_too_new_and_none_outrun(
        tmp_path, session, scope, floor_of):
    floor_of("0.8")
    for day in DAYS:
        with gzip.open(FIXTURES / f"{day}.jsonl.gz", "rb") as src, \
                (tmp_path / f"{day}.jsonl").open("wb") as dst:
            shutil.copyfileobj(src, dst)
    reader.replay(tmp_path, scope)
    oee = analysis.oee_breakdown(session, line_code="CONSTRUCTORS", shift="last_play")
    assert oee["machines_total"] == 15
    assert oee["stations_rated"] == 9
    assert oee["stations_too_new"] == 6
    assert oee["stations_counts_outrun"] == 0
    concrete = _station(oee, CONCRETE.split("-")[1])
    # 2,467 s of running at 4 s a unit holds 616.75: 616 booked, not 617.
    assert concrete["good_qty"] == 616
    assert concrete["runtime_seconds"] == pytest.approx(2467, abs=1)
    assert concrete["performance"] == pytest.approx(0.9988, abs=1e-4)
    assert all(s["too_new"] for s in oee["stations"][-6:])
