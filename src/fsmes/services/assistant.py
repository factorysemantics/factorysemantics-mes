"""The floor assistant: answer, propose, or show me.

Three things a person on a plant floor asks, and they want different replies:

    "what is the fill weight spec?"        -> answer it
    "release the next bottling order"      -> propose it, and let me confirm
    "how do I record an inspection?"       -> show me, on my screen

The third is the one that matters and the one nobody ships. Training a new
operator is walking them to the screen and pointing; a chatbot that instead
performs the task for them has taught nothing. So a guide is a sequence of
real UI elements, and the widget highlights them in turn.

Guides are authored data, not model output. An 8B model picks reliably from a
short list of titles; it does not reliably invent correct CSS selectors, and a
guide that highlights the wrong button is worse than no guide. So the model
routes and explains, and the steps are always exactly right.

Actions are proposed, never performed. The assistant returns the request it
would make and the screen asks for confirmation - the same discipline as
dry_run on the agent tools, for the same reason: a plant is not a place for a
model to act unattended.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
#: The model this product ships with, and the default of `[system]
#: local_model_name`. Which model on this machine answers is the plant's
#: hardware and the plant's choice, so every function that asks one takes the
#: name rather than reading this - the caller has the session to read it
#: through and this module deliberately has none.
MODEL = "qwen3:8b"

#: How much of this plant's own facts reach the model, and how long it is
#: given to answer. `[admin] assistant_context_chars` and
#: `assistant_timeout_seconds`; both defaults are the literals that were here.
CONTEXT_CHARS = 3000
TIMEOUT = 60.0

# --------------------------------------------------------------------------
# Guides. Each step names a `data-assist` anchor, which is stable in a way an
# id or a nth-child selector is not - it exists to be pointed at.
# --------------------------------------------------------------------------

GUIDES: list[dict] = [
    {
        "id": "record-check",
        "title": "Record a quality inspection",
        "when": (
                 "recording a measurement, entering a fill weight, logging an inspection result, "
                 "doing a quality check"
                ),
        "needs": "quality.record",
        "steps": [
            {"page": "/dashboard", "anchor": "quality-form",
             "title": "Find the quality check panel",
             "body": (
                      "Inspections are recorded from the floor screen, in the shop-floor actions "
                      "panel."
                     )},
            {"page": "/dashboard", "anchor": "quality-spec",
             "title": "Choose the characteristic",
             "body": (
                      "Pick what you measured. The list only shows characteristics with a "
                      "specification, because a measurement with nothing to judge it against "
                      "cannot pass or fail."
                     )},
            {"page": "/dashboard", "anchor": "quality-value",
             "title": "Type the measured value",
             "body": (
                      "Enter what the gauge actually read. Do not round toward the middle of the "
                      "tolerance - an out-of-spec reading is meant to be recorded."
                     )},
            {"page": "/dashboard", "anchor": "quality-submit",
             "title": "Record it",
             "body": (
                      "If the value falls outside the specification the MES raises a non- "
                      "conformance automatically. That is the system working, not an error you "
                      "caused."
                     )},
            {"page": "/dashboard/quality", "anchor": "measurement-chart",
             "title": "See it against the limits",
             "body": "Your reading joins the chart here, plotted against the specification band."},
        ],
    },
    {
        "id": "find-instruction",
        "title": "Find the work instruction for a job",
        "when": (
            "finding a procedure, reading the work instruction, what does the "
            "SOP say, how is this job supposed to be done, which revision is "
            "current"
        ),
        "needs": "plant.read",
        "steps": [
            {"page": "/dashboard/quality",
             "anchor": "instruction-inline",
             "title": "The procedure sits beside the work",
             "body": (
                 "Choosing a characteristic shows the approved instruction for "
                 "it right here, so you are not hunting through a folder while "
                 "holding a gauge."
             )},
            {"page": "/dashboard/instructions",
             "anchor": "instruction-list",
             "title": "Or open the full catalogue",
             "body": (
                 "Every controlled document, showing which revision is in "
                 "force. That revision is the one you follow."
             )},
            {"page": "/dashboard/instructions",
             "anchor": "instruction-body",
             "title": "Read it, and check its history",
             "body": (
                 "The revision history below shows who approved it and when, "
                 "and lets you read what a superseded revision said - which is "
                 "the question an auditor asks."
             )},
        ],
    },
    {
        "id": "book-production",
        "title": "Book output",
        "when": (
                 "booking output, reporting good parts, recording scrap, entering counts a "
                 "machine made"
                ),
        "needs": "production.book",
        "steps": [
            {"page": "/dashboard", "anchor": "report-form",
             "title": "Open the production panel",
             "body": "Booking output is on the floor screen."},
            {"page": "/dashboard", "anchor": "report-equipment",
             "title": "Pick the machine",
             "body": "Choose the machine that made the parts."},
            {"page": "/dashboard", "anchor": "report-order",
             "title": "Say which order",
             "body": (
                      "It defaults to the order this machine is running, but with two "
                      "orders queued the default is a guess - naming it is what stops "
                      "output landing on the wrong order and surfacing at month end."
                     )},
            {"page": "/dashboard", "anchor": "report-good",
             "title": "Enter good and scrap",
             "body": (
                      "Count what was actually made. Scrap is not a failure to hide - unrecorded "
                      "scrap makes the yield figure a lie."
                     )},
            {"page": "/dashboard", "anchor": "report-submit",
             "title": "Book it",
             "body": (
                      "The counts land against the order and appear in the audit trail under your "
                      "name."
                     )},
        ],
    },
    {
        "id": "issue-material",
        "title": "Issue material to an order",
        "when": (
                 "issuing material, consuming a lot, adding raw material to an order, material "
                 "issue"
                ),
        "needs": "production.consume",
        "steps": [
            {"page": "/dashboard", "anchor": "consume-form",
             "title": "Open the material panel",
             "body": "Issuing material is on the floor screen."},
            {"page": "/dashboard", "anchor": "consume-order",
             "title": "Choose the order and the lot",
             "body": (
                      "Which order is consuming, and from which lot. This pairing is what builds "
                      "genealogy - later it answers 'what went into this batch'."
                     )},
            {"page": "/dashboard", "anchor": "consume-submit",
             "title": "Issue it",
             "body": (
                      "The lot's remaining quantity drops and the consumption is recorded against "
                      "the order."
                     )},
            {"page": "/dashboard/orders", "anchor": "order-genealogy",
             "title": "See it in genealogy",
             "body": "What you issued now appears here, under Consumed."},
        ],
    },
    {
        "id": "label-a-stop",
        "title": "Say why the machine is down",
        "when": (
            "machine stopped, marking a machine down, labelling downtime, "
            "recording a stop reason, why is the line down"
        ),
        "needs": "equipment.state",
        "steps": [
            {"page": "/dashboard/station",
             "anchor": "station-machine",
             "title": "Your machine",
             "body": "The station remembers which machine is yours; change it here."},
            {"page": "/dashboard/station",
             "anchor": "station-state",
             "title": "Hit the state that is true",
             "body": (
                 "Going down asks why before it accepts - an unlabelled stop "
                 "is the row the downtime pareto cannot explain."
             )},
            {"page": "/dashboard/station",
             "anchor": "station-reason",
             "title": "Say why, briefly",
             "body": (
                 "A few words is enough: jam at infeed, waiting on fitter. "
                 "Every reason typed here is a row the manager can act on."
             )},
        ],
    },
    {
        "id": "do-a-maintenance-job",
        "title": "Do a maintenance job on your machine",
        "when": (
            "a PM is due, starting maintenance, completing a service, "
            "recording maintenance findings"
        ),
        "needs": "maintenance.perform",
        "steps": [
            {"page": "/dashboard/station",
             "anchor": "station-maintenance",
             "title": "The jobs owed on this machine",
             "body": (
                 "Preventive work raised by the plans, and corrective work "
                 "somebody reported. Start it before you touch the machine - "
                 "the clock on the job is the downtime record."
             )},
            {"page": "/dashboard/station",
             "anchor": "station-maintenance",
             "title": "Complete it with findings",
             "body": (
                 "What you found is the part the next person needs. "
                 "Completing re-baselines the plan from the work actually done."
             )},
        ],
    },
    {
        "id": "close-nc",
        "title": "Close a non-conformance",
        "when": "closing a non-conformance, dispositioning an NCR, clearing a quality hold",
        "needs": "quality.close_nc",
        "steps": [
            {"page": "/dashboard/quality", "anchor": "nc-list",
             "title": "Find the open non-conformances",
             "body": (
                      "Every one was raised by a measurement outside spec. The description "
                      "carries the reading and the limits it broke."
                     )},
            {"page": "/dashboard/quality", "anchor": "nc-list",
             "title": "Close it once it is dealt with",
             "body": (
                      "Closing is a supervisor's decision, not an inspector's - recording a "
                      "problem and disposing of it are different jobs, and the roles separate "
                      "them deliberately."
                     )},
        ],
    },
    {
        "id": "create-order",
        "title": "Create a work order",
        "when": (
            "creating an order, raising a work order, a new job, getting a hot "
            "order onto the floor"
        ),
        "needs": "orders.create",
        "steps": [
            {"page": "/dashboard/orders",
             "anchor": "order-create",
             "title": "New order",
             "body": (
                 "The code is suggested for you; pick the material and the "
                 "quantity. A due date is what lets the schedule promise "
                 "anything about it later."
             )},
            {"page": "/dashboard/orders",
             "anchor": "order-list",
             "title": "It joins the list",
             "body": (
                 "Released orders are on the floor immediately; planned ones "
                 "wait for a release. The tile filters show where it landed."
             )},
        ],
    },
    {
        "id": "act-on-order",
        "title": "Release, hold or close an order",
        "when": (
            "releasing an order, putting an order on hold, a concern on an "
            "order, resuming held work, closing or cancelling an order"
        ),
        "needs": "orders.close",
        "steps": [
            {"page": "/dashboard/orders",
             "anchor": "order-list",
             "title": "Pick the order",
             "body": "The actions offered depend on where the order is in its life."},
            {"page": "/dashboard/orders",
             "anchor": "order-actions",
             "title": "Act on it",
             "body": (
                 "Hold stops everything - booking, material, scheduling - and "
                 "demands a reason, because the person resuming it has to know "
                 "what was wrong. Cancel does not come back; Hold does."
             )},
            {"page": "/dashboard/orders",
             "anchor": "order-staging",
             "title": "Check what the floor still needs",
             "body": (
                 "Before releasing, staging says whether any station runs "
                 "short before this order finishes."
             )},
        ],
    },
    {
        "id": "order-progress",
        "title": "See where a work order is",
        "when": (
                 "checking order progress, how far along an order is, what step an order is on, "
                 "order status, yield per operation"
                ),
        "needs": "plant.read",
        "steps": [
            {"page": "/dashboard/orders", "anchor": "order-list",
             "title": "Pick the order",
             "body": "Every order with its progress bar. Select one to open it."},
            {"page": "/dashboard/orders", "anchor": "order-route",
             "title": "Read the route",
             "body": (
                      "Each operation in sequence with what that station actually booked, so a "
                      "losing step is visible instead of averaged away."
                     )},
            {"page": "/dashboard/orders", "anchor": "order-genealogy",
             "title": "See what went into it",
             "body": (
                      "The lots consumed - including lots made by an earlier order, which is the "
                      "chain a recall has to walk."
                     )},
        ],
    },
    {
        "id": "why-stopped",
        "title": "Find out why the line stopped",
        "when": (
                 "why the line stopped, downtime reasons, what caused a stop, availability loss, "
                 "OEE losses"
                ),
        "needs": "plant.read",
        "steps": [
            {"page": "/dashboard/analysis", "anchor": "analysis-page",
             "title": "Open shift analysis",
             "body": (
                      "The OEE waterfall names where the hours went, with the downtime pareto "
                      "beneath it. Unlabelled downtime is reported as unlabelled rather than "
                      "quietly dropped."
                     )},
        ],
    },
    {
        "id": "add-person",
        "title": "Add someone and give them a role",
        "when": (
                 "adding an employee, creating an account, new starter, assigning a role, making "
                 "someone a quality inspector"
                ),
        "needs": "users.manage",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "user-form",
             "title": "Create the account",
             "body": "A code, a name, an initial password, and the role they start in."},
            {"page": "/dashboard/admin", "anchor": "role-list",
             "title": "Check what that role grants",
             "body": (
                      "A role is a bundle of capabilities, not a rank. Quality Inspector records "
                      "inspections and nothing else - it cannot book production or change a "
                      "machine's state."
                     )},
            {"page": "/dashboard/admin", "anchor": "user-table",
             "title": "Change a role later",
             "body": (
                      "Reassigning takes effect on that person's very next action - capabilities "
                      "are read live, not carried in their session."
                     )},
        ],
    },
    {
        "id": "define-role",
        "title": "Define a new role",
        "when": (
                 "creating a role, custom permissions, restricting what someone can do, a role "
                 "that only does one thing"
                ),
        "needs": "users.manage",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "role-form",
             "title": "Name the role",
             "body": "A code the system uses and a display name people read."},
            {"page": "/dashboard/admin", "anchor": "cap-checks",
             "title": "Tick what it may do",
             "body": (
                      "Each capability explains itself. Grant only what the job needs - that is "
                      "the whole reason roles are bundles rather than ranks."
                     )},
        ],
    },
    {
        "id": "define-routing",
        "title": "Define a routing",
        "when": (
                 "creating a routing, setting up how a product is made, adding operations, the "
                 "path a material takes"
                ),
        "needs": "masterdata.write",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "routing-form",
             "title": "Name the routing and its material",
             "body": "A routing is the ordered path one material takes through the plant."},
            {"page": "/dashboard/admin", "anchor": "routing-ops",
             "title": "Add the operations in order",
             "body": (
                      "Each operation runs on a named machine. Sequence numbers step by ten so an "
                      "operation can be inserted later without renumbering everything."
                     )},
        ],
    },
    # ----------------------------------------------------------- the signing walks
    # Five things a person may put in force, and the agent may put in force
    # none of them: decision 0035, and `capabilities.py` says the same in the
    # agent role's own description. That makes "approve the draft severity" a
    # request the assistant can answer perfectly - by walking the person to the
    # control they sign it on - and the one it used to answer with "I cannot do
    # that", which is true of the tool and useless to the person.
    #
    # `signing: True` is what lets a walk be *offered* to somebody who may not
    # follow it (see `signing_guides`). Every other guide is hidden from a
    # person who lacks its capability, because teaching a task that ends in a
    # refusal is worse than saying so - but for these the name of who may sign
    # is the answer, so the walk is listed and refused with that name.
    #
    # Two of the five are signed from the floor screen's own panel, because
    # only the two vocabularies have a review built for them (`review.py`'s
    # KINDS). The other three are signed on their own screens. Each walk ends
    # on the control that is actually there.
    {
        "id": "approve-downtime-reason",
        "title": "Sign off a downtime reason",
        "when": (
            "approving a downtime reason, putting a stop code in force, signing a "
            "drafted reason, approving a retirement of a reason code, a reason "
            "waiting for approval"
        ),
        "needs": "process.approve",
        "signing": True,
        "steps": [
            {"page": "/dashboard", "anchor": "pending-approvals",
             "needs": "process.approve",
             "title": "What is waiting for you",
             "body": (
                 "Every draft that needs your signature, oldest wait first. A reason "
                 "that has sat here for days is a stop the pareto still cannot name."
             )},
            {"page": "/dashboard", "anchor": "pending-review",
             "needs": "process.approve",
             "title": "Press Review on the reason you mean",
             "body": (
                 "Not Approve - there is deliberately no approve button on a row. A "
                 "row says a draft exists and nothing about what it would do to the "
                 "plant, and signing from it is signing blind."
             )},
            {"page": "/dashboard", "anchor": "review-changes",
             "needs": "process.approve",
             "title": "What it actually changes",
             "body": (
                 "The draft against what is in force now, line by line, in the "
                 "plant's own words. Nothing here was written by a model."
             )},
            {"page": "/dashboard", "anchor": "review-coverage",
             "needs": "process.approve",
             "title": "How much it covers",
             "body": (
                 "How many stops this vocabulary can already name, and how many it "
                 "leaves unlabelled. A word that covers nothing is not an improvement."
             )},
            {"page": "/dashboard", "anchor": "review-approve",
             "needs": "process.approve",
             "title": "Press Approve",
             "body": (
                 "You press it, not me - I hold no approve capability at all. From "
                 "that moment it is the word on every operator's screen, and the "
                 "revision it replaced stays in the history so it can be put back."
             )},
        ],
        "evidence": {"page": "/dashboard/reasons", "anchor": "reason-vocabulary",
                     "title": "It is in force",
                     "body": "The vocabulary now lists it as in force, with the revision you signed."},
    },
    {
        "id": "approve-nc-severity",
        "title": "Sign off a non-conformance severity",
        "when": (
            "approving a severity, putting a severity in force, signing a drafted "
            "severity, approving how findings are graded, a severity waiting for "
            "approval"
        ),
        "needs": "quality.approve",
        "signing": True,
        "steps": [
            {"page": "/dashboard", "anchor": "pending-approvals",
             "needs": "quality.approve",
             "title": "What is waiting for you",
             "body": (
                 "Severities wait here beside downtime reasons. Both are vocabularies "
                 "the whole plant then has to live with."
             )},
            {"page": "/dashboard", "anchor": "pending-review",
             "needs": "quality.approve",
             "title": "Press Review on the severity you mean",
             "body": (
                 "The row carries the code and how long it has waited; the review "
                 "behind it carries what signing would do."
             )},
            {"page": "/dashboard", "anchor": "review-changes",
             "needs": "quality.approve",
             "title": "What it actually changes",
             "body": (
                 "Read the description most of all. It is what decides whether two "
                 "inspectors grade the same defect the same way."
             )},
            {"page": "/dashboard", "anchor": "review-affected",
             "needs": "quality.approve",
             "title": "What it touches",
             "body": (
                 "Findings already graded at this severity keep the grading they were "
                 "given - a typed history stays as it was typed."
             )},
            {"page": "/dashboard", "anchor": "review-approve",
             "needs": "quality.approve",
             "title": "Press Approve",
             "body": (
                 "Yours to press and never mine. From here on it is a grade an "
                 "inspector can put on a non-conformance."
             )},
        ],
        "evidence": {"page": "/dashboard/severities", "anchor": "severity-vocabulary",
                     "title": "It is in force",
                     "body": "The severities list shows it in force, at the revision you signed."},
    },
    {
        "id": "approve-instruction",
        "title": "Put a work instruction in force",
        "when": (
            "approving a work instruction, putting a document in force, signing a "
            "procedure, approving a revision, withdrawing a document"
        ),
        "needs": "documents.approve",
        "signing": True,
        "steps": [
            {"page": "/dashboard/instructions", "anchor": "instruction-list",
             "needs": "documents.approve",
             "title": "Open the document",
             "body": (
                 "Documents are signed on their own screen rather than on the floor "
                 "screen's panel: there is nothing to sign until you have read the "
                 "revision, and the revision is here."
             )},
            {"page": "/dashboard/instructions", "anchor": "instruction-body",
             "needs": "documents.approve",
             "title": "Read the revision you are signing",
             "body": (
                 "All of it. Once it is in force this is what the plant is held to, "
                 "and what the assistant quotes instead of answering for itself."
             )},
            {"page": "/dashboard/instructions", "anchor": "instruction-approve",
             "needs": "documents.approve",
             "title": "Press Approve revision",
             "body": (
                 "Under your own name. The revision it replaces stays readable in the "
                 "history; Withdraw beside it takes a document out of force without "
                 "deleting anything."
             )},
        ],
        "evidence": {"page": "/dashboard/instructions", "anchor": "instruction-list",
                     "title": "Which revision is in force",
                     "body": "The catalogue says in force against it, at the revision you signed."},
    },
    {
        "id": "approve-trigger",
        "title": "Put a trigger in force",
        "when": (
            "approving a trigger, putting a trigger in force, arming an alarm rule, "
            "withdrawing a trigger, a trigger waiting for approval"
        ),
        "needs": "triggers.approve",
        "signing": True,
        "steps": [
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-filters", "needs": "triggers.approve",
             "title": "Find the draft",
             "body": (
                 "Filter by status draft to see only what is waiting. Triggers are "
                 "signed here rather than on the floor screen's panel."
             )},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-approve", "needs": "triggers.approve",
             "title": "Press Approve on its row",
             "body": (
                 "Read the tag, the condition and the action first: this is logic "
                 "against a live plant, and the OPC agent picks it up within half a "
                 "minute. Withdraw beside it takes one back out."
             )},
        ],
        "evidence": {"page": "/dashboard/triggers", "tab": "triggers",
                     "anchor": "trigger-list",
                     "title": "It is watching now",
                     "body": "The row says in force, and the fired count starts from zero."},
    },
    {
        "id": "approve-adjustment",
        "title": "Decide a proposed setpoint change",
        "when": (
            "approving an adjustment, approving a setpoint change, rejecting a "
            "setpoint proposal, the human in the loop before a PLC write, a change "
            "waiting for a decision"
        ),
        "needs": "adjustments.approve",
        "signing": True,
        "steps": [
            {"page": "/dashboard/adjustments", "anchor": "adjustment-queue",
             "needs": "adjustments.approve",
             "title": "The queue of proposals",
             "body": (
                 "Each row is a number somebody wants written to a machine, with the "
                 "reason they gave. Nothing here has reached a PLC."
             )},
            {"page": "/dashboard/adjustments", "anchor": "adjustment-approve",
             "needs": "adjustments.approve",
             "title": "Press Approve on the one you mean",
             "body": (
                 "This is the human in the loop, and it is the only thing standing "
                 "between a proposal and a live setpoint. Reject beside it asks why, "
                 "and the why is what the next proposal is written against."
             )},
        ],
        "evidence": {"page": "/dashboard/adjustments", "anchor": "adjustment-queue",
                     "title": "What you decided",
                     "body": "The row carries your decision and the time it was made."},
    },
]

GUIDE_BY_ID = {g["id"]: g for g in GUIDES}


# --------------------------------------------------------------------------
# Surfaces. A write tool the agent may propose, mapped to the screen that does
# the same thing by hand: which anchors, which proposed argument fills which
# control, and where the evidence appears afterwards. "Show me" walks the
# person to the real button with the form already filled in; "Do it" runs the
# tool and then walks them to the evidence. One mapping, both modes - and like
# guides it is authored data, so the model never invents a selector.
#
# Two fields are allowed to vary per proposal, for the one tool that is not
# about one screen. A step's `page`, `anchor` and `tab` are formatted with the
# proposal's own arguments the way its `title` and `body` already were, so a
# tool that serves every domain can point at the domain it was asked about;
# and `needs` may be `None`, meaning the capability is decided per call and is
# in `agent.PER_CALL_NEEDS` with its reason. `context` names a function that
# adds values the steps may use beyond the arguments themselves, read from the
# same registry the API reads.
# --------------------------------------------------------------------------


def _setting_context(args: dict, capabilities: set[str] | None) -> dict:
    """What a settings proposal's steps know beyond their own arguments: the
    section the key is listed under, what kind of value it is, and whether the
    person reading the card holds the capability that gates Save. Looked up per
    call from the registry, which is what makes one authored walk right for
    every domain's settings instead of one walk per domain."""
    from fsmes.services import plant_settings

    try:
        section, table, _schema = plant_settings.owner(args.get("domain") or "",
                                                       args.get("key") or "")
    except plant_settings.Unknown:
        # The tool will be refused by the API for the same reason. A walk with
        # blanks in it is better than a walk that raises on the way to saying so.
        return {}
    return {"section_label": section.label,
            # The pack's own name for the key, which is the one name for a
            # setting that the schema, `fsmes pack check` and the page all
            # already agree on. The pack table is the section's, not the
            # workspace's: they usually match and `[oee] coverage_floor` on an
            # Engineering page is where they do not.
            "pack_key": f"[{table}] {args.get('key')}",
            # The bare capability name, for the step to carry, so the screen
            # can tell "not drawn yet" from "not yours to see" rather than
            # guessing. Empty when the section gates nothing, which the screen
            # reads as "unknown" and says nothing about.
            "capability_key": section.define or "",
            "saving": _saving_line(section.define, capabilities)}


