"""The shadow scorecard, checked against a run whose differences are known.

There is no real plant's data here and never will be. The evidence is the
same scripted demo run the published confirmation examples come from, and
the "incumbent's export" is that run written out again as a file with a
short list of deliberate perturbations applied to it. That makes the whole
truth known: the report must name every perturbation, and nothing else.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fsmes.integrations.erp import examples, incumbent, scorecard, scorecard_html
from fsmes.integrations.erp.incumbent import Mapping, MappingError

#: Every perturbation, and the differences each one must produce.
#: `(order, operation, field)`. This list is the test's specification and
#: the assertions below are generated from it, so a perturbation cannot be
#: added without saying what it should surface.
LATE_BY = timedelta(minutes=7)
SHORT_BY = 1.0
EXTRA_SCRAP = 3.0

EXPECTED_DIFFERENCES = {
    ("WO-2026-0041", "10", "good_qty"),        # a unit the two records do not share
    ("WO-2026-0041", "20", "completed_at"),    # the step booked seven minutes late
    ("WO-2026-0041", "20", "duration_seconds"),  # which makes it seven minutes longer
    ("WO-2026-0042", "10", "scrap_qty"),       # three units of scrap on one side only
}
ONLY_THE_INCUMBENT_BOOKED = "WO-2026-0099"
ONLY_THIS_MES_HAS = "WO-2026-0043"


@pytest.fixture(scope="module")
def run() -> dict[str, dict]:
    """The scripted demo run's nine confirmations, on its fixed clock."""
    return examples.confirmations()


def our_confirmations(tmp_path: Path, run: dict[str, dict]) -> Path:
    """This MES's side, as the file adapter's outbox would hold it."""
    folder = tmp_path / "outbox"
    folder.mkdir(exist_ok=True)
    for key, payload in run.items():
        (folder / f"{key.replace(':', '_')}.json").write_text(
            json.dumps(payload), encoding="utf-8")
    return folder


def faithful_export(run: dict[str, dict]) -> list[dict]:
    """The same run again, in the incumbent's generic export shape."""
    rows = []
    for payload in run.values():
        operation = str(payload["seq"]) if payload["kind"] == "operation_confirmation" else ""
        rows.append({
            "order": payload["order"],
            "operation": operation,
            "equipment": payload.get("equipment") or "",
            "good_qty": payload["good_qty"],
            "scrap_qty": payload["scrap_qty"],
            "started_at": payload.get("started_at") or "",
            "completed_at": payload.get("completed_at") or "",
        })
    return rows


def perturbed_export(run: dict[str, dict]) -> list[dict]:
    """The faithful export with the listed perturbations, and no others."""
    rows = [dict(row) for row in faithful_export(run)]
    by_key = {(row["order"], row["operation"]): row for row in rows}

    by_key[("WO-2026-0041", "10")]["good_qty"] = (
        float(by_key[("WO-2026-0041", "10")]["good_qty"]) - SHORT_BY)
    late = datetime.fromisoformat(by_key[("WO-2026-0041", "20")]["completed_at"]) + LATE_BY
    by_key[("WO-2026-0041", "20")]["completed_at"] = late.isoformat()
    by_key[("WO-2026-0042", "10")]["scrap_qty"] = (
        float(by_key[("WO-2026-0042", "10")]["scrap_qty"]) + EXTRA_SCRAP)
    # A cell the export simply does not fill in. Unknown, not zero.
    by_key[("WO-2026-0042", "20")]["scrap_qty"] = ""

    rows = [row for row in rows if row["order"] != ONLY_THIS_MES_HAS]
    rows.append({"order": ONLY_THE_INCUMBENT_BOOKED, "operation": "10", "equipment": "MIX-1",
                 "good_qty": 40, "scrap_qty": 0, "started_at": "2026-03-04 09:00:00",
                 "completed_at": "2026-03-04 09:30:00"})
    return rows


def write_csv(path: Path, rows: list[dict], columns: dict[str, str] | None = None) -> Path:
    """Write an export, optionally under the incumbent's own headings."""
    columns = columns or {}
    headings = [columns.get(c, c) for c in incumbent.COLUMNS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headings)
        writer.writeheader()
        for row in rows:
            writer.writerow({columns.get(c, c): row.get(c, "") for c in incumbent.COLUMNS})
    return path


def card_for(tmp_path: Path, run: dict[str, dict], rows: list[dict]) -> scorecard.Scorecard:
    export = incumbent.read(write_csv(tmp_path / "incumbent.csv", rows))
    ours = scorecard.read_confirmations(our_confirmations(tmp_path, run))
    return scorecard.compare(export, ours)


# ---------------------------------------------------------------- the record


def test_a_run_compared_with_a_faithful_copy_of_itself_reports_no_difference(tmp_path, run):
    card = card_for(tmp_path, run, faithful_export(run))
    assert card.differences == []
    assert card.orders_only_incumbent == [] and card.orders_only_mes == []
    assert card.operations_only_incumbent == [] and card.operations_only_mes == []
    assert card.agreed == card.compared > 0


