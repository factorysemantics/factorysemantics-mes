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
MODEL = "qwen3:8b"

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
]

GUIDE_BY_ID = {g["id"]: g for g in GUIDES}


# --------------------------------------------------------------------------
# Surfaces. A write tool the agent may propose, mapped to the screen that does
# the same thing by hand: which anchors, which proposed argument fills which
# control, and where the evidence appears afterwards. "Show me" walks the
# person to the real button with the form already filled in; "Do it" runs the
# tool and then walks them to the evidence. One mapping, both modes - and like
# guides it is authored data, so the model never invents a selector.
# --------------------------------------------------------------------------

SURFACES: dict[str, dict] = {
    "record_check": {
        "title": "Record a quality inspection",
        "needs": "quality.record",
        "pages": ["/dashboard", "/dashboard/quality"],
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
}


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def surface_for(tool: str, args: dict) -> dict | None:
    """The surface for a proposal, with the proposed arguments filled in."""
    surface = SURFACES.get(tool)
    if surface is None:
        return None
    values = _Blank({k: v for k, v in args.items() if v is not None})
    steps = []
    for step in surface["steps"]:
        rendered = {k: v for k, v in step.items() if k != "fill"}
        rendered["title"] = step["title"].format_map(values)
        rendered["body"] = step["body"].format_map(values)
        if "fill" in step:
            value = step["fill"]["value"].format_map(values)
            rendered["fill"] = {"value": value or step["fill"].get("default", "")}
        steps.append(rendered)
    return {"title": surface["title"], "steps": steps, "evidence": dict(surface["evidence"])}


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
    allowed = [s for s in SURFACES.values() if s["needs"] in capabilities]
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


def _ask_model(prompt: str, timeout: float = 60.0) -> str | None:
    payload = {"model": MODEL, "prompt": prompt, "stream": False, "think": False}
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

    Deterministic on purpose. Asked to make this judgement, the model kept
    routing "what does our procedure say about an out-of-tolerance reading"
    to a walkthrough, which reads as dodging the question. The model is an
    optimisation on top of this gate, never the gate itself: it decides
    *which* guide, once the phrasing has already established that a guide is
    what was asked for.
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


def route(question: str, capabilities: set[str], db=None) -> dict | None:
    """Which guide, if any, answers 'how do I…'."""
    guides = visible_guides(capabilities, db)
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
        f"Question: {question}\nId:"
    )
    by_id = {g["id"]: g for g in guides}
    if reply:
        token = reply.strip().split()[0].strip(".,:;\"'").lower()
        if token in by_id:
            return by_id[token]
    return _lexical_match(question, guides)


def answer(question: str, context: dict, capabilities: set[str]) -> str:
    """Answer a question about this plant, from facts gathered by the caller."""
    guides = visible_guides(capabilities)
    reply = _ask_model(
        "You are the assistant inside a Manufacturing Execution System, "
        "helping the person signed in at a plant.\n\n"
        "Answer in at most four sentences, plainly, using only the facts "
        "below. If the facts do not contain the answer, say so and suggest "
        "where in the system to look. Never invent a number.\n\n"
        "If the facts include an approved work instruction that covers the "
        "question, answer from it and name the document and revision. The "
        "plant's signed procedure outranks anything you would otherwise "
        "say.\n\n"
        # default=str because the facts carry timestamps straight from the
        # services, and a serialisation error here would take down the
        # whole answer for want of one date.
        f"Facts about this plant right now:\n"
        f"{json.dumps(context, indent=1, default=str)[:3000]}\n\n"
        f"Things this person is able to do here: {', '.join(sorted(capabilities))}\n"
        f"Tasks you can walk them through: {', '.join(g['title'] for g in guides)}\n\n"
        f"Question: {question}\nAnswer:"
    )
    if reply:
        return reply
    return (
        "The local model is not answering, so I cannot help with that one. "
        "The numbers on screen are still live - and I can still walk you "
        "through a task if you ask how to do something."
    )
