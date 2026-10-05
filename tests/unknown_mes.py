"""Build a SQL MES this repository has never seen, out of the bottling line it knows by heart.

Why this file exists
--------------------
`skills/plant-from-your-mes/` is a folder a *customer's* agent takes in. It has
to work against a schema nobody here has read, and the only way to prove that
is to point it at a database whose table and column names it was not written
for, and ask whether the plant shape it writes out is the bottling line.

So this builds a deliberately foreign Manufacturing Execution System: twelve
tables, invented here, with names and codings chosen to be *unlike* anything in
`src/fsmes` — a state column of three-letter words rather than integers, assets
behind an integer surrogate key, tag history in one tall table keyed by a tag
path, and two tables for things FactorySemantics does not model at all (tool
changes and energy meter reads), because the skill is also asked to report what
it found and could not use.

Clean room, deliberately. Every name below was made up for this file. Nothing
here is derived from any commercial MES product's schema or documentation — see
CONTRIBUTING.md's provenance rule. It is a plausible shape, not a real one.

The numbers, though, are not invented: every state, count and analog sample is
replayed out of `labs/kepsim/line.json` through the product's own generator, so
the bottling line's six stations, its rates, its scrap shares and its analog
bases and noise are genuinely in there to be recovered. That is what makes the
blind test a measurement rather than a restatement.

It is also salted with exactly the things that must never come out the other
side: order numbers, lot numbers, pallet ids, customer names, operator codes,
inspector codes, non-conformance numbers, and free-text downtime notes written
the way a maintenance technician writes them.

Run it by hand:

    python tests/unknown_mes.py /tmp/unknown.db
"""
from __future__ import annotations

import csv
import random
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The window the invented plant ran in. Fixed, so the fixture is reproducible.
EPOCH = datetime(2026, 3, 2, 6, 0, 0, tzinfo=UTC)

# The generator's integer state column, re-coded into this MES's own words. A
# skill that only understands 0..5 would pass a test against our own tables and
# fail at the customer, which is the failure this re-coding exists to catch.
STATE_WORDS = {0: "STOP", 1: "RUN", 2: "WAIT-IN", 3: "WAIT-OUT", 4: "FAULT", 5: "SETUP"}

# The customer's own downtime vocabulary: their codes, their categories, their
# wording. The skill may carry the category (it is a short controlled list) and
# must never carry the text.
STOP_REASONS = [
    ("MEC-04", "Mechanical", "Infeed star wheel jammed, cleared by hand and restarted"),
    ("MEC-11", "Mechanical", "Gripper pads worn through, swapped the set on arm two"),
    ("ELE-02", "Electrical", "Drive tripped on overcurrent, reset at the panel"),
    ("MAT-07", "Material", "Ran out of preforms, waiting on the forklift from the warehouse"),
    ("QUA-03", "Quality", "Fill weights drifting low, held the line for the lab result"),
    ("OPR-01", "Operator", "Operator away on break, no relief on shift"),
    ("CHG-01", "Changeover", "Planned changeover to the other product, per the setup sheet"),
]

# Names that must not survive the profiler. Invented.
CUSTOMERS = ["Northgate Provisions Ltd", "Val-Mart Retail Group", "Cucina Rossi SpA", "Harbour & Sons"]
ITEMS = [
    ("FG-88214", "500 ml still, clear PET, 24-pack shrink"),
    ("FG-88215", "500 ml sparkling, clear PET, 24-pack shrink"),
    ("FG-91003", "1 L still, blue tint PET, 12-pack tray"),
]
OPERATORS = ["OP-1182", "OP-1204", "OP-1377", "OP-1402"]
INSPECTORS = ["QC-204", "QC-219"]
CREWS = ["Red crew", "Blue crew", "Green crew"]
TOOLS = ["TL-GRIP-02", "TL-STAR-11", "TL-NOZ-07", "TL-BLADE-04"]
ERP_ENDPOINTS = [
    ("POST", "/api/resource/Job Card/JC-2026-0881/confirm"),
    ("POST", "/api/resource/Stock Entry"),
    ("GET", "/api/resource/Work Order?filters=[[\"status\",\"=\",\"In Process\"]]"),
]