def _saving_line(needs: str | None, capabilities: set[str] | None) -> str:
    """What the last step of a settings walk says about who may press Save.

    It used to say only that saving needs a capability. Read by somebody who
    held it, at the end of a walk that had just failed to find the box, that
    sentence was half of "it said I didn't have permission" (Scott, 2026-09-25,
    signed in as ADMIN, holding `process.define`). A card that knows the
    person's capabilities - the same set the offer was filtered on - can say
    which side of it they are on, so it does.
    """
    if not needs:
        return ("Nothing gates saving this one: anybody who can see the screen "
                "can press Save.")
    if capabilities is None:
        # Nobody asked on anyone's behalf. Say what is needed and claim nothing
        # about a person who is not in the question.
        return f"Saving needs {needs}."
    if needs in capabilities:
        return f"Saving needs {needs} - you hold it, so pressing Save is yours to do."
    return (f"Saving needs {needs}, which you do not hold. Somebody who does can "
            "press Save, or a plant administrator can grant it.")


def _routing_context(args: dict, capabilities: set[str] | None) -> dict:
    """What a routing proposal's steps know beyond their own arguments.

    `operations` is a list of dicts, and a list of dicts formatted into a
    sentence by `str.format` reads like a stack trace. The routing form has no
    control per operation either - it grows rows as you press Add operation -
    so the one honest thing a step can do is name them in the plant's own
    words and let the person type the rows. That is what this writes.
    """
    rows = args.get("operations") or []
    written = []
    for row in rows:
        if not isinstance(row, dict):
            written.append(str(row))
            continue
        seq, name = row.get("seq"), row.get("name") or row.get("operation") or ""
        where = row.get("equipment") or row.get("machine") or ""
        written.append(" ".join(str(part) for part in (seq, name, where and f"on {where}") if part))
    return {"operations_line": "; ".join(written) or "none given",
            "operation_count": str(len(rows))}


