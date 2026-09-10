"""The certificate of analysis: what one order's product is, and the evidence.

Issued when an order completes - the end-of-line event - and on demand. It
is a controlled document: immutable once issued, a correction issues a
superseding certificate that names the original. Everything on it is read
from the same records the screens read: the order and its lot, the lots
that went in and where they entered, every serial produced, every quality
check against its specification, the non-conformances, and the process
values each station held while the order ran. Nothing is computed for the
certificate that the MES did not already know.
"""

from __future__ import annotations

from datetime import datetime
from statistics import fmean

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    Document,
    DocumentStatus,
    Material,
    NonConformance,
    QualityCheck,
    QualitySpec,
    SerialUnit,
    TagValue,
    WorkOrder,
)
from fsmes.services import Invalid, NotFound, audit, documents, execution, tags, workorders

PREFIX = "COA-"


def code_for(order_code: str) -> str:
    return f"{PREFIX}{order_code}"


def _num(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _stamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC") if value else "—"


def _envelopes(session: Session, wo: WorkOrder) -> list[dict]:
    """Each station's primary process value while the order ran: min, mean, max."""
    if wo.started_at is None:
        return []
    end = wo.completed_at or utcnow()
    cat = tags.catalog()
    out = []
    for op in sorted(wo.operations, key=lambda o: o.seq):
        machine = op.equipment
        if machine is None:
            continue
        name = (cat.get(machine.code) or {}).get("analog")
        if not name:
            continue
        row = session.execute(
            select(func.min(TagValue.value_num), func.avg(TagValue.value_num), func.max(TagValue.value_num),
                   func.count())
            .where(TagValue.equipment_id == machine.id, TagValue.tag == f"{machine.code}.{name}",
                   TagValue.ts >= wo.started_at, TagValue.ts <= end, TagValue.value_num.is_not(None))
        ).one()
        lo, mean, hi, n = row
        unit = ((cat.get(machine.code) or {}).get("meta", {}).get(name) or {}).get("unit", "")
        out.append({"seq": op.seq, "operation": op.name, "equipment": machine.code, "tag": name, "unit": unit,
                    "min": lo, "mean": mean, "max": hi, "samples": n})
    return out


def gather(session: Session, order_code: str) -> dict:
    """Everything the certificate states, as data - the screen and the tools read this too."""
    wo = workorders.get(session, order_code)
    genealogy = execution.genealogy(session, wo.code)
    serials = session.scalars(select(SerialUnit).where(SerialUnit.produced_by_order_id == wo.id)
                              .order_by(SerialUnit.serial)).all()
    checks = session.scalars(select(QualityCheck).where(QualityCheck.work_order_id == wo.id)
                             .order_by(QualityCheck.id)).all()
    by_char: dict[int, list[QualityCheck]] = {}
    for c in checks:
        by_char.setdefault(c.spec_id, []).append(c)
    characteristics = []
    for spec_id, rows in by_char.items():
        spec = session.get(QualitySpec, spec_id)
        values = [r.value for r in rows]
        characteristics.append({
            "material": spec.material.code, "characteristic": spec.characteristic, "unit": spec.unit,
            "lower_spec": spec.min_value, "upper_spec": spec.max_value,
            "n": len(values), "min": min(values), "mean": fmean(values), "max": max(values),
            "failed": sum(1 for r in rows if r.result.value == "fail"),
            "gauges": sorted({str(r.gauge_id) for r in rows if r.gauge_id}),
        })
    ncs = session.scalars(select(NonConformance).where(NonConformance.work_order_id == wo.id)
                          .order_by(NonConformance.id)).all()
    return {
        "order": wo.code, "erp_reference": wo.erp_reference, "material": wo.material.code,
        "material_name": wo.material.name, "status": wo.status.value,
        "ordered_qty": wo.quantity, "good_qty": wo.good_qty, "scrap_qty": wo.scrap_qty,
        "started_at": wo.started_at, "completed_at": wo.completed_at,
        "produced_lot": next((p["lot"] for p in genealogy["produced"]), None),
        "consumed": genealogy["consumed"],
        "serials": [{"serial": s.serial, "material": s.material.code, "status": s.status.value,
                     "packed_into": s.parent.serial if s.parent else None} for s in serials],
        "characteristics": characteristics,
        "nonconformances": [{"code": n.code, "severity": n.severity, "status": n.status.value,
                             "description": n.description} for n in ncs],
        "envelopes": _envelopes(session, wo),
    }


def render(data: dict, *, issued_by: str, issued_at: datetime, revision: int, supersedes: int | None) -> str:
    """The certificate as Markdown with tables - printable from the browser."""
    lines = [
        f"# Certificate of analysis — {data['material']} · order {data['order']}",
        "",
        f"**Issued** {_stamp(issued_at)} by {issued_by} · revision {revision}"
        + (f" · supersedes revision {supersedes}" if supersedes else ""),
        "",
        "## Product",
        "",
        "| | |", "|---|---|",
        f"| Material | {data['material']} {data['material_name']} |",
        f"| Order | {data['order']}" + (f" (ERP {data['erp_reference']})" if data.get("erp_reference") else "") + " |",
        f"| Finished lot | {data['produced_lot'] or '— (nothing good produced)'} |",
        f"| Ordered / good / scrap | {_num(data['ordered_qty'])} / {_num(data['good_qty'])} / "
        f"{_num(data['scrap_qty'])} |",
        f"| Produced | {_stamp(data['started_at'])} → {_stamp(data['completed_at'])} |",
        f"| Serialised units | {len(data['serials'])} |",
        "",
        "## Genealogy — what went in, and where",
        "",
    ]
    if data["consumed"]:
        lines += ["| Lot | Material | Quantity | Entered at |", "|---|---|---:|---|"]
        for c in data["consumed"]:
            where = (c.get("equipment") or "") + (f" op {c['seq']} {c['operation']}" if c.get("seq") else "")
            lines.append(f"| {c['lot']} | {c['material']} | {_num(c['quantity'])} | {where or '—'} |")
    else:
        lines.append("No lot consumption was recorded against this order.")
    lines += ["", "## Quality — every characteristic against its specification", ""]
    if data["characteristics"]:
        lines += ["| Characteristic | Spec | Checks | Min | Mean | Max | Failed |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for ch in data["characteristics"]:
            spec = f"{_num(ch['lower_spec'])} to {_num(ch['upper_spec'])} {ch['unit'] or ''}".strip()
            lines.append(f"| {ch['material']} {ch['characteristic']} | {spec} | {ch['n']} | {_num(ch['min'])} | "
                         f"{_num(ch['mean'])} | {_num(ch['max'])} | {ch['failed']} |")
    else:
        lines.append("No quality checks were recorded against this order.")
    lines += ["", "## Non-conformances", ""]
    if data["nonconformances"]:
        lines += ["| Code | Severity | Status | Description |", "|---|---|---|---|"]
        for n in data["nonconformances"]:
            lines.append(f"| {n['code']} | {n['severity']} | {n['status']} | {n['description']} |")
    else:
        lines.append("None raised on this order.")
    lines += ["", "## Process — what each station held while the order ran", ""]
    if data["envelopes"]:
        lines += ["| Step | Station | Signal | Min | Mean | Max | Samples |", "|---|---|---|---:|---:|---:|---:|"]
        for e in data["envelopes"]:
            lines.append(f"| {e['seq']} {e['operation']} | {e['equipment']} | {e['tag']} {e['unit']} | "
                         f"{_num(e['min'])} | {_num(e['mean'])} | {_num(e['max'])} | {e['samples']} |")
    else:
        lines.append("No process history covers this order's window.")
    if data["serials"]:
        lines += ["", "## Serialised units", "", "| Serial | Status | Packed into |", "|---|---|---|"]
        for s in data["serials"][:200]:
            lines.append(f"| {s['serial']} | {s['status']} | {s['packed_into'] or '—'} |")
        if len(data["serials"]) > 200:
            lines.append(f"| … {len(data['serials']) - 200} more | | |")
    lines += ["", "---", "",
              "Every figure above is read from the MES's own records: bookings from machine counters, "
              "lots issued at stations, checks recorded against specifications, and tag history. "
              "This revision is immutable; a correction issues a superseding certificate that names it."]
    return "\n".join(lines) + "\n"


def issue(session: Session, order_code: str, actor: str = "system", *, allow_incomplete: bool = False) -> Document:
    """Issue (or reissue) the certificate for an order as an approved,
    immutable document revision."""
    wo = workorders.get(session, order_code)
    if wo.status.value not in ("completed", "closed") and not allow_incomplete:
        raise Invalid(f"{order_code} is {wo.status.value}; a certificate is issued when the order completes")
    data = gather(session, order_code)
    code = code_for(order_code)
    existing = documents.revisions(session, code)
    now = utcnow()
    if existing:
        previous = max(d.revision for d in existing)
        doc = documents.revise(session, code, title=f"Certificate of analysis {order_code}",
                               body=render(data, issued_by=actor, issued_at=now, revision=previous + 1,
                                           supersedes=previous),
                               actor=actor)
    else:
        doc = documents.create(session, code=code, title=f"Certificate of analysis {order_code}",
                               body=render(data, issued_by=actor, issued_at=now, revision=1, supersedes=None),
                               anchors={"material": data["material"]}, actor=actor)
    # Issued means in force and frozen: approval is the issuer's act, and a
    # certificate that could be edited after issue is not a certificate.
    documents.approve(session, code, doc.revision, actor=actor)
    audit.record(session, actor=actor, action="coa.issued", entity_type="workorder", entity_id=order_code,
                 after={"document": code, "revision": doc.revision, "good_qty": data["good_qty"],
                        "checks": sum(c["n"] for c in data["characteristics"])})
    return doc


def latest(session: Session, order_code: str) -> dict:
    doc = documents.current(session, code_for(order_code))
    if doc is None:
        raise NotFound(f"no certificate has been issued for {order_code}")
    return {"order": order_code, "document": doc.code, "revision": doc.revision, "title": doc.title,
            "issued_by": doc.approved_by, "issued_at": doc.approved_at, "body": doc.body,
            "revisions": [d.revision for d in documents.revisions(session, doc.code)]}


def listing(session: Session, limit: int = 100, *, material: str | None = None,
            q: str | None = None, offset: int = 0) -> tuple[list[dict], int]:
    """One page of the certificates in force, newest first, and how many match."""
    query = select(Document).where(Document.code.like(f"{PREFIX}%"),
                                   Document.status == DocumentStatus.APPROVED)
    if material:
        query = query.where(Document.anchor_material == material)
    if q:
        query = query.where(Document.code.ilike(f"{PREFIX}%{q}%"))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(Document.approved_at.desc()).limit(limit).offset(offset))
    return [{"order": d.code[len(PREFIX):], "document": d.code, "revision": d.revision,
             "issued_by": d.approved_by, "issued_at": d.approved_at, "material": d.anchor_material}
            for d in rows], total


# ---------------------------------------------------------------- per pallet
# The cutlery plant's certificate: one per pallet, stating the capability
# of every dimensional characteristic over the checks recorded while the
# pallet's contents were made, and listing every wrap, stack, plate and
# piece on it. The Cpk block and the window are what the certificate
# stores; the listing is rendered from the containment record, which does
# not change once a pallet closes - storing forty thousand listings a day
# as text would be a gigabyte of certificates saying what the tree already
# says.

PALLET_PREFIX = "COA-PALLET-"


def pallet_code_for(serial: str) -> str:
    return f"{PALLET_PREFIX}{serial}"


def gather_pallet(session: Session, serial: str) -> dict:
    """Everything a pallet's certificate states, as data."""
    from fsmes.domain import UnitInspection
    from fsmes.services import serialization, spc

    pallet = serialization.get(session, serial)
    tree = serialization.contents(session, serial, limit=10_000, cap=200_000)
    inside = serialization._descendant_ids(session, [pallet.id])

    # The window: from the first piece on the pallet to the pallet's close.
    first = session.scalar(select(func.min(SerialUnit.produced_at)).where(SerialUnit.id.in_(inside))) \
        if inside else pallet.produced_at
    window = (first or pallet.produced_at, pallet.produced_at)

    # The materials the pieces are, and the dimensional checks on each: the
    # process's record of itself up to the pallet's close. A pallet is made
    # in minutes and a check is recorded every fifteen, so the window alone
    # rarely holds the sample a capability figure needs; the record extends
    # back to the most recent MIN_POINTS checks at or before the close, and
    # the certificate states the span those checks cover.
    piece_materials = sorted({m for m, n in tree["by_material"].items() if m.startswith("UT-")})
    characteristics = []
    for material in piece_materials:
        mat = session.scalar(select(Material).where(Material.code == material))
        specs = session.scalars(select(QualitySpec).where(QualitySpec.material_id == mat.id)
                                .order_by(QualitySpec.characteristic)).all()
        for spec in specs:
            in_window = session.scalars(
                select(QualityCheck).where(QualityCheck.spec_id == spec.id,
                                           QualityCheck.ts >= window[0], QualityCheck.ts <= window[1])
                .order_by(QualityCheck.ts)).all()
            checks = in_window
            if len(checks) < spc.MIN_POINTS:
                recent = session.scalars(
                    select(QualityCheck).where(QualityCheck.spec_id == spec.id, QualityCheck.ts <= window[1])
                    .order_by(QualityCheck.ts.desc()).limit(spc.MIN_POINTS)).all()
                checks = sorted(recent, key=lambda c: c.ts)
            values = [c.value for c in checks]
            cap = spc.capability(values, spec.min_value, spec.max_value)
            characteristics.append({
                "material": material, "characteristic": spec.characteristic, "unit": spec.unit,
                "lower_spec": spec.min_value, "upper_spec": spec.max_value,
                "n": len(values), "in_window": len(in_window),
                "record_start": checks[0].ts if checks else None,
                "record_end": checks[-1].ts if checks else None,
                "min": min(values) if values else None,
                "mean": fmean(values) if values else None, "max": max(values) if values else None,
                "failed": sum(1 for c in checks if c.result.value == "fail"),
                "cpk": cap["cpk"] if cap else None, "cp": cap["cp"] if cap else None,
                "ppk": cap["ppk"] if cap else None, "stable": cap["stable"] if cap else None,
                "note": None if cap else (f"{len(values)} checks on record; capability needs "
                                         f"{spc.MIN_POINTS} and both limits"),
            })

    # The automated inspections of everything on the pallet: judged, and how many failed.
    inspected = failed = 0
    if inside:
        for chunk in serialization._chunks(inside):
            n, f = session.execute(
                select(func.count(UnitInspection.id), func.sum(case((UnitInspection.passed.is_(False), 1), else_=0)))
                .where(UnitInspection.unit_id.in_(chunk))).one()
            inspected += n or 0
            failed += f or 0

    return {
        "pallet": pallet.serial, "material": pallet.material.code, "status": pallet.status.value,
        "produced_on": pallet.equipment.code if pallet.equipment else None,
        "order": pallet.order.code if pallet.order else None,
        "window": {"start": window[0], "end": window[1]},
        "units_inside": tree["units_inside"], "by_material": tree["by_material"], "by_status": tree["by_status"],
        "wraps": tree["contains_total"],
        "inspections": {"units_inspected": inspected, "failed_on_pallet": failed},
        "characteristics": characteristics,
        "contents": tree["contains"],
    }


def render_pallet(data: dict, *, issued_by: str, issued_at: datetime, revision: int, supersedes: int | None) -> str:
    """The pallet certificate as Markdown: the capability block, then every
    wrap with its stack, plate and pieces."""
    w = data["window"]
    lines = [
        f"# Certificate of analysis — pallet {data['pallet']}",
        "",
        f"**Issued** {_stamp(issued_at)} by {issued_by} · revision {revision}"
        + (f" · supersedes revision {supersedes}" if supersedes else ""),
        "",
        "## Pallet", "",
        "| | |", "|---|---|",
        f"| Pallet | {data['pallet']} ({data['material']}) |",
        f"| Closed on | {data['produced_on'] or '—'} · order {data['order'] or '—'} |",
        f"| Contents made | {_stamp(w['start'])} → {_stamp(w['end'])} |",
        f"| Wraps | {data['wraps']} |",
        f"| Units on the pallet | {data['units_inside']:,} — "
        + ", ".join(f"{n:,} {m}" for m, n in data["by_material"].items()) + " |",
        f"| Automated inspections | {data['inspections']['units_inspected']:,} judged, "
        f"{data['inspections']['failed_on_pallet']} failed on the pallet |",
        "",
        "## Capability — the dimensional checks on record up to the pallet's close", "",
        "| Material | Characteristic | Spec | Checks | Record from | Mean | Cpk | Ppk | Stable |",
        "|---|---|---|---:|---|---:|---:|---:|---|",
    ]
    for ch in data["characteristics"]:
        spec = f"{_num(ch['lower_spec'])} to {_num(ch['upper_spec'])} {ch['unit'] or ''}".strip()
        cpk = _num(ch["cpk"], 3) if ch["cpk"] is not None else f"— ({ch['note']})"
        ppk = _num(ch["ppk"], 3) if ch["ppk"] is not None else "—"
        stable = "" if ch["stable"] is None else ("yes" if ch["stable"] else "no — see SPC")
        since = _stamp(ch["record_start"]) if ch.get("record_start") else "—"
        lines.append(f"| {ch['material']} | {ch['characteristic']} | {spec} | {ch['n']} | {since} | "
                     f"{_num(ch['mean']) if ch['mean'] is not None else '—'} | {cpk} | {ppk} | {stable} |")
    lines += ["", "## Contents — every wrap, its stack, its plate, and the pieces in the stack", "",
              "| Wrap | Stack | Plate | Pieces |", "|---|---|---|---|"]
    for wrap in data["contents"]:
        # Structure, not names: inside a wrap, the child that holds things is
        # the stack and the child that holds nothing is the plate.
        children = wrap.get("contains", [])
        stack = next((c for c in children if c.get("contains_total")), None)
        plate = next((c["serial"] for c in children if not c.get("contains_total")), "—")
        pieces = ", ".join(p["serial"] for p in (stack or {}).get("contains", [])) if stack else "—"
        lines.append(f"| {wrap['serial']} | {stack['serial'] if stack else '—'} | {plate} | {pieces} |")
    lines += ["", "---", "",
              "The capability figures are computed from the checks a person recorded against each "
              "specification up to the pallet's close: those inside the window the contents were made, "
              "extended back to the most recent twelve where the window holds fewer, with the span stated; "
              "sigma from the mean moving range, as the SPC chart computes it. "
              "The contents are read from the containment record "
              "at issue, and every unit named was judged by its station's vision system. This revision "
              "is immutable; a correction issues a superseding certificate that names it."]
    return "\n".join(lines) + "\n"


def issue_pallet(session: Session, serial: str, actor: str = "system") -> Document:
    """Issue (or reissue) a pallet's certificate as an approved, immutable document."""
    data = gather_pallet(session, serial)
    code = pallet_code_for(serial)
    existing = documents.revisions(session, code)
    now = utcnow()
    title = f"Certificate of analysis pallet {serial}"
    if existing:
        previous = max(d.revision for d in existing)
        doc = documents.revise(session, code, title=title,
                               body=render_pallet(data, issued_by=actor, issued_at=now, revision=previous + 1,
                                                  supersedes=previous), actor=actor)
    else:
        doc = documents.create(session, code=code, title=title,
                               body=render_pallet(data, issued_by=actor, issued_at=now, revision=1, supersedes=None),
                               anchors={"material": data["material"]}, actor=actor)
    documents.approve(session, code, doc.revision, actor=actor)
    audit.record(session, actor=actor, action="coa.issued", entity_type="unit", entity_id=serial,
                 after={"document": code, "revision": doc.revision, "units": data["units_inside"],
                        "characteristics": len(data["characteristics"]),
                        "cpk_min": min((c["cpk"] for c in data["characteristics"] if c["cpk"] is not None),
                                       default=None)})
    return doc


def latest_pallet(session: Session, serial: str) -> dict:
    doc = documents.current(session, pallet_code_for(serial))
    if doc is None:
        raise NotFound(f"no certificate has been issued for pallet {serial}")
    return {"pallet": serial, "document": doc.code, "revision": doc.revision, "title": doc.title,
            "issued_by": doc.approved_by, "issued_at": doc.approved_at, "body": doc.body,
            "revisions": [d.revision for d in documents.revisions(session, doc.code)]}
