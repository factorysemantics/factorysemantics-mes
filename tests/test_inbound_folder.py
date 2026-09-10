"""The CSV folder driver: what a plant's own export does when it lands.

Three promises are pinned here, because each of them is a way a file
interface loses data quietly. An input file is always moved and never
deleted. The same file dropped twice changes nothing. And every run states
its totals, with the first reason for each row it would not take.
"""

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest

from fsmes.db import utcnow
from fsmes.domain import EquipmentStateName, ProductionLog
from fsmes.integrations.inbound import folder
from fsmes.services import equipment, execution, workorders

MAPPING = {
    "counts": {
        "source": "replay:incumbent-mes",
        "source_kind": "replay",
        "timezone": "UTC",
        "columns": {
            "external_key": "entry_id",
            "recorded_at": "entered_at",
            "order": "order_no",
            "equipment": "machine",
            "good": "good_qty",
            "scrap": "scrap_qty",
        },
    },
    "downtime": {
        "source": "replay:incumbent-mes",
        "source_kind": "replay",
        "timezone": "UTC",
        "columns": {
            "external_key": "stop_id",
            "recorded_at": "entered_at",
            "equipment": "machine",
            "started_at": "stop_start",
            "ended_at": "stop_end",
            "reason": "reason_code",
        },
    },
}

COUNTS_CSV = (
    "entry_id,entered_at,order_no,machine,good_qty,scrap_qty\n"
    "1,2026-09-10 17:00:00,,MIX01,3,1\n"
    "2,2026-09-10 17:00:00,,MIX01,2,0\n"
)


@pytest.fixture()
def inbound_root(tmp_path) -> Path:
    root = tmp_path / "inbound"
    for stream in ("counts", "downtime"):
        folder.folders_for(root, stream).make()
    return root


@pytest.fixture()
def mappings(tmp_path):
    path = tmp_path / "inbound_mapping.json"
    path.write_text(json.dumps(MAPPING), encoding="utf-8")
    return folder.load_mapping(path)


def drop(root: Path, stream: str, name: str, text: str) -> Path:
    path = root / stream / name
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------- mapping


def test_a_missing_mapping_file_says_what_it_was_for(tmp_path):
    with pytest.raises(folder.MappingError, match="what this plant's columns are called"):
        folder.load_mapping(tmp_path / "nowhere.json")


def test_a_mapping_naming_a_field_the_contract_does_not_have_is_refused(tmp_path):
    spec = {"counts": {**MAPPING["counts"], "columns": {"external_key": "id", "widgets": "w"}}}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(folder.MappingError, match="widgets"):
        folder.load_mapping(path)


def test_a_mapping_naming_a_time_zone_this_machine_does_not_know_is_refused(tmp_path):
    spec = {"counts": {**MAPPING["counts"], "timezone": "Mars/Olympus"}}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(folder.MappingError, match="time zone"):
        folder.load_mapping(path)


# -------------------------------------------------------------------- timing


def test_a_naive_timestamp_with_no_configured_zone_is_rejected_rather_than_read_as_utc(mappings):
    mapping = dataclasses.replace(mappings["counts"], timezone=None)
    with pytest.raises(ValueError, match="could move it by hours"):
        folder.to_event({"entry_id": "1", "entered_at": "2026-09-10 17:00:00", "order_no": "",
                         "machine": "MIX01", "good_qty": "3", "scrap_qty": "1"}, mapping)


def test_a_local_time_is_converted_and_not_shifted(tmp_path):
    spec = {"counts": {**MAPPING["counts"], "timezone": "America/Chicago"}}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    mapping = folder.load_mapping(path)["counts"]
    event = folder.to_event({"entry_id": "1", "entered_at": "2026-09-10 12:00:00", "order_no": "",
                             "machine": "MIX01", "good_qty": "3", "scrap_qty": "1"}, mapping)
    # Central daylight time in September is UTC-5.
    assert event.recorded_at == datetime(2026, 9, 10, 17, 0)


# ---------------------------------------------------------------- processing


def test_counts_dropped_as_csv_appear_in_the_mes_attributed_to_their_source(
        session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)

    report = folder.run_once(scope, inbound_root, mappings)

    assert (report.rows, report.applied, report.rejected) == (2, 2, 0)
    rows = session.query(ProductionLog).all()
    assert [row.good_qty for row in rows] == [3.0, 2.0]
    assert {row.source_system for row in rows} == {"replay:incumbent-mes"}


def test_the_same_rows_as_json_land_the_same_way(session, scope, inbound_root, mappings):
    payload = [{"entry_id": "1", "entered_at": "2026-09-10 17:00:00", "order_no": "",
                "machine": "MIX01", "good_qty": 3, "scrap_qty": 1}]
    drop(inbound_root, "counts", "counts.json", json.dumps(payload))

    report = folder.run_once(scope, inbound_root, mappings)

    assert (report.rows, report.applied) == (1, 1)
    assert session.query(ProductionLog).one().good_qty == 3.0


def test_the_same_file_processed_twice_changes_nothing(session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)
    folder.run_once(scope, inbound_root, mappings)
    drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)

    second = folder.run_once(scope, inbound_root, mappings)

    assert (second.rows, second.applied, second.duplicates) == (2, 0, 2)
    assert session.query(ProductionLog).count() == 2


