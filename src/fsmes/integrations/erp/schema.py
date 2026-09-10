"""The ERP contract as a published JSON Schema, with every field explained.

The models in `contract.py` are the contract. They are also Python, which
is no use to the ERP analyst who has to say whether what this MES would
send is correct. So the same models are rendered here as a JSON Schema
document — one an ERP team can read, hand to their own tooling, and check
files against with no connection to the MES and no Python.

Three things are carried per field, because an analyst asks all three:
what it means on the floor, where the MES gets it, and when it is null.
`FIELD_NOTES` holds them, `field_notes_are_complete()` proves none is
missing, and `confirmation_schema()` folds them into the document, so the
published schema explains itself even when it travels alone.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import TypeAdapter

from fsmes.integrations.erp.contract import (
    ComponentUse,
    Confirmation,
    OperationConfirmation,
    OrderCompletion,
    ProductionRequest,
)

SCHEMA_ID = "https://docs.factorysemantics.com/reference/erp-confirmation.schema.json"

#: The documents this schema describes, in the order the docs present them.
MODELS = (OperationConfirmation, OrderCompletion, ComponentUse, ProductionRequest)

#: The two documents the MES sends outbound. The root of the schema is
#: either of these: what the file adapter writes and what `fsmes erp
#: validate` reads. `ProductionRequest` is the inbound direction and lives
#: in `$defs` so a schedule file can be checked against it too.
OUTBOUND = (OperationConfirmation, OrderCompletion)

DOCUMENT_NOTES = {
    "OperationConfirmation": (
        "One step of one order, confirmed the moment the step is done: quantities, the "
        "machine and its cost centre, the time the step was open, and every lot consumed "
        "at it. This is the document an ERP posts labour, machine time and consumption "
        "against."
    ),
    "OrderCompletion": (
        "The order-level close, sent once when the last step of the order is done: what "
        "was ordered, what came out good, what was scrapped, how far the line ran past "
        "the order, and the finished-goods lot if there is one."
    ),
    "ComponentUse": "One lot consumed at one operation. Appears inside an operation confirmation.",
    "ProductionRequest": (
        "The inbound direction: an order arriving from the ERP. Included here because a "
        "plant checking its file exchange usually wants to check both directions with "
        "the same tool."
    ),
}


class FieldNote(NamedTuple):
    """What an ERP analyst asks about a field, in the order they ask it."""

    floor: str
    """What it means on the floor."""
    source: str
    """Where the MES gets it."""
    absent: str
    """When it is null, and why null is the honest value."""

    def as_description(self) -> str:
        return f"{self.floor} Where the MES gets it: {self.source} When it is null: {self.absent}"


_KIND = FieldNote(
    floor="Which of the two documents this is; the first thing a reader should branch on.",
    source="Constant, written by the MES.",
    absent="Never.",
)
_MESSAGE_KEY = FieldNote(
    floor="The idempotency key. A second file carrying a key already seen is the same fact "
          "arriving twice, not a second confirmation, and must be posted once.",
    source="Built from the order and the step: `<order>:op<seq>` for an operation, "
           "`<order>:completion` for the close.",
    absent="Never.",
)
_ORDER = FieldNote(
    floor="The order number the floor ran, as it appears on the dispatch list and the "
          "operator's screen.",
    source="The work order, which took it from the schedule the ERP sent.",
    absent="Never.",
)
_ERP_REFERENCE = FieldNote(
    floor="The ERP's own key for this order, carried back untouched so the ERP can match "
          "the confirmation without parsing the order code.",
    source="The `erp_reference` on the order, set when the ERP's schedule was imported.",
    absent="When the order was raised in the MES rather than sent by an ERP. There is no "
           "ERP key to carry, and inventing one would match the confirmation to the wrong "
           "document.",
)
_MATERIAL = FieldNote(
    floor="What the order makes: the finished-goods code the line is set up to produce.",
    source="The order's material.",
    absent="Never.",
)

FIELD_NOTES: dict[str, dict[str, FieldNote]] = {
    "OperationConfirmation": {
        "kind": _KIND,
        "message_key": _MESSAGE_KEY,
        "order": _ORDER,
        "erp_reference": _ERP_REFERENCE,
        "material": _MATERIAL,
        "seq": FieldNote(
            floor="The step number in the routing — 10, 20, 30 — the same numbering the "
                  "operator sees on the screen.",
            source="The routing as it was when the order was created. Later routing edits "
                   "never rewrite an order that has already run.",
            absent="Never.",
        ),
        "operation": FieldNote(
            floor="What the step is called: Mix, Pack, Deburr.",
            source="The same routing snapshot as `seq`.",
            absent="Never.",
        ),
        "equipment": FieldNote(
            floor="The machine that did the work — an ISA-95 work unit.",
            source="The machine the operation is assigned to.",
            absent="When the step has no machine against it, such as a hand operation. "
                   "Null, not the line's code: a bench is not a machine.",
        ),
        "work_center": FieldNote(
            floor="The line the machine belongs to.",
            source="Walked up the equipment tree from the machine to the first work centre "
                   "above it, however many cells sit in between.",
            absent="When the machine hangs outside any line. That is a real state of the "
                   "equipment tree, not a gap to be filled in.",
        ),
        "cost_center": FieldNote(
            floor="The cost centre this work is accounted to — the account the ERP posts "
                  "the hours and the consumption against.",
            source="The machine's own cost centre, or the first one found above it: cell, "
                   "then line. Plants set it once per line and override only where "
                   "accounting genuinely differs.",
            absent="When nothing from the machine upwards carries one. Null is the honest "
                   "answer: a cost centre guessed here posts a plant's labour to the wrong "
                   "account, and nobody finds it for a month.",
        ),
        "input_qty": FieldNote(
            floor="How many units this step had to work with.",
            source="The order quantity at the first step; the previous step's good quantity "
                   "at every step after it.",
            absent="Never.",
        ),
        "good_qty": FieldNote(
            floor="Units this step made that passed.",
            source="What was booked against the step: machine counter deltas from OPC, or a "
                   "person typing a count.",
            absent="Never. Zero means the step made none; it never means nobody was watching.",
        ),
        "scrap_qty": FieldNote(
            floor="Units this step made that did not pass.",
            source="The same bookings as `good_qty`.",
            absent="Never.",
        ),
        "wip_qty": FieldNote(
            floor="What went into the step and has not come out of it either way: "
                  "`input_qty - good_qty - scrap_qty`. **A negative number is reported, not "
                  "clamped** — it means more came out of the step than went in, which really "
                  "happens when one coalesced counter delta carries the line past the order, "
                  "and hiding it behind a zero would be inventing production.",
            source="Arithmetic on the three quantities above, and nothing else.",
            absent="Never.",
        ),
        "setup_seconds": FieldNote(
            floor="Planned set-up time for this step. Planned, not measured: the MES does "
                  "not watch a fitter change a die.",
            source="The routing snapshot.",
            absent="When the routing carries no set-up time. Null, not zero — 'no set-up "
                   "planned' and 'nobody ever recorded one' are different facts, and only "
                   "one of them means the ERP should post no set-up.",
        ),
        "machine_seconds": FieldNote(
            floor="How long the step was actually open on the machine, in seconds.",
            source="Measured: `completed_at - started_at`.",
            absent="When the step has no start or no finish to subtract. Null rather than "
                   "zero, because a step of unknown length is not a step that took no time.",
        ),
        "labour_seconds": FieldNote(
            floor="Planned labour for what this step handled. Planned, not clocked — nobody "
                  "badges on and off an operation in this MES.",
            source="The routing's labour seconds per unit multiplied by the units handled "
                   "(`good_qty + scrap_qty`).",
            absent="When the routing carries no labour time per unit.",
        ),
        "started_at": FieldNote(
            floor="When the step started, UTC, with no offset marker — the MES's single "
                  "timestamp convention.",
            source="The moment the operation was started, by an operator or by the first "
                   "machine count arriving.",
            absent="When the step was never started.",
        ),
        "completed_at": FieldNote(
            floor="When the step finished, on the same clock as `started_at`.",
            source="The moment the operation was completed.",
            absent="When the step is not finished.",
        ),
        "components": FieldNote(
            floor="Every lot consumed at this step, one line each.",
            source="The consumption booked against the operation, by scanner or by hand.",
            absent="Never null; an empty list means nothing was booked against the step. "
                   "That is not the same as nothing having been used, and an ERP that "
                   "back-flushes a bill of materials should treat an empty list as 'the MES "
                   "has no record', not as 'no material was consumed'.",
        ),
    },
    "OrderCompletion": {
        "kind": _KIND,
        "message_key": _MESSAGE_KEY,
        "order": _ORDER,
        "erp_reference": _ERP_REFERENCE,
        "material": _MATERIAL,
        "ordered_qty": FieldNote(
            floor="What the ERP asked for.",
            source="The order's quantity, as imported from the schedule.",
            absent="Never.",
        ),
        "good_qty": FieldNote(
            floor="What the order actually yielded — the good quantity of its last step, "
                  "not the sum of every step.",
            source="The last operation's good quantity.",
            absent="Never.",
        ),
        "scrap_qty": FieldNote(
            floor="Everything scrapped anywhere on the order, summed across the steps.",
            source="The sum of the operations' scrap.",
            absent="Never.",
        ),
        "over_qty": FieldNote(
            floor="How far past the ordered quantity the line actually ran. Zero on an order "
                  "that stopped where it was asked to — and sent anyway, because a reader "
                  "who never sees the field cannot tell an order that did not over-run from "
                  "one whose over-run was not reported.",
            source="`max(good_qty - ordered_qty, 0)`. Derivable from the two numbers above, "
                   "and stated because an ERP that posts a good quantity without noticing it "
                   "exceeds the order is how a stock discrepancy nobody can explain starts.",
            absent="Never.",
        ),
        "lot": FieldNote(
            floor="The finished-goods lot the order produced — what a goods receipt is "
                  "posted against.",
            source="Named after the order: `<order>-FG`, created when the order completed.",
            absent="When the order yielded nothing good. There is no lot, and an ERP posting "
                   "a receipt against a lot the MES never made is worse than a null.",
        ),
        "started_at": FieldNote(
            floor="When the order started on the floor, UTC, with no offset marker.",
            source="The moment its first step was started.",
            absent="When the order never started.",
        ),
        "completed_at": FieldNote(
            floor="When the order finished, on the same clock as `started_at`.",
            source="The moment its last step was completed.",
            absent="When the order is not finished.",
        ),
    },
    "ComponentUse": {
        "lot": FieldNote(
            floor="The lot number of what was consumed: the number on the pallet, the drum "
                  "or the label.",
            source="The MES lot the consumption was booked against.",
            absent="Never.",
        ),
        "material": FieldNote(
            floor="What that lot is.",
            source="The lot's material.",
            absent="Never.",
        ),
        "quantity": FieldNote(
            floor="How much of that lot this step consumed, in the material's own unit — "
                  "kilograms, litres, each.",
            source="The booked consumption. The unit itself is not in this document; it is "
                   "the material master's, on both sides.",
            absent="Never.",
        ),
        "equipment": FieldNote(
            floor="The machine that consumed it.",
            source="The operation's machine.",
            absent="When the operation has no machine against it.",
        ),
    },
    "ProductionRequest": {
        "code": FieldNote(
            floor="The order number the floor will see.",
            source="The ERP's order or job number, from the schedule file or the API payload.",
            absent="Never; a request with no order code is rejected rather than given a "
                   "number the MES invented.",
        ),
        "material": FieldNote(
            floor="What to make.",
            source="The ERP's material code, which must already exist in the MES's material "
                   "master.",
            absent="Never; a request with no material is rejected.",
        ),
        "quantity": FieldNote(
            floor="How many the ERP is asking for.",
            source="The schedule.",
            absent="Never null. A file that omits it is read as 0 — the one place this "
                   "contract fills a gap, and it fills it with the smallest number rather "
                   "than a plausible one, so an order for nothing looks like an order for "
                   "nothing.",
        ),
        "due_date": FieldNote(
            floor="When the plant promised it.",
            source="The ERP's requested finish date.",
            absent="When the schedule carries none. The MES then sequences by priority "
                   "alone rather than inventing a date the plant never promised.",
        ),
        "priority": FieldNote(
            floor="Which order the dispatch list puts first; lower runs earlier.",
            source="The ERP's priority, or 50 when the schedule does not say.",
            absent="Never; the default is stated rather than left empty.",
        ),
        "erp_reference": FieldNote(
            floor="The ERP's own key for this order, kept so every confirmation can carry "
                  "it back.",
            source="The payload's `erp_reference` or `id`, falling back to the order code.",
            absent="Never in practice, because of that fallback.",
        ),
    },
}


def field_notes_are_complete() -> list[str]:
    """Fields with no note, and notes for fields that no longer exist.

    A contract field nobody explained is a field an ERP team has to guess
    at, so this is asserted by a test rather than left to a reviewer.
    """
    missing: list[str] = []
    for model in MODELS:
        described = FIELD_NOTES.get(model.__name__, {})
        for name in model.model_fields:
            if name not in described:
                missing.append(f"{model.__name__}.{name} has no note")
        for name in described:
            if name not in model.model_fields:
                missing.append(f"{model.__name__}.{name} is explained but no longer exists")
    return missing


def _describe(defs: dict) -> None:
    """Fold the notes into the generated `$defs`, in place."""
    for name, definition in defs.items():
        if name in DOCUMENT_NOTES:
            definition["description"] = DOCUMENT_NOTES[name]
        for field, prop in definition.get("properties", {}).items():
            note = FIELD_NOTES.get(name, {}).get(field)
            if note is None:
                continue
            prop["description"] = note.as_description()
            prop["x-fsmes-floor"] = note.floor
            prop["x-fsmes-source"] = note.source
            prop["x-fsmes-null"] = note.absent


def confirmation_schema() -> dict:
    """The published JSON Schema for what this MES sends an ERP.

    Generated from the contract models every time, so it cannot drift from
    the code that writes the files.
    """
    generated = TypeAdapter(Confirmation).json_schema(ref_template="#/$defs/{model}")
    defs = generated.get("$defs", {})
    # ProductionRequest is the other direction and is not part of the root
    # union, but a plant checking its file exchange wants both, so it is
    # published in the same document.
    request = ProductionRequest.model_json_schema(ref_template="#/$defs/{model}")
    defs.update(request.pop("$defs", {}))
    defs["ProductionRequest"] = request
    _describe(defs)
    root = {k: v for k, v in generated.items() if k != "$defs"}
    # `oneOf`, not `anyOf`: the two documents are told apart by `kind`, and a
    # validator that says "matched neither" is more use to an ERP team than
    # one that says "matched none of the alternatives" twice over.
    if "anyOf" in root:
        root = {"oneOf": root.pop("anyOf"), **root}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "FactorySemantics MES — outbound ERP confirmation",
        "description": (
            "What this MES sends an ERP when work is done: one confirmation per completed "
            "operation, one completion per finished order. SAP-shaped on purpose — a "
            "confirmation per operation carrying quantities, cost centre, times and "
            "component consumption is what CO11N wants and what ERPNext's Job Cards and "
            "Stock Entries are built from — but shaped for SAP is not the same as tested "
            "against SAP, and no SAP has consumed one of these. Times are UTC without an "
            "offset marker. There is no money anywhere in this document: the MES reports "
            "quantities and time against a cost centre, and the ERP owns what they are "
            "worth."
        ),
        **root,
        "$defs": defs,
    }
