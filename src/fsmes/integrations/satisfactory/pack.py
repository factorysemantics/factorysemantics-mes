"""A plant pack's master data, from one day of the game's polls.

    fsmes satisfactory-pack <day file or folder> <masterdata folder> \
        --enterprise CODE[:Name] --site CODE[:Name] --area CODE[:Name]

Writes `equipment.json`, `materials.json`, `routings.json`, `bom.json` and
`downtime_reasons.json` - the files `fsmes pack apply` reads. Every building
the day ever listed is a machine, named for what it was last set to make;
every item it made or used is a material; every recipe is a routing of one
operation on the first building seen making it, and a bill of what goes in.

It is a starting point, not a mirror. The reader makes any building placed
since on first sight, under its kind's work centre, and renames one whose
recipe changes; the pack only spares the plant from coming up empty.
"""

from __future__ import annotations

import json
from pathlib import Path

from fsmes.integrations.satisfactory import feed, rules
from fsmes.integrations.satisfactory.reader import work_centre

#: The three levels above the work centres: (code, name) each. They are the
#: plant's own, so the pack's author names them; the product names none.
Tree = tuple[tuple[str, str], tuple[str, str], tuple[str, str]]


def material_code(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.upper())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:40]


def _last_sight(polls: list[feed.Poll]) -> dict[str, dict]:
    """Every building the polls ever listed, as it was the last time."""
    last: dict[str, dict] = {}
    for poll in polls:
        for building in poll.factory or []:
            if isinstance(building.get("ID"), str):
                last[building["ID"]] = building
    return last


def build(polls: list[feed.Poll], tree: Tree) -> dict[str, list[dict]]:
    buildings = _last_sight(polls)
    (enterprise, site, area) = tree
    equipment: list[dict] = [
        {"code": enterprise[0], "name": enterprise[1], "level": "enterprise"},
        {"code": site[0], "name": site[1], "level": "site", "parent": enterprise[0]},
        {"code": area[0], "name": area[1], "level": "area", "parent": site[0]},
    ]
    centres: dict[str, str] = {}
    units: list[dict] = []
    materials: dict[str, dict] = {}
    routings: dict[str, dict] = {}
    bom: list[dict] = []
    for building_id in sorted(buildings, key=rules.code):
        building = buildings[building_id]
        centre, centre_name = work_centre(building)
        centres.setdefault(centre, centre_name)
        unit = {"code": rules.code(building_id), "name": rules.display_name(building)[:120],
                "level": "work_unit", "parent": centre}
        max_rate = rules.max_per_minute(building)
        if rules.configured(building) and max_rate > 0:
            unit["ideal_cycle_seconds"] = round(60.0 / max_rate, 6)
        units.append(unit)
        if not rules.configured(building):
            continue
        products = building.get("production") or []
        ingredients = building.get("ingredients") or []
        for item in products:
            code = material_code(item["Name"])
            materials[code] = {"code": code, "name": item["Name"], "unit": "ea", "type": "finished"}
        for item in ingredients:
            code = material_code(item["Name"])
            if code not in materials:
                materials[code] = {"code": code, "name": item["Name"], "unit": "ea", "type": "raw"}
        if not products:
            continue
        output = material_code(products[0]["Name"])
        recipe = str(building["Recipe"])
        routing_code = f"RT-{material_code(recipe)}"[:40]
        if routing_code in routings:
            continue
        routings[routing_code] = {
            "code": routing_code, "name": recipe, "material": output,
            "operations": [{"seq": 10, "name": recipe, "equipment": unit["code"]}],
        }
        made = rules.max_per_minute(building)
        for item in ingredients:
            used = float(item.get("MaxConsumed") or 0)
            if made > 0 and used > 0:
                bom.append({"parent": output, "component": material_code(item["Name"]),
                            "quantity": round(used / made, 6), "operation_seq": 10})
    # Iron ingot is made by one building and used by the next: a thing both
    # made and used here is intermediate, made and never used is finished,
    # used and never made is raw.
    used = {row["component"] for row in bom}
    for code, row in materials.items():
        if row["type"] == "finished" and code in used:
            row["type"] = "intermediate"
    equipment += [{"code": c, "name": n, "level": "work_center", "parent": area[0]}
                  for c, n in sorted(centres.items())]
    equipment += units
    reasons = [{"code": code, "name": name, "description": description}
               for code, (name, description) in rules.REASONS.items()]
    return {
        "equipment": equipment,
        "materials": sorted(materials.values(), key=lambda m: m["code"]),
        "routings": [routings[k] for k in sorted(routings)],
        "bom": bom,
        "downtime_reasons": reasons,
    }


def write(source: Path, out: Path, tree: Tree) -> dict[str, int]:
    """Write the pack's master data from a day's file (or a folder of them)
    into `out`. Returns how many rows each file got."""
    source = Path(source).expanduser()
    polls = feed.read_all(source) if source.is_dir() else feed.read_file(source)
    data = build(polls, tree)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in data.items():
        (out / f"{name}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    return {name: len(rows) for name, rows in data.items()}
