"""Drafting work instructions from what the plant already knows.

The specification says fill weight must be 494-506 g on FG-BOTTLE, and the
routing says which machine fills it. That is most of an instruction already;
what is missing is the prose an operator reads at 6am. The local model writes
that prose from those facts.

Deliberately a draft. It arrives as revision 1 in draft status, marked with
the model that wrote it, and someone has to approve it before the floor sees
it. A procedure nobody read and approved is not a procedure, and a model that
could put words directly in front of an operator is a hazard rather than a
feature.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import QualitySpec, Routing
from fsmes.services import documents

OLLAMA = "http://127.0.0.1:11434"
#: The model this product ships with, and the default of `[system]
#: local_model_name`.
MODEL = "qwen3:8b"
#: How long this plant waits for it. `[admin] drafting_timeout_seconds`, and
#: the literal that was here - the longest of the six, because it is the one
#: writing prose.
TIMEOUT = 180.0

#: The opening line, which names what is being written and is not a style.
OPENING = "Write a work instruction for a factory operator."

#: The structure a drafted instruction takes. This much **is** the plant's -
#: a plant whose quality system mandates Scope / Hazards / Steps / Records is
#: getting the wrong shape today - and is the default of `[admin]
#: document_house_style`.
HOUSE_STYLE = """- Markdown. A one-line Purpose, then numbered Steps, then a short
  "If it fails" section.
- Six to ten steps. Each step is one action, in the imperative.
- No preamble, no closing remarks, no headings above the Purpose line."""

#: What no plant may edit away, added to whatever the plant writes above.
#:
#: The middle clause is the reason this is a separate constant rather than
#: three more lines of a setting: **an operator must never be told to adjust a
#: reading toward the middle.** That is not a house style - it is the
#: difference between a measurement and a fiction, and a plant that could edit
#: it out of the prompt would be a plant whose own drafted procedures quietly
#: taught its people to falsify a reading. The two around it are the same kind
#: of thing one step down: a drafted document that invented a tolerance or a
#: tool would be a controlled document made of nothing.
INVARIANTS = """- Use only the facts given. Never invent a tolerance, a tool, or a machine.
- Say what to do when the reading is out of tolerance: record it as measured.
  An operator must never be told to adjust a reading toward the middle."""


def instructions(session: Session) -> str:
    """The whole brief handed to the model: this plant's structure, and the
    clauses no plant may edit away, in that order."""
    from fsmes.services import plant_settings

    style = str(plant_settings.setting(session, "admin", "document_house_style"))
    return f"{OPENING}\n\nRules:\n{style}\n{INVARIANTS}\n"


def _ask(prompt: str, timeout: float | None = None, model: str | None = None) -> str | None:
    payload = {"model": model or MODEL, "prompt": prompt, "stream": False, "think": False}
    timeout = TIMEOUT if timeout is None else timeout
    try:
        req = urllib.request.Request(
            f"{OLLAMA}/api/generate", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.load(r).get("response") or "").strip() or None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def proposals(session: Session) -> list[dict]:
    """What this plant has enough facts to write an instruction about.

    Every quality specification earns one - a characteristic with a tolerance
    and no procedure for measuring it is a gap - and every routing earns a
    setup instruction.
    """
    out: list[dict] = []

    for spec in session.scalars(select(QualitySpec)):
        material = spec.material.code
        # The machines that actually make this material, from its routing.
        # An earlier version passed "the first machine in the plant" as an
        # example, and the model duly wrote "weigh it using the ACME machine" -
        # a fact that invites invention is worse than no fact at all.
        stations = [
            {"operation": op.name, "machine": op.equipment.code}
            for routing in session.scalars(
                select(Routing).where(Routing.material.has(code=material)))
            for op in routing.operations
        ]
        out.append({
            "code": f"WI-{material}-{spec.characteristic}".upper().replace("_", "-"),
            "title": f"Recording {spec.characteristic.replace('_', ' ')} on {material}",
            "anchors": {"material": material, "characteristic": spec.characteristic},
            "facts": {
                "material": material,
                "characteristic": spec.characteristic,
                "unit": spec.unit,
                "lower_limit": spec.min_value,
                "upper_limit": spec.max_value,
                "where_recorded": "the Quality check panel on the floor screen",
                "on_out_of_spec": (
                    "the MES raises a non-conformance automatically; a "
                    "supervisor dispositions it"
                ),
                "stations_making_this": stations or None,
            },
        })

    for routing in session.scalars(select(Routing)):
        steps = [{"seq": op.seq, "name": op.name, "equipment": op.equipment.code}
                 for op in routing.operations]
        if not steps:
            continue
        out.append({
            "code": f"WI-{routing.code}-SETUP".upper(),
            "title": f"Running {routing.name}",
            "anchors": {"material": routing.material.code},
            "facts": {
                "routing": routing.code,
                "material": routing.material.code,
                "operations": steps,
                "where_booked": "the Report production panel on the floor screen",
                "scrap_rule": (
                    "count scrap honestly; unrecorded scrap makes the yield "
                    "figure a lie"
                ),
            },
        })

    return out


def draft_one(session: Session, proposal: dict, actor: str = "system") -> dict:
    """Write and store one instruction as an unapproved draft."""
    existing = documents.revisions(session, proposal["code"])
    if existing:
        return {"code": proposal["code"], "skipped": "already exists",
                "revisions": len(existing)}

    from fsmes.services import plant_settings

    model = str(plant_settings.setting(session, "system", "local_model_name"))
    body = _ask(
        f"{instructions(session)}\n"
        f"Plant facts:\n{json.dumps(proposal['facts'], indent=1, default=str)}\n\n"
        f"Instruction title: {proposal['title']}\n\n"
        f"Write the instruction now.",
        timeout=float(plant_settings.setting(session, "admin", "drafting_timeout_seconds")),
        model=model,
    )
    if not body:
        return {"code": proposal["code"], "error": "the local model did not answer"}

    # The model that actually wrote it, not the one the product ships: the
    # column is `drafted_by_model` and it has to be true of this document.
    doc = documents.create(
        session, code=proposal["code"], title=proposal["title"], body=body,
        anchors=proposal["anchors"], actor=actor, drafted_by_model=model)
    return {"code": doc.code, "revision": doc.revision, "title": doc.title,
            "status": doc.status.value, "drafted_by_model": model,
            "words": len(body.split())}


def draft_all(session: Session, actor: str = "system") -> list[dict]:
    return [draft_one(session, p, actor) for p in proposals(session)]
