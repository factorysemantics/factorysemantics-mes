"""Vary a line, run every variant, and put the scorecards side by side.

A single scored run says whether the MES is honest about one plant. A sweep
says which conditions it stops being honest under - which is the question that
tells you what to build next.

Variance is applied to the line description, never to the MES's own models. A
simulator that shares the product's assumptions cannot catch the product's
blind spots, so the knobs here are deliberately physical: buffers, rates, how
long a machine is down, how much it scraps.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# What a sweep is allowed to vary, and how to reach it in the config. Keeping
# this explicit means a typo is an error rather than a silently ignored knob.
KNOBS: dict[str, str] = {
    "buffer_capacity": "buffers.capacity",
    "buffer_initial": "buffers.initial",
    "seed": "seed",
    "duration_s": "duration_s",
    "rate_per_min": "stations[].rate_per_min",
    "scrap_pct": "stations[].scrap_pct",
    "down_seconds": "events[down].duration",
}


def _set_path(config: dict, dotted: str, value: Any) -> None:
    node = config
    parts = dotted.split(".")
    for key in parts[:-1]:
        node = node.setdefault(key, {})
    node[parts[-1]] = value


def apply_variant(config: dict, variant: dict) -> dict:
    """Return a copy of the line with one variant's knobs applied.

    `station` scopes a knob to one machine; without it a station-level knob
    applies to every station, which is how you ask "what if the whole line
    were 20% slower".
    """
    out = copy.deepcopy(config)
    for knob, value in variant.items():
        # Underscore keys are scope, not knobs: `_station` says which machine a
        # knob applies to. Validating them as knobs rejected every scoped
        # sweep, which is what --station passes on every call.
        if knob.startswith("_"):
            continue
        if knob not in KNOBS:
            raise KeyError(f"Unknown knob {knob!r}. Known: {', '.join(sorted(KNOBS))}")
        if knob in ("buffer_capacity", "buffer_initial"):
            out.setdefault("buffers", {})[knob.split("_", 1)[1]] = value
        elif knob in ("seed", "duration_s"):
            out[knob] = value
        elif knob in ("rate_per_min", "scrap_pct"):
            target = variant.get("_station")
            for station in out.get("stations", []):
                if target is None or station["name"] == target:
                    station[knob] = value
        elif knob == "down_seconds":
            target = variant.get("_station")
            for event in out.get("events", []):
                if event.get("type") != "down":
                    continue
                if target is None or event.get("station") == target:
                    event["end"] = int(event["start"]) + int(value)
    return out


def expand(grid: dict[str, Iterable]) -> list[dict]:
    """A dict of knob -> values becomes the cartesian product of variants.

    {"buffer_capacity": [6, 20]} -> [{"buffer_capacity": 6}, {...20}]
    """
    import itertools

    keys = [k for k in grid if not k.startswith("_")]
    fixed = {k: v for k, v in grid.items() if k.startswith("_")}
    combos = itertools.product(*(list(grid[k]) for k in keys))
    return [{**fixed, **dict(zip(keys, combo, strict=True))} for combo in combos]


def label(variant: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in variant.items() if not k.startswith("_")) or "baseline"


def write_variant(base_config: Path, variant: dict, target: Path) -> Path:
    """Materialise one variant as a line description on disk."""
    config = apply_variant(json.loads(Path(base_config).read_text(encoding="utf-8")), variant)
    config["_variant"] = label(variant)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return target


def compare(cards: list[dict]) -> dict:
    """Put scorecards beside each other, with the spread that matters.

    Reports what differs, not everything - a table where every row is
    identical teaches nothing, and the point of a sweep is the row that
    is not.
    """
    rows = []
    for card in cards:
        m = card.get("metrics", {})
        lags = [f["lag_sim_seconds"] for f in card.get("faults", [])
                if f.get("lag_sim_seconds") is not None]
        rows.append({
            "variant": card.get("variant") or "baseline",
            "plant": card.get("plant"),
            "planned_stop_misclassified": m.get("planned_stop_misclassified"),
            "breakdown_recall": m.get("breakdown_recall"),
            "faults_scored": m.get("faults_scored"),
            "faults_scripted": m.get("faults_scripted"),
            "mean_lag_sim_s": round(sum(lags) / len(lags), 2) if lags else None,
        })

    def spread(key: str):
        vals = [r[key] for r in rows if r[key] is not None]
        return {"min": min(vals), "max": max(vals)} if vals else None

    misclassified = [r for r in rows if r["planned_stop_misclassified"]]
    return {
        "runs": rows,
        "spread": {k: spread(k) for k in
                   ("breakdown_recall", "planned_stop_misclassified", "mean_lag_sim_s")},
        # The finding, stated rather than left for the reader to spot.
        "verdict": (
            f"{len(misclassified)} of {len(rows)} variants booked a planned stop as downtime"
            if misclassified else
            "no variant misclassified a planned stop"
        ),
    }