def test_an_input_file_is_moved_and_never_deleted(session, scope, inbound_root, mappings):
    path = drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)

    folder.run_once(scope, inbound_root, mappings)

    assert not path.exists()
    assert (inbound_root / "processed" / "counts" / "counts.csv").is_file()


def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(
        session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)
    folder.run_once(scope, inbound_root, mappings)
    drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)
    folder.run_once(scope, inbound_root, mappings)

    kept = list((inbound_root / "processed" / "counts").iterdir())
    assert len(kept) == 2


def test_a_file_this_mes_cannot_read_at_all_goes_to_rejected_with_its_reason(
        session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.json", "{not json")

    report = folder.run_once(scope, inbound_root, mappings)

    assert report.rejected == 1
    assert (inbound_root / "rejected" / "counts" / "counts.json").is_file()
    rejects = (inbound_root / "rejected" / "counts" / "counts.json.rejects.txt").read_text()
    assert "could not be read" in rejects


def test_a_file_of_another_kind_is_left_where_it_is(session, scope, inbound_root, mappings):
    path = drop(inbound_root, "counts", "README.txt", "how our export works")

    report = folder.run_once(scope, inbound_root, mappings)

    assert report.skipped == 1
    assert path.is_file()


def test_the_rejects_report_states_its_total_and_the_first_reason_per_row(
        session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.csv",
         "entry_id,entered_at,order_no,machine,good_qty,scrap_qty\n"
         "1,2026-09-10 17:00:00,,MIX01,3,1\n"
         "2,2026-09-10 17:00:00,,NOPE,2,0\n"
         "3,2026-09-10 17:00:00,,MIX01,two,0\n"
         ",2026-09-10 17:00:00,,MIX01,1,0\n")

    report = folder.run_once(scope, inbound_root, mappings)

    assert (report.rows, report.applied, report.rejected) == (4, 1, 3)
    text = (inbound_root / "rejected" / "counts" / "counts.csv.rejects.txt").read_text()
    assert "read 4, recorded 1, already seen 0, rejected 3" in text
    assert "row 2:" in text and "NOPE" in text
    assert "row 3:" in text and "not a number" in text
    assert "row 4:" in text
    # Some rows were taken, so the input belongs with the processed files;
    # the report of what was left out is what goes to rejected.
    assert (inbound_root / "processed" / "counts" / "counts.csv").is_file()


def test_one_bad_row_does_not_cost_the_good_ones(session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.csv",
         "entry_id,entered_at,order_no,machine,good_qty,scrap_qty\n"
         "1,2026-09-10 17:00:00,,NOPE,9,0\n"
         "2,2026-09-10 17:00:00,,MIX01,2,0\n")

    folder.run_once(scope, inbound_root, mappings)

    assert session.query(ProductionLog).one().good_qty == 2.0


def test_a_downtime_file_labels_the_stops_this_mes_watched(session, scope, inbound_root, mappings):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.DOWN, actor="opc-agent")
    state = equipment.current_states(session)[0]
    state.started_at = datetime(2026, 9, 10, 9, 10)
    state.ended_at = datetime(2026, 9, 10, 9, 35)
    session.flush()
    drop(inbound_root, "downtime", "stops.csv",
         "stop_id,entered_at,machine,stop_start,stop_end,reason_code\n"
         "44,2026-09-10 17:00:00,MIX01,2026-09-10 09:12:00,2026-09-10 09:31:00,blade change\n")

    report = folder.run_once(scope, inbound_root, mappings)

    assert report.applied == 1
    assert (state.reason, state.reason_source) == ("blade change", "replay:incumbent-mes")


def test_a_run_states_its_totals_in_one_line(session, scope, inbound_root, mappings):
    drop(inbound_root, "counts", "counts.csv", COUNTS_CSV)
    lines = folder.run_once(scope, inbound_root, mappings).render()
    assert lines[-1] == "1 file, 2 rows: 2 recorded, 0 already seen, 0 rejected"


def test_a_count_that_books_against_a_released_order_is_reported_as_such(
        session, scope, inbound_root, mappings):
    workorders.create(session, code="WO-1", material_code="FG-COLA", quantity=9, actor="test")
    workorders.release(session, "WO-1", "test")
    drop(inbound_root, "counts", "counts.csv",
         "entry_id,entered_at,order_no,machine,good_qty,scrap_qty\n"
         f"1,{utcnow().isoformat(sep=' ', timespec='seconds')},WO-1,MIX01,3,0\n")

    folder.run_once(scope, inbound_root, mappings)

    assert execution.unassigned_production(session)["total"] == 0
    assert session.query(ProductionLog).one().work_order_id is not None


def test_the_mapping_the_repository_ships_is_one_the_driver_can_read():
    """The example maps the demo plant's own CSV shape, and nothing else.

    It is an example of *this* plant's columns. No commercial system's
    export format is described anywhere in this repository, and this test
    exists to keep the shipped file honest rather than to bless a format.
    """
    mappings = folder.load_mapping(Path("config/inbound_mapping.json"))
    assert sorted(mappings) == ["counts", "downtime", "quality"]
    assert all(m.timezone for m in mappings.values())