def _role_context(args: dict, capabilities: set[str] | None) -> dict:
    """A role proposal's capability list, as a line a person can tick against.

    Same reason as the routing one: `capabilities` is a list, the screen draws
    one checkbox per capability the product knows, and none of those boxes can
    be addressed by an authored anchor. So the step rings the grid and the
    card says exactly which words to find in it.
    """
    wanted = args.get("capabilities") or []
    return {"capabilities_line": ", ".join(str(c) for c in wanted) or "none",
            "capability_count": str(len(wanted))}


SURFACES: dict[str, dict] = {
    "record_check": {
        "title": "Record a quality inspection",
        "needs": "quality.record",
        # The station screen records one too, on the machine in front of the
        # person. The walkthrough still points at the floor page's form:
        # the steps below are a single authored list, and one wrong selector
        # is worse than a walk to a screen that definitely has the control.
        "pages": ["/dashboard", "/dashboard/station", "/dashboard/quality"],
        "example": "Record {characteristic} {mid} on {machine}",
        "steps": [
            {"page": "/dashboard", "anchor": "quality-form",
             "title": "The quality check panel",
             "body": (
                 "This is where an inspection is recorded by hand. I have filled it in "
                 "from what you asked - check each field before you press Record."
             )},
            {"page": "/dashboard", "anchor": "quality-spec",
             "title": "The characteristic",
             "fill": {"value": "{material}::{characteristic}"},
             "body": (
                 "{characteristic} on {material}. The list only shows characteristics "
                 "with a specification, because a measurement with nothing to judge it "
                 "against cannot pass or fail."
             )},
            {"page": "/dashboard", "anchor": "quality-value",
             "title": "The measured value",
             "fill": {"value": "{value}"},
             "body": (
                 "{value}, as you told me. Enter what the gauge actually read - an "
                 "out-of-spec reading is meant to be recorded, not rounded."
             )},
            {"page": "/dashboard", "anchor": "quality-submit",
             "title": "Press Record",
             "body": (
                 "You press it, not me: it is recorded under your own name. If the value "
                 "is outside the specification the MES opens a non-conformance - that is "
                 "the system working, not an error you caused."
             )},
        ],
        "evidence": {"page": "/dashboard/quality", "anchor": "measurement-chart",
                     "title": "Your reading, against the limits",
                     "body": "The check joins the chart here, plotted against the specification band."},
    },
    "book_output": {
        "title": "Book output",
        "needs": "production.book",
        "pages": ["/dashboard", "/dashboard/station"],
        "example": "Book 10 good and 1 scrap on {machine}",
        "steps": [
            {"page": "/dashboard", "anchor": "report-equipment",
             "title": "The machine",
             "fill": {"value": "{equipment}"},
             "body": "{equipment} made the parts. I have picked it; change it if I got that wrong."},
            {"page": "/dashboard", "anchor": "report-order",
             "title": "Which order",
             "fill": {"value": "{order}"},
             "body": (
                 "It defaults to the order this machine is running. With two orders "
                 "queued the default is a guess - naming it is what stops output landing "
                 "on the wrong order and surfacing at month end."
             )},
            {"page": "/dashboard", "anchor": "report-good",
             "title": "Good parts",
             "fill": {"value": "{good}"},
             "body": "{good} good, as you said."},
            {"page": "/dashboard", "anchor": "report-scrap",
             "title": "Scrap",
             "fill": {"value": "{scrap}", "default": "0"},
             "body": "Scrap is not a failure to hide - unrecorded scrap makes the yield figure a lie."},
            {"page": "/dashboard", "anchor": "report-submit",
             "title": "Press Book",
             "body": "The counts land against the order and appear in the audit trail under your name."},
        ],
        "evidence": {"page": "/dashboard/orders", "anchor": "order-list",
                     "title": "The order moved",
                     "body": "Its progress bar includes what you just booked."},
    },
    "issue_material": {
        "title": "Issue material to an order",
        "needs": "production.consume",
        "pages": ["/dashboard", "/dashboard/orders"],
        "example": "Issue 5 from lot {lot} to {order}",
        "steps": [
            {"page": "/dashboard", "anchor": "consume-order",
             "title": "The order that consumes",
             "fill": {"value": "{order}"},
             "body": "{order}. This pairing is what builds genealogy - later it answers 'what went into this batch'."},
            {"page": "/dashboard", "anchor": "consume-lot",
             "title": "From which lot",
             "fill": {"value": "{lot}"},
             "body": "{lot}. Only lots with stock are listed."},
            {"page": "/dashboard", "anchor": "consume-quantity",
             "title": "How much",
             "fill": {"value": "{quantity}"},
             "body": "{quantity}, in the lot's unit."},
            {"page": "/dashboard", "anchor": "consume-submit",
             "title": "Press Issue",
             "body": "The lot's remaining quantity drops and the consumption is recorded against the order."},
        ],
        "evidence": {"page": "/dashboard/orders", "anchor": "order-genealogy",
                     "title": "It shows in genealogy",
                     "body": "Open the order: what you issued now appears here, under Consumed."},
    },
    "set_machine_state": {
        "title": "Say what a machine is doing",
        "needs": "equipment.state",
        "pages": ["/dashboard/station", "/dashboard", "/dashboard/machines"],
        "example": "Mark {machine} down for a jam at the infeed",
        "steps": [
            {"page": "/dashboard/station", "anchor": "station-machine",
             "title": "Your machine",
             "fill": {"value": "{equipment}"},
             "body": "{equipment}. The station remembers which machine is yours; I have picked it."},
            {"page": "/dashboard/station", "anchor": "station-state",
             "title": "Press {state}",
             "body": (
                 "Press the state that is true. Going down asks why before it accepts - "
                 "say: {reason}. An unlabelled stop is the row the downtime pareto cannot explain."
             )},
        ],
        "evidence": {"page": "/dashboard/station", "anchor": "station-pill",
                     "title": "The machine's state now",
                     "body": "This pill is what the whole plant sees for this machine, with the reason beside it."},
    },
    "create_order": {
        "title": "Create a work order",
        "needs": "orders.create",
        "pages": ["/dashboard/orders"],
        "example": "Create order WO-NEW-01 for 500 of {material} and release it",
        "steps": [
            {"page": "/dashboard/orders", "anchor": "order-create",
             "title": "New order",
             "body": "This button opens the form. I will open it for you on the next step."},
            {"page": "/dashboard/orders", "anchor": "order-new-code", "open": "order-create",
             "title": "The order code",
             "fill": {"value": "{code}"},
             "body": "{code}. Codes are yours to choose; the ERP contract expects them unique."},
            {"page": "/dashboard/orders", "anchor": "order-new-material", "open": "order-create",
             "title": "What to make",
             "fill": {"value": "{material}"},
             "body": "{material}. The routing on this material decides which machines the order visits."},
            {"page": "/dashboard/orders", "anchor": "order-new-qty", "open": "order-create",
             "title": "How many",
             "fill": {"value": "{quantity}"},
             "body": "{quantity}."},
            {"page": "/dashboard/orders", "anchor": "order-new-release", "open": "order-create",
             "title": "Release it now?",
             "fill": {"value": "{release}", "default": "true"},
             "body": (
                 "Ticked, the order goes to the floor at once; unticked it stays planned "
                 "until someone releases it."
             )},
            {"page": "/dashboard/orders", "anchor": "order-new-create", "open": "order-create",
             "title": "Press Create",
             "body": "The order is created under your name and appears in the list."},
        ],
        "evidence": {"page": "/dashboard/orders", "anchor": "order-list",
                     "title": "It is in the list",
                     "body": "Your new order, with its status and progress."},
    },
    "order_action": {
        "title": "Act on an order",
        "needs": "orders.release",
        "pages": ["/dashboard/orders", "/dashboard"],
        "example": "Release {planned_order}",
        "steps": [
            {"page": "/dashboard/orders", "anchor": "order-list",
             "title": "Find {code}",
             "body": "Click {code} in the list to open it. The filters above narrow the list if it is long."},
            {"page": "/dashboard/orders", "anchor": "order-actions",
             "title": "Press {action}",
             "body": (
                 "Only the actions that make sense for the order's status are shown. "
                 "A hold asks for a reason - the person resuming it has to know what was wrong."
             )},
        ],
        "evidence": {"page": "/dashboard/orders", "anchor": "order-list",
                     "title": "Its status changed",
                     "body": "The order's status column now shows the new state."},
    },
    # The two vocabularies. The screens these walk to were built for a person
    # holding `<domain>.define` to draft on (#87, #97), and drafting is all the
    # agent role can do here: it holds `process.define` and `quality.define`
    # and neither `*.approve`, so "Do it" ends at a draft and the evidence step
    # says who signs it. Retiring is not offered - the count of intervals or
    # records a word labels is what a person is told before they take it off
    # the list, and that conversation belongs on the screen.
    "draft_downtime_reason": {
        "title": "Draft a downtime reason",
        "needs": "process.define",
        "pages": ["/dashboard/reasons", "/dashboard/machines", "/dashboard/station"],
        "example": "Draft a downtime reason for a jam at the infeed",
        "steps": [
            {"page": "/dashboard/reasons", "anchor": "reason-form",
             "title": "The form that drafts a word",
             "body": (
                 "This is where the plant's downtime vocabulary is written by hand. "
                 "I have filled it in from what you asked - read each field before "
                 "you save, because this word ends up on every operator's screen."
             )},
            {"page": "/dashboard/reasons", "anchor": "reason-code",
             "title": "The code",
             "fill": {"value": "{code}"},
             "body": (
                 "{code}. This is what the pareto groups on and what leaves the "
                 "plant, so it outlives the wording beside it. Typing a code that "
                 "already exists drafts its next revision rather than a second word."
             )},
            {"page": "/dashboard/reasons", "anchor": "reason-name",
             "title": "What the operator reads",
             "fill": {"value": "{name}"},
             "body": "{name}. This is the button at the machine."},
            {"page": "/dashboard/reasons", "anchor": "reason-description",
             "title": "What it means",
             "fill": {"value": "{description}", "default": ""},
             "body": (
                 "The sentence shown beside the button. Two operators picking the "
                 "same word for the same stop is the whole point of writing it down."
             )},
            {"page": "/dashboard/reasons", "anchor": "reason-submit",
             "title": "Press Save draft",
             "body": (
                 "You press it, not me - it is drafted under your own name. It "
                 "changes nothing on the floor: somebody holding process.approve "
                 "signs it on the Floor screen first."
             )},
        ],
        "evidence": {"page": "/dashboard/reasons", "anchor": "reason-vocabulary",
                     "title": "It is in the vocabulary, as a draft",
                     "body": (
                         "Your word is in the list with status draft. It reaches the "
                         "floor when somebody holding process.approve signs it on the "
                         "Floor screen's Waiting for you panel - never me."
                     )},
    },
    "draft_nc_severity": {
        "title": "Draft a non-conformance severity",
        "needs": "quality.define",
        "pages": ["/dashboard/severities", "/dashboard/quality"],
        "example": "Draft a non-conformance severity for a cosmetic defect",
        "steps": [
            {"page": "/dashboard/severities", "anchor": "severity-form",
             "title": "The form that drafts a word",
             "body": (
                 "This is where the plant's severities are written by hand. I have "
                 "filled it in from what you asked - read it before you save: this "
                 "word grades findings, and the grading is what people act on."
             )},
            {"page": "/dashboard/severities", "anchor": "severity-code",
             "title": "The code",
             "fill": {"value": "{code}"},
             "body": (
                 "{code}. This is stored on every non-conformance raised at this "
                 "severity. Some codes the product writes itself and those can "
                 "never be retired; the list says which."
             )},
            {"page": "/dashboard/severities", "anchor": "severity-name",
             "title": "What a person reads",
             "fill": {"value": "{name}"},
             "body": "{name}."},
            {"page": "/dashboard/severities", "anchor": "severity-description",
             "title": "What a finding at this severity means",
             "fill": {"value": "{description}", "default": ""},
             "body": (
                 "The sentence that decides whether two inspectors grade the same "
                 "defect the same way. It is worth more than the name."
             )},
            {"page": "/dashboard/severities", "anchor": "severity-submit",
             "title": "Press Save draft",
             "body": (
                 "You press it, not me - it is drafted under your own name, and it "
                 "grades nothing until somebody holding quality.approve signs it."
             )},
        ],
        "evidence": {"page": "/dashboard/severities", "anchor": "severity-vocabulary",
                     "title": "It is in the severities, as a draft",
                     "body": (
                         "Your word is in the list with status draft. It can be put on "
                         "a quality record once somebody holding quality.approve signs "
                         "it on the Floor screen's Waiting for you panel."
                     )},
    },
    # A setting, which is the other shape. The two walks above end at a draft
    # somebody signs; this one ends at a number that is in force the moment
    # Save is pressed, because nothing records the value that judged anything
    # and there is nothing for a revision to protect (decision 0035, rule 3).
    # So there is no approve step to point at, and no `approve` capability
    # anywhere in it. One walk for every domain: the page is one file serving
    # each workspace, and the step carries the workspace and the key it was
    # asked about.
    "write_plant_setting": {
        "title": "Change one of this plant's own settings",
        # Per call, from the section the key belongs to - see
        # `agent.PER_CALL_NEEDS`. There is no one capability to name: the same
        # tool writes a Quality number and, the day that section becomes live,
        # an Engineering one.
        "needs": None,
        "context": _setting_context,
        # The screen this walks onto, named the way its steps name it: one
        # file serves every workspace, so it matches no single path and the
        # example is offered wherever the rest of them are rather than first
        # on one screen.
        "pages": ["/dashboard/config/{domain}"],
        # Quality is the only workspace with live settings today. It stays a
        # true example when the next one arrives.
        "example": "Set the Cpk bar for capable to 1.33",
        "steps": [
            # Both steps carry the capability that gates the section, because
            # both controls are only drawn for somebody who holds it: the page
            # offers a reader the value and no input. That makes "the anchor is
            # not here" answerable on the screen - not yet drawn, or not yours
            # to see - instead of guessed at.
            {"page": "/dashboard/config/{domain}?setting={key}",
             "anchor": "setting-in-focus",
             "needs": "{capability_key}",
             "title": "The box this setting is changed in",
             "fill": {"value": "{value}"},
             "body": (
                 "{section_label}, on the {domain} Configuration page. I have typed "
                 "{value} into the box for {pack_key} - read it before you save, "
                 "because there is no draft and nobody signs it after you."
             )},
            {"page": "/dashboard/config/{domain}?setting={key}",
             "anchor": "setting-save-in-focus",
             "needs": "{capability_key}",
             "title": "Press Save",
             "body": (
                 "You press it, not me - it is saved under your own name. It is in "
                 "force on the next reading, everywhere, with no restart, and the "
                 "audit trail keeps what it was so typing the old value back is the "
                 "undo. {saving}"
             )},
        ],
        "evidence": {"page": "/dashboard/config/{domain}?setting={key}",
                     "anchor": "setting-in-focus",
                     "needs": "{capability_key}",
                     "title": "What your plant is set to now",
                     "body": (
                         "The box holds the value in force, and the line beside it "
                         "says whether that is the product's default or this plant's "
                         "own choice."
                     )},
    },
    "raise_corrective_maintenance": {
        "title": "Raise corrective work",
        "needs": "maintenance.perform",
        "pages": ["/dashboard/maintenance", "/dashboard/station", "/dashboard/machines"],
        "example": "Raise corrective work on {machine}: replace the worn infeed belt",
        "steps": [
            {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-machine",
             "title": "The machine that broke",
             "fill": {"value": "{machine}"},
             "body": "{machine}. Corrective work is on the Work tab, beside the open jobs."},
            {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-summary",
             "title": "What needs doing",
             "fill": {"value": "{summary}"},
             "body": "One line a fitter can act on. I wrote: {summary}"},
            {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-reason",
             "title": "Why",
             "fill": {"value": "{reason}", "default": ""},
             "body": "Optional, but the why is what the history is worth reading for later."},
            {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-cm-submit",
             "title": "Press Raise",
             "body": "A maintenance order is created against the machine, under your name."},
        ],
        "evidence": {"page": "/dashboard/maintenance", "tab": "work", "anchor": "maintenance-work",
                     "title": "It is in the open work",
                     "body": "The new job is listed here until someone starts and completes it."},
    },

    # ---------------------------------------------------------------- quality
    # The three non-conformance verbs. None of them has a box for the code:
    # the screen picks a finding by drawing a button on its row, so the first
    # step of each is the search box with the code typed into it. That is not
    # a detour - it is what narrows the list to one row, which is what makes
    # the row button on the next step the right one rather than the first one.
    "review_nonconformance": {
        "title": "Take a non-conformance under review",
        "needs": "quality.close_nc",
        "pages": ["/dashboard/quality"],
        "example": "Take NC-00001 under review",
        "steps": [
            {"page": "/dashboard/quality", "anchor": "nc-search",
             "fill": {"value": "{code}"},
             "title": "Find {code}",
             "body": (
                 "There is no box to type a finding's code into on this screen - you "
                 "reach one by narrowing the list to it. I have typed {code} in here, "
                 "so the row below should be the only one left."
             )},
            {"page": "/dashboard/quality", "anchor": "nc-review",
             "needs": "quality.close_nc",
             "title": "Press Take under review",
             "body": (
                 "This says somebody is looking at {code}, and stops two people "
                 "deciding it twice. It does not decide anything yet."
             )},
        ],
        "evidence": {"page": "/dashboard/quality", "anchor": "nc-list",
                     "title": "It says who is on it",
                     "body": "The row now reads under review, with your name and the time."},
    },
    "disposition_nonconformance": {
        "title": "Decide what happens to a non-conformance",
        "needs": "quality.close_nc",
        "pages": ["/dashboard/quality"],
        "example": "Scrap NC-00001 - the batch is out of spec",
        "steps": [
            {"page": "/dashboard/quality", "anchor": "nc-search",
             "fill": {"value": "{code}"},
             "title": "Find {code}",
             "body": (
                 "Typed in for you, so the list is down to the one finding. The next "
                 "step opens the decision box on this row and not on somebody else's."
             )},
            {"page": "/dashboard/quality", "anchor": "nc-decide",
             "needs": "quality.close_nc",
             "title": "Press Decide",
             "body": "This opens the decision box underneath, for {code}."},
            {"page": "/dashboard/quality", "anchor": "nc-disposition-choice",
             "open": "nc-decide", "needs": "quality.close_nc",
             "fill": {"value": "{disposition}"},
             "title": "What happens to the material",
             "body": (
                 "{disposition}. Use as is, rework, scrap or return - four words, "
                 "because a decision nobody can name is a decision nobody can count."
             )},
            {"page": "/dashboard/quality", "anchor": "nc-disposition-reason",
             "open": "nc-decide", "needs": "quality.close_nc",
             "fill": {"value": "{reason}"},
             "title": "Why",
             "body": (
                 "{reason}. This is the sentence somebody reads in six months when "
                 "the same defect comes back, so it is worth more than the choice above."
             )},
            {"page": "/dashboard/quality", "anchor": "nc-disposition-save",
             "open": "nc-decide", "needs": "quality.close_nc",
             "title": "Press Record the decision",
             "body": (
                 "You press it, not me - it is recorded under your own name. The "
                 "button stays dead until both boxes have something in them."
             )},
        ],
        "evidence": {"page": "/dashboard/quality", "anchor": "nc-list",
                     "title": "The decision is on the record",
                     "body": "The row carries the disposition and your reason under it."},
    },
    "close_nonconformance": {
        "title": "Close a non-conformance",
        "needs": "quality.close_nc",
        "pages": ["/dashboard/quality"],
        "example": "Close NC-00001",
        "steps": [
            {"page": "/dashboard/quality", "anchor": "nc-search",
             "fill": {"value": "{code}"},
             "title": "Find {code}",
             "body": "Typed in for you, so the row below is the one you mean.",
             },
            {"page": "/dashboard/quality", "anchor": "nc-close",
             "needs": "quality.close_nc",
             "title": "Press Close",
             "body": (
                 "Closing says the finding is dealt with. It does not erase it: the "
                 "record stays, and the chart still counts it."
             )},
        ],
        "evidence": {"page": "/dashboard/quality", "anchor": "nc-filters",
                     "title": "Where a closed finding goes",
                     "body": (
                         "The list shows open work by default, so {code} has left it. "
                         "Set the status filter here to closed to see it again."
                     )},
    },
    # ------------------------------------------------------------ maintenance
    "perform_maintenance": {
        "title": "Start or complete a maintenance job",
        "needs": "maintenance.perform",
        "pages": ["/dashboard/maintenance", "/dashboard/station"],
        "example": "Complete CM-00001 - replaced the belt",
        "steps": [
            {"page": "/dashboard/maintenance", "tab": "work",
             "anchor": "maintenance-work", "needs": "maintenance.perform",
             "title": "Find {order} in the open work",
             "body": (
                 "Press {action} on that row. The ring is round the whole list rather "
                 "than one button: every row has the same button, and pointing at the "
                 "first one would be pointing at the wrong job."
             )},
            {"page": "/dashboard/maintenance", "tab": "work",
             "anchor": "maintenance-complete-findings",
             "needs": "maintenance.perform",
             "fill": {"value": "{findings}", "default": ""},
             "title": "What you found",
             "body": (
                 "Only for Complete - pressing it opens this box. Starting a job "
                 "needs nothing more than the button. What you write here is the "
                 "history the next fitter reads before opening the guard."
             )},
            {"page": "/dashboard/maintenance", "tab": "work",
             "anchor": "maintenance-complete-confirm",
             "needs": "maintenance.perform",
             "title": "Press Complete job",
             "body": "Under your name, with the minutes it actually took."},
        ],
        "evidence": {"page": "/dashboard/maintenance", "tab": "history",
                     "anchor": "maintenance-history",
                     "title": "It is in the history",
                     "body": "Completed work lands here with the findings beside it."},
    },
    "raise_due_maintenance": {
        "title": "Raise the maintenance work that is due",
        "needs": "maintenance.perform",
        "pages": ["/dashboard/maintenance"],
        "example": "Raise the maintenance that is due",
        "steps": [
            {"page": "/dashboard/maintenance", "tab": "due",
             "anchor": "maintenance-raise", "needs": "maintenance.perform",
             "title": "Press Raise due work",
             "body": (
                 "One press turns every plan that has come due into a job somebody "
                 "can pick up. Nothing is due twice: a plan with an open job is "
                 "skipped."
             )},
        ],
        "evidence": {"page": "/dashboard/maintenance", "tab": "work",
                     "anchor": "maintenance-work",
                     "title": "The jobs it raised",
                     "body": "They are in the open work now, waiting for a fitter."},
    },
    "create_maintenance_plan": {
        "title": "Write a maintenance plan",
        "needs": "maintenance.plan",
        "pages": ["/dashboard/maintenance"],
        "example": "Plan a belt check on {machine} every 200 running hours",
        "steps": [
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-code", "needs": "maintenance.plan",
             "fill": {"value": "{code}"},
             "title": "The plan's code",
             "body": "{code}. Every job this plan raises carries it."},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-name", "needs": "maintenance.plan",
             "fill": {"value": "{name}"},
             "title": "What it is",
             "body": "{name} - the line a fitter reads on the job."},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-machine", "needs": "maintenance.plan",
             "fill": {"value": "{machine}"},
             "title": "Which machine",
             "body": "{machine}. A plan belongs to one machine, so its hours are that machine's."},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-trigger", "needs": "maintenance.plan",
             "fill": {"value": "{trigger}"},
             "title": "What makes it due",
             "body": (
                 "{trigger}. Running hours, units made, or days on the calendar - "
                 "three different ideas of wear, and the plant picks the one that "
                 "matches the part."
             )},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-interval", "needs": "maintenance.plan",
             "fill": {"value": "{interval}"},
             "title": "How often",
             "body": "{interval}, in whatever the trigger above counts."},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-minutes", "needs": "maintenance.plan",
             "fill": {"value": "{expected_minutes}", "default": ""},
             "title": "How long it should take",
             "body": (
                 "Left empty this takes the plant's own default rather than a number "
                 "I made up. It is what the schedule reserves, so a wrong one shows "
                 "up as a line that never runs on time."
             )},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-document", "needs": "maintenance.plan",
             "fill": {"value": "{document_code}", "default": ""},
             "title": "The procedure, if there is one",
             "body": (
                 "A controlled document code. The job then carries the approved "
                 "revision, so the fitter is not reading last year's copy."
             )},
            {"page": "/dashboard/maintenance", "tab": "plans",
             "anchor": "maintenance-plan-submit", "needs": "maintenance.plan",
             "title": "Press Create plan",
             "body": "Under your name. It starts counting from now."},
        ],
        "evidence": {"page": "/dashboard/maintenance", "tab": "plans",
                     "anchor": "maintenance-plans",
                     "title": "The plan is in the list",
                     "body": "With what it watches and when it next comes due."},
    },

    # ------------------------------------------------------------ master data
    # Four things one screen makes, each behind its own tab. Every step carries
    # the tab, because a person who is already on this page may be standing on
    # another one of them.
    "create_material": {
        "title": "Add a material",
        "needs": "masterdata.write",
        "pages": ["/dashboard/masterdata"],
        "example": "Add a material CAP-28MM, bottle cap, counted in pieces",
        "steps": [
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-material-code", "needs": "masterdata.write",
             "fill": {"value": "{code}"},
             "title": "The code",
             "body": (
                 "{code}. This is what orders, lots and the ERP all say, so it "
                 "outlives the name beside it."
             )},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-material-name", "needs": "masterdata.write",
             "fill": {"value": "{name}"},
             "title": "What a person calls it",
             "body": "{name}."},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-material-unit", "needs": "masterdata.write",
             "fill": {"value": "{unit}", "default": "ea"},
             "title": "What it is counted in",
             "body": (
                 "{unit}. Getting this wrong is not cosmetic - every quantity in "
                 "the plant is read in this unit afterwards."
             )},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-material-type", "needs": "masterdata.write",
             "fill": {"value": "{type}", "default": "raw"},
             "title": "Raw, intermediate or finished",
             "body": "{type}. It decides where the material can appear in a bill of materials."},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-material-submit", "needs": "masterdata.write",
             "title": "Press Add",
             "body": "Created under your own name."},
        ],
        "evidence": {"page": "/dashboard/masterdata", "tab": "materials",
                     "anchor": "masterdata-material-table",
                     "title": "It is in the materials",
                     "body": "Your new material is in this list."},
    },
    "create_equipment": {
        "title": "Add a machine or a line",
        "needs": "masterdata.write",
        "pages": ["/dashboard/masterdata", "/dashboard/machines"],
        "example": "Add a work unit CAP01 under LINE1",
        "steps": [
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-code", "needs": "masterdata.write",
             "fill": {"value": "{code}"},
             "title": "The code",
             "body": "{code}. Every event, every stop and every count is filed under it."},
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-name", "needs": "masterdata.write",
             "fill": {"value": "{name}"},
             "title": "What it is called on the floor",
             "body": "{name}."},
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-level", "needs": "masterdata.write",
             "fill": {"value": "{level}", "default": "work_unit"},
             "title": "What kind of thing it is",
             "body": (
                 "{level}. A work unit is a machine; the levels above it are how the "
                 "plant adds up - a number on a line is the sum of its units."
             )},
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-parent", "needs": "masterdata.write",
             "fill": {"value": "{parent}", "default": ""},
             "title": "What it sits under",
             "body": (
                 "Left empty it hangs off nothing and appears in no line's figures. "
                 "That is occasionally right and usually a mistake."
             )},
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-cycle", "needs": "masterdata.write",
             "fill": {"value": "{ideal_cycle_seconds}", "default": ""},
             "title": "Its ideal cycle, in seconds",
             "body": (
                 "This is the denominator of performance. Empty is honest when nobody "
                 "knows it; a guessed number quietly rewrites every OEE figure this "
                 "machine will ever report."
             )},
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-cost-center", "needs": "masterdata.write",
             "fill": {"value": "{cost_center}", "default": ""},
             "title": "Its cost centre",
             "body": "What finance calls this machine, when finance calls it anything."},
            {"page": "/dashboard/masterdata", "tab": "equipment",
             "anchor": "masterdata-equipment-submit", "needs": "masterdata.write",
             "title": "Press Add",
             "body": "Created under your own name."},
        ],
        "evidence": {"page": "/dashboard/masterdata", "tab": "equipment",
                     "anchor": "masterdata-equipment-table",
                     "title": "It is in the equipment",
                     "body": "With its level and what it hangs off."},
    },
    "create_spec": {
        "title": "Write a specification",
        "needs": "masterdata.write",
        "pages": ["/dashboard/masterdata", "/dashboard/quality"],
        "example": "Specify fill weight on {material} between 495 and 505",
        "steps": [
            {"page": "/dashboard/masterdata", "tab": "specs",
             "anchor": "masterdata-spec-material", "needs": "masterdata.write",
             "fill": {"value": "{material}"},
             "title": "Which material",
             "body": "{material}. A specification belongs to the thing being made."},
            {"page": "/dashboard/masterdata", "tab": "specs",
             "anchor": "masterdata-spec-characteristic", "needs": "masterdata.write",
             "fill": {"value": "{characteristic}"},
             "title": "What is measured",
             "body": (
                 "{characteristic}. Spell it the way the gauge label does - this is "
                 "the word an inspector picks from a list at the machine."
             )},
            {"page": "/dashboard/masterdata", "tab": "specs",
             "anchor": "masterdata-spec-unit", "needs": "masterdata.write",
             "fill": {"value": "{unit}", "default": ""},
             "title": "In what unit",
             "body": "{unit}."},
            {"page": "/dashboard/masterdata", "tab": "specs",
             "anchor": "masterdata-spec-min", "needs": "masterdata.write",
             "fill": {"value": "{min_value}", "default": ""},
             "title": "The bottom of the band",
             "body": (
                 "Empty means there is no lower limit, which is different from a "
                 "lower limit of zero. Leave it empty rather than inventing one."
             )},
            {"page": "/dashboard/masterdata", "tab": "specs",
             "anchor": "masterdata-spec-max", "needs": "masterdata.write",
             "fill": {"value": "{max_value}", "default": ""},
             "title": "The top of the band",
             "body": (
                 "A reading outside these two raises a non-conformance by itself, "
                 "so the band is a decision about what the plant stops for."
             )},
            {"page": "/dashboard/masterdata", "tab": "specs",
             "anchor": "masterdata-spec-submit", "needs": "masterdata.write",
             "title": "Press Add",
             "body": "The characteristic can be measured from the next shift on."},
        ],
        "evidence": {"page": "/dashboard/masterdata", "tab": "specs",
                     "anchor": "masterdata-spec-table",
                     "title": "It is in the specifications",
                     "body": "With its band, ready to judge a reading against."},
    },
    "add_bom_component": {
        "title": "Add a component to a bill of materials",
        "needs": "masterdata.write",
        "pages": ["/dashboard/masterdata"],
        "example": "Put 2 of a component into {material}",
        "steps": [
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-material-table", "needs": "masterdata.write",
             "title": "Click {material} in this list",
             "body": (
                 "There is no box for the parent material: the bill of materials "
                 "form below works on whichever row is selected here. Click {material} "
                 "and the form takes its name."
             )},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-bom-component", "needs": "masterdata.write",
             "fill": {"value": "{component}"},
             "title": "What goes into it",
             "body": "{component}."},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-bom-quantity", "needs": "masterdata.write",
             "fill": {"value": "{quantity}"},
             "title": "How much, per one made",
             "body": (
                 "{quantity}, in the component's own unit. This is the number that "
                 "decides what a thousand units will consume, so it is worth reading twice."
             )},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-bom-seq", "needs": "masterdata.write",
             "fill": {"value": "{operation_seq}", "default": ""},
             "title": "At which operation",
             "body": (
                 "Empty means it is not tied to a step of the routing. Naming the "
                 "operation is what lets the floor issue it at the right machine."
             )},
            {"page": "/dashboard/masterdata", "tab": "materials",
             "anchor": "masterdata-bom-submit", "needs": "masterdata.write",
             "title": "Press Add component",
             "body": "It joins the bill under your name."},
        ],
        "evidence": {"page": "/dashboard/masterdata", "tab": "materials",
                     "anchor": "masterdata-bom-table",
                     "title": "The bill of materials",
                     "body": "Your component is in it, with the quantity per unit made."},
    },
    # ----------------------------------------------------------- administration
    "create_user": {
        "title": "Create a sign-in",
        "needs": "users.manage",
        "pages": ["/dashboard/admin"],
        "example": "Create a sign-in for a new operator",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "user-new-code",
             "needs": "users.manage", "fill": {"value": "{code}"},
             "title": "The sign-in code",
             "body": (
                 "{code}. It is what the audit trail carries for everything this "
                 "person does, so it should be recognisable a year from now."
             )},
            {"page": "/dashboard/admin", "anchor": "user-new-name",
             "needs": "users.manage", "fill": {"value": "{name}"},
             "title": "Their name",
             "body": "{name}."},
            {"page": "/dashboard/admin", "anchor": "user-new-password",
             "needs": "users.manage", "fill": {"value": "{password}"},
             "title": "A first password",
             "body": (
                 "Change it to something you are willing to say out loud once, "
                 "because that is how it reaches them. It is visible here on purpose - "
                 "a password nobody can read is a password nobody can hand over."
             )},
            {"page": "/dashboard/admin", "anchor": "user-new-role",
             "needs": "users.manage", "fill": {"value": "{role}", "default": "operator"},
             "title": "Their role",
             "body": (
                 "{role}. The role is the whole of what they may do - there is no "
                 "second permission screen behind this one."
             )},
            {"page": "/dashboard/admin", "anchor": "user-new-create",
             "needs": "users.manage",
             "title": "Press Create",
             "body": "The sign-in exists from this moment; nobody signs it off after you."},
        ],
        "evidence": {"page": "/dashboard/admin", "anchor": "user-table",
                     "title": "They are in the people list",
                     "body": "With the role they were given."},
    },
    "create_role": {
        "title": "Define a role",
        "needs": "users.manage",
        "context": _role_context,
        "pages": ["/dashboard/admin"],
        "example": "Define a line-lead role that can release orders",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "role-new-code",
             "needs": "users.manage", "fill": {"value": "{code}"},
             "title": "The role's code",
             "body": "{code}. Every sign-in given this role inherits it whole."},
            {"page": "/dashboard/admin", "anchor": "role-new-name",
             "needs": "users.manage", "fill": {"value": "{name}"},
             "title": "What it is called",
             "body": "{name}."},
            {"page": "/dashboard/admin", "anchor": "role-new-description",
             "needs": "users.manage", "fill": {"value": "{description}", "default": ""},
             "title": "What the role is for",
             "body": (
                 "One line. It is what the next administrator reads before deciding "
                 "whether to put somebody in it."
             )},
            {"page": "/dashboard/admin", "anchor": "cap-checks",
             "needs": "users.manage",
             "title": "Tick {capability_count} capabilities",
             "body": (
                 "Tick these, and nothing else: {capabilities_line}. I cannot tick "
                 "them for you - there is one box per capability the product knows "
                 "and no authored step can name a box that is drawn from a list. "
                 "Read the sentence beside each one; that is what it actually allows."
             )},
            {"page": "/dashboard/admin", "anchor": "role-save",
             "needs": "users.manage",
             "title": "Press Create role",
             "body": "It is available to assign from now on."},
        ],
        "evidence": {"page": "/dashboard/admin", "anchor": "role-list",
                     "title": "The role and what it holds",
                     "body": "Its card lists every capability it carries."},
    },
    "update_role": {
        "title": "Change what a role may do",
        "needs": "users.manage",
        "context": _role_context,
        "pages": ["/dashboard/admin"],
        "example": "Let the line-lead role hold orders as well",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "role-list",
             "needs": "users.manage",
             "title": "Press Edit on {code}",
             "body": (
                 "The ring is round all the roles rather than one card: every card "
                 "has the same Edit button, and pointing at the first would be "
                 "pointing at the wrong role. Press the one on {code}."
             )},
            {"page": "/dashboard/admin", "anchor": "role-new-name",
             "needs": "users.manage", "fill": {"value": "{name}"},
             "title": "Its name",
             "body": "{name}. The code above is fixed once a role exists."},
            {"page": "/dashboard/admin", "anchor": "role-new-description",
             "needs": "users.manage", "fill": {"value": "{description}", "default": ""},
             "title": "What it is for",
             "body": "Worth rewriting when what it may do has changed."},
            {"page": "/dashboard/admin", "anchor": "cap-checks",
             "needs": "users.manage",
             "title": "The ticks are the whole answer",
             "body": (
                 "What is ticked when you save is what the role holds - this replaces "
                 "the list, it does not add to it. It should end up as exactly: "
                 "{capabilities_line}. Everybody already in this role gets the change "
                 "on their next page load."
             )},
            {"page": "/dashboard/admin", "anchor": "role-save",
             "needs": "users.manage",
             "title": "Press Save",
             "body": "Under your own name, and in force at once."},
        ],
        "evidence": {"page": "/dashboard/admin", "anchor": "role-list",
                     "title": "What the role holds now",
                     "body": "The card's chips are the capabilities as they now stand."},
    },
    "assign_role": {
        "title": "Put somebody in a role",
        "needs": "users.manage",
        "pages": ["/dashboard/admin"],
        "example": "Put a person into the supervisor role",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "user-filter-text",
             "needs": "users.manage", "fill": {"value": "{user}"},
             "title": "Find {user}",
             "body": (
                 "Typed in for you, so the table below narrows to the one person. "
                 "There is no form for this - the change is made on their row."
             )},
            {"page": "/dashboard/admin", "anchor": "user-role-select",
             "needs": "users.manage",
             "title": "Choose {role} on their row",
             "body": (
                 "I have deliberately not set this for you: this dropdown saves the "
                 "moment it changes, with no button after it, so filling it in would "
                 "be making the change rather than showing you where it is made."
             )},
        ],
        "evidence": {"page": "/dashboard/admin", "anchor": "user-table",
                     "title": "Their role now",
                     "body": "The Role column on their row is what they hold from the next page load."},
    },
    "create_routing": {
        "title": "Create a routing",
        "needs": "masterdata.write",
        "context": _routing_context,
        "pages": ["/dashboard/admin"],
        "example": "Create a routing for {material} through mixing and packing",
        "steps": [
            {"page": "/dashboard/admin", "anchor": "routing-new-code",
             "needs": "masterdata.write", "fill": {"value": "{code}"},
             "title": "The routing's code",
             "body": "{code}."},
            {"page": "/dashboard/admin", "anchor": "routing-new-name",
             "needs": "masterdata.write", "fill": {"value": "{name}"},
             "title": "What it is called",
             "body": "{name}."},
            {"page": "/dashboard/admin", "anchor": "routing-new-material",
             "needs": "masterdata.write", "fill": {"value": "{material}"},
             "title": "What it makes",
             "body": (
                 "{material}. An order for this material follows these operations, "
                 "in this order, on these machines."
             )},
            {"page": "/dashboard/admin", "anchor": "routing-ops",
             "needs": "masterdata.write",
             "title": "The {operation_count} operations",
             "body": (
                 "Type them here, in order: {operations_line}. The rows are added as "
                 "you go with Add operation, so there is no fixed box for me to fill - "
                 "and the sequence numbers are what decide which machine runs first."
             )},
            {"page": "/dashboard/admin", "anchor": "routing-create",
             "needs": "masterdata.write",
             "title": "Press Create routing",
             "body": "Orders planned after this follow it."},
        ],
        "evidence": {"page": "/dashboard/admin", "anchor": "routing-list",
                     "title": "It is in the routings",
                     "body": "With the material it makes and how many operations it has."},
    },
    # -------------------------------------------------------------- documents
    # Two tools, one form: `create_document` writes a controlled document and
    # `draft_instruction` writes one already tied to a material and a
    # characteristic. The screen has no box for that tie, and the walk says so
    # rather than quietly dropping half the proposal.
    "create_document": {
        "title": "Draft a controlled document",
        "needs": "documents.write",
        "pages": ["/dashboard/instructions"],
        "example": "Draft a work instruction for the changeover",
        "steps": [
            {"page": "/dashboard/instructions", "anchor": "instruction-new-code",
             "needs": "documents.write", "fill": {"value": "{code}"},
             "title": "The document's code",
             "body": (
                 "{code}. Typing a code that already exists drafts its next revision "
                 "rather than a second document, which is how a procedure keeps its "
                 "history."
             )},
            {"page": "/dashboard/instructions", "anchor": "instruction-new-title",
             "needs": "documents.write", "fill": {"value": "{title}"},
             "title": "Its title",
             "body": "{title}."},
            {"page": "/dashboard/instructions", "anchor": "instruction-new-body",
             "needs": "documents.write", "fill": {"value": "{body}", "default": ""},
             "title": "The text itself",
             "body": (
                 "Read it before you save. Once this revision is approved it is what "
                 "the plant is held to, and what the assistant quotes instead of "
                 "answering from its own idea of the job."
             )},
            {"page": "/dashboard/instructions", "anchor": "instruction-new-create",
             "needs": "documents.write",
             "title": "Press Create draft",
             "body": (
                 "A draft is in force nowhere. Somebody holding documents.approve "
                 "puts this revision in force - never me."
             )},
        ],
        "evidence": {"page": "/dashboard/instructions", "anchor": "instruction-list",
                     "title": "It is in the catalogue, as a draft",
                     "body": "Open the row to read it and to see which revision is in force."},
    },
    "draft_instruction": {
        "title": "Draft a work instruction for a check",
        "needs": "documents.write",
        "pages": ["/dashboard/instructions", "/dashboard/quality"],
        "example": "Write an instruction for measuring {characteristic}",
        "steps": [
            {"page": "/dashboard/instructions", "anchor": "instruction-new-code",
             "needs": "documents.write", "fill": {"value": "{code}"},
             "title": "The document's code",
             "body": "{code}. An existing code drafts its next revision."},
            {"page": "/dashboard/instructions", "anchor": "instruction-new-title",
             "needs": "documents.write", "fill": {"value": "{title}"},
             "title": "Its title",
             "body": "{title}."},
            {"page": "/dashboard/instructions", "anchor": "instruction-new-body",
             "needs": "documents.write", "fill": {"value": "{body}"},
             "title": "The procedure",
             "body": (
                 "This is what an inspector reads beside the gauge, so it is worth "
                 "reading as one."
             )},
            {"page": "/dashboard/instructions", "anchor": "instruction-anchors",
             "needs": "documents.write",
             "title": "What it is about: {material} {characteristic}",
             "body": (
                 "This form has no box for that. Tying a document to a material and "
                 "a characteristic is what makes it appear beside the check on the "
                 "quality screen, and today only a tool can set it - a document "
                 "drafted by hand here is untied until somebody adds the tie. These "
                 "chips are where it shows once it is set."
             )},
            {"page": "/dashboard/instructions", "anchor": "instruction-new-create",
             "needs": "documents.write",
             "title": "Press Create draft",
             "body": "Drafted under your name; somebody holding documents.approve signs it."},
        ],
        "evidence": {"page": "/dashboard/instructions", "anchor": "instruction-list",
                     "title": "It is in the catalogue, as a draft",
                     "body": "With the revision it would become if it is approved."},
    },

    # --------------------------------------------------------------- triggers
    "draft_trigger": {
        "title": "Draft a trigger",
        "needs": "triggers.write",
        "pages": ["/dashboard/triggers"],
        "example": "Draft a trigger for a wash temperature above 80",
        "steps": [
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-code", "needs": "triggers.write",
             "fill": {"value": "{code}"},
             "title": "The trigger's code",
             "body": "{code}. Every firing is filed under it."},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-name", "needs": "triggers.write",
             "fill": {"value": "{name}"},
             "title": "What it is watching for",
             "body": "{name}, in the words somebody woken by it would want to read."},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-machine", "needs": "triggers.write",
             "fill": {"value": "{machine}", "default": ""},
             "title": "On which machine",
             "body": (
                 "Empty means any machine publishing this tag. That is right for a "
                 "plant-wide rule and wrong for a rule about one line."
             )},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-tag", "needs": "triggers.write",
             "fill": {"value": "{tag}"},
             "title": "Which tag",
             "body": (
                 "{tag}. The list beside the box is what this plant actually "
                 "publishes - a tag nobody publishes is a trigger that never fires."
             )},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-condition", "needs": "triggers.write",
             "fill": {"value": "{condition}"},
             "title": "The comparison",
             "body": "{condition}."},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-threshold", "needs": "triggers.write",
             "fill": {"value": "{threshold}"},
             "title": "The number it is compared against",
             "body": "{threshold}."},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-sustained", "needs": "triggers.write",
             "fill": {"value": "{sustained_seconds}", "default": "0"},
             "title": "How long it must hold",
             "body": (
                 "Zero fires on the first reading. A few seconds is what stops one "
                 "noisy sample waking somebody at three in the morning."
             )},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-cooldown", "needs": "triggers.write",
             "fill": {"value": "{cooldown_seconds}", "default": ""},
             "title": "How long before it may fire again",
             "body": "Empty takes this plant's own default rather than a number I chose."},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-action", "needs": "triggers.write",
             "fill": {"value": "{action}"},
             "title": "What it does when it fires",
             "body": (
                 "{action}. Only actions this product catalogues are on the list; a "
                 "trigger cannot be taught to do something new from this screen."
             )},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-params", "needs": "triggers.write",
             "fill": {"value": "{action_params}", "default": ""},
             "title": "What the action needs to know",
             "body": (
                 "The action's own settings, as JSON. The help line under the form "
                 "says what the chosen action expects."
             )},
            {"page": "/dashboard/triggers", "tab": "triggers",
             "anchor": "trigger-submit", "needs": "triggers.write",
             "title": "Press Save draft",
             "body": (
                 "A draft watches nothing. Somebody holding triggers.approve puts it "
                 "in force, and the OPC agent picks it up within half a minute."
             )},
        ],
        "evidence": {"page": "/dashboard/triggers", "tab": "triggers",
                     "anchor": "trigger-list",
                     "title": "It is in the list, as a draft",
                     "body": "With how often it has fired, which for a draft is never."},
    },
    # ------------------------------------------------------------ adjustments
    "propose_adjustment": {
        "title": "Propose a setpoint change",
        "needs": "adjustments.propose",
        "pages": ["/dashboard/adjustments"],
        "example": "Propose taking the wash setpoint down two degrees",
        "steps": [
            {"page": "/dashboard/adjustments", "anchor": "adjustment-machine",
             "needs": "adjustments.propose", "fill": {"value": "{machine}"},
             "title": "Which machine",
             "body": (
                 "{machine}. Only machines publishing a writable setpoint are listed, "
                 "because nothing else can be changed from here."
             )},
            {"page": "/dashboard/adjustments", "anchor": "adjustment-tag",
             "needs": "adjustments.propose", "fill": {"value": "{tag}"},
             "title": "Which setpoint",
             "body": (
                 "{tag}. This list is rebuilt from the machine above, so it is the "
                 "second thing to set and not the first."
             )},
            {"page": "/dashboard/adjustments", "anchor": "adjustment-value",
             "needs": "adjustments.propose", "fill": {"value": "{value}"},
             "title": "The new value",
             "body": (
                 "{value}. The line under the box gives the bounds this tag allows "
                 "and what is driving it now - read it before you propose."
             )},
            {"page": "/dashboard/adjustments", "anchor": "adjustment-rationale",
             "needs": "adjustments.propose", "fill": {"value": "{rationale}"},
             "title": "Why",
             "body": (
                 "{rationale}. This is the whole of what the person approving it will "
                 "have to go on, so it is the field that decides whether it is approved."
             )},
            {"page": "/dashboard/adjustments", "anchor": "adjustment-submit",
             "needs": "adjustments.propose",
             "title": "Press Propose",
             "body": (
                 "A proposal reaches no machine. Somebody holding adjustments.approve "
                 "decides, and only then does a PLC see it."
             )},
        ],
        "evidence": {"page": "/dashboard/adjustments", "anchor": "adjustment-queue",
                     "title": "It is in the queue",
                     "body": "Waiting for a decision, with your reason in the Why column."},
    },
    # -------------------------------------------------------------- scheduling
    "plan_order": {
        "title": "Plan one order",
        "needs": "scheduling.plan",
        "pages": ["/dashboard/schedule", "/dashboard/orders"],
        "example": "Plan {planned_order}",
        "steps": [
            {"page": "/dashboard/schedule", "tab": "board",
             "anchor": "schedule-plan-order", "needs": "scheduling.plan",
             "fill": {"value": "{order}"},
             "title": "Which order",
             "body": (
                 "{order}. There is no box for a start time on this screen - planning "
                 "by hand always starts from now and from what the machines are "
                 "already promised."
             )},
            {"page": "/dashboard/schedule", "tab": "board",
             "anchor": "schedule-plan-one", "needs": "scheduling.plan",
             "title": "Press Plan this order",
             "body": (
                 "It is laid onto the machines its routing names, after whatever they "
                 "are already committed to."
             )},
        ],
        "evidence": {"page": "/dashboard/schedule", "tab": "board",
                     "anchor": "schedule-board",
                     "title": "Where it landed",
                     "body": "The board shows the operations and when each machine picks them up."},
    },
    "plan_all_orders": {
        "title": "Plan every open order",
        "needs": "scheduling.plan",
        "pages": ["/dashboard/schedule"],
        "example": "Plan every open order",
        "steps": [
            {"page": "/dashboard/schedule", "tab": "board",
             "anchor": "schedule-plan-all", "needs": "scheduling.plan",
             "title": "Press Plan every open order",
             "body": (
                 "This replans the whole book, so promises already made can move. "
                 "There is no start-time box: it plans from now."
             )},
        ],
        "evidence": {"page": "/dashboard/schedule", "tab": "promises",
                     "anchor": "schedule-promises",
                     "title": "What was promised",
                     "body": "One row per order, with the date the plan now says."},
    },
    "add_shift": {
        "title": "Add a shift",
        "needs": "scheduling.plan",
        "pages": ["/dashboard/schedule"],
        "example": "Add a night shift from 22:00 to 06:00",
        "steps": [
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-shift-code", "needs": "scheduling.plan",
             "fill": {"value": "{code}"},
             "title": "The shift's code",
             "body": "{code}."},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-shift-name", "needs": "scheduling.plan",
             "fill": {"value": "{name}"},
             "title": "What it is called",
             "body": "{name}."},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-shift-starts", "needs": "scheduling.plan",
             "fill": {"value": "{starts}"},
             "title": "When it starts",
             "body": "{starts}, on the plant's own clock."},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-shift-ends", "needs": "scheduling.plan",
             "fill": {"value": "{ends}"},
             "title": "When it ends",
             "body": (
                 "{ends}. An end earlier than the start is a shift that crosses "
                 "midnight, which is allowed and is usually what a night shift means."
             )},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-shift-days", "needs": "scheduling.plan",
             "fill": {"value": "{days}", "default": ""},
             "title": "Which days",
             "body": (
                 "Seven characters, Monday first, one for a working day. Left as it "
                 "came it is this plant's own working week rather than a week I chose."
             )},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-shift-submit", "needs": "scheduling.plan",
             "title": "Press Add shift",
             "body": (
                 "A shift added here is the whole plant's - this form has no machine "
                 "box, so a shift for one line only is not something the screen can "
                 "express today."
             )},
        ],
        "evidence": {"page": "/dashboard/schedule", "tab": "calendar",
                     "anchor": "schedule-shifts",
                     "title": "The shifts this plant works",
                     "body": "Yours is in the list, with the days it applies to."},
    },
    "add_calendar_exception": {
        "title": "Add a shutdown or an overtime day",
        "needs": "scheduling.plan",
        "pages": ["/dashboard/schedule"],
        "example": "Mark next Monday a shutdown for the annual service",
        "steps": [
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-exc-day", "needs": "scheduling.plan",
             "fill": {"value": "{day}"},
             "title": "Which day",
             "body": "{day}."},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-exc-kind", "needs": "scheduling.plan",
             "fill": {"value": "{kind}"},
             "title": "Shutdown or overtime",
             "body": (
                 "{kind}. A shutdown takes the day out of the plan; overtime puts a "
                 "non-working day into it."
             )},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-exc-reason", "needs": "scheduling.plan",
             "fill": {"value": "{reason}"},
             "title": "Why",
             "body": (
                 "{reason}. A day missing from the plan with no reason beside it is "
                 "the row somebody argues about at the end of the month."
             )},
            {"page": "/dashboard/schedule", "tab": "calendar",
             "anchor": "schedule-exc-submit", "needs": "scheduling.plan",
             "title": "Press Add",
             "body": (
                 "Like shifts, an exception from this screen is the whole plant's: "
                 "there is no machine box on the form."
             )},
        ],
        "evidence": {"page": "/dashboard/schedule", "tab": "calendar",
                     "anchor": "schedule-exceptions",
                     "title": "The exceptions",
                     "body": "Your day is in the list, and the board plans around it."},
    },
    # ------------------------------------------------------------- serial units
    "produce_units": {
        "title": "Produce serialised units",
        "needs": "production.book",
        "pages": ["/dashboard/trace"],
        "example": "Produce 5 serialised units of {material}",
        "steps": [
            {"page": "/dashboard/trace", "anchor": "trace-produce-material",
             "needs": "production.book", "fill": {"value": "{material}"},
             "title": "What is being made",
             "body": "{material}."},
            {"page": "/dashboard/trace", "anchor": "trace-produce-order",
             "needs": "production.book", "fill": {"value": "{order}", "default": ""},
             "title": "Against which order",
             "body": (
                 "Empty makes units that belong to no order. That is occasionally "
                 "right - a sample - and is otherwise the thing that makes a count "
                 "disagree with the order book."
             )},
            {"page": "/dashboard/trace", "anchor": "trace-produce-machine",
             "needs": "production.book", "fill": {"value": "{machine}", "default": ""},
             "title": "On which machine",
             "body": "What the unit's own history will say it was made on."},
            {"page": "/dashboard/trace", "anchor": "trace-produce-count",
             "needs": "production.book", "fill": {"value": "{count}", "default": "1"},
             "title": "How many",
             "body": (
                 "{count}. Each one gets its own serial, minted by the plant - there "
                 "is no box to name a serial here, and a serial somebody types is a "
                 "serial that can already exist."
             )},
            {"page": "/dashboard/trace", "anchor": "trace-produce-submit",
             "needs": "production.book",
             "title": "Press Produce",
             "body": "The units exist from this moment, each traceable on its own."},
        ],
        "evidence": {"page": "/dashboard/trace", "anchor": "trace-produce-result",
                     "title": "The serials it minted",
                     "body": "Each is a link: following one shows that unit's whole history."},
    },
}


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def surface_for(tool: str, args: dict,
                capabilities: set[str] | None = None) -> dict | None:
    """The surface for a proposal, with the proposed arguments filled in.

    `capabilities` is the person the proposal is for, when there is one, so a
    step can say whether they hold what the control behind it needs rather than
    only that something is needed. `None` means nobody is named, and then the
    words claim nothing about anybody.
    """
    surface = SURFACES.get(tool)
    if surface is None:
        return None
    values = _Blank({k: v for k, v in args.items() if v is not None})
    context = surface.get("context")
    if context is not None:
        values.update({k: v for k, v in context(args, capabilities).items() if v is not None})
    steps = [_render(step, values) for step in surface["steps"]]
    return {"title": surface["title"], "steps": steps,
            "evidence": _render(surface["evidence"], values)}


