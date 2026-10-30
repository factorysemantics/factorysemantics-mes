"""Power in one poll, read as figures. Pure, like `rules`.

The game's own units, said on every figure: MW for a draw or an output at a
moment, MWh for energy over time, kWh for the energy one part cost.

What the mod gives, and what is made of it here:

* **a building's draw** - `PowerInfo.PowerConsumed` (MW now) and
  `PowerInfo.MaxPowerConsumed` (MW at full speed, its clock included);
* **a circuit** - `getPower[]`: `PowerCapacity`, `PowerConsumed`,
  `PowerMaxConsumed` (the peak demand: what every building on it would draw
  at once), `PowerProduction`, the battery figures, and `FuseTriggered`. Its
  headroom is `PowerCapacity - PowerMaxConsumed`: below zero, the fuse goes
  the moment every building wants power at once;
* **a generator** - `getGenerators[]`: `RegulatedDemandProd` (MW it is
  making now), `DynamicProdCapacity` (MW it can make with the fuel it has),
  `LoadPercentage`, and the fuel in it (`FuelInventory`, `FuelAmount`). The
  handoff that asked for generators named `ProductionRate`; the feed of
  2026-10-30 has no such field, and `RegulatedDemandProd` is the one that
  moves with the load;
* **the ideal energy of a part** - the recipe's `Products[].FactoryRate`
  (parts a minute at 100 % clock) from the game's recipe reference, and the
  building kind's nominal draw: its `MaxPowerConsumed` as the feed reports it
  for a building of that kind at 100 % clock (`ManuSpeed`). Nominal MW over
  the rate is MW-minutes a part; x 1000 / 60 is kWh. A recipe the reference
  does not hold, or a kind never seen at 100 % with power, has no ideal: the
  figure is *unknown*, never a guess.

`PowerProduction` reads 0 on every circuit of every poll so far (the mod's
figure, not the factory's: the circuit was carrying 48.5 MW at the time), so
nothing here treats it as the circuit's output. It is kept as a reading,
because it is what the feed said.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

from fsmes.services.energy import (  # noqa: F401 - the names, kept below the readers
    CAPACITY,
    CIRCUIT_PREFIX,
    DRAW,
    DRAW_MAX,
    ENERGY,
    FUEL,
    FUEL_ITEMS,
    IDEAL,
    LOAD,
    OUTPUT,
    TRIPPED,
    kwh_per_unit,
    trip_sentence,
)

CIRCUIT_FIELDS = {
    "CapacityMW": "PowerCapacity",
    "ConsumedMW": "PowerConsumed",
    "PeakDemandMW": "PowerMaxConsumed",
    "ProductionMW": "PowerProduction",
    "BatteryPercent": "BatteryPercent",
    "BatteryCapacityMWh": "BatteryCapacity",
}

#: Where the poller keeps the game's recipe list, beside its day files.
RECIPES = ("ref/getRecipes.json", "ref/getRecipes.json.gz")


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def circuit_tag(group: int | str, field: str) -> str:
    return f"{CIRCUIT_PREFIX}CIRCUIT-{group}.{field}"


# ---- buildings -------------------------------------------------------------


def draw(building: dict) -> tuple[float | None, float | None]:
    """(MW now, MW at full speed) for one building or generator. None where
    the feed does not carry the figure."""
    power = building.get("PowerInfo") or {}
    return _num(power.get("PowerConsumed")), _num(power.get("MaxPowerConsumed"))


def mwh(mw: float | None, seconds: float) -> float:
    """Energy over an interval, from the draw read at its end - the same way
    production books the rate read at its end."""
    if mw is None or mw <= 0 or seconds <= 0:
        return 0.0
    return mw * seconds / 3600.0



def nominal_draws(factory: list[dict] | None) -> dict[str, float]:
    """MW at full speed per kind of building, from the buildings of this poll
    running at 100 % clock with power. A kind with none is absent."""
    out: dict[str, float] = {}
    for building in factory or []:
        speed = _num(building.get("ManuSpeed"))
        _, top = draw(building)
        kind = building.get("ClassName")
        if isinstance(kind, str) and speed is not None and abs(speed - 100.0) < 1e-6 and top:
            out[kind] = top
    return out


@dataclass(frozen=True)
class Ideal:
    kwh: float | None
    why: str
    """How the figure was made, or why there is none."""


class Recipes:
    """The game's recipe list, as the poller fetched it once into `ref/`."""

    def __init__(self, entries: list[dict] | None = None) -> None:
        self.by_class: dict[str, dict] = {}
        self.by_name: dict[str, dict] = {}
        for entry in entries or []:
            if isinstance(entry.get("ClassName"), str):
                self.by_class[entry["ClassName"]] = entry
            if isinstance(entry.get("Name"), str):
                self.by_name.setdefault(entry["Name"], entry)

    def __len__(self) -> int:
        return len(self.by_class) or len(self.by_name)

    @classmethod
    def load(cls, folder: Path) -> Recipes:
        """The reference beside the day files, or an empty list when there is
        none: then every ideal is unknown, and says why."""
        for name in RECIPES:
            path = Path(folder) / name
            if path.is_file():
                opener = gzip.open if path.suffix == ".gz" else open
                with opener(path, "rt", encoding="utf-8") as fh:
                    data = json.load(fh)
                return cls(data if isinstance(data, list) else [])
        return cls()

    def find(self, building: dict) -> dict | None:
        by_class = building.get("RecipeClassName")
        if isinstance(by_class, str) and by_class in self.by_class:
            return self.by_class[by_class]
        name = building.get("Recipe")
        return self.by_name.get(name) if isinstance(name, str) else None

    def ideal(self, building: dict, nominal: dict[str, float]) -> Ideal | None:
        """The kWh one part of this building's first product should cost at
        100 % clock. None for a building with no recipe set: there is no part
        to cost."""
        recipe_name = building.get("Recipe")
        if not building.get("IsConfigured") or not recipe_name or recipe_name == "Unassigned":
            return None
        recipe = self.find(building)
        if recipe is None:
            return Ideal(None, f"unknown: the recipe {recipe_name!r} is not in the game's "
                               "recipe reference")
        products = building.get("production") or []
        wanted = products[0].get("ClassName") if products else None
        rate = None
        for product in recipe.get("Products") or []:
            if wanted is None or product.get("ClassName") == wanted:
                rate = _num(product.get("FactoryRate"))
                break
        if not rate:
            return Ideal(None, f"unknown: the recipe {recipe_name!r} gives no factory rate "
                               "for this building's product")
        kind = building.get("ClassName")
        mw = nominal.get(kind) if isinstance(kind, str) else None
        if not mw:
            return Ideal(None, f"unknown: no {building.get('Name') or kind} has been seen "
                               "at 100 % clock with power, so its nominal draw is not known")
        return Ideal(mw * 1000.0 / 60.0 / rate,
                     f"{mw:g} MW nominal over {rate:g} a minute ({recipe_name})")


