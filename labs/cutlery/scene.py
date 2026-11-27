"""Build a named scene of the cutlery plant: a copy of the pack, with one story in it.

    python labs/cutlery/scene.py labs/cutlery/scenes/hot-mould.json --out ~/scenes/hot-mould
    FSMES_PLANT_REGISTRY=~/scenes/hot-mould/fleet.toml fsmes plant cutlery init
    FSMES_PLANT_REGISTRY=~/scenes/hot-mould/fleet.toml fsmes plant cutlery start --speed 10

A scene is data (`scenes/<name>.json`): events added to named lines of
`line.json` and settings over theirs (a line's `events` there replaces its own,
for a scene that needs one of the everyday events out of its way), sections
added to the floor script (`pieces`, `breakdowns`), rows added to the copy's
master data (`masterdata`), rows left out of it by code (`masterdata_left_out`,
for an order another system brings), and the answer key. A scene may
`extends` another in the same folder and say only what it changes. This
writes a copy of the pack with both applied, generates its replay into the
copy's `out/`, and writes a fleet file beside it. **The pack in this
directory is never touched**, so the everyday shift - `fsmes demo cutlery`,
the lab, the public demo - replays exactly what it replayed before.

Why a copy and not a lab plan in `labs/experiments/`: the lab runner scripts
one line per plant and refuses a factory of sixty-one (`fsmes.lab.build`),
and a scene is a plant someone opens in a browser and films, not a scored
run that is torn down at the end.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: What the copy leaves behind: generated data, run results, and the lab's
#: own tools and pictures. A pack carries no code (decision 0022).
LEFT_OUT = ("out", ".data", "analysis", "images", "walkthroughs", "positions", "scenes",
            "__pycache__", "*.py", "fleet.toml")


def scened_line(line: dict, scene: dict) -> dict:
    """`line.json` with the scene's events added to the lines it names, and
    its `lines` settings (a line's own `duration_s`, say) over theirs."""
    events = scene.get("events") or {}
    settings = scene.get("lines") or {}
    names = {entry["name"] for entry in line["lines"]}
    unknown = sorted((set(events) | set(settings)) - names)
    if unknown:
        raise SystemExit(f"the scene names lines this plant does not have: {', '.join(unknown)}")
    out = json.loads(json.dumps(line))
    for entry in out["lines"]:
        entry.update(scened_line_settings(settings.get(entry["name"]) or {}))
        added = [{k: v for k, v in e.items() if not k.startswith("_")}
                 for e in events.get(entry["name"], [])]
        entry["events"] = list(entry.get("events") or []) + added
    return out


#: The floor script's sections a scene may bring.
FLOOR_SECTIONS = ("pieces", "breakdowns")


def scened_line_settings(settings: dict) -> dict:
    """A line's scene settings without their `_` notes, events included."""
    out = {k: v for k, v in settings.items() if not k.startswith("_")}
    if "events" in out:
        out["events"] = [{k: v for k, v in e.items() if not k.startswith("_")}
                         for e in out["events"]]
    return out


def scened_floor(floor: dict, scene: dict) -> dict:
    """The floor script with the scene's `pieces` and `breakdowns` in it."""
    out = dict(floor)
    for section in FLOOR_SECTIONS:
        if scene.get(section):
            out[section] = scene[section]
    return out


def add_masterdata(folder: Path, scene: dict) -> None:
    """The scene's master-data rows, added to the copy's files of the same name.

    Only added, never replacing: a row whose code the pack already has is
    refused, so a scene cannot quietly rewrite the plant it was built on.
    """
    for name, rows in (scene.get("masterdata") or {}).items():
        if name.startswith("_"):
            continue
        path = folder / name
        have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        keys = {json.dumps({k: r.get(k) for k in ("code", "person", "skill")}, sort_keys=True)
                for r in have}
        for row in rows:
            key = json.dumps({k: row.get(k) for k in ("code", "person", "skill")}, sort_keys=True)
            if key in keys:
                raise SystemExit(f"the scene adds a row to {name} the pack already has: {row}")
        path.write_text(json.dumps(have + list(rows), indent=2) + "\n", encoding="utf-8")


def leave_out_masterdata(folder: Path, scene: dict) -> None:
    """The rows a scene names by code, taken out of the copy's files.

    For a row another system brings: the coil-out-erp scene's fork order is
    dropped by ERPNext, and the pack's own row of that code would have the MES
    find the order already there. A code the pack does not have is refused,
    so a typo cannot leave out nothing and pass.
    """
    for name, codes in (scene.get("masterdata_left_out") or {}).items():
        if name.startswith("_"):
            continue
        path = folder / name
        have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        missing = sorted(set(codes) - {row.get("code") for row in have})
        if missing:
            raise SystemExit(f"the scene leaves out rows of {name} the pack does not have: {', '.join(missing)}")
        kept = [row for row in have if row.get("code") not in set(codes)]
        path.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")


def load_scene(scene_path: Path) -> dict:
    """A scene file, with the scene it `extends` (in the same folder) under it.

    One level, top-level keys: what the scene says replaces what the one it
    extends says, whole. The breakdown stays in one file, and a variant of
    it says only what it changes.
    """
    scene_path = Path(scene_path)
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    parent = scene.pop("extends", None)
    if not parent:
        return scene
    base = json.loads((scene_path.parent / parent).read_text(encoding="utf-8"))
    if base.get("extends"):
        raise SystemExit(f"{parent} extends another scene itself; a scene extends one level only")
    return {**base, **scene}


def build(scene_path: Path, into: Path) -> Path:
    scene = load_scene(scene_path)
    into = Path(into).expanduser().resolve()
    if into.exists() and any(into.iterdir()):
        raise SystemExit(f"{into} is not empty; a scene is built into a folder of its own")
    shutil.copytree(HERE, into, ignore=shutil.ignore_patterns(*LEFT_OUT), dirs_exist_ok=True)

    line = json.loads((HERE / "line.json").read_text(encoding="utf-8"))
    (into / "line.json").write_text(json.dumps(scened_line(line, scene), indent=1) + "\n",
                                    encoding="utf-8")
    floor = json.loads((HERE / "floor.json").read_text(encoding="utf-8"))
    (into / "floor.json").write_text(json.dumps(scened_floor(floor, scene), indent=1) + "\n",
                                     encoding="utf-8")
    add_masterdata(into / "masterdata", scene)
    leave_out_masterdata(into / "masterdata", scene)
    (into / "scene.json").write_text(json.dumps(scene, indent=1) + "\n", encoding="utf-8")
    (into / "fleet.toml").write_text(
        f"# The {scene['name']} scene of the cutlery plant, built by labs/cutlery/scene.py.\n"
        f"# Start it at {scene.get('speed', 1)}x: fsmes plant cutlery start --speed "
        f"{scene.get('speed', 1)}\n"
        'packs = ["."]\n\n[environment]\n'
        f'data_dir = "{(into / ".data").as_posix()}"\n', encoding="utf-8")

    from fsmes.sim.generate import generate

    generate(into / "line.json", into / "out", write_docs=True)
    if scene.get("breakdowns") and scene.get("answer"):
        # The true answers, read off the hour just generated: what the replay
        # will play to the MES, so a person can check the screens against it.
        from fsmes.sim.breakdown import answer_key

        specs = json.loads((into / "masterdata" / "quality_specs.json").read_text(encoding="utf-8"))
        key = answer_key(scene, into / "out", specs)
        (into / "answer-key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    return into


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scene", type=Path, help="scenes/<name>.json")
    parser.add_argument("--out", type=Path, required=True, help="an empty folder for the copy")
    args = parser.parse_args()
    built = build(args.scene, args.out)
    print(f"{built}: the cutlery pack with the scene in it; "
          f"FSMES_PLANT_REGISTRY={built / 'fleet.toml'} fsmes plant cutlery init, then start")


if __name__ == "__main__":
    main()
