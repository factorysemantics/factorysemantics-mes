"""Turn Engineering's tag worksheet into a tag map.

The worksheet is a CSV because that is what actually comes back from a plant:
somebody opens the template in Excel, fills a row per machine, and mails it.
This module is the bridge from that spreadsheet to `config/tag_map_*.json`,
so nobody has to hand-write JSON to bring a line online.

One row per machine, in process order (the row order becomes the routing
order). Columns:

    equipment      MES code for the machine (short, no spaces: FIL01, CAP01)
    name           human name, shown on every dashboard
    node_state     full OPC node id of the machine-state tag
    node_good      full node id of the cumulative good-parts counter
    node_scrap     full node id of the cumulative scrap counter (blank = none)
    node_analog    full node id of one process value worth trending (blank = none)
    analog_name    what that process value is called (MotorTemp, WashTemp...)
    state_map      what each raw state value means, as pairs:
                   "0=idle; 1=running; 4=down; 5=setup"
    cycle_seconds  rated seconds per unit (blank = unknown)
    notes          anything worth keeping; not read by software

Blank cells are honest answers. A machine with no scrap counter gets no
ScrapCount subscription and its quality reads unknown — which is true. A blank
cycle time means OEE performance reads unknown rather than being computed
against a number somebody invented to fill a box.

Everything the worksheet produces is **read-only**: `order_tag` is null on
every machine, so the MES observes the plant and never writes to it.
"""

import csv
import json
from datetime import date
from pathlib import Path

from fsmes.domain import EquipmentStateName

_MES_STATES = {s.value for s in EquipmentStateName}
_NODE_COLUMNS = {"node_state": "State", "node_good": "GoodCount", "node_scrap": "ScrapCount"}


class WorksheetError(ValueError):
    """A worksheet problem, reported with the row it came from."""


def _parse_state_map(raw: str, row: int) -> dict[str, str]:
    """'0=idle; 1=running' -> {'0': 'idle', '1': 'running'}. Accepts ; or |
    between pairs and = or : within one, because this comes out of Excel."""
    mapping: dict[str, str] = {}
    for pair in raw.replace("|", ";").split(";"):
        pair = pair.strip()
        if not pair:
            continue
        sep = "=" if "=" in pair else ":"
        if sep not in pair:
            raise WorksheetError(f"row {row}: state_map entry {pair!r} is not 'value=state'")
        value, state = (part.strip() for part in pair.split(sep, 1))
        state = state.lower()
        if state not in _MES_STATES:
            raise WorksheetError(
                f"row {row}: state_map maps {value!r} to {state!r}, which is not an MES state "
                f"(use one of: {', '.join(sorted(_MES_STATES))})"
            )
        mapping[value] = state
    return mapping


def worksheet_to_tag_map(path: Path) -> tuple[dict, list[str]]:
    """Read the worksheet; return (tag-map data, human warnings).

    Errors are for rows the software cannot honestly interpret. Warnings are
    for blanks that are allowed but have consequences the plant should know
    about (no cycle time -> no performance figure, and so on).
    """
    path = Path(path)
    machines: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    with path.open(encoding="utf-8-sig", newline="") as f:  # -sig: Excel loves BOMs
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise WorksheetError(f"{path} is empty")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        if "equipment" not in fields:
            raise WorksheetError(f"{path} has no 'equipment' column — is this the right file?")

        for index, raw_row in enumerate(reader, start=2):  # row 1 is the header
            row = {key: (raw_row.get(orig) or "").strip() for key, orig in fields.items()}
            if not any(row.values()):
                continue  # blank line
            code = row.get("equipment", "")
            if not code:
                raise WorksheetError(f"row {index}: no equipment code")
            if code in seen:
                raise WorksheetError(f"row {index}: equipment {code!r} appears twice")
            seen.add(code)

            nodes = {tag: row[col] for col, tag in _NODE_COLUMNS.items() if row.get(col)}
            analog_node, analog_name = row.get("node_analog", ""), row.get("analog_name", "")
            if analog_node and not analog_name:
                raise WorksheetError(
                    f"row {index} ({code}): node_analog is filled but analog_name is blank — "
                    f"the name is what every dashboard will label the value with"
                )
            if analog_node:
                nodes[analog_name] = analog_node
            if not nodes:
                raise WorksheetError(f"row {index} ({code}): no node ids at all — nothing to subscribe to")

            state_map = _parse_state_map(row.get("state_map", ""), index)
            if state_map and "State" not in nodes:
                raise WorksheetError(f"row {index} ({code}): state_map given but node_state is blank")
            if "State" in nodes and not state_map:
                warnings.append(
                    f"{code}: no state_map — the State tag must then already carry the words "
                    f"running/idle/down/setup, which a PLC almost never does"
                )
            if "State" not in nodes:
                warnings.append(f"{code}: no state tag — downtime and availability will read unknown")
            if "GoodCount" not in nodes:
                warnings.append(f"{code}: no good counter — production will not book from this machine")
            if "ScrapCount" not in nodes:
                warnings.append(f"{code}: no scrap counter — quality will read unknown (which is honest)")

            entry: dict = {"equipment": code, "order_tag": None, "nodes": nodes}
            if row.get("name"):
                entry["name"] = row["name"]
            if analog_node:
                entry["analog"] = analog_name
            if state_map:
                entry["state_map"] = state_map
            if row.get("cycle_seconds"):
                try:
                    entry["cycle_seconds"] = float(row["cycle_seconds"])
                except ValueError:
                    raise WorksheetError(
                        f"row {index} ({code}): cycle_seconds {row['cycle_seconds']!r} is not a number"
                    ) from None
            else:
                warnings.append(f"{code}: no cycle time — OEE performance will read unknown until one is set")
            machines.append(entry)

    if not machines:
        raise WorksheetError(f"{path} has a header but no machine rows")

    data = {
        "_comment": (
            f"Generated from {path.name} on {date.today().isoformat()} by `fsmes make-tag-map`. "
            f"Row order is process order. Every machine is read-only (order_tag null): "
            f"the MES observes this plant and never writes to it. "
            f"Regenerate from the worksheet rather than editing by hand where possible."
        ),
        "machines": machines,
    }
    return data, warnings


def write_tag_map(worksheet: Path, out: Path) -> list[str]:
    """Convert and write, returning the warnings. Round-trips the result
    through the real loader first, so a file that would crash the agent at
    3am instead refuses to be written at all."""
    from fsmes.integrations.opc.tag_map import load_tag_map

    data, warnings = worksheet_to_tag_map(worksheet)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    load_tag_map(out)  # the loader is the authority; let it object now, not at 3am
    return warnings
