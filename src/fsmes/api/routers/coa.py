"""Certificates of analysis: issued at the end of the line, readable forever."""

from __future__ import annotations

from fastapi import APIRouter, Query

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import coa

router = APIRouter()


@router.get("")
def certificates(
    db: DbDep,
    material: str | None = None,
    q: str | None = Query(None, description="Match an order code."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """Certificates in force, newest first, one page at a time - one is
    issued per completed order, so this is the plant's order history."""
    items, total = coa.listing(db, limit, material=material, q=q, offset=offset)
    return paging.page(items, total, limit, offset)


@router.get("/{order}")
def certificate(order: str, db: DbDep) -> dict:
    """The certificate for an order: the revision in force, as Markdown, with
    the revisions that preceded it."""
    return coa.latest(db, order)


@router.get("/{order}/data")
def certificate_data(order: str, db: DbDep) -> dict:
    """What the certificate states, as data rather than prose."""
    return coa.gather(db, order)


@router.post("/{order}", status_code=201, dependencies=[require("quality.close_nc")])
def issue(order: str, db: DbDep, actor: ActorDep) -> dict:
    """Issue - or reissue, superseding - the certificate. A supervisor's act;
    the end of the line does it by itself when an order completes."""
    doc = coa.issue(db, order, actor=actor)
    return coa.latest(db, order) | {"issued_revision": doc.revision}


# ---------------------------------------------------------------- per pallet
@router.get("/pallet/{serial}")
def pallet_certificate(serial: str, db: DbDep) -> dict:
    """A pallet's certificate: the revision in force, as Markdown."""
    return coa.latest_pallet(db, serial)


@router.get("/pallet/{serial}/data")
def pallet_certificate_data(serial: str, db: DbDep) -> dict:
    """What a pallet's certificate states, as data: the capability of every
    dimensional characteristic over the window its contents were made, the
    automated inspections, and the contents."""
    return coa.gather_pallet(db, serial)


@router.post("/pallet/{serial}", status_code=201, dependencies=[require("quality.close_nc")])
def issue_pallet_certificate(serial: str, db: DbDep, actor: ActorDep) -> dict:
    """Issue, or reissue, the certificate for a pallet."""
    doc = coa.issue_pallet(db, serial, actor=actor)
    return {"pallet": serial, "document": doc.code, "issued_revision": doc.revision}
