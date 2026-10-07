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
    # Process engineering: the vocabulary the plant's own records are written
    # in. Split from `masterdata.write` rather than folded into it, because
    # naming six downtime reasons should not mean being administrator of the
    # whole plant - which was the only way to do it before these two existed.
    "process.define":    "Draft the plant's process vocabulary: the downtime reasons an operator picks from",
    "process.approve":   "Put a process vocabulary in force - what every stop from now on is named with",
    # Quality engineering: the plant's own words for its quality records, and
    # the numbers its charts and certificates are judged against. Split from
    # `masterdata.write` for the same reason process engineering was - naming
    # four severities should not mean being administrator of the whole plant.
    "quality.define":    "Draft the plant's quality vocabulary: the severities a non-conformance is raised at",
    "quality.approve":   "Put a quality vocabulary in force - what every non-conformance from now on is graded with",
    # Supply chain: what this plant asks of the link to its ERP - how long it
    # keeps trying to deliver a confirmation, which order statuses it will
    # take an order in, and how long it waits on a system it does not own.
    # Named in decision 0035 §2 on 2026-09-21 and unused until 2026-09-25;
    # there is no `erp.approve` beside it because none of these has a pending
    # state to sign - rule three of that decision.
    "erp.define":        "Set what this plant asks of its ERP link: retries, open statuses, timeouts and tolerances",
    # Controls engineering. Named in decision 0035 §2 on 2026-09-21 and added
    # on 2026-09-25, when the first thing to gate on it existed: the numbers
    # behind the OPC agent, the namespace outbox and the trigger evaluator, on
    # Engineering's Configuration page. 0035 said adding this later would be
    # cheap because a capability is a string and a role is a list of them, and
    # it was.
    #
    # `signals.approve` is in that table too and is deliberately **not** here.
    # There is nothing yet for it to approve: these numbers take effect when
    # they are saved (rule three of 0035), and a capability a plant could grant
    # that gates nothing is a role saying something untrue about itself. The
    # day controls engineering has a vocabulary to put in force, it arrives
    # with the thing it gates.
    "signals.define":    "Set the numbers behind the plant's signals: how hard the OPC "
                         "agent retries, how densely tags are sampled, how fast an "
                         "approved trigger reaches a machine",
    # Naming a person in an aggregate answer. Decision 0039 clause 3, and the
    # only capability in this table that **no shipped role holds** - not the
    # administrator, and not the analyst whose whole job is the analysis this
    # gates. A plant that wants a per-operator ranking grants it deliberately,
    # to a role it defines, and every answer it unlocks writes an audit row the
    # person named can find (`analysis.person_named`).
    #
    # `audit.read` is deliberately not this gate. Reading one conversation to
    # find out why the assistant failed, and ranking eleven operators by how
    # often they asked for help, are two different acts, and two acts should not
    # share one gate - which is what an analysis over a table built to debug an
    # assistant would otherwise be.
    "people.analyse":    "Name a person in an analysis: see a rollup broken down by "
                         "account rather than by role, workcenter or shift",
}

_VIEWER = ("plant.read",)
_OPERATOR = (
    *_VIEWER,
    "production.book", "production.consume",
    "orders.create", "orders.release", "quality.record", "equipment.state",
    "maintenance.perform",
)
#: The analysis agent's bundle: see the plant, and see the record of what was
#: done to it. Nothing that writes. Deliberately not `_VIEWER` plus one thing -
#: it is written out, because a capability added to the viewer's bundle in a
#: later release must not silently reach a role whose whole claim is that it
#: holds two.
_ANALYST = ("plant.read", "audit.read")
#: The production planner's bundle: see the plant, and put an order in the
#: book. Deliberately not the operator's: a planner plans, and releasing an
#: order to the floor is the supervisor's act, so `orders.release` is not
#: here. The old ladder could not express this either - anyone who could
#: create an order could also release it, book against it and close it.
_PLANNER = ("plant.read", "orders.create")
_SUPERVISOR = (*_OPERATOR, "orders.close", "quality.close_nc", "audit.read",
               "documents.write", "triggers.write", "adjustments.propose")