def _render(step: dict, values: dict) -> dict:
    """One authored step with this proposal's values in it.

    `page`, `anchor`, `tab` and `needs` are formatted along with the words, so a
    tool that serves every domain can point at the one it was asked about, and
    name the capability that domain's section is gated on. An authored step that
    names no placeholder is unchanged by this.
    """
    rendered = {k: v for k, v in step.items() if k != "fill"}
    for field in ("title", "body", "page", "anchor", "tab", "needs"):
        if field in step:
            rendered[field] = step[field].format_map(values)
    if "fill" in step:
        value = step["fill"]["value"].format_map(values)
        rendered["fill"] = {"value": value or step["fill"].get("default", "")}
    return rendered


def may_propose(tool: str, capabilities: set[str]) -> bool:
    """Whether this person could have this surface's tool used on their behalf.

    One capability for almost every surface. `needs: None` means it is decided
    per call, and then the question is whether they hold any of the capabilities
    that could gate one - the same question the tool catalogue asks, asked in
    one place so the panel and the catalogue cannot disagree.
    """
    needs = SURFACES[tool]["needs"]
    if needs is not None:
        return needs in capabilities
    from fsmes.services import agent

    return bool((agent.needs_any(tool) or set()) & capabilities)


def suggestions(screen: str | None, capabilities: set[str], names: dict) -> list[str]:
    """Plain-language things to try, for this screen and this person.

    The examples use real codes from the plant (`names`) so a person can send
    one as-is and see something happen; what is on the screen they are
    looking at comes first.
    """
    values = _Blank({k: v for k, v in names.items() if v})
    fallback = _Blank({"machine": "a machine", "characteristic": "a check", "mid": "a value",
                       "order": "an order", "planned_order": "the next planned order", "lot": "a lot",
                       "material": "a material"})
    allowed = [s for tool, s in SURFACES.items() if may_propose(tool, capabilities)]
    here = sorted((s for s in allowed if screen in s.get("pages", [])), key=lambda s: s["pages"].index(screen))
    elsewhere = [s for s in allowed if s not in here]
    out: list[str] = []
    for surface in [*here, *elsewhere]:
        text = surface["example"].format_map(_Blank({**fallback, **values}))
        if text not in out:
            out.append(text)
    if "plant.read" in capabilities:
        out.append("What is running right now, and is anything down?")
    return out[:4]


