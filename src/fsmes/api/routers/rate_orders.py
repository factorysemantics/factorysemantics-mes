"""The plant's standing rate order: a name and, per item, a target rate in
units a minute. Any signed-in role reads it; changing it is master data.

A plant holds one or none. `PUT` sets it, replacing the one there; `DELETE`
takes it away, and the plant has no target again - which is not a target of
zero. `fsmes.services.rate_orders` says what each figure means.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.services import rate_orders

router = APIRouter()


class RateItem(BaseModel):
    item: str = Field(description="The part's name - on a game plant, the game's (`Iron Plate`).")
    per_minute: float = Field(description="The target rate, units a minute, above zero.")


class RateOrderIn(BaseModel):
    code: str
    name: str
    note: str | None = None
    items: list[RateItem]


@router.get("")
def get_rate_order(db: DbDep) -> dict:
    """The plant's rate order, or `order: null` when it has none."""
    return {"order": rate_orders.as_json(rate_orders.current(db))}


@router.put("", dependencies=[require("masterdata.write")])
def put_rate_order(body: RateOrderIn, db: DbDep, actor: ActorDep) -> dict:
    """Set the plant's rate order, replacing the one there."""
    order = rate_orders.put(db, code=body.code, name=body.name, note=body.note,
                            items=[i.model_dump() for i in body.items], actor=actor)
    return {"order": rate_orders.as_json(order)}


@router.delete("", status_code=204, dependencies=[require("masterdata.write")])
def delete_rate_order(db: DbDep, actor: ActorDep) -> None:
    """Take the rate order away."""
    rate_orders.remove(db, actor=actor)
