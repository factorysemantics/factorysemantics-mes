"""What is actually running, and what it has been saying.

Everything here was already inspectable - by shelling into the machine and
reading journalctl. That is fine for the person who built the plant and no use
to the person running it at 3am. Surfacing it costs nothing and is the
difference between a system you can operate and one you have to be initiated
into.

Reads only. Nothing here restarts a service: taking the plant down is a
decision for someone at a terminal who meant it.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from fastapi import APIRouter

from fsmes.api.deps import DbDep, require
from fsmes.config import get_settings

router = APIRouter()

# The three processes a plant runs, and what each is for.
COMPONENTS = [
    ("api", "The dashboard and the API everything else talks to."),
    ("opc-agent", "Subscribes to the machines and books what they report."),
    ("opc-replay", "Serves the simulated line as OPC UA tags."),
    ("opc-sim", "Serves the built-in toy machines as OPC UA tags."),
    ("operations", "The people part: inspections, material issue, order release."),
]


def _log_path(name: str) -> Path:
    return Path(get_settings().log_dir) / f"{name}.jsonl"


def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@router.get("/services", dependencies=[require("audit.read")])
def services() -> dict:
    """Each component of this plant, whether it is live, and how recently it
    said anything.

    A log that has stopped growing is the loudest signal a component is stuck,
    and it is the one nobody notices without looking.
    """
    settings = get_settings()
    endpoint = settings.opc_endpoint
    opc_host, opc_port = "127.0.0.1", None
    try:
        rest = endpoint.split("//", 1)[1].split("/", 1)[0]
        opc_host, port_text = rest.rsplit(":", 1)
        opc_port = int(port_text)
    except (IndexError, ValueError):
        pass

    out = []
    for name, purpose in COMPONENTS:
        path = _log_path(name)
        if not path.exists():
            continue
        stat = path.stat()
        entry = {
            "component": name,
            "purpose": purpose,
            "log_bytes": stat.st_size,
            "last_wrote": stat.st_mtime,
        }
        if name == "api":
            entry["listening"] = _port_open(settings.api_host, settings.api_port)
            entry["where"] = f"{settings.api_host}:{settings.api_port}"
        if name in ("opc-replay", "opc-sim") and opc_port:
            entry["listening"] = _port_open(opc_host, opc_port)
            entry["where"] = endpoint
        out.append(entry)

    return {
        "plant": {
            "database": settings.database_url,
            "api": f"{settings.api_host}:{settings.api_port}",
            "opc_endpoint": endpoint,
            "tag_map": str(settings.tag_map_file),
            "replay_dir": str(settings.replay_dir),
            "sim_speed": settings.sim_speed,
            "opc_publish_ms": settings.opc_publish_ms,
        },
        "components": out,
    }


@router.get("/logs/{component}", dependencies=[require("audit.read")])
def logs(component: str, lines: int = 120, level: str | None = None) -> dict:
    """The tail of one component's log.

    Structured JSONL, so it is parsed here rather than thrown at the screen as
    text - a log you can filter is a log somebody will actually read.
    """
    known = {name for name, _ in COMPONENTS}
    if component not in known:
        return {"error": f"unknown component {component!r}. Known: {', '.join(sorted(known))}"}

    path = _log_path(component)
    if not path.exists():
        return {"component": component, "entries": [],
                "note": "this component has not written a log on this plant"}

    # Read the tail without loading a multi-megabyte file into memory.
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            window = min(size, max(4096, lines * 400))
            fh.seek(size - window)
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"component": component, "error": str(exc), "entries": []}

    entries = []
    for line in raw.splitlines()[1:]:          # first line is likely partial
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if level and str(record.get("level", "")).lower() != level.lower():
            continue
        entries.append({
            "ts": record.get("timestamp") or record.get("ts"),
            "level": record.get("level"),
            "event": record.get("event"),
            "logger": record.get("logger"),
            # Everything else the component chose to record, which is where
            # the useful detail lives.
            "detail": {k: v for k, v in record.items()
                       if k not in ("timestamp", "ts", "level", "event", "logger")},
        })

    return {"component": component, "entries": entries[-lines:],
            "log_bytes": path.stat().st_size}


@router.get("/activity", dependencies=[require("audit.read")])
def activity(db: DbDep, limit: int = 60, actor: str | None = None,
             action: str | None = None) -> dict:
    """The audit trail, filtered.

    Humans and agents are in the same spine, so actor="AGENT" answers "what
    has the agent done to my plant" and nothing else has to be built for it.
    """
    from sqlalchemy import select

    from fsmes.domain import AuditLog

    query = select(AuditLog).order_by(AuditLog.id.desc())
    if actor:
        query = query.where(AuditLog.actor == actor)
    if action:
        query = query.where(AuditLog.action.like(f"{action}%"))

    rows = db.scalars(query.limit(min(limit, 500))).all()
    actors = sorted({r.actor for r in db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(500))})
    return {
        "entries": [
            {"ts": r.ts, "actor": r.actor, "on_behalf_of": r.on_behalf_of, "action": r.action,
             "entity_type": r.entity_type, "entity_id": r.entity_id,
             "before": r.before, "after": r.after}
            for r in rows
        ],
        "actors": actors,
    }


@router.get("/retention")
def retention_status(db: DbDep) -> dict:
    """Tag history: how much is held, the oldest sample, and the policy."""
    from fsmes.config import get_settings
    from fsmes.services import retention

    return retention.report(db, get_settings().tag_retention_days)