DDL = """
CREATE TABLE plant_asset (
    asset_id    INTEGER PRIMARY KEY,
    asset_code  TEXT NOT NULL,
    asset_name  TEXT NOT NULL,
    asset_kind  TEXT NOT NULL,
    line_code   TEXT NOT NULL,
    seq_no      INTEGER NOT NULL
);
CREATE TABLE tag_definition (
    tag_id      INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES plant_asset(asset_id),
    tag_path    TEXT NOT NULL,
    eng_unit    TEXT,
    value_type  TEXT NOT NULL
);
CREATE TABLE tag_sample (
    sample_id   INTEGER PRIMARY KEY,
    tag_id      INTEGER NOT NULL REFERENCES tag_definition(tag_id),
    sample_ts   TEXT NOT NULL,
    num_value   REAL
);
CREATE TABLE asset_state_log (
    log_id      INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES plant_asset(asset_id),
    state_word  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    seconds     INTEGER NOT NULL,
    reason_id   INTEGER REFERENCES stop_reason(reason_id),
    reason_note TEXT,
    logged_by   TEXT
);
CREATE TABLE stop_reason (
    reason_id   INTEGER PRIMARY KEY,
    reason_code TEXT NOT NULL,
    category    TEXT NOT NULL,
    reason_text TEXT NOT NULL
);
CREATE TABLE work_order (
    wo_id       INTEGER PRIMARY KEY,
    wo_number   TEXT NOT NULL,
    item_code   TEXT NOT NULL,
    item_desc   TEXT NOT NULL,
    cust_name   TEXT NOT NULL,
    qty_ordered INTEGER NOT NULL,
    released_at TEXT NOT NULL,
    closed_at   TEXT
);
CREATE TABLE wo_output (
    out_id      INTEGER PRIMARY KEY,
    wo_id       INTEGER NOT NULL REFERENCES work_order(wo_id),
    asset_id    INTEGER NOT NULL REFERENCES plant_asset(asset_id),
    booked_at   TEXT NOT NULL,
    good_qty    INTEGER NOT NULL,
    scrap_qty   INTEGER NOT NULL,
    lot_number  TEXT NOT NULL,
    pallet_id   TEXT,
    oper_code   TEXT NOT NULL
);
CREATE TABLE qa_result (
    qa_id       INTEGER PRIMARY KEY,
    wo_id       INTEGER NOT NULL REFERENCES work_order(wo_id),
    asset_id    INTEGER NOT NULL REFERENCES plant_asset(asset_id),
    checked_at  TEXT NOT NULL,
    charact     TEXT NOT NULL,
    measured    REAL,
    verdict     TEXT NOT NULL,
    nc_number   TEXT,
    nc_status   TEXT,
    insp_code   TEXT NOT NULL
);
CREATE TABLE shift_calendar (
    shift_id    INTEGER PRIMARY KEY,
    shift_code  TEXT NOT NULL,
    starts_at   TEXT NOT NULL,
    ends_at     TEXT NOT NULL,
    crew_name   TEXT NOT NULL
);
CREATE TABLE doc_register (
    doc_id       INTEGER PRIMARY KEY,
    doc_number   TEXT NOT NULL,
    doc_title    TEXT NOT NULL,
    revision     TEXT NOT NULL,
    effective_at TEXT NOT NULL
);
CREATE TABLE erp_call_log (
    call_id    INTEGER PRIMARY KEY,
    called_at  TEXT NOT NULL,
    verb       TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    direction  TEXT NOT NULL,
    body_bytes INTEGER NOT NULL,
    http_status INTEGER NOT NULL,
    ref_number TEXT
);
CREATE TABLE tool_change (
    tc_id       INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES plant_asset(asset_id),
    tool_code   TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    cycles_run  INTEGER NOT NULL,
    cost_amount REAL NOT NULL,
    change_note TEXT
);
CREATE TABLE energy_meter_read (
    read_id   INTEGER PRIMARY KEY,
    asset_id  INTEGER NOT NULL REFERENCES plant_asset(asset_id),
    read_at   TEXT NOT NULL,
    kwh_total REAL NOT NULL
);
"""

