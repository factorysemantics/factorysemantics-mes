"""One building in one poll, read as a machine state.

Pure: a building's JSON and its circuits' fuses in, a `Reading` out. The
reader decides what to write; this decides what the game said.

The order is the order of causes, the first that holds wins:

1. **down / fuse** - the building's circuit has its fuse tripped. The mod
   says so on the building (`PowerInfo.FuseTriggered`) and on the circuit
   group (`getPower[].FuseTriggered` over `AssociatedCircuits`); either is
   enough, and every building on a tripped circuit is down for it.
2. **down / no_power** - a building with a recipe on no circuit at all
   (`CircuitID` -1): it cannot run, and it is not the operator's choice.
3. **idle / paused** - the player switched the building off (`IsPaused`).
4. **idle / no_recipe** - a building with nothing set to make. It was put
   there; it was never told what to do.
5. **running** - the game says it is producing.
6. **idle / blocked** - the output slot cannot take another cycle's worth.
7. **idle / starved** - an input slot holds less than one cycle needs.
8. **idle** - not producing, and the feed does not say why.

Starved and blocked are idle with a reason, never down: the MES's states are
running, idle, down and setup, and a machine waiting on its neighbours is
waiting, not broken (the bottling lab reads them the same way).
"""

from __future__ import annotations

from dataclasses import dataclass

from fsmes.domain.equipment import EquipmentStateName

#: The reason codes this reader writes, with the sentence that goes with
#: each. The pack's downtime reasons are generated from the same table.
REASONS: dict[str, tuple[str, str]] = {
    "fuse": ("Fuse tripped",
             "the building's power circuit tripped its fuse: more power asked for than made"),
    "no_power": ("Not on a power circuit",
                 "the building has a recipe but no power line, so it cannot run"),
    "paused": ("Switched off in the game",
               "the player paused this building"),
    "no_recipe": ("No recipe set",
                  "the building was placed but not told what to make"),
    "starved": ("Starved",
                "an input holds less than one cycle needs"),
    "blocked": ("Blocked",
                "the output is too full to take another cycle"),
}

#: Which reasons are downtime (and so in the downtime pareto). The rest are
#: idle with a reason.
DOWN_REASONS = frozenset({"fuse", "no_power"})


@dataclass(frozen=True)
class Reading:
    state: EquipmentStateName
    reason_code: str | None = None

    @property
    def reason(self) -> str | None:
        return REASONS[self.reason_code][1] if self.reason_code else None


def tripped_circuits(power: list[dict] | None) -> set[int]:
    """The circuit IDs whose group has its fuse tripped, from `getPower`.
    Empty when the power answer is missing - then the building's own flag
    is the only evidence, and it is still enough."""
    tripped: set[int] = set()
    for group in power or []:
        if group.get("FuseTriggered"):
            for circuit in group.get("AssociatedCircuits") or []:
                if isinstance(circuit, int):
                    tripped.add(circuit)
    return tripped


def circuit_of(building: dict) -> int | None:
    circuit = (building.get("PowerInfo") or {}).get("CircuitID")
    return circuit if isinstance(circuit, int) else None


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _blocked(building: dict) -> bool:
    """An output slot that cannot take one more cycle's worth."""
    per_cycle = {p.get("ClassName"): _num(p.get("Amount")) for p in building.get("production") or []}
    for slot in building.get("OutputInventory") or []:
        cap = _num(slot.get("MaxAmount"))
        need = per_cycle.get(slot.get("ClassName"), 1.0) or 1.0
        if cap and _num(slot.get("Amount")) + need > cap:
            return True
    return False


def _starved(building: dict) -> bool:
    """An input slot holding less than one cycle needs, or an ingredient
    with no slot at all. A recipe with no ingredients (a miner's) is never
    starved."""
    held = {s.get("ClassName"): _num(s.get("Amount")) for s in building.get("InputInventory") or []}
    for ingredient in building.get("ingredients") or []:
        if held.get(ingredient.get("ClassName"), 0.0) < (_num(ingredient.get("Amount")) or 1.0):
            return True
    return False


def configured(building: dict) -> bool:
    recipe = building.get("Recipe")
    return bool(building.get("IsConfigured")) and bool(recipe) and recipe != "Unassigned"


def read(building: dict, tripped: set[int] | None = None) -> Reading:
    """What one building was doing at one poll."""
    power = building.get("PowerInfo") or {}
    circuit = circuit_of(building)
    if power.get("FuseTriggered") or (circuit is not None and circuit in (tripped or set())):
        return Reading(EquipmentStateName.DOWN, "fuse")
    has_recipe = configured(building)
    if has_recipe and circuit == -1:
        return Reading(EquipmentStateName.DOWN, "no_power")
    if building.get("IsPaused"):
        return Reading(EquipmentStateName.IDLE, "paused")
    if not has_recipe:
        return Reading(EquipmentStateName.IDLE, "no_recipe")
    if building.get("IsProducing"):
        return Reading(EquipmentStateName.RUNNING)
    if _blocked(building):
        return Reading(EquipmentStateName.IDLE, "blocked")
    if _starved(building):
        return Reading(EquipmentStateName.IDLE, "starved")
    return Reading(EquipmentStateName.IDLE)


def rate_per_minute(building: dict) -> float:
    """Units of the building's first product a minute, as the game measured
    it at this poll (`production[0].CurrentProd`). A building that makes two
    products is counted in its first, the one its recipe is named for."""
    products = building.get("production") or []
    return max(0.0, _num(products[0].get("CurrentProd"))) if products else 0.0


def max_per_minute(building: dict) -> float:
    products = building.get("production") or []
    return max(0.0, _num(products[0].get("MaxProd"))) if products else 0.0


def code(building_id: str) -> str:
    """The MES equipment code for a building: its game ID without the
    `Build_` and `_C` around the class, upper case. Stable for the life of
    the building in the save; a building torn down and rebuilt is a new one,
    as it is in the game.

        Build_ConstructorMk1_C_2146677430 -> CONSTRUCTORMK1-2146677430
    """
    text = building_id
    if text.startswith("Build_"):
        text = text[len("Build_"):]
    text = text.replace("_C_", "-", 1)
    return text.upper()[:40]


def display_name(building: dict) -> str:
    """"Constructor · Iron Rod", or the building's name alone when it has
    no recipe yet."""
    name = str(building.get("Name") or building.get("ClassName") or building.get("ID"))
    return f"{name} · {building['Recipe']}" if configured(building) else name