def visible_guides(capabilities: set[str], db=None) -> list[dict]:
    """Only guides the person could actually follow - built-in ones and, when
    a database is given, the recorded walkthroughs in force at this plant.

    Teaching someone a task they will be refused at the last step is worse
    than saying it is not theirs to do.
    """
    out = [g for g in GUIDES if g["needs"] in capabilities]
    if db is not None:
        from fsmes.services import documents, walkthroughs
        out += [walkthroughs.as_guide(d) for d in documents.approved_walkthroughs(db)
                if (d.needs or "plant.read") in capabilities]
    return out


#: What a signing walk says to somebody who may not sign. The capability, and
#: the product's own plain description of it - so "approve the draft severity"
#: is answered with who signs it, which is the true answer, rather than with
#: "no tool named approve is available to you", which is a fact about the
#: catalogue and no use to anybody standing at a machine.
SIGNING_NOTE = ("Signing this needs {needs} - {about} - which you do not hold. "
                "Somebody who does presses it; the assistant never approves "
                "anything, for anybody.")


def signing_guides(capabilities: set[str]) -> list[dict]:
    """The walks to a signing control that this person may *not* follow.

    Every other guide is simply hidden from somebody who lacks its capability
    (`visible_guides`), because teaching a task that ends in a refusal is
    worse than saying it is not theirs. A signing walk is the exception: the
    person asking to approve a draft is asking about a thing that exists and
    is waiting, and the useful answer names who may sign it. So it is listed,
    carrying that sentence, and refused if it is asked for.
    """
    from fsmes.services import capabilities as capability_names

    out = []
    for guide in GUIDES:
        if not guide.get("signing") or guide["needs"] in capabilities:
            continue
        about = capability_names.CAPABILITIES.get(guide["needs"], "").rstrip(".")
        out.append({**guide,
                    "gated": SIGNING_NOTE.format(needs=guide["needs"], about=about)})
    return out


