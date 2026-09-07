"""What a person is allowed to do, as capabilities rather than rank.

The MES shipped with a role *ladder*: viewer → operator → supervisor → admin,
each rung inheriting everything below it. That models seniority, and seniority
is not what a plant actually grants. It cannot express "this person records
inspections and nothing else", because anyone who can inspect is an operator,
and every operator can also book production, issue material and change machine
states.

So a role is now a named bundle of capabilities, and bundles are data. The
built-in four keep exactly the powers they had - existing accounts are
unaffected - and Quality Inspector exists because the ladder could not say it.
An admin can define more without a deployment.

Capability names are `area.verb`. They are the vocabulary the whole system
gates on: the API, the agent tools, and the screens all ask the same question.
"""

from __future__ import annotations

# Every capability the product recognises, with the sentence an admin building
# a role needs to read. Adding one here and gating an endpoint with it is the
# entire process for making a new power grantable.
CAPABILITIES: dict[str, str] = {
    "plant.read":        "See the plant: machines, orders, quality, analysis",
    "production.book":   "Book production and scrap against an operation",
    "production.consume": "Issue material from a lot to an order",
    "orders.create":     "Create work orders",
    "orders.release":    "Release work orders to the floor",
    "orders.close":      "Close or cancel work orders",
    "quality.record":    "Record quality checks",
    "quality.close_nc":  "Close non-conformances",
    "equipment.state":   "Set a machine's state and label its downtime",
    "masterdata.write":  "Define equipment, materials, BOMs and routings",
    "users.manage":      "Create accounts and assign roles",
    "audit.read":        "Read the audit trail",
    "documents.write":   "Draft and revise work instructions",
    "documents.approve": "Approve a work instruction and put it in force",
    "maintenance.perform": "Raise, start and complete maintenance work",
    "maintenance.plan":    "Define preventive maintenance plans",
    "scheduling.plan":     "Schedule work and define the plant calendar",
    "triggers.write":      "Draft triggers: a condition on a tag, then one catalogued action",
    "triggers.approve":    "Put a trigger in force, or withdraw one - logic against a live plant",
    "adjustments.propose": "Recommend a setpoint change, with a rationale and evidence",
    "adjustments.approve": "Approve or reject a setpoint change - the human in the loop before a PLC write",
}

_VIEWER = ("plant.read",)
_OPERATOR = (
    *_VIEWER,
    "production.book", "production.consume",
    "orders.create", "orders.release", "quality.record", "equipment.state",
    "maintenance.perform",
)
_SUPERVISOR = (*_OPERATOR, "orders.close", "quality.close_nc", "audit.read",
               "documents.write", "triggers.write", "adjustments.propose")
_ADMIN = (*_SUPERVISOR, "masterdata.write", "users.manage",
          "documents.approve", "maintenance.plan",
          "scheduling.plan", "triggers.approve", "adjustments.approve")

# The built-ins. The first four are the old ladder expressed as bundles, so
# nothing an existing account could do changes. They are protected from
# deletion because an MES with no admin role is a plant nobody can administer.
BUILTIN_ROLES: dict[str, dict] = {
    "viewer": {
        "name": "Viewer",
        "description": "Read the plant and change nothing.",
        "capabilities": list(_VIEWER),
    },
    "operator": {
        "name": "Operator",
        "description": "Run production: book output, issue material, record checks, set machine states.",
        "capabilities": list(_OPERATOR),
    },
    "supervisor": {
        "name": "Supervisor",
        "description": "Everything an operator does, plus closing orders and non-conformances.",
        "capabilities": list(_SUPERVISOR),
    },
    "admin": {
        "name": "Administrator",
        "description": "Everything, including master data and user administration.",
        "capabilities": list(_ADMIN),
    },
    "agent": {
        "name": "Agent",
        "description": (
            "What an agent deployment holds by default: run production and record "
            "what it sees, read the audit trail, draft instructions. It never "
            "approves, never administers accounts and never defines master data - "
            "an admin grants more, deliberately, per plant."
        ),
        "capabilities": [*_OPERATOR, "audit.read", "documents.write", "triggers.write",
                         "adjustments.propose"],
    },
    "quality_inspector": {
        "name": "Quality Inspector",
        "description": (
            "Records inspections and nothing else. Cannot book production, issue "
            "material or change machine states - the role the old ladder could "
            "not express."
        ),
        "capabilities": ["plant.read", "quality.record"],
    },
}

PROTECTED = ("admin",)


def unknown(capabilities: list[str]) -> list[str]:
    """Capability names the product does not recognise.

    A typo in a role definition is silent otherwise: the role simply never
    grants the thing the admin thought they granted.
    """
    return sorted(c for c in capabilities if c not in CAPABILITIES)
