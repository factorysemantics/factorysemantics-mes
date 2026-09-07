"""Derive labs/cutlery/tag_map.json from line.json - the mega-factory's
mechanical derivation, unchanged: cycle_seconds = 60 / rate_per_min and the
same state map every lab plant uses.

    python3 labs/cutlery/make_tag_map.py
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Loaded by path under its own name: this file and the mega-factory's share
# a module name, and a bare import would find whichever is first on the path.
_spec = importlib.util.spec_from_file_location("mf_make_tag_map", HERE.parent / "megafactory" / "make_tag_map.py")
_mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mf)


def build(line_json: Path) -> dict:
    result = _mf.build(line_json)
    result["_comment"] = ("The cutlery plant, derived mechanically from line.json by "
                          "make_tag_map.py - see build_config.py for the plant itself.")
    return result


if __name__ == "__main__":
    result = build(HERE / "line.json")
    (HERE / "tag_map.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {HERE / 'tag_map.json'} ({len(result['machines'])} machines)")