def listed_guides(capabilities: set[str], db=None) -> list[dict]:
    """Every walk worth naming to this person: the ones they can follow, and
    the signing walks they cannot, each saying who can.

    This is what the agent's conversation is given. `visible_guides` is what
    a screen is given, and what the facts brain routes over, because neither
    of those can say "not yours, ask them" - they can only put a walk up.
    """
    return [*visible_guides(capabilities, db), *signing_guides(capabilities)]


def guide_by_id(guide_id: str, capabilities: set[str], db=None) -> dict | None:
    """A guide the person may follow, built-in or recorded (`doc:<code>`)."""
    if guide_id.startswith("doc:"):
        if db is None:
            return None
        from fsmes.services import documents, walkthroughs
        doc = documents.current(db, guide_id[4:])
        if doc is None or doc.kind != "walkthrough" or (doc.needs or "plant.read") not in capabilities:
            return None
        return walkthroughs.as_guide(doc)
    found = GUIDE_BY_ID.get(guide_id)
    if found is None or found["needs"] not in capabilities:
        return None
    return found


def _catalogue(guides: list[dict]) -> str:
    return "\n".join(f"{g['id']}: {g['title']} — {g['when']}" for g in guides)


def _ask_model(prompt: str, timeout: float | None = None,
               model: str | None = None) -> str | None:
    payload = {"model": model or MODEL, "prompt": prompt, "stream": False, "think": False}
    timeout = TIMEOUT if timeout is None else timeout
    try:
        req = urllib.request.Request(
            f"{OLLAMA}/api/generate", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.load(r).get("response") or "").strip()
    except (urllib.error.URLError, OSError, ValueError):
        return None


