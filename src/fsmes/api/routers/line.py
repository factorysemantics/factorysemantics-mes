"""The 3D line view's feed.

Split in two on purpose. `/line/layout` is the plant: fetched once when the page
opens, because machines do not move while you watch. `/line/events` is the
production: polled twice a second, and carries only what has happened since the
caller's cursor.

Nothing here computes production. Every unit returned is a `ProductionLog` row
the MES already booked, so the picture on screen can be trusted to the same
degree as the OEE figures beside it.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from fsmes.api.deps import DbDep
from fsmes.services import line as line_service

router = APIRouter()


@router.get("/layout")
def layout(db: DbDep, line: str | None = None) -> dict:
    """The scene: stations in process order, with shape and position.

    Omit `line` to get the plant's most populated one, which is the one worth
    looking at.
    """
    return line_service.layout(db, line_code=line)


@router.get("/events")
def events(db: DbDep, line: str | None = None, since: int = -1) -> dict:
    """Station status plus units booked since `since` (a ProductionLog id).

    Call once with the default `since=-1` to get a cursor without a backlog,
    then keep passing back the `cursor` from the previous answer.
    """
    return line_service.events(db, line_code=line, since=since)


@router.get("/wip")
def wip(db: DbDep, line: str | None = None) -> dict:
    """Work in progress at each station, summed over the orders on the floor.
    Omit `line` for the plant's most populated one."""
    from fsmes.services import tags

    centre, _units = line_service.resolve_line(db, line)
    return tags.line_wip(db, centre)


@router.get("/models/{name}")
def model(name: str) -> FileResponse:
    """One of the plant's own machine models, from `models/` beside its layout.

    Only a lowercase `<name>.glb` from that folder, and only up to 8 MB;
    anything else is a 404 that says nothing about what else is on disk.
    """
    path = line_service.model_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="no such model")
    return FileResponse(path, media_type="model/gltf-binary",
                        headers={"Cache-Control": "private, max-age=3600"})
