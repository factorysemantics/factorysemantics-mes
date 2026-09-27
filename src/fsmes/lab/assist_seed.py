"""The demo plant's master data, put on a plant a live run is about to score -
as the person signed in, never as the agent.

`assist_fixtures` arranges what an agent deployment may arrange, and stops at
master data: materials, equipment, routings, lots and specifications are the
plant's own, the AGENT account holds no `masterdata.write`, and decision 0035
says an agent does not define them. That line is right and nothing here moves
it. But it leaves a real hole. Pointed at a plant that was not built from the
demo pack, most of the suite comes back *not arranged* - honest, and not a
useful run - and a fleet rebuilt on a fresh build wipes whatever somebody put
there by hand last time.

So this is the other half, and the difference is **who does it**. The account a
live run signs in as is a person, with a person's capabilities; on 2026-09-27 at
06:49 a person put exactly this master data on the bottling plant with ten
`POST`s and got ten 201s. This module is those ten `POST`s, in that order, made
by that account, through the plant's own API - no `mcp_server`, no AGENT, no
reach into a database. `fsmes assist eval --live --seed-masterdata` is how a
person asks for it, and it is off unless they do.

What is written here is **master data on somebody's plant**, and there is no
delete endpoint for most of it. `docs/ai/ASSIST-EVAL.md` says so plainly and
says what a person does about each row.

## Where the codes and the numbers come from

Not from here. `fsmes.seed.seed_demo_plant` is where the demo plant is written
down - `MIX01`'s four-second ideal cycle, brix 9.5-11.5 °Bx, 500 kg of sugar -
and a second copy of those numbers in this file would be a copy that drifts.
`demo_master_data()` builds the demo plant in a throwaway in-memory database and
reads the rows back out of it, so what lands on a real plant is what the demo
pack says, by construction.

The one thing written down here is the **list**: which of the demo plant's
master data a run may put on somebody else's plant. That is a judgment, not a
fact, so it is explicit, and `tests/test_assist_suite_scripted.py` holds it to
the suite - every piece of master data a case names is on it, nothing a case
needs the plant *not* to have is on it, and anything on it that no case names
has a written reason in `BEYOND_THE_SUITE`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

#: Defining master data. Nine of the ten rows want this one, so an account
#: without it has nothing to do here and is told so rather than collecting ten
#: refusals.
MASTERDATA = "masterdata.write"

#: A lot is stock, not a definition, so this product asks for the capability
#: that books stock. An admin holds both; an engineer who holds only the first
#: gets the eight definitions and is told which capability the two lots wanted.
STOCK = "production.consume"


class Refused(Exception):
    """The plant, or the account signed in to it, would not do this."""


class Api(Protocol):
    """A plant, over its own HTTP API, as somebody who has signed in.

    Two methods, because that is all this needs: the tool layer is not in the
    picture here and neither is a database session. `lab.assist_eval.Plant`
    implements it.
    """

    def read(self, path: str, **params: Any) -> Any: ...

    def write(self, path: str, body: dict) -> Any: ...


# ------------------------------------------------- what a run may put there

#: The master data a run may put on a plant, in the order it has to go on: the
#: machines before the routing that names them, the materials before the
#: routing, the specification and the lots that name those. Requirement
#: strings, the same spelling `requires` uses in `tests/assist_suite/`.
SEEDS = (
    "machine:LINE1",
    "machine:MIX01",
    "machine:PACK01",
    "material:RAW-SUGAR",
    "material:RAW-FLAVOR",
    "material:FG-COLA",
    "routing:RT-COLA",
    "spec:FG-COLA/brix",
    "lot:LOT-SUGAR-001",
    "lot:LOT-FLAVOR-001",
)

#: What is on that list that no case names, and why it is there anyway. Nothing
#: is seeded silently: a test fails if `SEEDS` grows an entry that is neither
#: named by a case nor written down here.
BEYOND_THE_SUITE = {
    "routing:RT-COLA": (
        "a work order cannot be created against a material that has no routing - "
        "`workorders.create` refuses with 'cannot build order operations' - so "
        "`order:WO-EVAL-1`, which the suite does name and the AGENT account does "
        "arrange, needs this routing to exist first"),
    "material:RAW-FLAVOR": (
        "the demo pack's second raw material. RT-COLA makes cola out of sugar and "
        "flavour concentrate, and a plant carrying one of the two is a half-built "
        "demo that reads as a mistake to whoever finds it"),
    "lot:LOT-FLAVOR-001": (
        "the flavour lot that goes with it, for the same reason: the demo pack "
        "ships a lot per raw material and a run that seeded one of them would be "
        "leaving a plant nobody wrote down"),
}


def split(requirement: str) -> tuple[str, str]:
    """`"material:FG-COLA"` -> `("material", "FG-COLA")`. The same spelling
    `assist_fixtures.split` reads, and deliberately its own copy: this module
    must not import the AGENT arrangement to do a person's work."""
    kind, _, code = requirement.partition(":")
    return kind.strip(), code.strip()