# ---- circuits --------------------------------------------------------------


@dataclass(frozen=True)
class Circuit:
    group: int
    figures: dict[str, float]
    tripped: bool

    @property
    def headroom(self) -> float | None:
        cap, peak = self.figures.get("CapacityMW"), self.figures.get("PeakDemandMW")
        return None if cap is None or peak is None else cap - peak

    @property
    def why(self) -> str | None:
        """Why the fuse is out, in the figures of the poll that saw it."""
        if not self.tripped:
            return None
        cap, peak = self.figures.get("CapacityMW"), self.figures.get("PeakDemandMW")
        return trip_sentence(peak, cap)


def circuits(power: list[dict] | None) -> list[Circuit]:
    out = []
    for i, group in enumerate(power or []):
        gid = group.get("CircuitGroupID")
        gid = gid if isinstance(gid, int) and not isinstance(gid, bool) else i
        figures = {tag: value for tag, key in CIRCUIT_FIELDS.items()
                   if (value := _num(group.get(key))) is not None}
        out.append(Circuit(gid, figures, bool(group.get("FuseTriggered"))))
    return out


# ---- generators ------------------------------------------------------------


@dataclass(frozen=True)
class Generator:
    output_mw: float | None
    capacity_mw: float | None
    load_percent: float | None
    fuel: str
    """What it burns, as the game names it, or "no fuel"."""
    fuel_items: float | None
    running: bool
    fuelled: bool


def generator(row: dict) -> Generator:
    output = _num(row.get("RegulatedDemandProd"))
    fuel = [slot for slot in row.get("FuelInventory") or [] if _num(slot.get("Amount"))]
    names = sorted({str(slot.get("Name") or slot.get("ClassName")) for slot in fuel})
    items = sum(_num(slot.get("Amount")) or 0.0 for slot in fuel) if fuel else 0.0
    burning = (_num(row.get("FuelAmount")) or 0.0) > 0
    return Generator(
        output_mw=output,
        capacity_mw=_num(row.get("DynamicProdCapacity")),
        load_percent=_num(row.get("LoadPercentage")),
        fuel=", ".join(names) if names else "no fuel",
        fuel_items=items,
        running=bool(output and output > 0) or bool(row.get("IsFullSpeed")),
        fuelled=bool(fuel) or burning,
    )