SHOW_ME = re.compile(
    r"\bhow (do|does|can|should|would) (i|we|you)\b"
    r"|\bhow to\b"
    r"|\bshow me\b|\bwalk me\b|\bteach me\b|\bguide me\b"
    r"|\bwhere do i (click|go|enter|record|find|put|type)\b"
    r"|\bsteps to\b",
    re.IGNORECASE,
)


def wants_showing(question: str) -> bool:
    """Does this person want a tour, or an answer?

    **This is the gate only for the facts brain** - the local model a plant
    without a key gets, which has no tools, no conversation and nothing but
    one message to judge on. There, deterministic is right: asked to make this
    judgement itself, qwen kept routing "what does our procedure say about an
    out-of-tolerance reading" to a walkthrough, which reads as dodging the
    question. The local model is an optimisation on top of this gate, never
    the gate itself: it decides *which* guide, once the phrasing has
    established that a guide is what was asked for.

    **It is not the gate when the agent is on.** That was the 2026-09-26
    mistake: `/assist/agent` ran `route()` first for everybody, so "no there
    should be a tool for you to show me how to do it" never reached the model
    that held the proposal - it matched `show me`, and the fallback picked a
    walkthrough about quality inspections. The agent has the conversation in
    view and a `show_guide` tool of its own; it is the gate now, and none of
    this is called while it is available.
    """
    return bool(SHOW_ME.search(question))


