"""System endpoints: health, shadow mode, metrics, audit trail queries."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from fsmes import identity
from fsmes import shadow as shadow_mode
from fsmes.api.deps import DbDep, ReadDbDep, require
from fsmes.domain import AuditLog, ErpMessage, MessageStatus, OrderStatus, TagValue, WorkOrder
from fsmes.services import connection as connection_service

router = APIRouter()


@router.get("/health")
def health(db: ReadDbDep) -> dict:
    """Alive, which plant this is, and whether it may act on that plant.

    `shadow` rides on health because health is the one endpoint everything
    already asks: an orchestrator, the MCP server's plant list, a person
    with curl. A monitor that knows a plant is up and does not know it is
    only watching will read its silence as everything being fine.

    The identity rides on it for the same reason, and for one more: a
    console polling twelve plants has to be able to tell them apart from
    what they say about themselves, not from the address it happened to
    dial. `timezone_defaulted` is there because a defaulted zone is a
    guess about the plant and a reader is entitled to know it was one.
    """
    db.execute(text("SELECT 1"))
    return {"status": "ok", "shadow": shadow_mode.enabled(), **identity.summary(),
            # How much of this plant the MES can currently see. A statement
            # about this deployment's own reach, not a number the plant
            # produced, which is what keeps it on the public side of the line
            # decision 0023 draws. A monitor that knows a plant is up and does
            # not know it has been blind to nine machines since Tuesday will
            # read its silence as everything being fine - the same argument
            # `shadow` rides on health for (decision 0030).
            "watching": connection_service.watching(db),
            # Not part of `summary()`: the id is between this plant and the
            # installation that created it, and has no business in the
            # namespace envelope or the backup manifest. None here means no
            # fleet tool created this plant, which means no fleet tool owns
            # it - decision 0023, condition 2.
            "instance_id": identity.instance_id()}


@router.get("/shadow")
def shadow() -> dict:
    """Shadow mode in full: whether it is on, what it guarantees, and every
    outbound path in this build with what shadow mode does to each.

    Public, like health. It is a statement about how this deployment is
    configured, and the person who most needs it - somebody deciding whether
    it is safe to point this MES at a running plant - has no account yet.
    """
    return {
        **identity.summary(),
        **shadow_mode.summary(),
        "paths": [
            {"name": p.name, "reaches": p.reaches, "verdict": p.verdict, "note": p.note}
            for p in shadow_mode.REGISTER
        ],
    }


@router.get("/pack")
def pack() -> dict:
    """Which pack this plant runs, whether it has drifted, and its schema.

    The console's other half. `/health` says which plant this is; this says
    what it was *given* - the pack, when it was applied and by which product
    version, whether the files have changed since, the schema revision
    against head, and which modules this plant serves. Four separate facts
    and a list, never merged into one light, because a person needs them
    apart.

    Public, like `/health` and `/shadow`, and for the same reason. A fleet
    console is a long-running process on a port: whatever credential it
    holds, whoever reaches that port holds too. Everything here is a
    statement about how this deployment is configured - no order, no serial,
    no person, no number a plant produced - so the console can hold no
    credential at all, which is a stronger property than holding a read-only
    one. The read-only machine role of the M8 design is still the right
    thing to add the day a console shows OEE or service liveness; this one
    shows neither.

    Anything this plant cannot know is in `unknown` with the reason, rather
    than defaulted: `drifted` is `null` when no pack has been applied or
    when the pack is not on this machine any more, and null is not "no
    drift".
    """
    from fsmes.pack import apply as applied

    return applied.what_this_plant_runs()


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: ReadDbDep) -> str:
    # Every series carries the plant, because a fleet scrapes several into
    # one Prometheus and a series without it silently becomes the sum of
    # every plant that has the same metric name.
    plant = identity.plant_name()
    lines = [f'mes_plant_info{{plant="{plant}"}} 1']
    for status in OrderStatus:
        count = db.scalar(select(func.count(WorkOrder.id)).where(WorkOrder.status == status)) or 0
        lines.append(f'mes_work_orders{{plant="{plant}",status="{status.value}"}} {count}')
    pending = db.scalar(select(func.count(ErpMessage.id)).where(ErpMessage.status == MessageStatus.PENDING)) or 0
    lines.append(f'mes_erp_messages_pending{{plant="{plant}"}} {pending}')
    # The highest id, not a count: counting two million rows on every scrape
    # was the most expensive thing this API did. Ids are monotonic; the number
    # says how many were ever written, which is what a rate wants.
    lines.append(f'mes_tag_values_max_id{{plant="{plant}"}} {db.scalar(select(func.max(TagValue.id))) or 0}')
    lines.append(f'mes_audit_entries_max_id{{plant="{plant}"}} {db.scalar(select(func.max(AuditLog.id))) or 0}')
    lines += _coverage_series(db, plant)
    return "\n".join(lines) + "\n"


#: The window every machine series here is measured over. One shift's worth,
#: named rather than left for a dashboard to guess at.
_METRICS_WINDOW_HOURS = 8.0


def _coverage_series(db: Session, plant: str) -> list[str]:
    """Availability and the coverage it was measured over, as a pair.

    **They are exported together on purpose.** An availability series on its
    own is a number a Grafana panel will render as a gauge over a shift, and
    nothing on that panel will say it was computed over eleven observed
    minutes. Exporting the pair means a panel cannot show one without being
    able to show the other, which is the same rule the screens follow.

    Availability is *absent* rather than zero when this MES cannot say — too
    little observed time, or a window below this plant's pack floor. Absent is
    what Prometheus already means by unknown; zero would put a working machine
    at the top of a worst-performer board.

    Built from `coverage.totals_many`, which is four grouped queries for the
    whole plant. A scrape is not a screen refresh and must not cost like one.
    """
    from datetime import timedelta

    from fsmes.db import utcnow
    from fsmes.services import coverage
    from fsmes.services import equipment as equipment_service

    machines = equipment_service.work_units(db)
    if not machines:
        return []
    end = utcnow()
    accounts = coverage.totals_many(db, [m.id for m in machines],
                                    end - timedelta(hours=_METRICS_WINDOW_HOURS), end)
    the_floor = coverage.floor()

    out = [
        f"# HELP mes_equipment_coverage Share of the last {_METRICS_WINDOW_HOURS:g}h this MES "
        "actually watched this machine.",
        "# TYPE mes_equipment_coverage gauge",
        "# HELP mes_equipment_availability Run time over observed time. Absent when this MES "
        "cannot say, never zero.",
        "# TYPE mes_equipment_availability gauge",
        "# HELP mes_equipment_not_observed_seconds Seconds of the window nobody watched, by cause.",
        "# TYPE mes_equipment_not_observed_seconds gauge",
    ]
    if the_floor is not None:
        out += ["# HELP mes_coverage_floor The coverage this plant's pack asks for before it "
                "reports a KPI.",
                "# TYPE mes_coverage_floor gauge",
                f'mes_coverage_floor{{plant="{plant}"}} {the_floor:g}']
    for machine in machines:
        account = accounts[machine.id]
        labels = f'plant="{plant}",equipment="{machine.code}"'
        cover = account.coverage
        if cover is not None:
            out.append(f"mes_equipment_coverage{{{labels}}} {cover:.6f}")
        availability = account.availability
        if availability is not None and coverage.withhold(cover, the_floor) is None:
            out.append(f"mes_equipment_availability{{{labels}}} {availability:.6f}")
        for cause, seconds in account.not_observed_by_cause.items():
            out.append(f'mes_equipment_not_observed_seconds{{{labels},cause="{cause}"}} '
                       f"{seconds:.1f}")
    return out


@router.get("/ai", dependencies=[require("audit.read")])
def local_ai() -> dict:
    """The local AI layer: model server, GPU, and every assigned job the
    model has - when it last did anything and where its output went.

    Machine-wide, not per-plant: one Ollama and one GPU serve every plant on
    the box, so both plants' Ops screens show the same panel. Reads only.
    """
    from fsmes.services import ai_status

    return ai_status.status()


@router.get("/audit", dependencies=[require("audit.read")])
def audit_trail(
    db: DbDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    limit: int = 100,
) -> list[dict]:
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if actor:
        query = query.where(AuditLog.actor == actor)
    return [
        {
            "ts": entry.ts,
            "actor": entry.actor,
            "on_behalf_of": entry.on_behalf_of,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "before": entry.before,
            "after": entry.after,
        }
        for entry in db.scalars(query)
    ]
