"""The parts mix beside OEE, against a standing rate order.

A Satisfactory factory is not asked for 500 plates by Friday. It is asked for
a steady mix: so many plates, screws and rods a minute, for as long as the
save runs. That is a rate order - a name and a target rate per item, with no
quantity and no due date - and the parts mix sets what was booked of each
item against it.

Around it, four things that make the mix true: the last time you played as
the window, buildings torn down in the game leaving the floor, a session line
that arrives a second after its poll read with that poll, and the game's own
production figures kept as the check on the bookings.

OEE does not change: the rate order never feeds it.
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from fsmes.domain.masterdata import Equipment
from fsmes.integrations.satisfactory import feed, reader
from fsmes.pack import masterdata as pack_masterdata
from fsmes.services import Invalid, analysis, calendar, last_play, parts_mix, rate_orders

FIXTURES = Path(__file__).parent / "fixtures" / "satisfactory"
TONIGHT = FIXTURES / "2026-10-30.jsonl.gz"
STARTER = Path(__file__).parents[1] / "labs" / "satisfactory" / "masterdata"

T0 = datetime(2026, 10, 30, 12, 0, 0)


def _minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


def _maker(ident: str, item: str, rate: float) -> dict:
    """One constructor making `item` at `rate` a minute (0: idle, not producing)."""
    return {"ID": f"Build_ConstructorMk1_C_{ident}", "Name": "Constructor",
            "ClassName": "Build_ConstructorMk1_C", "Recipe": item, "IsConfigured": True,
            "IsProducing": rate > 0, "IsPaused": False, "ManuSpeed": 100,
            "PowerInfo": {"CircuitID": 1, "FuseTriggered": False,
                          "PowerConsumed": 4.0, "MaxPowerConsumed": 4.0},
            "production": [{"Name": item, "ClassName": "Desc_X_C", "Amount": 1,
                            "MaxAmount": 200, "CurrentProd": rate, "MaxProd": 60}],
            "ingredients": [], "InputInventory": [], "OutputInventory": []}


def _poll(ts: datetime, buildings: list[dict], *, paused: bool = False,
          stats: list[dict] | None = None, session_after: float = 0.0) -> list[dict]:
    stamp = ts.isoformat() + "+00:00"
    later = (ts + timedelta(seconds=session_after)).isoformat() + "+00:00"
    lines = [{"ts": stamp, "endpoint": "getFactory", "data": buildings}]
    if stats is not None:
        lines.append({"ts": stamp, "endpoint": "getProdStats", "data": stats})
    lines.append({"ts": later, "endpoint": "getSessionInfo",
                  "data": {"SessionName": "S", "IsPaused": paused}})
    return lines


def _stat(item: str, made: float, most: float = 60.0) -> dict:
    return {"Name": item, "ClassName": "Desc_X_C", "CurrentProd": made, "MaxProd": most,
            "CurrentConsumed": 0.0, "MaxConsumed": 0.0}


def _write(folder: Path, polls: list[list[dict]]) -> Path:
    path = folder / "2026-10-30.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for poll in polls:
            for line in poll:
                fh.write(json.dumps(line) + "\n")
    return path


def _order(session, **rates: float) -> None:
    rate_orders.put(session, code="MIX", name="Test mix", actor="test",
                    items=[{"item": k.replace("_", " "), "per_minute": v}
                           for k, v in rates.items()])
    session.flush()


def _row(block: dict, item: str) -> dict:
    return next(r for r in block["items"] if r["item"] == item)


def _plates_and_idle_rods(tmp_path, scope, *, stats: bool = True, also: list[dict] = ()):
    buildings = [_maker("1", "Iron Plate", 39.8), _maker("2", "Iron Rod", 0.0), *also]
    table = [_stat("Iron Plate", 39.8)] if stats else None
    _write(tmp_path, [_poll(_minutes(i), buildings, stats=table) for i in range(11)])
    return reader.replay(tmp_path, scope, now=_minutes(10))


# ------------------------------------------------------------------ the mix against the order


def test_rods_ordered_at_20_a_minute_and_none_made_are_short_by_20_at_0_of_a_33_percent_share(
        tmp_path, session, scope):
    _plates_and_idle_rods(tmp_path, scope)
    _order(session, Iron_Plate=40, Iron_Rod=20)
    block = parts_mix.breakdown(session, T0, _minutes(10))

    assert block["has_target"] is True
    assert block["watched_minutes"] == 10.0
    assert block["unit"] == "units/min" and block["share_unit"] == "%"
    rod, plate = block["items"][0], block["items"][1]
    # Shortest first: the rods' gap is the bigger shortfall.
    assert rod["item"] == "Iron Rod" and plate["item"] == "Iron Plate"
    assert rod["target_per_minute"] == 20.0
    assert rod["actual_per_minute"] == 0.0
    assert rod["gap_per_minute"] == -20.0
    assert rod["target_share_percent"] == 33.3
    assert rod["actual_share_percent"] == 0.0
    assert plate["actual_per_minute"] == pytest.approx(39.8, abs=0.01)
    assert plate["gap_per_minute"] == pytest.approx(-0.2, abs=0.01)
    assert plate["target_share_percent"] == 66.7
    assert plate["actual_share_percent"] == 100.0
    # The check: the game's own figure for plates stands beside the booking.
    assert plate["game_made_per_minute"] == 39.8
    assert block["items_total"] == 2


def test_an_item_made_but_not_in_the_order_shows_no_target_and_says_why(
        tmp_path, session, scope):
    _plates_and_idle_rods(tmp_path, scope, also=[_maker("3", "Screws", 30.0)])
    _order(session, Iron_Plate=40, Iron_Rod=20)
    block = parts_mix.breakdown(session, T0, _minutes(10))

    screws = _row(block, "Screws")
    assert screws["target_per_minute"] is None
    assert screws["gap_per_minute"] is None
    assert screws["target_share_percent"] is None
    assert screws["why"] == "no target: not in the rate order"
    assert screws["actual_per_minute"] == 30.0
    # Shares are of the order's items, so screws stand outside them.
    assert screws["actual_share_percent"] is None
    assert block["items"][-1]["item"] == "Screws"
    assert block["items_total"] == 3


def test_a_plant_with_no_order_shows_no_target_column_and_no_panel_without_parts_readings(
        tmp_path, session, scope):
    _plates_and_idle_rods(tmp_path, scope, stats=False)
    # No order and no parts readings: no block at all, and none on OEE.
    assert parts_mix.breakdown(session, T0, _minutes(10)) is None
    assert "parts_mix" not in analysis.oee_breakdown(session, hours=1)


def test_a_plant_with_parts_readings_but_no_order_shows_the_mix_with_no_target(
        tmp_path, session, scope):
    _plates_and_idle_rods(tmp_path, scope)
    block = parts_mix.breakdown(session, T0, _minutes(10))
    assert block["has_target"] is False and block["order"] is None
    assert all(r["target_per_minute"] is None for r in block["items"])
    assert {r["why"] for r in block["items"]} == {"no target: no rate order"}
    assert block["share_basis"] == "every item made"
    assert _row(block, "Iron Plate")["actual_share_percent"] == 100.0


def test_the_rate_order_never_changes_an_oee_figure(tmp_path, session, scope):
    _plates_and_idle_rods(tmp_path, scope)
    before = analysis.oee_breakdown(session, hours=1)
    _order(session, Iron_Plate=40, Iron_Rod=20)
    after = analysis.oee_breakdown(session, hours=1)
    for key in ("line_oee", "coverage", "constraint"):
        assert before.get(key) == after.get(key)
    assert after["parts_mix"]["has_target"] is True


def test_a_machine_shows_its_item_and_its_rate_against_the_order(tmp_path, session, scope):
    _plates_and_idle_rods(tmp_path, scope)
    _order(session, Iron_Plate=40, Iron_Rod=20)
    plate = session.scalar(select(Equipment).where(Equipment.code == "CONSTRUCTORMK1-1"))
    line = parts_mix.machine(session, plate, T0, _minutes(10))
    assert line["item"] == "Iron Plate"
    assert line["units"] == pytest.approx(398, abs=1)
    assert line["actual_per_minute"] == pytest.approx(39.8, abs=0.01)
    assert line["target_per_minute"] == 40.0
    assert line["unit"] == "units/min"


def test_the_games_own_figure_counts_a_poll_whose_statistics_name_no_plates_as_none_made(
        tmp_path, session, scope):
    making = [_maker("1", "Iron Plate", 30.0), _maker("2", "Wire", 30.0)]
    # Plates in the game's statistics at two polls of four; wire at all four.
    polls = [_poll(_minutes(i), making,
                   stats=[_stat("Wire", 30.0)] + ([_stat("Iron Plate", 30.0)] if i < 2 else []))
             for i in range(4)]
    _write(tmp_path, polls)
    reader.replay(tmp_path, scope, now=_minutes(3))
    block = parts_mix.breakdown(session, T0 - timedelta(seconds=1), _minutes(3))
    assert _row(block, "Wire")["game_made_per_minute"] == 30.0
    # 30 + 30 + 0 + 0 over four polls, not 30 over the two that named plates.
    assert _row(block, "Iron Plate")["game_made_per_minute"] == 15.0


# ------------------------------------------------------------------ the rate order itself


def test_an_admin_sets_reads_and_removes_the_rate_order_and_an_operator_may_only_read(
        admin, client):
    assert client.get("/rate-order").json() == {"order": None}
    body = {"code": "MIX", "name": "Iron parts", "items": [
        {"item": "Iron Plate", "per_minute": 40}, {"item": "Iron Rod", "per_minute": 20}]}
    assert client.put("/rate-order", json=body).status_code == 403
    put = admin.put("/rate-order", json=body)
    assert put.status_code == 200, put.text
    order = client.get("/rate-order").json()["order"]
    assert order["items_total"] == 2 and order["per_minute_total"] == 60
    assert [i["share_percent"] for i in order["items"]] == [66.7, 33.3]
    assert order["unit"] == "units/min"
    bad = dict(body, items=[{"item": "Iron Plate", "per_minute": 0}])
    assert admin.put("/rate-order", json=bad).status_code in (400, 422)
    assert admin.delete("/rate-order").status_code == 204
    assert client.get("/rate-order").json() == {"order": None}


def test_the_starter_rate_order_is_valid_and_is_seeded_once_and_then_left_alone(session):
    assert pack_masterdata.problems(STARTER) == []
    receipt = pack_masterdata.seed(session, STARTER)
    order = rate_orders.current(session)
    assert order.code == "MIX"
    assert rate_orders.targets(order) == {"Iron Plate": 24.0, "Screws": 24.0, "Iron Rod": 12.0}
    assert "rate_orders" in receipt
    # The plant's own order is the plant's: a second seed does not overwrite it.
    rate_orders.put(session, code="MINE", name="Mine", actor="test",
                    items=[{"item": "Wire", "per_minute": 30}])
    again = pack_masterdata.seed(session, STARTER)
    assert rate_orders.current(session).code == "MINE"
    assert again["rate_orders"].get("left_alone") == 1


def test_a_pack_with_two_rate_orders_or_a_rate_of_zero_is_refused_in_sentences(tmp_path):
    two = [{"code": "A", "name": "A", "items": [{"item": "Wire", "per_minute": 1}]},
           {"code": "B", "name": "B", "items": [{"item": "Wire", "per_minute": 1}]}]
    (tmp_path / "rate_orders.json").write_text(json.dumps(two), encoding="utf-8")
    assert any("one" in p for p in pack_masterdata.problems(tmp_path))
    zero = [{"code": "A", "name": "A", "items": [{"item": "Wire", "per_minute": 0}]}]
    (tmp_path / "rate_orders.json").write_text(json.dumps(zero), encoding="utf-8")
    assert any("above zero" in p for p in pack_masterdata.problems(tmp_path))


# ------------------------------------------------------------------ the last time you played


def test_the_last_play_window_of_tonights_game_is_04_41_to_05_23_and_fully_watched(
        tmp_path, session, scope):
    with gzip.open(TONIGHT, "rb") as src, (tmp_path / "2026-10-30.jsonl").open("wb") as dst:
        shutil.copyfileobj(src, dst)
    reader.replay(tmp_path, scope)

    chicago = ZoneInfo("America/Chicago")
    shift = calendar.last_play_shift(session, chicago)
    start = shift.starts_at.replace(tzinfo=UTC).astimezone(chicago)
    end = shift.ends_at.replace(tzinfo=UTC).astimezone(chicago)
    # The screen floors the start and ceils the end to the minute.
    assert start.strftime("%H:%M") == "04:41"
    assert (end + timedelta(seconds=59)).strftime("%H:%M") == "05:23"
    assert shift.key() == last_play.SPEC
    # Coverage of the feed: every second of the window lay between two polls
    # of the one stretch.
    window = (shift.ends_at - shift.starts_at).total_seconds()
    assert last_play.watched_seconds(session, shift.starts_at, shift.ends_at) == window
    # The picker offers it, and the analysis windows on it.
    assert analysis.shifts(session)["last_play"]["key"] == "last_play"
    oee = analysis.oee_breakdown(session, shift="last_play")
    assert oee["window"]["named"] == "last_play"
    assert oee["parts_mix"]["watched_minutes"] == pytest.approx(window / 60, abs=0.01)


def test_a_pause_ends_a_stretch_and_the_last_play_is_the_one_after_it(tmp_path, session, scope):
    making = [_maker("1", "Iron Plate", 30.0)]
    polls = [_poll(_minutes(i), making) for i in range(4)]
    polls.append(_poll(_minutes(4), making, paused=True))
    polls += [_poll(_minutes(i), making) for i in range(5, 8)]
    _write(tmp_path, polls)
    reader.replay(tmp_path, scope, now=_minutes(7))
    shift = calendar.last_play_shift(session)
    assert (shift.starts_at, shift.ends_at) == (_minutes(5), _minutes(7))
    assert last_play.watched_seconds(session, T0, _minutes(7)) == 5 * 60


def test_a_plant_no_game_feeds_has_no_last_play_and_asking_is_refused_in_a_sentence(session):
    assert analysis.shifts(session)["last_play"] is None
    with pytest.raises(Invalid, match="last time you played"):
        calendar.last_play_shift(session)


# ------------------------------------------------------------------ torn down


def _machine(session, code: str) -> Equipment:
    machine = session.scalar(select(Equipment).where(Equipment.code == code))
    session.refresh(machine)
    return machine


def _absent_polls(*, back: bool) -> list[list[dict]]:
    """Building 2 listed twice, then left out of four polls, a pause (which
    does not count), and a fifth - and, if `back`, listed again."""
    keep, gone = _maker("1", "Iron Plate", 30.0), _maker("2", "Iron Rod", 15.0)
    polls = [_poll(_minutes(i), [keep, gone]) for i in range(2)]
    polls += [_poll(_minutes(i), [keep]) for i in range(2, 6)]
    polls.append(_poll(_minutes(6), [keep], paused=True))
    polls.append(_poll(_minutes(7), [keep]))
    if back:
        polls.append(_poll(_minutes(8), [keep, gone]))
    return polls


def test_a_building_absent_five_unpaused_polls_is_retired_torn_down_at_the_fifth(
        tmp_path, session, scope):
    _write(tmp_path, _absent_polls(back=False))
    done = reader.replay(tmp_path, scope, now=_minutes(7))
    gone = _machine(session, "CONSTRUCTORMK1-2")
    assert gone.retired_reason == "torn down"
    # Not at the fourth absence, and not at the pause: at the fifth unpaused poll.
    assert gone.retired_at == _minutes(7)
    assert done.report.retired == 1
    assert _machine(session, "CONSTRUCTORMK1-1").retired_at is None


def test_a_torn_down_building_comes_back_when_the_game_lists_it_again(tmp_path, session, scope):
    _write(tmp_path, _absent_polls(back=True))
    done = reader.replay(tmp_path, scope, now=_minutes(8))
    gone = _machine(session, "CONSTRUCTORMK1-2")
    assert gone.retired_at is None and gone.retired_reason is None
    assert (done.report.retired, done.report.returned) == (1, 1)


def test_how_many_absent_polls_tear_a_building_down_is_set_in_config():
    from fsmes.config import Settings
    assert Settings().satisfactory_torn_down_polls == 5
    assert Settings(satisfactory_torn_down_polls=3).satisfactory_torn_down_polls == 3


# ------------------------------------------------------------------ the late line


def test_a_session_line_one_second_after_its_poll_is_read_with_that_poll(tmp_path, scope):
    making = [_maker("1", "Iron Plate", 30.0)]
    path = _write(tmp_path, [_poll(_minutes(i), making, session_after=1.0) for i in range(3)])
    polls = feed.read_file(path)
    assert len(polls) == 3
    assert all(p.factory is not None and p.session is not None for p in polls)
    # Skipped falls to 0: no poll is left with no building list.
    assert reader.replay(tmp_path, scope, now=_minutes(2)).report.skipped == 0


def test_a_genuinely_empty_poll_is_still_skipped_as_no_building_list(tmp_path, scope):
    making = [_maker("1", "Iron Plate", 30.0)]
    lines = _poll(_minutes(0), making) + _poll(_minutes(1), making)
    lines.append({"ts": _minutes(2).isoformat() + "+00:00", "endpoint": "getSessionInfo",
                  "data": {"SessionName": "S", "IsPaused": False}})
    _write(tmp_path, [lines])
    assert reader.replay(tmp_path, scope, now=_minutes(2)).report.skipped == 1
