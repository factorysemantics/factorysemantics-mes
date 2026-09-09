"""The recorded hour: the KepSim line, flattened into one static file.

Why this exists. The 3D line view should put on its whole show on a laptop with
nothing running — no database, no OPC server, no agent — because the moment you
most want to show someone a line is the moment you have not got a plant handy.

Why it is built this way. It would be easy to hand-write a plausible-looking
animation file. Instead this reads the same generated tables the replay server
publishes, maps their raw PLC states through the same tag map the OPC agent
uses, and borrows the layout from the same `services.line.layout()` the live
endpoint calls. What the demo plays is therefore the same hour, described the
same way, as what the live view would have shown — the only thing missing in
between is the database.

The one place it deliberately mirrors the agent rather than the raw data:
counters that fall are treated as a reset, and the units around a reset are not
claimed. The recording contains a scripted counter reset at t=3000 precisely so
that behaviour gets exercised. See `feed.js`, which applies the same rule when
it replays this file.
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from fsmes.config import get_settings
from fsmes.integrations.opc.csv_replay import load_table
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.seed_kepsim import LINE_CODE
from fsmes.services import line as line_service

# Where the page looks for it. Under web/ so it is served as a plain static
# asset, which is what makes "open it with nothing running" true.
DEFAULT_OUTPUT = Path(__file__).parent / "web" / "demo" / "kepsim-hour.json"
TICK_SECONDS = 1.0


def build(session: Session, *, replay_dir: Path | None = None, tag_map: Path | None = None,
          line_code: str = LINE_CODE) -> dict:
    """Assemble the recorded hour. The session is only used for the layout."""
    settings = get_settings()
    directory = Path(replay_dir or settings.replay_dir)
    machines = load_tag_map(Path(tag_map or settings.tag_map_file))

    layout = line_service.layout(session, line_code=line_code)
    by_code = {m.equipment: m for m in machines}

    stations = []
    ticks = None
    for entry in layout["stations"]:
        spec = by_code.get(entry["code"])
        if spec is None:
            raise ValueError(
                f"{entry['code']} is on line {line_code} but not in the tag map — "
                f"point --tag-map at the map that describes this line."
            )
        rows = load_table(directory, spec.object)
        if ticks is None:
            ticks = len(rows)
        elif len(rows) != ticks:
            raise ValueError(f"{spec.object}.csv has {len(rows)} rows, expected {ticks}")

        stations.append({
            "code": spec.equipment,
            "analog": spec.analog,
            # [state, good, scrap, analog] per tick. Positional, because named
            # keys on 3600 rows would quadruple the file for no one's benefit.
            "rows": [
                [
                    # The MES state, not the raw PLC integer: the demo shows what
                    # the MES would say, mapped by the same tag map the agent uses.
                    str(spec.to_state(row["State"])),
                    int(row["GoodCount"]),
                    int(row["ScrapCount"]),
                    round(float(row[spec.analog]), 1),
                ]
                for row in rows
            ],
        })

    line_rows = load_table(directory, "Line")
    order = line_rows[0].get("OrderId")

    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": f"{directory.as_posix()} (labs/kepsim)",
        "line": layout["line"]["code"],
        "order": str(int(order)) if order is not None else None,
        "tick_seconds": TICK_SECONDS,
        "ticks": ticks,
        "layout": layout,
        "stations": stations,
    }
