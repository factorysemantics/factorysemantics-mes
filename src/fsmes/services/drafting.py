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
MODEL = "qwen3:8b"

HOUSE_STYLE = """Write a work instruction for a factory operator.

Rules:
- Markdown. A one-line Purpose, then numbered Steps, then a short
  "If it fails" section.
- Six to ten steps. Each step is one action, in the imperative.
- Use only the facts given. Never invent a tolerance, a tool, or a machine.
- Say what to do when the reading is out of tolerance: record it as measured.
  An operator must never be told to adjust a reading toward the middle.
- No preamble, no closing remarks, no headings above the Purpose line.
"""


def _ask(prompt: str, timeout: float = 180.0) -> str | None:
    payload = {"model": MODEL, "prompt": prompt, "stream": False, "think": False}
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

    body = _ask(
        f"{HOUSE_STYLE}\n"
        f"Plant facts:\n{json.dumps(proposal['facts'], indent=1, default=str)}\n\n"
        f"Instruction title: {proposal['title']}\n\n"
        f"Write the instruction now."
    )
    if not body:
        return {"code": proposal["code"], "error": "the local model did not answer"}

    doc = documents.create(
        session, code=proposal["code"], title=proposal["title"], body=body,
        anchors=proposal["anchors"], actor=actor, drafted_by_model=MODEL)
    return {"code": doc.code, "revision": doc.revision, "title": doc.title,
            "status": doc.status.value, "drafted_by_model": MODEL,
            "words": len(body.split())}


def draft_all(session: Session, actor: str = "system") -> list[dict]:
    return [draft_one(session, p, actor) for p in proposals(session)]
