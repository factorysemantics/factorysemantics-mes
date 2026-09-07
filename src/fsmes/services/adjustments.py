"""The recommendation queue: propose, decide, write, verify.

Three guards, each checked on its own so no single mistake reaches a PLC:

1. The manifest must declare the tag writable with bounds - checked here,
   at proposal, from the tag catalog.
2. The API enforces the bounds again at approval, against the bounds
   snapshotted on the recommendation, so an edited manifest cannot widen a
   pending one.
3. The OPC agent refuses anything outside the manifest's bounds at the
   moment of writing, from its own copy of the manifest.

After the write the agent reads the driven process value back and the
system records whether it followed. A recommendation that was written and
did not move the process is a finding, not a success.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import AdjustmentStatus, RecommendedAdjustment
from fsmes.services import Conflict, Invalid, NotFound, audit, masterdata, tags

# How much of the way to the setpoint the process must have travelled, by
# the time verification runs, to count as having followed. Half: a first-
# order lag reaches 63% in one time constant, and the agent waits three.
FOLLOWED_FRACTION = 0.5


def _writable(equipment_code: str, tag: str, cat: dict | None = None) -> dict:
    """Guard 1: the manifest's word on this tag. Raises if it may not be written."""
    entry = (cat if cat is not None else tags.catalog()).get(equipment_code) or {}
    meta = entry.get("meta", {}).get(tag)
    if meta is None:
        raise Invalid(f"{equipment_code}.{tag} is not in the tag manifest; nothing may be written to it")
    if not meta.get("writable"):
        raise Invalid(f"{equipment_code}.{tag} is not declared writable")
    if meta.get("min") is None or meta.get("max") is None:
        raise Invalid(f"{equipment_code}.{tag} is writable but has no declared bounds; refusing")
    return meta


def _check_bounds(value: float, minimum: float, maximum: float, where: str) -> None:
    if not minimum <= value <= maximum:
        raise Invalid(f"{value:g} is outside the declared bounds {minimum:g}-{maximum:g} ({where})")


def _next_code(session: Session) -> str:
    n = session.scalar(select(func.count()).select_from(RecommendedAdjustment)) or 0
    return f"ADJ-{n + 1:05d}"


def propose(session: Session, *, equipment_code: str, tag: str, value: float, rationale: str,
            evidence: dict | None = None, verify_after_seconds: float | None = None,
            actor: str = "system", cat: dict | None = None) -> RecommendedAdjustment:
    """A recommendation. Nothing is written; a person decides."""
    masterdata.get_equipment(session, equipment_code)
    meta = _writable(equipment_code, tag, cat)
    minimum, maximum = float(meta["min"]), float(meta["max"])
    _check_bounds(value, minimum, maximum, "at proposal")
    if not rationale.strip():
        raise Invalid("a recommendation needs a rationale - what was seen, and why this value")
    current = tags.latest_value(session, equipment_code, tag)
    open_already = session.scalar(select(RecommendedAdjustment).where(
        RecommendedAdjustment.equipment_code == equipment_code, RecommendedAdjustment.tag == tag,
        RecommendedAdjustment.status.in_((AdjustmentStatus.PROPOSED, AdjustmentStatus.APPROVED,
                                          AdjustmentStatus.WRITTEN))))
    if open_already is not None:
        raise Conflict(f"{open_already.code} is already open on {equipment_code}.{tag}; decide it first")
    rec = RecommendedAdjustment(
        code=_next_code(session), equipment_code=equipment_code, tag=tag, drives=meta.get("drives"),
        current_value=current, proposed_value=value, minimum=minimum, maximum=maximum,
        rationale=rationale, evidence=evidence or {}, proposed_by=actor,
        verify_after_seconds=verify_after_seconds if verify_after_seconds is not None
        else max(3 * float(meta.get("lag_s", 40.0)), 30.0))
    session.add(rec)
    session.flush()
    audit.record(session, actor=actor, action="adjustment.proposed", entity_type="equipment",
                 entity_id=equipment_code,
                 after={"code": rec.code, "tag": tag, "from": current, "to": value, "rationale": rationale[:200]})
    return rec


def get(session: Session, code: str) -> RecommendedAdjustment:
    rec = session.scalar(select(RecommendedAdjustment).where(RecommendedAdjustment.code == code))
    if rec is None:
        raise NotFound(f"no recommendation {code}")
    return rec


def approve(session: Session, code: str, note: str | None = None, actor: str = "system") -> RecommendedAdjustment:
    """Guard 2: a person says yes, and the bounds are checked again."""
    rec = get(session, code)
    if rec.status is not AdjustmentStatus.PROPOSED:
        raise Invalid(f"{code} is {rec.status.value}, not proposed")
    _check_bounds(rec.proposed_value, rec.minimum, rec.maximum, "at approval")
    rec.status = AdjustmentStatus.APPROVED
    rec.decided_by, rec.decided_at, rec.decision_note = actor, utcnow(), note
    audit.record(session, actor=actor, action="adjustment.approved", entity_type="equipment",
                 entity_id=rec.equipment_code, after={"code": code, "tag": rec.tag, "value": rec.proposed_value})
    return rec