def _lexical_match(question: str, guides: list[dict]) -> dict | None:
    """The fallback when the model is unavailable or unhelpful.

    Crude on purpose: overlapping words between the question and a guide's
    'when' line. A wrong guide is recoverable - the person reads the title and
    closes it - but no assistant at all when Ollama is down is not.
    """
    # route() has already established that a guide is what was asked for.
    words = set(re.findall(r"[a-z]{4,}", question.lower()))
    if not words:
        return None
    best, score = None, 0
    for guide in guides:
        hay = set(re.findall(r"[a-z]{4,}", (guide["when"] + " " + guide["title"]).lower()))
        overlap = len(words & hay)
        if overlap > score:
            best, score = guide, overlap
    return best if score >= 2 else None


def route(question: str, capabilities: set[str], db=None, guides=None, *,
          timeout: float | None = None, model: str | None = None) -> dict | None:
    """Which guide, if any, answers 'how do I…'.

    `guides` lets a caller read them first and close its database session
    before getting here, because what follows is a call to a model and a
    session held across one holds SQLite's single write lock across it too.
    """
    guides = visible_guides(capabilities, db) if guides is None else guides
    if not guides or not wants_showing(question):
        return None


    # A guide is for someone who wants to be shown where to click. Someone
    # asking what a procedure SAYS wants the answer, not a tour of the screen
    # that holds it - routing both to a guide made the assistant feel like it
    # was dodging the question.
    reply = _ask_model(
        "Decide whether a factory worker wants to be SHOWN how to do "
        "something on screen, or wants a question ANSWERED.\n\n"
        "Reply with a guide id only if they are asking to be walked through "
        "performing a task ('how do I…', 'where do I click to…', 'show me…').\n"
        "Reply NONE if they are asking what something is, what a procedure "
        "says, what the current numbers are, or anything answerable in "
        "words.\n\n"
        f"Guides:\n{_catalogue(guides)}\n\n"
        f"Question: {question}\nId:",
        timeout=timeout, model=model,
    )
    by_id = {g["id"]: g for g in guides}
    if reply:
        token = reply.strip().split()[0].strip(".,:;\"'").lower()
        if token in by_id:
            return by_id[token]
    return _lexical_match(question, guides)


def answer(question: str, context: dict, capabilities: set[str], *,
           context_chars: int | None = None, timeout: float | None = None,
           model: str | None = None) -> str:
    """Answer a question about this plant, from facts gathered by the caller.

    `context_chars`, `timeout` and `model` are this plant's own answers -
    `[admin] assistant_context_chars`, `[admin] assistant_timeout_seconds` and
    `[system] local_model_name` - read by the caller through the session it
    already has. They are parameters rather than read here for the reason the
    docstring above `route` gives: this function is one model call, and a
    database session held across one holds SQLite's single write lock across it
    too. `None` is the literal that was here.
    """
    chars = CONTEXT_CHARS if context_chars is None else int(context_chars)
    guides = visible_guides(capabilities)
    reply = _ask_model(
        "You are the assistant inside a Manufacturing Execution System, "
        "helping the person signed in at a plant.\n\n"
        "Answer in at most four sentences, plainly, using only the facts "
        "below. If the facts do not contain the answer, say so and suggest "
        "where in the system to look. Never invent a number.\n\n"
        # 2026-09-26: with the agent switched off mid-conversation, this
        # model was handed "change the cpk to 2" and "which spc rules are on
        # hold" and improvised - "consult the quality manager", "check the
        # quality procedure document". It cannot change anything and it did
        # not say so, which is the one sentence the person needed.
        "You can read and explain; you cannot change anything in this plant "
        "and you have no tools. If you are asked to change, set, release, "
        "record or book something, say plainly that this plant's agent is "
        "off so you cannot make changes, and name the screen where the "
        "person can do it themselves. Never send them to a document or to "
        "another person instead of saying that.\n\n"
        "If the facts include an approved work instruction that covers the "
        "question, answer from it and name the document and revision. The "
        "plant's signed procedure outranks anything you would otherwise "
        "say.\n\n"
        # default=str because the facts carry timestamps straight from the
        # services, and a serialisation error here would take down the
        # whole answer for want of one date.
        f"Facts about this plant right now:\n"
        f"{json.dumps(context, indent=1, default=str)[:chars]}\n\n"
        f"Things this person is able to do here: {', '.join(sorted(capabilities))}\n"
        f"Tasks you can walk them through: {', '.join(g['title'] for g in guides)}\n\n"
        f"Question: {question}\nAnswer:",
        timeout=timeout, model=model,
    )
    if reply:
        return reply
    return (
        "The local model is not answering, so I cannot help with that one. "
        "The numbers on screen are still live - and I can still walk you "
        "through a task if you ask how to do something."
    )