def test_the_report_names_every_perturbation_and_nothing_else(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    assert {(d.order, d.operation, d.field) for d in card.differences} == EXPECTED_DIFFERENCES
    assert len(card.differences) == len(EXPECTED_DIFFERENCES)


def test_an_order_only_one_record_holds_is_named_on_the_side_that_holds_it(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    assert card.orders_only_incumbent == [ONLY_THE_INCUMBENT_BOOKED]
    assert card.orders_only_mes == [ONLY_THIS_MES_HAS]
    assert (ONLY_THE_INCUMBENT_BOOKED, "10") in card.operations_only_incumbent
    assert {order for order, _ in card.operations_only_mes} == {ONLY_THIS_MES_HAS}


def test_a_blank_cell_counts_as_not_compared_rather_than_as_agreement(tmp_path, run):
    faithful = {a.field: a for a in card_for(tmp_path, run, faithful_export(run)).operation_agreement}
    perturbed = {a.field: a for a in
                 card_for(tmp_path, run, perturbed_export(run)).operation_agreement}
    assert faithful["scrap_qty"].not_stated == 0
    # The blanked cell, and the operations of the dropped order, are all
    # not-compared rather than agreements.
    assert perturbed["scrap_qty"].not_stated == 1
    assert perturbed["scrap_qty"].agree < faithful["scrap_qty"].agree


def test_every_list_states_the_total_it_came_out_of(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    as_dict = card.as_dict()
    assert as_dict["orders"]["total"] == (len(card.orders_both) + len(card.orders_only_incumbent)
                                          + len(card.orders_only_mes))
    assert as_dict["operations"]["total"] == (len(card.operations_both)
                                              + len(card.operations_only_incumbent)
                                              + len(card.operations_only_mes))
    for agreement in card.operation_agreement:
        assert agreement.total == agreement.agree + agreement.differ + agreement.not_stated


def test_what_it_cannot_tell_you_names_both_records_gaps(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    said = " ".join(card.cannot_tell)
    assert ONLY_THE_INCUMBENT_BOOKED in said and ONLY_THIS_MES_HAS in said
    assert "not compared" in said or "was compared" in said
    assert "neither record covers" in said


def test_it_never_says_which_record_is_right(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    said = " ".join(card.render()).lower() + " " + card.to_json().lower()
    for verdict in ("wrong", "incorrect", "missed", "failed", "error", "inaccurate"):
        assert verdict not in said, f"the scorecard passed judgement: {verdict!r}"
    assert "does not say which" in said


def test_order_totals_are_compared_only_when_the_export_carries_them(tmp_path, run):
    only_operations = [row for row in faithful_export(run) if row["operation"]]
    card = card_for(tmp_path, run, only_operations)
    assert all(agreement.total == 0 for agreement in card.order_agreement)
    assert any("no order-total rows" in says for says in card.cannot_tell)
    assert any("summing them" in says for says in card.cannot_tell)


# ---------------------------------------------------------------- the mapping


def test_the_incumbents_own_headings_and_codes_are_config_not_code(tmp_path, run):
    headings = {"order": "ORDER_NO", "operation": "OPER", "equipment": "WORK_CTR",
                "good_qty": "QTY_OK", "scrap_qty": "QTY_SCRAP",
                "started_at": "DT_START", "completed_at": "DT_END"}
    rows = []
    for row in faithful_export(run):
        renamed = dict(row)
        renamed["order"] = row["order"].replace("WO-2026-00", "47")
        if row["operation"]:
            renamed["operation"] = row["operation"].rjust(4, "0")
        rows.append(renamed)
    path = write_csv(tmp_path / "theirs.csv", rows, headings)
    (tmp_path / "map.json").write_text(json.dumps({
        "columns": headings,
        "orders": {f"47{n}": f"WO-2026-00{n}" for n in ("41", "42", "43")},
        "operations": {"0010": "10", "0020": "20"},
    }), encoding="utf-8")

    mapping = Mapping.load(tmp_path / "map.json")
    card = scorecard.compare(incumbent.read(path, mapping),
                             scorecard.read_confirmations(our_confirmations(tmp_path, run)))
    assert card.orders_only_incumbent == [] and card.orders_only_mes == []
    assert card.differences == []


def test_a_mapping_file_with_a_section_this_reader_does_not_use_is_refused(tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"column": {"order": "X"}}), encoding="utf-8")
    with pytest.raises(MappingError) as raised:
        Mapping.load(tmp_path / "map.json")
    assert "Nothing was mapped" in str(raised.value)


def test_a_mapping_file_naming_a_column_this_reader_does_not_read_is_refused(tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"columns": {"batch": "CHARG"}}),
                                       encoding="utf-8")
    with pytest.raises(MappingError):
        Mapping.load(tmp_path / "map.json")


def test_tolerances_come_from_the_mapping_file_and_an_argument_beats_it(tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"tolerances": {"quantity": 2, "seconds": 5}}),
                                       encoding="utf-8")
    mapping = Mapping.load(tmp_path / "map.json")
    assert scorecard.Tolerances.from_mapping(mapping) == scorecard.Tolerances(2.0, 5.0)
    assert scorecard.Tolerances.from_mapping(mapping, quantity=0).quantity == 0.0