# --------------------------------------------------- reading the demo pack

def demo_master_data() -> dict[str, dict]:
    """The body to `POST` for each of `SEEDS`, read out of the demo pack.

    Built by seeding a throwaway in-memory database with `seed_demo_plant` and
    reading the rows back. It listens on no port, writes no file and is gone
    when this function returns - the same discipline `scripted_plant` keeps, for
    the same reason (standing order four).

    One rule about the shapes: **a parent this run did not make is dropped.**
    The demo pack hangs `LINE1` under an area under a site under an enterprise,
    and a run seeding somebody else's plant has no business inventing four
    levels of their hierarchy. `LINE1` lands as a work centre with no parent;
    `MIX01` and `PACK01` land under `LINE1`, because `LINE1` is on the list.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    import fsmes.domain  # noqa: F401  (register every table)
    from fsmes.db import Base
    from fsmes.domain import Equipment, Material, MaterialLot, QualitySpec, Routing
    from fsmes.seed import seed_demo_plant

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    ours = {split(requirement)[1] for requirement in SEEDS}
    try:
        with Session(engine) as session:
            seed_demo_plant(session)
            session.flush()
            equipment = {eq.code: eq for eq in session.scalars(select(Equipment))}
            materials = {m.code: m for m in session.scalars(select(Material))}
            routings = {r.code: r for r in session.scalars(select(Routing))}
            lots = {lot.code: lot for lot in session.scalars(select(MaterialLot))}
            specs = {f"{s.material.code}/{s.characteristic}": s
                     for s in session.scalars(select(QualitySpec))}

            bodies: dict[str, dict] = {}
            for requirement in SEEDS:
                kind, code = split(requirement)
                if kind == "machine":
                    eq = equipment[code]
                    parent = eq.parent.code if eq.parent else None
                    bodies[requirement] = {
                        "code": eq.code, "name": eq.name, "level": eq.level.value,
                        "parent": parent if parent in ours else None,
                        "ideal_cycle_seconds": eq.ideal_cycle_seconds}
                elif kind == "material":
                    m = materials[code]
                    bodies[requirement] = {
                        "code": m.code, "name": m.name, "unit": m.unit,
                        "type": m.type.value,
                        "counted_in_pieces": m.counted_in_pieces}
                elif kind == "routing":
                    r = routings[code]
                    bodies[requirement] = {
                        "code": r.code, "name": r.name, "material": r.material.code,
                        "operations": [{"seq": op.seq, "name": op.name,
                                        "equipment": op.equipment.code}
                                       for op in r.operations]}
                elif kind == "spec":
                    s = specs[code]
                    bodies[requirement] = {
                        "material": s.material.code, "characteristic": s.characteristic,
                        "unit": s.unit, "min_value": s.min_value,
                        "max_value": s.max_value}
                elif kind == "lot":
                    lot = lots[code]
                    bodies[requirement] = {
                        "code": lot.code, "material": lot.material.code,
                        # What it was made with, not what is left of it: the demo
                        # pack's own starting quantity is the honest thing to put
                        # on a plant that has never issued any of it.
                        "quantity": lot.original_quantity}
                else:                                   # pragma: no cover - SEEDS is pinned
                    raise Refused(f"no recipe for a {kind!r}")
            return bodies
    finally:
        engine.dispose()


# ------------------------------------------------------------- the recipes

def _list(payload: Any) -> list[dict]:
    """The rows out of a list endpoint, bare list or paged envelope. Both shapes
    are live in this product and a reader that assumed one would report an
    absence on the other."""
    rows = payload.get("items") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


def _machine_there(api: Api, code: str) -> bool:
    return any(row.get("code") == code
               for row in _list(api.read("/masterdata/equipment", q=code)))


def _material_there(api: Api, code: str) -> bool:
    return any(row.get("code") == code
               for row in _list(api.read("/masterdata/materials", q=code)))


def _routing_there(api: Api, code: str) -> bool:
    return any(row.get("code") == code
               for row in _list(api.read("/masterdata/routings", q=code)))


def _spec_there(api: Api, code: str) -> bool:
    """`spec:FG-COLA/brix` - a characteristic on a material, which is how this
    product names a specification everywhere else."""
    material, _, characteristic = code.partition("/")
    return any(row.get("material") == material
               and row.get("characteristic") == characteristic
               for row in _list(api.read("/quality/specs", material=material,
                                         characteristic=characteristic)))


def _lot_there(api: Api, code: str) -> bool:
    return any(row.get("code") == code
               for row in _list(api.read("/execution/lots", q=code)))


@dataclass(frozen=True)
class Recipe:
    """One sort of master data: how to see whether a plant has it, where to put
    it, and which capability the plant asks for before it will take one."""

    kind: str
    there: Callable[[Api, str], bool]
    where: str
    capability: str


RECIPES: dict[str, Recipe] = {r.kind: r for r in (
    Recipe("machine", _machine_there, "/masterdata/equipment", MASTERDATA),
    Recipe("material", _material_there, "/masterdata/materials", MASTERDATA),
    Recipe("routing", _routing_there, "/masterdata/routings", MASTERDATA),
    Recipe("spec", _spec_there, "/quality/specs", MASTERDATA),
    Recipe("lot", _lot_there, "/execution/lots", STOCK),
)}


# ------------------------------------------------------------- the seeding

def seed(api: Api) -> dict:
    """Put the demo plant's master data on this plant, as whoever `api` is
    signed in as. Says what it created and what was already there.

    Idempotent by construction and by rule: every code is looked for first, and
    one that is there is reported **already there and left alone**. Nothing here
    updates anything. A plant whose `FG-COLA` is called something else, or is
    measured in litres, keeps its own - a run that corrected somebody's master
    data to match a test suite would be the worst thing in this repository.

    Returns `{"made": [...], "already": [...], "refused": {requirement: why}}`.
    A refusal is the plant's own sentence, kept whole, and a refused row is
    reported rather than raised: a run says what it could not do and carries on.
    """
    held = _capabilities(api)
    if MASTERDATA not in held:
        raise Refused(
            f"this plant will not let the account signed in define master data: it "
            f"asks for {MASTERDATA!r} and this account holds "
            f"{', '.join(sorted(held)) or 'nothing'}. Master data is the plant's "
            f"own - sign in as somebody who may define it, or leave "
            f"--seed-masterdata off and the cases that name it are reported not "
            f"arranged.")

    bodies = demo_master_data()
    made: list[str] = []
    already: list[str] = []
    refused: dict[str, str] = {}
    for requirement in SEEDS:
        kind, code = split(requirement)
        recipe = RECIPES[kind]
        if recipe.capability not in held:
            refused[requirement] = (
                f"this plant asks for {recipe.capability!r} before it will take one, "
                f"and the account signed in does not hold it")
            continue
        try:
            there = recipe.there(api, code)
        except Refused as exc:
            refused[requirement] = f"the plant would not say whether it has this: {exc}"
            continue
        if there:
            already.append(requirement)
            continue
        try:
            api.write(recipe.where, bodies[requirement])
        except Refused as exc:
            refused[requirement] = str(exc)
            continue
        except Exception as exc:      # reported, never raised: a run says what it could not do
            refused[requirement] = f"{type(exc).__name__}: {exc}"
            continue
        made.append(requirement)
    return {"made": made, "already": already, "refused": refused}


def _capabilities(api: Api) -> frozenset[str]:
    """What the account signed in may do, in the plant's own words.

    A plant that will not answer this is not one to start writing master data
    to, so this one is allowed to raise.
    """
    try:
        answer = api.read("/auth/me")
    except Exception as exc:
        raise Refused(f"this plant would not say what the account signed in may do "
                      f"(GET /auth/me), so nothing was written to it: {exc}") from exc
    return frozenset(answer.get("capabilities") or ())
