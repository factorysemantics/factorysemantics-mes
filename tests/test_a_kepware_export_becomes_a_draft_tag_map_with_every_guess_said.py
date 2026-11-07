"""`fsmes tagmap from-kepware`: a project export from Kepware becomes a draft tag map.

The fixture is an invented two-station line (`PRESS-1` with tools A and B, a
`WASH-1`) on a channel `Line`, plus a `Scratch` channel that is not a line.
What is guarded is the honesty of the draft: every placement is a guess that
says why, every tag it could not place is listed, and the two add up to the
export's own count.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fsmes.cli import app
from fsmes.integrations.opc.kepware_export import ExportError, draft_tag_map, words, write_draft
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import check as checker

TOY = Path(__file__).resolve().parent / "fixtures" / "kepware" / "toy_project.json"


def _machines(data: dict) -> dict[str, dict]:
    return {m["equipment"]: m for m in data["machines"]}


def test_names_are_split_into_words_however_the_plc_programmer_wrote_them():
    assert words("GoodCount_A") == ["good", "count", "a"]
    assert words("Pallet ID Load") == ["pallet", "id", "load"]
    assert words("PR_fault") == ["pr", "fault"]
    assert words("PLCHeartbeat") == ["plc", "heartbeat"]


def test_one_machine_per_device_on_the_chosen_channel_and_nothing_from_another():
    data, _ = draft_tag_map(TOY, "Line")
    assert [m["equipment"] for m in data["machines"]] == ["PRESS-1", "WASH-1"]
    assert all("Scratch" not in node for m in data["machines"] for node in m["nodes"].values())


def test_every_tag_is_either_placed_or_listed_as_unmapped_and_the_totals_say_so():
    data, _ = draft_tag_map(TOY, "Line")
    placed = sum(len(m["nodes"]) for m in data["machines"])
    unmapped = sum(len(v) for v in data["_unmapped"].values())
    assert data["_totals"] == {"devices": 2, "machines": 2, "tags": 20, "placed": placed,
                               "unmapped": unmapped}
    assert placed + unmapped == 20


def test_every_placed_tag_carries_a_guess_saying_why():
    data, _ = draft_tag_map(TOY, "Line")
    for machine in data["machines"]:
        assert set(machine["nodes"]) <= set(machine["_guess"])
        assert all(machine["_guess"][key] for key in machine["nodes"])
    press = _machines(data)["PRESS-1"]
    assert "Reject" in press["_guess"]["Reject_A"]
    assert "Good" in press["_guess"]["Good_B"]


def test_node_ids_are_kepwares_own_channel_device_group_tag_path():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert press["nodes"]["Good_A"] == "ns=2;s=Line.PRESS-1.Good_A"
    assert press["nodes"]["ToolB.Die_Serial"] == "ns=2;s=Line.PRESS-1.ToolB.Die_Serial"


def test_two_groups_holding_a_tag_of_one_name_keep_both_and_neither_is_the_plain_one():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert {"ToolA.Die_Serial", "ToolB.Die_Serial"} <= set(press["nodes"])
    assert "Die_Serial" not in press["nodes"]


def test_a_tool_per_side_counts_on_both_sides_good_and_reject():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert press["counters"] == {"good": ["Good_A", "Good_B"], "scrap": ["Reject_A", "Reject_B"]}


def test_a_total_beside_a_good_counter_is_recorded_and_never_booked_twice():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert "TotalCount" in press["extra_tags"]
    assert "TotalCount" not in press["counters"]["good"]
    assert "not booked" in press["_guess"]["TotalCount"]


def test_a_count_with_no_good_counter_beside_it_is_read_as_the_good_count():
    wash = _machines(draft_tag_map(TOY, "Line")[0])["WASH-1"]
    assert wash["counters"] == {"good": ["Count"], "scrap": []}


def test_a_downtime_code_is_the_state_and_the_reason_and_its_numbers_are_left_to_the_plant():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert press["state_tag"] == press["reason_tag"] == "DowntimeCode"
    assert press["state_map"] == {"0": "running", "*": "down"}
    assert "reason_map" not in press
    assert "reason_map" in press["_guess"]["DowntimeCode"]


def test_a_fault_code_wins_over_an_alarm_word_and_the_alarm_is_only_recorded():
    wash = _machines(draft_tag_map(TOY, "Line")[0])["WASH-1"]
    assert wash["state_tag"] == "FaultCode"
    assert "AlarmWord" in wash["extra_tags"]


def test_a_status_word_is_recorded_and_not_read_as_the_state_because_its_values_are_unknown():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert press["state_tag"] != "Status"
    assert "Status" in press["extra_tags"]
    assert "state_map" in press["_guess"]["Status"]


def test_a_text_identifier_is_recorded_as_text():
    wash = _machines(draft_tag_map(TOY, "Line")[0])["WASH-1"]
    assert "Lot_Number" in wash["extra_tags"]
    assert "String" in wash["_guess"]["Lot_Number"]


def test_the_first_real_number_is_the_process_value_and_the_rest_are_recorded():
    press = _machines(draft_tag_map(TOY, "Line")[0])["PRESS-1"]
    assert press["analog"] == "HydraulicPressure"
    assert "OilTemp" in press["extra_tags"]


def test_a_tag_the_word_list_cannot_place_is_unmapped_with_its_type_not_guessed():
    data, _ = draft_tag_map(TOY, "Line")
    left = {t["tag"]: t for t in data["_unmapped"]["PRESS-1"]}
    assert left["Setpoint_Speed"]["type"] == "Short"
    assert left["Setpoint_Speed"]["node"] == "ns=2;s=Line.PRESS-1.Setpoint_Speed"
    assert "Setpoint_Speed" not in _machines(data)["PRESS-1"]["nodes"]


def test_every_machine_is_read_only_and_has_no_invented_cycle_time():
    data, notes = draft_tag_map(TOY, "Line")
    for machine in data["machines"]:
        assert machine["order_tag"] is None
        assert "cycle_seconds" not in machine
    assert sum("cycle_seconds" in note for note in notes) == 2


def test_only_the_devices_asked_for_are_drafted():
    data, _ = draft_tag_map(TOY, "Line", ["WASH-1"])
    assert [m["equipment"] for m in data["machines"]] == ["WASH-1"]
    assert data["_totals"]["tags"] == 6


def test_a_channel_that_is_not_there_is_refused_with_the_ones_that_are():
    with pytest.raises(ExportError, match=r"no channel 'Lien'.*It has 2: Line, Scratch"):
        draft_tag_map(TOY, "Lien")


def test_a_device_that_is_not_there_is_refused_with_the_ones_that_are():
    with pytest.raises(ExportError, match=r"no device 'PRESS-2'.*It has 2: PRESS-1, WASH-1"):
        draft_tag_map(TOY, "Line", ["PRESS-2"])


def test_a_file_that_is_not_a_project_export_is_refused_in_words(tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"machines": []}), encoding="utf-8")
    with pytest.raises(ExportError, match="no `channels` list"):
        draft_tag_map(other, "Line")


def test_a_channel_on_which_nothing_looks_like_a_machine_writes_no_draft(tmp_path):
    out = tmp_path / "draft.json"
    with pytest.raises(ExportError, match="none of the 1 tags"):
        write_draft(TOY, out, "Scratch")
    assert not out.exists()


def test_the_draft_loads_through_the_agents_own_reader(tmp_path):
    out = tmp_path / "draft.json"
    write_draft(TOY, out, "Line")
    machines = {m.equipment: m for m in load_tag_map(out)}
    press = machines["PRESS-1"]
    assert press.order_tag is None
    assert press.counter_tags == {"Good_A": "good", "Good_B": "good",
                                  "Reject_A": "scrap", "Reject_B": "scrap"}
    assert press.to_state(0) == "running" and press.to_state(41) == "down"
    assert "Setpoint_Speed" not in press.tags


def test_pack_check_reads_the_drafts_underscore_notes_as_notes(tmp_path):
    write_draft(TOY, tmp_path / "tag_map.json", "Line")
    (tmp_path / "plant.toml").write_text(
        '[pack]\nformat = 1\nrequires = ">=0.1.2"\n\n'
        '[plant]\nname = "acceptance"\nlabel = "A plant written for this test"\n'
        'timezone = "Europe/Berlin"\nprofile = "laptop"\n\n'
        '[files]\ntag_map = "tag_map.json"\n', encoding="utf-8")
    assert [str(p) for p in checker.check(tmp_path).problems] == []


def test_the_command_writes_the_draft_says_the_totals_and_ends_on_opc_verify(tmp_path):
    out = tmp_path / "draft.json"
    result = CliRunner().invoke(app, ["tagmap", "from-kepware", str(TOY), "--channel", "Line",
                                      "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "20 tags, 18 placed, 2 unmapped" in result.output
    assert result.output.strip().splitlines()[-1].startswith("Next: `fsmes opc-verify`")


def test_the_command_says_what_is_wrong_and_exits_one_on_a_wrong_channel(tmp_path):
    result = CliRunner().invoke(app, ["tagmap", "from-kepware", str(TOY), "--channel", "Lien",
                                      "--out", str(tmp_path / "draft.json")])
    assert result.exit_code == 1
    assert "Export problem" in result.output and "Line, Scratch" in result.output


def test_several_channels_are_drafted_together_and_a_device_with_nothing_placeable_is_listed_whole():
    data, notes = draft_tag_map(TOY, ["Line", "Scratch"])
    assert [m["equipment"] for m in data["machines"]] == ["PRESS-1", "WASH-1"]
    assert data["_unmapped"]["Device1"] == [{"tag": "Tag1", "type": "Word",
                                             "node": "ns=2;s=Scratch.Device1.Tag1"}]
    assert data["_totals"]["tags"] == 21 and data["_totals"]["unmapped"] == 3
    assert any(note.startswith("Device1: none of its 1 tags") for note in notes)


def test_a_device_name_two_channels_share_becomes_two_machines_named_by_their_channel(tmp_path):
    def channel(name):
        return {"common.ALLTYPES_NAME": name, "devices": [{"common.ALLTYPES_NAME": "Device1", "tags": [
            {"common.ALLTYPES_NAME": "FaultCode", "servermain.TAG_DATA_TYPE": 5},
            {"common.ALLTYPES_NAME": "GoodCount", "servermain.TAG_DATA_TYPE": 7}]}]}
    export = tmp_path / "two.json"
    export.write_text(json.dumps({"project": {"channels": [channel("PLC1"), channel("PLC2")]}}),
                      encoding="utf-8")
    out = tmp_path / "draft.json"
    data, _ = write_draft(export, out, ["PLC1", "PLC2"])
    assert [m["equipment"] for m in data["machines"]] == ["PLC1-Device1", "PLC2-Device1"]
    assert {m.equipment for m in load_tag_map(out)} == {"PLC1-Device1", "PLC2-Device1"}
    assert "Device1" in data["machines"][1]["_guess"]["_equipment"]


def test_a_tag_with_no_data_type_is_unmapped_as_type_unknown_and_never_counted(tmp_path):
    export = tmp_path / "untyped.json"
    export.write_text(json.dumps({"project": {"channels": [{"common.ALLTYPES_NAME": "C", "devices": [
        {"common.ALLTYPES_NAME": "M1", "tags": [
            {"common.ALLTYPES_NAME": "FaultCode", "servermain.TAG_DATA_TYPE": 5},
            {"common.ALLTYPES_NAME": "GoodCount"}]}]}]}}), encoding="utf-8")
    data, _ = draft_tag_map(export, "C")
    assert data["_unmapped"]["M1"][0]["type"] == "type unknown"
    assert data["machines"][0]["counts"] is False


def _one_device(tmp_path: Path, tags: list[tuple[str, int]]) -> dict:
    export = tmp_path / "one.json"
    export.write_text(json.dumps({"project": {"channels": [{"common.ALLTYPES_NAME": "C", "devices": [
        {"common.ALLTYPES_NAME": "M1", "tags": [
            {"common.ALLTYPES_NAME": n, "servermain.TAG_DATA_TYPE": t} for n, t in tags]}]}]}}),
        encoding="utf-8")
    return draft_tag_map(export, "C")[0]["machines"][0]


def test_of_two_downtime_tags_the_code_is_the_state_whatever_the_alphabet_says(tmp_path):
    m = _one_device(tmp_path, [("Downtime Flag", 5), ("DowntimeCode", 5)])
    assert m["state_tag"] == "DowntimeCode"
    assert "Downtime Flag" in m["extra_tags"]


def test_of_two_equal_fault_tags_the_first_in_the_export_is_the_state(tmp_path):
    m = _one_device(tmp_path, [("Fault Word 2", 5), ("Fault Word 1", 5)])
    assert m["state_tag"] == "Fault Word 2"


def test_a_shift_total_beside_per_lane_pass_counters_is_recorded_and_not_counted_again(tmp_path):
    m = _one_device(tmp_path, [("Lane1_Pass", 7), ("Lane2_Pass", 7), ("Shift_Total_Pass", 7), ("Lifetime Pass", 7)])
    assert m["counters"]["good"] == ["Lane1_Pass", "Lane2_Pass"]
    assert {"Shift_Total_Pass", "Lifetime Pass"} <= set(m["extra_tags"])
    assert "not booked" in m["_guess"]["Shift_Total_Pass"]


def test_a_lifetime_count_per_side_with_no_good_counter_counts_on_both_sides(tmp_path):
    m = _one_device(tmp_path, [("A side Lifetime Count", 7), ("B side Lifetime Count", 7)])
    assert m["counters"]["good"] == ["A side Lifetime Count", "B side Lifetime Count"]


def test_an_ok_flag_beside_a_good_count_is_recorded_as_a_flag_and_not_counted(tmp_path):
    m = _one_device(tmp_path, [("Tray_Good_Count", 7), ("Tray_OK_To_Process", 5), ("Cycle Was Good", 5)])
    assert m["counters"]["good"] == ["Tray_Good_Count"]
    assert "flag" in m["_guess"]["Tray_OK_To_Process"]
    assert "Cycle Was Good" in m["extra_tags"]