def test_a_quantity_tolerance_the_plant_sets_is_applied_and_printed(tmp_path, run):
    rows = perturbed_export(run)
    export = incumbent.read(write_csv(tmp_path / "incumbent.csv", rows))
    ours = scorecard.read_confirmations(our_confirmations(tmp_path, run))
    card = scorecard.compare(export, ours, scorecard.Tolerances(quantity=SHORT_BY, seconds=60))
    assert ("WO-2026-0041", "10", "good_qty") not in {
        (d.order, d.operation, d.field) for d in card.differences}
    assert "within 1 unit" in " ".join(card.render())


def test_a_row_with_no_order_code_is_counted_rather_than_dropped(tmp_path, run):
    rows = [*faithful_export(run), {"order": "", "operation": "10", "good_qty": 5}]
    export = incumbent.read(write_csv(tmp_path / "incumbent.csv", rows))
    assert export.rows_read == len(rows)
    assert len(export.bookings) == len(rows) - 1
    assert export.unusable and "no order code" in export.unusable[0][1]


def test_a_timestamp_the_export_writes_its_own_way_needs_a_format_not_a_guess(tmp_path, run):
    rows = [dict(row) for row in faithful_export(run)]
    for row in rows:
        for column in ("started_at", "completed_at"):
            if row[column]:
                row[column] = datetime.fromisoformat(row[column]).strftime("%d.%m.%Y %H:%M:%S")
    path = write_csv(tmp_path / "incumbent.csv", rows)

    guessed = incumbent.read(path)
    assert guessed.unreadable, "a timestamp it cannot parse must be reported, not assumed"
    assert all(b.started_at is None for b in guessed.bookings)

    told = incumbent.read(path, Mapping(time_format="%d.%m.%Y %H:%M:%S"))
    assert told.unreadable == []
    assert scorecard.compare(told, scorecard.read_confirmations(
        our_confirmations(tmp_path, run))).differences == []


def test_the_same_confirmation_as_json_and_as_xml_is_one_confirmation(tmp_path, run):
    folder = tmp_path / "outbox"
    written = examples.write(folder)
    assert any(p.suffix == ".xml" for p in written) and any(p.suffix == ".json" for p in written)
    ours = scorecard.read_confirmations(folder)
    assert len(ours.operations) + len(ours.completions) == len(examples.PUBLISHED)


# ---------------------------------------------------------------- the reports


def test_the_html_report_is_one_file_that_needs_nothing_from_the_internet(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    page = scorecard_html.render(card)
    assert "http://" not in page and "https://" not in page
    assert "<script" not in page
    assert "does not say which side is right" in page
    for order, operation, _ in EXPECTED_DIFFERENCES:
        assert order in page and f">{operation}<" in page
    assert f"all {len(card.differences)}" in page


def test_the_json_holds_every_difference_and_says_what_it_could_not_compare(tmp_path, run):
    card = card_for(tmp_path, run, perturbed_export(run))
    loaded = json.loads(card.to_json())
    assert loaded["kind"] == "shadow_scorecard"
    assert len(loaded["differences"]) == len(EXPECTED_DIFFERENCES)
    assert loaded["what_this_cannot_tell_you"]
    assert loaded["agreement"]["compared"] == card.compared


def test_the_command_writes_both_reports_and_says_what_it_compared(tmp_path, run):
    from typer.testing import CliRunner

    from fsmes.cli import app

    rows = perturbed_export(run)
    write_csv(tmp_path / "incumbent.csv", rows)
    our_confirmations(tmp_path, run)
    result = CliRunner().invoke(app, [
        "shadow", "scorecard",
        "--incumbent", str(tmp_path / "incumbent.csv"),
        "--ours", str(tmp_path / "outbox"),
        "--json", str(tmp_path / "card.json"),
        "--html", str(tmp_path / "card.html"),
    ])
    assert result.exit_code == 0, result.output
    assert "orders" in result.output and "what this cannot tell you" in result.output
    assert json.loads((tmp_path / "card.json").read_text())["kind"] == "shadow_scorecard"
    assert (tmp_path / "card.html").read_text().startswith("<!doctype html>")


def test_two_records_with_no_order_code_in_common_say_so_rather_than_report_nothing(tmp_path, run):
    from typer.testing import CliRunner

    from fsmes.cli import app

    rows = [dict(row, order="X" + row["order"]) for row in faithful_export(run)]
    write_csv(tmp_path / "incumbent.csv", rows)
    our_confirmations(tmp_path, run)
    result = CliRunner().invoke(app, [
        "shadow", "scorecard", "--incumbent", str(tmp_path / "incumbent.csv"),
        "--ours", str(tmp_path / "outbox")])
    assert result.exit_code == 1
    assert "Not one order code appears in both records" in result.output