# How often analog history is kept, in seconds of line time. A real plant
# historian stores on change or on a deadband; ten seconds is a plain
# fixed-cadence stand-in, and it is what the skill has to measure back out.
SAMPLE_EVERY_S = 10

# Which generated columns are which kind of tag. The skill is not told this.
COUNTER_COLUMNS = ("GoodCount", "ScrapCount", "TotalCount")
DISCRETE_COLUMNS = ("ReadyBit",)
# State and AlarmWord live in the state log and the notes, not in tag history.
SKIP_COLUMNS = ("TSec", "State", "AlarmWord")

UNITS = {
    "CycleTimeMs": "ms", "RunMinutes": "min", "ReadyBit": None,
    "GoodCount": "ea", "ScrapCount": "ea", "TotalCount": "ea",
}


def _stamp(second: int) -> str:
    return (EPOCH + timedelta(seconds=second)).isoformat().replace("+00:00", "Z")


def _generate_line(line_json: Path) -> tuple[dict, dict[str, list[dict]]]:
    """Replay the bottling line through the product's own generator."""
    from fsmes.sim.generate import generate, load_config

    config = load_config(line_json)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate(line_json, out, write_docs=False)
        tables: dict[str, list[dict]] = {}
        for station in config["stations"]:
            name = station["name"]
            with (out / f"{name}.csv").open(encoding="ascii", newline="") as handle:
                tables[name] = list(csv.DictReader(handle))
    return config, tables


def _intervals(rows: list[dict]) -> list[tuple[int, int, int]]:
    """Collapse a per-second State column into (state, start, end) intervals."""
    out: list[tuple[int, int, int]] = []
    start = 0
    current = int(rows[0]["State"])
    for row in rows[1:]:
        state = int(row["State"])
        if state != current:
            out.append((current, start, int(row["TSec"])))
            current, start = state, int(row["TSec"])
    out.append((current, start, int(rows[-1]["TSec"]) + 1))
    return out