def reject(session: Session, code: str, note: str | None = None, actor: str = "system") -> RecommendedAdjustment:
    rec = get(session, code)
    if rec.status not in (AdjustmentStatus.PROPOSED, AdjustmentStatus.APPROVED):
        raise Invalid(f"{code} is {rec.status.value}; it cannot be rejected now")
    rec.status = AdjustmentStatus.REJECTED
    rec.decided_by, rec.decided_at, rec.decision_note = actor, utcnow(), note
    audit.record(session, actor=actor, action="adjustment.rejected", entity_type="equipment",
                 entity_id=rec.equipment_code, after={"code": code, "note": note})
    return rec


# ------------------------------------------------------------ the agent side

def approved_writes(session: Session) -> list[dict]:
    """What the OPC agent should write now, detached from the session."""
    rows = session.scalars(select(RecommendedAdjustment).where(
        RecommendedAdjustment.status == AdjustmentStatus.APPROVED).order_by(RecommendedAdjustment.id))
    return [{"code": r.code, "equipment": r.equipment_code, "tag": r.tag, "value": r.proposed_value,
             "drives": r.drives, "minimum": r.minimum, "maximum": r.maximum,
             "verify_after_seconds": r.verify_after_seconds} for r in rows]


def mark_written(session: Session, code: str, value: float, actor: str = "opc-agent") -> RecommendedAdjustment:
    rec = get(session, code)
    rec.status = AdjustmentStatus.WRITTEN
    rec.written_at, rec.written_value = utcnow(), value
    audit.record(session, actor=actor, action="adjustment.written", entity_type="equipment",
                 entity_id=rec.equipment_code, after={"code": code, "tag": rec.tag, "value": value})
    return rec


def mark_failed(session: Session, code: str, error: str, actor: str = "opc-agent") -> RecommendedAdjustment:
    rec = get(session, code)
    rec.status = AdjustmentStatus.FAILED
    rec.verified_at = utcnow()
    rec.verification = {"error": error[:300]}
    audit.record(session, actor=actor, action="adjustment.failed", entity_type="equipment",
                 entity_id=rec.equipment_code, after={"code": code, "error": error[:200]})
    return rec


def due_for_verification(session: Session) -> list[dict]:
    now = utcnow()
    rows = session.scalars(select(RecommendedAdjustment).where(
        RecommendedAdjustment.status == AdjustmentStatus.WRITTEN))
    return [{"code": r.code, "equipment": r.equipment_code, "tag": r.tag, "drives": r.drives,
             "written_value": r.written_value, "current_value": r.current_value}
            for r in rows if r.written_at and now - r.written_at >= timedelta(seconds=r.verify_after_seconds)]


def verify(session: Session, code: str, *, pv_before: float | None, pv_after: float | None,
           actor: str = "opc-agent") -> RecommendedAdjustment:
    """Did the process follow? Judged against how far it had to travel."""
    rec = get(session, code)
    if rec.status is not AdjustmentStatus.WRITTEN:
        raise Invalid(f"{code} is {rec.status.value}, not written")
    start = rec.current_value if rec.current_value is not None else pv_before
    followed = None
    if pv_before is not None and pv_after is not None and start is not None:
        distance = rec.written_value - start if rec.written_value is not None else 0.0
        travelled = pv_after - pv_before
        followed = (abs(distance) < 1e-9) or (travelled / distance >= FOLLOWED_FRACTION if distance else True)
    rec.verified_at = utcnow()
    rec.verification = {"pv_before": pv_before, "pv_after": pv_after, "setpoint": rec.written_value,
                        "followed": followed}
    rec.status = AdjustmentStatus.VERIFIED if followed else AdjustmentStatus.FAILED
    audit.record(session, actor=actor, action="adjustment.verified" if followed else "adjustment.failed",
                 entity_type="equipment", entity_id=rec.equipment_code,
                 after={"code": code, **rec.verification})
    return rec


# ------------------------------------------------------------------ reading

def out(rec: RecommendedAdjustment) -> dict:
    return {
        "code": rec.code, "equipment": rec.equipment_code, "tag": rec.tag, "drives": rec.drives,
        "current_value": rec.current_value, "proposed_value": rec.proposed_value,
        "minimum": rec.minimum, "maximum": rec.maximum, "rationale": rec.rationale, "evidence": rec.evidence,
        "proposed_by": rec.proposed_by, "proposed_at": rec.proposed_at, "status": rec.status.value,
        "decided_by": rec.decided_by, "decided_at": rec.decided_at, "decision_note": rec.decision_note,
        "written_at": rec.written_at, "written_value": rec.written_value,
        "verify_after_seconds": rec.verify_after_seconds, "verified_at": rec.verified_at,
        "verification": rec.verification,
    }


def listing(session: Session, status: str | None = None, limit: int = 100, *,
            equipment: str | None = None, offset: int = 0) -> tuple[list[dict], int]:
    """One page of the queue, newest first, and how many match."""
    query = select(RecommendedAdjustment).order_by(RecommendedAdjustment.id.desc())
    if status:
        query = query.where(RecommendedAdjustment.status == AdjustmentStatus(status))
    if equipment:
        query = query.where(RecommendedAdjustment.equipment_id == masterdata.get_equipment(session, equipment).id)
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    return [out(r) for r in session.scalars(query.limit(limit).offset(offset))], total