_ADMIN = (*_SUPERVISOR, "masterdata.write", "users.manage",
          "documents.approve", "maintenance.plan",
          "scheduling.plan", "triggers.approve", "adjustments.approve",
          "process.define", "process.approve",
          "quality.define", "quality.approve",
          "erp.define",
          # The administrator holds everything, so a new capability lands here
          # and a plant that has not redefined the role gets it on upgrade.
          # It is deliberately not on `agent`: an agent may draft a vocabulary
          # for somebody to approve, and retuning how hard the OPC agent
          # retries a booking is not drafting - there is nobody in the loop
          # after it.
          "signals.define")

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
            "what it sees, read the audit trail, draft instructions and draft the "
            "plant's process and quality vocabularies. It never approves - not an "
            "instruction, not a trigger, not a setpoint, not a reason code and not "
            "a severity - never administers accounts and never defines master "
            "data; an admin grants more, deliberately, per plant."
        ),
        "capabilities": [*_OPERATOR, "audit.read", "documents.write", "triggers.write",
                         "adjustments.propose", "process.define", "quality.define"],
    },
    # The analysis agent's role, and the narrowest bundle in the product: it
    # reads the plant and reads the record of what was done to it, and holds
    # nothing that writes anything anywhere. The kind that runs under it is
    # offered a catalogue built by *exclusion* - every read tool, no write tool
    # (`agent.KINDS`) - and this role is the second gate behind that one: even
    # a catalogue with a bug in it cannot write, because the account it calls
    # the plant's own API with may not.
    #
    # `audit.read` is here and not on `viewer` because the whole point of the
    # kind is to explain what happened, and what happened includes what the
    # plant's own AI and people did - the trace and the audit trail. It reads
    # the record; it adds nothing to it.
    "analyst": {
        "name": "Analyst",
        "description": (
            "Read the plant and the record of what was done to it, and change "
            "nothing. What the analysis agent holds: every read there is, no "
            "write anywhere - it cannot book, cannot draft, cannot propose and "
            "cannot approve. An admin who grants it more has made it something "
            "other than an analyst."
        ),
        "capabilities": list(_ANALYST),
    },
    # The production planner's role. In a real plant the orders come from a
    # planner or from the ERP, and this is that person expressed as a bundle:
    # read the plant, put an order in the book, and nothing else. It is the
    # second-narrowest role in the product, and it is narrow on purpose -
    # `orders.release` is the supervisor's, so a plant whose orders arrive
    # this way still has the sequence planner -> supervisor -> floor that a
    # real plant has, and the audit trail says which of the three acted.
    "planner": {
        "name": "Planner",
        "description": (
            "Plan work: create work orders and read the plant. Cannot release "
            "an order to the floor, book against one or close one - planning "
            "and running are two acts, and a plant whose planner could do "
            "both has no sequence left to audit."
        ),
        "capabilities": list(_PLANNER),
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

# What a protected role may not be saved without. Take `users.manage` off
# `admin` and the screen that could grant it back is the screen you have just
# locked yourself out of - a plant nobody can administer, with no way in from
# the inside. Deletion is already refused for the same reason; this is the
# other way to arrive at the same plant.
REQUIRED: dict[str, tuple[str, ...]] = {
    "admin": ("users.manage",),
}


def unknown(capabilities: list[str]) -> list[str]:
    """Capability names the product does not recognise.

    A typo in a role definition is silent otherwise: the role simply never
    grants the thing the admin thought they granted.
    """
    return sorted(c for c in capabilities if c not in CAPABILITIES)


def missing_required(code: str, capabilities: list[str]) -> list[str]:
    """What a protected role is about to lose that it may not lose."""
    return [c for c in REQUIRED.get(code, ()) if c not in capabilities]


def differs_from_shipped(code: str, capabilities: list[str]) -> bool:
    """Whether this bundle is something other than the one the product ships.

    A role nobody has changed keeps getting new capabilities from upgrades; a
    role a plant has redefined must not, or the upgrade quietly overrules the
    admin. This is the question that tells the two apart.
    """
    spec = BUILTIN_ROLES.get(code)
    return spec is not None and set(capabilities) != set(spec["capabilities"])


# ----------------------------------------------------- who holds one of them

#: How many role names a refusal says before it counts the rest. Three fits a
#: sentence read on a phone at a machine; house rule two says the ones it does
#: not name are still counted out loud.
NAMED = 3


def describe(capability: str) -> str:
    """The product's own plain words for one capability, without its full stop.

    The description an admin reads while building a role is the description a
    person refused for want of that capability should read too - one sentence
    per capability, written once, in `CAPABILITIES`.
    """
    return CAPABILITIES.get(capability, "").rstrip(".")


def holders(capability: str, roles: dict[str, list[str]] | None = None) -> list[str]:
    """The names of the roles that hold one capability.

    `roles` is `{name: capabilities}` - this plant's own roles, read by a
    caller that had a database session open (`auth.role_bundles`). Empty or
    absent, the answer is the bundles the product ships: right for a plant that
    has not redefined anything, and the only honest answer available where
    there is no plant to ask (the scripted eval suite, a CLI). Empty rather
    than `None` counts as absent on purpose - every plant has roles, so an
    empty mapping is a caller that did not read them, not a plant with none.
    """
    bundles = roles or {spec["name"]: list(spec["capabilities"])
                        for spec in BUILTIN_ROLES.values()}
    return [name for name, granted in bundles.items() if capability in granted]


def who_holds(capability: str, roles: dict[str, list[str]] | None = None) -> str:
    """Who can do the thing this capability gates, as a clause in a sentence.

    Never "somebody who does": a person standing at a machine has to know who
    to call. When more roles hold it than a sentence can carry, the named ones
    are counted against the total rather than trailed off - unknown is not
    zero, and neither is "and others".
    """
    names = holders(capability, roles)
    if not names:
        return ("No role at this plant holds it, so a plant administrator has to "
                "grant it before anybody can")
    shown = names[:NAMED]
    line = f"{_written(shown)} can"
    if len(names) > len(shown):
        line += f" - {len(shown)} of the {len(names)} roles that hold it"
    return line


def _written(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


#: What the assistant says about anything a person's role does not let them do.
#:
#: One pattern for every refusal, because there was nearly a second one. Before
#: this, a request outside somebody's role came back as *"no tool named
#: 'close_nonconformance' is available to this person"* - a true fact about the
#: catalogue and no use whatever to an operator looking at a non-conformance
#: that needs closing. The signing walks (#113) already answered their half of
#: this properly; this is the same sentence, from the same lookup, for the
#: other half.
#:
#: The order of the three facts is load-bearing. A tool result is summarised to
#: 160 characters in the turn record and in the transcript the panel shows, so
#: the capability, the fact that it is not held, and who holds it come first;
#: the plain description, which is the part a reader can most afford to lose,
#: comes last. Some descriptions are a sentence and a half on their own.
NOT_YOURS = ("{needs} is what {gates}, and you do not hold it. {who}. "
             "That capability is: {about}.")


def not_yours(capability: str, *, gates: str = "this needs",
              roles: dict[str, list[str]] | None = None) -> str:
    """The one refusal sentence, for one capability.

    `gates` is how the capability relates to what was asked - "this needs" for
    a tool that was not offered, "signs this" for a signature that is somebody
    else's to give.
    """
    return NOT_YOURS.format(needs=capability, gates=gates,
                            who=who_holds(capability, roles),
                            about=describe(capability) or "not described in this version")