def build(target: Path, line_json: Path) -> Path:
    """Write the foreign MES to `target` and return it."""
    config, tables = _generate_line(line_json)
    duration = int(config.get("duration_s", 3600))
    rng = random.Random(20261004)

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    db = sqlite3.connect(target)
    db.executescript(DDL)

    # ---------------------------------------------------------- assets, tags
    asset_ids: dict[str, int] = {}
    for index, station in enumerate(config["stations"], start=1):
        name = station["name"]
        asset_ids[name] = index
        db.execute(
            "INSERT INTO plant_asset (asset_id, asset_code, asset_name, asset_kind, line_code, seq_no)"
            " VALUES (?,?,?,?,?,?)",
            (index, f"KC-LN3-M{index:02d}", f"{name} unit {index}", "machine", "KC-LN3", index),
        )

    tag_ids: dict[tuple[str, str], int] = {}
    next_tag = 1
    for station in config["stations"]:
        name = station["name"]
        columns = [c for c in tables[name][0] if c not in SKIP_COLUMNS]
        for column in columns:
            unit = UNITS.get(column, _unit_for(station, column))
            kind = "INT" if column in COUNTER_COLUMNS or column in DISCRETE_COLUMNS else "FLOAT"
            db.execute(
                "INSERT INTO tag_definition (tag_id, asset_id, tag_path, eng_unit, value_type) VALUES (?,?,?,?,?)",
                (next_tag, asset_ids[name], f"KC.LN3.M{asset_ids[name]:02d}.{column.upper()}", unit, kind),
            )
            tag_ids[(name, column)] = next_tag
            next_tag += 1

    samples: list[tuple[int, str, float]] = []
    for station in config["stations"]:
        name = station["name"]
        for row in tables[name]:
            second = int(row["TSec"])
            if second % SAMPLE_EVERY_S:
                continue
            stamp = _stamp(second)
            for column, value in row.items():
                if column in SKIP_COLUMNS:
                    continue
                if value == "" or value is None:
                    # The generator's `quiet` event: that tag was not
                    # publishing at that second, so their historian has no
                    # row for it. A real one is full of these, and a fake
                    # one that filled the gap in would be teaching the skill
                    # to expect a tag history with no holes in it.
                    continue
                samples.append((tag_ids[(name, column)], stamp, float(value)))
    db.executemany("INSERT INTO tag_sample (tag_id, sample_ts, num_value) VALUES (?,?,?)", samples)

    # ------------------------------------------------------- their vocabulary
    for index, (code, category, text) in enumerate(STOP_REASONS, start=1):
        db.execute(
            "INSERT INTO stop_reason (reason_id, reason_code, category, reason_text) VALUES (?,?,?,?)",
            (index, code, category, text),
        )
    faults = [i for i, (_, cat, _) in enumerate(STOP_REASONS, start=1) if cat != "Changeover"]
    setup_reason = next(i for i, (_, cat, _) in enumerate(STOP_REASONS, start=1) if cat == "Changeover")

    # ------------------------------------------------------------ state log
    for station in config["stations"]:
        name = station["name"]
        for state, start, end in _intervals(tables[name]):
            word = STATE_WORDS[state]
            reason_id: int | None = None
            note: str | None = None
            by: str | None = None
            if word == "SETUP":
                reason_id = setup_reason
            # A real MES labels most stops and misses some. The share that is
            # missing is itself a finding, so it is deliberate here.
            elif word in ("FAULT", "STOP") and rng.random() < 0.8:
                reason_id = rng.choice(faults)
            if reason_id is not None:
                note = STOP_REASONS[reason_id - 1][2]
                by = rng.choice(OPERATORS)
            db.execute(
                "INSERT INTO asset_state_log (asset_id, state_word, started_at, ended_at, seconds,"
                " reason_id, reason_note, logged_by) VALUES (?,?,?,?,?,?,?,?)",
                (asset_ids[name], word, _stamp(start), _stamp(end), end - start, reason_id, note, by),
            )

    # --------------------------------------------------------------- orders
    order_ids = list(config.get("orders") or [4711])
    for index, code in enumerate(order_ids):
        item, desc = ITEMS[index % len(ITEMS)]
        span = duration // max(len(order_ids), 1)
        db.execute(
            "INSERT INTO work_order (wo_id, wo_number, item_code, item_desc, cust_name, qty_ordered,"
            " released_at, closed_at) VALUES (?,?,?,?,?,?,?,?)",
            (index + 1, f"WO-2026-{code:07d}", item, desc, CUSTOMERS[index % len(CUSTOMERS)],
             4800 + 200 * index, _stamp(index * span), _stamp(min((index + 1) * span, duration))),
        )

    # ----------------------------------------------------------- production
    for station in config["stations"]:
        name = station["name"]
        rows = tables[name]
        previous_good = previous_scrap = 0
        for row in rows:
            second = int(row["TSec"])
            if second == 0 or second % 60:
                continue
            good = int(row["GoodCount"])
            scrap = int(row["ScrapCount"])
            # A counter reset is a re-baseline, never negative production.
            delta_good = max(good - previous_good, 0)
            delta_scrap = max(scrap - previous_scrap, 0)
            previous_good, previous_scrap = good, scrap
            if not (delta_good or delta_scrap):
                continue
            wo = 1 + (second * len(order_ids)) // max(duration, 1)
            wo = min(wo, len(order_ids))
            db.execute(
                "INSERT INTO wo_output (wo_id, asset_id, booked_at, good_qty, scrap_qty, lot_number,"
                " pallet_id, oper_code) VALUES (?,?,?,?,?,?,?,?)",
                (wo, asset_ids[name], _stamp(second), delta_good, delta_scrap,
                 f"LOT-26{second:05d}-{asset_ids[name]}", f"PAL-{700000 + second}",
                 rng.choice(OPERATORS)),
            )

    # -------------------------------------------------------------- quality
    fill = next((s for s in config["stations"] if any(a.get("name") == "FillWeight"
                                                      for a in s.get("analogs", []))), None)
    nc_seq = 0
    if fill is not None:
        rows = {int(r["TSec"]): r for r in tables[fill["name"]]}
        for second in range(0, duration, 120):
            row = rows.get(second)
            if row is None or row["FillWeight"] in ("", None):
                # No reading at that second: the tag was not publishing, and
                # their MES has no check for it either. A foreign MES with a
                # measurement for every two minutes of a quiet tag would be a
                # foreign MES that invented them.
                continue
            measured = float(row["FillWeight"])
            verdict = "PASS" if 492.0 <= measured <= 508.0 else "FAIL"
            nc_number = nc_status = None
            if verdict == "FAIL":
                nc_seq += 1
                nc_number = f"NCR-2026-{nc_seq:04d}"
                nc_status = rng.choice(["Open", "Open", "Rework", "Scrap", "Use as is"])
            db.execute(
                "INSERT INTO qa_result (wo_id, asset_id, checked_at, charact, measured, verdict,"
                " nc_number, nc_status, insp_code) VALUES (?,?,?,?,?,?,?,?,?)",
                (1 + (second * len(order_ids)) // max(duration, 1) if len(order_ids) > 1 else 1,
                 asset_ids[fill["name"]], _stamp(second), "Net fill weight", measured, verdict,
                 nc_number, nc_status, rng.choice(INSPECTORS)),
            )

    # ---------------------------------------------------- shifts, documents
    for index, code in enumerate(("A", "B", "C")):
        db.execute(
            "INSERT INTO shift_calendar (shift_code, starts_at, ends_at, crew_name) VALUES (?,?,?,?)",
            (code, _stamp(index * 28800 - 21600), _stamp((index + 1) * 28800 - 21600), CREWS[index]),
        )

    documents = (
        [(f"SOP-QA-{n:03d}", f"Net content check, line {n}") for n in range(1, 10)]
        + [(f"WI-FIL-{n:03d}", f"Filler changeover work instruction {n}") for n in range(1, 7)]
        + [(f"FRM-MNT-{n:04d}", f"Maintenance record form {n}") for n in range(1, 5)]
    )
    for index, (number, title) in enumerate(documents, start=1):
        db.execute(
            "INSERT INTO doc_register (doc_id, doc_number, doc_title, revision, effective_at)"
            " VALUES (?,?,?,?,?)",
            (index, number, title, f"Rev {1 + index % 4}", _stamp(0)),
        )

    # ------------------------------------------------------------------ ERP
    for second in range(0, duration, 60):
        verb, endpoint = ERP_ENDPOINTS[(second // 60) % len(ERP_ENDPOINTS)]
        status = 200 if (second // 60) % 17 else 417
        db.execute(
            "INSERT INTO erp_call_log (called_at, verb, endpoint, direction, body_bytes, http_status,"
            " ref_number) VALUES (?,?,?,?,?,?,?)",
            (_stamp(second), verb, endpoint, "out" if verb == "POST" else "in",
             rng.randint(280, 2400), status, f"JC-2026-{800 + second // 60:04d}"),
        )

    # ------------------------------- two things FactorySemantics does not do
    for index in range(18):
        second = (index + 1) * (duration // 20)
        db.execute(
            "INSERT INTO tool_change (asset_id, tool_code, changed_at, cycles_run, cost_amount,"
            " change_note) VALUES (?,?,?,?,?,?)",
            (1 + index % len(config["stations"]), TOOLS[index % len(TOOLS)], _stamp(second),
             rng.randint(40000, 190000), round(rng.uniform(45.0, 920.0), 2),
             "Worn past the gauge, replaced on the planned stop"),
        )
    for station in config["stations"]:
        for hour in range(0, max(duration // 3600, 1) + 1):
            db.execute(
                "INSERT INTO energy_meter_read (asset_id, read_at, kwh_total) VALUES (?,?,?)",
                (asset_ids[station["name"]], _stamp(hour * 3600), round(1200.0 + hour * 18.4, 2)),
            )

    db.commit()
    db.close()
    return target


def _unit_for(station: dict, column: str) -> str | None:
    for analog in station.get("analogs") or ([station["analog"]] if station.get("analog") else []):
        if analog.get("name") == column:
            return analog.get("unit")
    return None


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/unknown_mes.py <target.db>")
    root = Path(__file__).resolve().parent.parent
    path = build(Path(sys.argv[1]), root / "labs" / "kepsim" / "line.json")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
