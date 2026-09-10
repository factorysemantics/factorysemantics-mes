"""The confirmation handoff: a published schema, worked example files, and
a validator a plant's ERP team can run with no ERP attached.

The whole point of this handoff is that somebody who has never seen this
code can hold a file and a rule against each other. So these tests pin the
three things that makes possible: every contract field is explained, the
example files on disk are what a run really produces, and the validator
catches the things an ERP would be right to reject.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from fsmes.cli import app
from fsmes.integrations.erp import b2mml, examples, schema, validate
from fsmes.integrations.erp.contract import ComponentUse, OperationConfirmation, OrderCompletion
from fsmes.integrations.erp.file_adapter import FileErpAdapter

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "operate" / "confirmation-examples"
runner = CliRunner()


def _operation(**overrides) -> dict:
    payload = {
        "kind": "operation_confirmation", "message_key": "WO-1:op10", "order": "WO-1",
        "material": "FG-COLA", "seq": 10, "operation": "Mix", "equipment": "MIX01",
        "work_center": "LINE1", "cost_center": "CC-1", "input_qty": 10.0, "good_qty": 9.0,
        "scrap_qty": 1.0, "wip_qty": 0.0, "machine_seconds": 60.0,
        "started_at": "2026-03-04T06:00:00", "completed_at": "2026-03-04T06:01:00",
        "components": [],
    }
    return {**payload, **overrides}


def _completion(**overrides) -> dict:
    payload = {
        "kind": "order_completion", "message_key": "WO-1:completion", "order": "WO-1",
        "material": "FG-COLA", "ordered_qty": 10.0, "good_qty": 9.0, "scrap_qty": 1.0,
        "over_qty": 0.0, "lot": "WO-1-FG",
    }
    return {**payload, **overrides}


def _problems(payload: dict) -> list[str]:
    return [f.says for f in validate.check_payload(payload)[1] if f.severity == "problem"]


def _notes(payload: dict) -> list[str]:
    return [f.says for f in validate.check_payload(payload)[1] if f.severity == "note"]


# --------------------------------------------------------------- the schema

def test_every_field_of_the_contract_is_explained_before_it_is_published():
    """A field nobody explained is a field an ERP team has to guess at.

    This fails when a field is added to the contract and left undescribed,
    which is the only way the published schema can go quietly incomplete.
    """
    assert schema.field_notes_are_complete() == []


def test_the_published_schema_says_what_each_field_means_where_it_comes_from_and_when_it_is_null():
    published = schema.confirmation_schema()
    for name, definition in published["$defs"].items():
        assert definition["description"], f"{name} has no description"
        for field, prop in definition["properties"].items():
            where = f"{name}.{field}"
            assert prop.get("description"), f"{where} has no description"
            assert prop.get("x-fsmes-floor"), f"{where} does not say what it means on the floor"
            assert prop.get("x-fsmes-source"), f"{where} does not say where the MES gets it"
            assert prop.get("x-fsmes-null"), f"{where} does not say when it is null"


def test_the_schema_is_the_two_outbound_documents_told_apart_by_kind():
    published = schema.confirmation_schema()
    assert published["oneOf"] == [{"$ref": "#/$defs/OperationConfirmation"},
                                  {"$ref": "#/$defs/OrderCompletion"}]
    for name, kind in (("OperationConfirmation", "operation_confirmation"),
                       ("OrderCompletion", "order_completion")):
        assert published["$defs"][name]["properties"]["kind"]["const"] == kind
    # The inbound direction travels in the same document, because a plant
    # checking its file exchange checks both directions.
    assert "ProductionRequest" in published["$defs"]


def test_the_schema_never_claims_an_sap_has_read_one_of_these():
    """SAP-shaped is not SAP-tested, and the schema is where a reader who
    has read nothing else will look for that distinction."""
    assert "no SAP has consumed one of these" in schema.confirmation_schema()["description"]


# ------------------------------------------------------------- the documents

def test_a_confirmation_survives_the_trip_out_to_b2mml_and_back():
    """The writer and the reader share one table of elements, so this is
    what proves the table is right in both directions."""
    for payload in (_operation(components=[{"lot": "LOT-1", "material": "RAW-SUGAR",
                                            "quantity": 2.5, "equipment": "MIX01"}]),
                    _completion()):
        back = b2mml.parse_confirmation(b2mml.render_confirmation(payload))
        stated = {k: v for k, v in payload.items() if v is not None and v != []}
        assert back == {**stated, **{k: back[k] for k in ("kind",)}}


def test_the_idempotency_key_travels_in_the_file_and_is_rebuilt_when_it_did_not():
    """The key is what stops one confirmation being posted twice, and until
    2026-09-10 the B2MML rendering dropped it on the floor."""
    xml = b2mml.render_confirmation(_operation())
    assert "<MessageKey>WO-1:op10</MessageKey>" in xml
    older = xml.replace("<MessageKey>WO-1:op10</MessageKey>\n    ", "")
    assert b2mml.parse_confirmation(older)["message_key"] == "WO-1:op10"


# ---------------------------------------------------------- the example files

def test_the_worked_examples_on_disk_are_what_a_run_of_the_demo_plant_produces(tmp_path):
    """Generated, not typed. A hand-edited example is a statement about
    what somebody thinks the MES sends, and this is what stops one."""
    examples.write(tmp_path)
    published = sorted(p.name for p in EXAMPLES.iterdir())
    assert published == sorted(p.name for p in tmp_path.iterdir())
    for name in published:
        assert (EXAMPLES / name).read_text(encoding="utf-8") == \
            (tmp_path / name).read_text(encoding="utf-8"), f"{name} has drifted from the run"


def test_the_examples_cover_the_cases_an_erp_team_asks_about():
    by_name = {example.name: example for example in examples.generate()}
    assert set(by_name) == {"operation-clean", "operation-with-scrap-and-components",
                            "operation-over-run", "completion-with-a-lot",
                            "completion-with-an-over-run", "completion-without-a-lot"}
    assert by_name["operation-over-run"].payload["wip_qty"] == -1.0
    assert by_name["operation-with-scrap-and-components"].payload["cost_center"] == "CC-PKG-1"
    assert len(by_name["operation-with-scrap-and-components"].payload["components"]) == 2
    assert by_name["completion-with-an-over-run"].payload["over_qty"] == 1.0
    assert by_name["completion-without-a-lot"].payload["lot"] is None
    # Every one of them came down from an ERP, so the reference the ERP
    # matches the confirmation back on is really there.
    assert all(e.payload["erp_reference"] for e in by_name.values())


def test_every_published_example_passes_the_validator():
    report = validate.validate(EXAMPLES)
    assert report.ok, [f.says for d in report.documents for f in d.problems]
    assert len(report.documents) == 12  # six confirmations, as JSON and as B2MML


# ------------------------------------------------------------- the validator

def test_the_validator_refuses_what_an_erp_would_be_right_to_refuse():
    assert "wip_qty says 5" in _problems(_operation(wip_qty=5.0))[0]
    assert "negative number" in _problems(_operation(good_qty=-1.0, wip_qty=11.0))[0]
    assert "before it started" in _problems(
        _operation(started_at="2026-03-04T07:00:00", completed_at="2026-03-04T06:00:00",
                   machine_seconds=None))[0]
    assert "machine_seconds is 999" in _problems(_operation(machine_seconds=999.0))[0]
    assert "message_key is empty" in _problems(_operation(message_key=" "))[0]
    assert "is not a confirmation" in _problems(
        {"kind": "equipment_state", "equipment": "MIX01", "state": "idle"})[0]
    assert _problems(_completion(good_qty=12.0, over_qty=0.0))[0].startswith("good_qty 12 is 2 past")


def test_an_over_run_that_is_reported_as_one_is_accepted_and_named():
    """The over-run itself is not the problem. Hiding it is."""
    assert _problems(_completion(good_qty=12.0, over_qty=2.0)) == []
    assert "ran 2 past the order" in _notes(_completion(good_qty=12.0, over_qty=2.0))[0]


def test_a_negative_work_in_progress_is_reported_not_failed():
    """More out of a step than went into it is real, and clamping it to
    zero would be inventing production in the other direction."""
    assert _problems(_operation(input_qty=15.0, good_qty=16.0, scrap_qty=0.0, wip_qty=-1.0)) == []
    notes = _notes(_operation(input_qty=15.0, good_qty=16.0, scrap_qty=0.0, wip_qty=-1.0))
    assert "put out 1 more than went into it" in notes[0]


def test_a_missing_cost_centre_is_said_out_loud_rather_than_filled_in():
    assert "no cost_center" in _notes(_operation(cost_center=None))[0]
    assert _problems(_operation(cost_center=None)) == []


def test_two_files_with_one_key_and_two_stories_are_a_problem(tmp_path):
    (tmp_path / "first.json").write_text(json.dumps(_operation()), encoding="utf-8")
    (tmp_path / "second.json").write_text(json.dumps(_operation(good_qty=8.0, wip_qty=1.0)),
                                          encoding="utf-8")
    report = validate.validate(tmp_path)
    assert not report.ok
    assert "not the same content" in report.documents[1].problems[0].says


def test_a_folder_says_what_it_skipped_as_well_as_what_it_checked(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps(_completion()), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("collected by hand on Tuesday", encoding="utf-8")
    (tmp_path / ".half.xml.part").write_text("<Production", encoding="utf-8")
    report = validate.validate(tmp_path)
    assert report.ok
    assert len(report.documents) == 1 and len(report.skipped) == 2
    assert "1 documents checked" in report.render()[-1] and "2 files skipped" in report.render()[-1]


# ------------------------------------------------- the outbox, end to end

def _write_some(adapter: FileErpAdapter) -> None:
    adapter.send_confirmation(OperationConfirmation(
        message_key="WO-9:op10", order="WO-9", material="FG-COLA", seq=10, operation="Mix",
        equipment="MIX01", work_center="LINE1", cost_center="CC-1", input_qty=5, good_qty=5,
        scrap_qty=0, wip_qty=0, machine_seconds=120.0,
        components=[ComponentUse(lot="LOT-1", material="RAW-SUGAR", quantity=2.5)]))
    adapter.send_confirmation(OperationConfirmation(
        message_key="WO-9:op20", order="WO-9", material="FG-COLA", seq=20, operation="Pack",
        equipment="PACK01", work_center="LINE1", cost_center="CC-1", input_qty=5, good_qty=5,
        scrap_qty=0, wip_qty=0))
    adapter.send_confirmation(OrderCompletion(
        message_key="WO-9:completion", order="WO-9", material="FG-COLA", ordered_qty=5,
        good_qty=5, scrap_qty=0, lot="WO-9-FG"))


def test_an_outbox_the_file_adapter_wrote_validates_as_it_stands(tmp_path):
    """The definition of done for this whole handoff: point the validator
    at a folder this MES filled, and get an answer."""
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    _write_some(adapter)
    report = validate.validate(tmp_path / "out")
    assert report.ok, [f.says for d in report.documents for f in d.problems]
    assert len(report.documents) == 3
    result = runner.invoke(app, ["erp", "validate", str(tmp_path / "out")])
    assert result.exit_code == 0
    assert "3 documents checked: 3 correct" in result.output


def test_the_outbox_sorts_by_name_into_the_order_the_confirmations_happened(tmp_path):
    """A collector reads the folder in name order, so name order has to be
    write order. Second-resolution timestamps were not: two confirmations
    written in the same second sorted by order code, and the same step
    written twice overwrote itself."""
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    _write_some(adapter)
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert [n.split("_")[0] for n in names] == ["000001", "000002", "000003"]
    assert [n.split("_", 2)[2] for n in names] == ["WO-9_op10.xml", "WO-9_op20.xml",
                                                   "WO-9_completion.xml"]


def test_a_restarted_worker_carries_on_numbering_where_the_folder_left_off(tmp_path):
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    _write_some(adapter)
    restarted = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    restarted.send_confirmation(OrderCompletion(
        message_key="WO-10:completion", order="WO-10", material="FG-COLA", ordered_qty=1,
        good_qty=1, scrap_qty=0, lot="WO-10-FG"))
    assert any(p.name.startswith("000004_") for p in (tmp_path / "out").iterdir())


def test_an_order_code_can_never_decide_where_a_confirmation_lands(tmp_path):
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    adapter.send_confirmation(OrderCompletion(
        message_key="../escape:completion", order="../escape", material="FG-COLA",
        ordered_qty=1, good_qty=1, scrap_qty=0))
    [written] = list((tmp_path / "out").iterdir())
    assert written.parent == tmp_path / "out"
    assert "escape" in written.name and ".." not in written.name


def test_the_validator_exits_non_zero_so_it_can_gate_a_deployment(tmp_path):
    (tmp_path / "wrong.json").write_text(json.dumps(_operation(wip_qty=99.0)), encoding="utf-8")
    result = runner.invoke(app, ["erp", "validate", str(tmp_path)])
    assert result.exit_code == 1
    assert "wip_qty says 99" in result.output
    missing = runner.invoke(app, ["erp", "validate", str(tmp_path / "nowhere")])
    assert missing.exit_code == 1 and "does not exist" in missing.output
