"""Link 3 of the plant's own answer key, walked on the shift that plants it.

`labs/multiplant/bottling/line.json` carries a `_chain` block: four links, in
the order the causes happened, each naming the record it is supposed to leave
behind. Link 3 is the one this screen exists for:

    "On the trend graph for FILL01: ChillerOutletTemp leaves 3.2 degC at 6:20
     and keeps climbing to the end of the shift, and ProductTemp - flat all
     morning - walks down from 8.4 to 6.4 degC between 6:40 and 7:00 and stays
     there for half an hour. Two tags on one machine, the second one twenty
     minutes behind the first."

Until this branch there was no screen on which that sentence could be true:
one tag at a time, and a window that ended at this second, so the finished
stretch of a shift could not be opened at all. The executor who planted the
chain wrote the honest line in the handoff that did it - "the walk back breaks
at the trend graph."

So this file does not test a route in the abstract. It builds the plant the
key describes, from the pack's own master data and the line's own simulated
shift, and then walks the link: the two tags on ONE axis over 6:00-7:30, and
the panel on a point inside the cold ramp naming the chiller order that link 2
says nobody started. Both reads come off the same window, settled once, which
is the whole of why the lines can be laid over each other.

The arithmetic is the generator's, not this file's. Nothing here says what the
temperature should be; it reads what `fsmes.sim.generate` wrote and asserts the
SHAPE the key asserts - up by at least a degree, down by at least a degree —
in the tag's own units. A line edited to move the story moves this test with
it, which is the point of keeping the key in the plant's own file.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fsmes import config
from fsmes import db as db_module
from fsmes.db import Base
from fsmes.domain import (
    Equipment,
    EquipmentState,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenancePlan,
    MaintenanceStatus,
    TagValue,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.services import Invalid, trend

#: Generating eight hours of a six-station line takes a few seconds, so this
#: is the slow tier and not the one every commit runs.
pytestmark = pytest.mark.slow

PACK = Path(__file__).resolve().parents[1] / "labs" / "multiplant" / "bottling"
LINE = json.loads((PACK / "line.json").read_text(encoding="utf-8"))
LINKS = {link["link"]: link for link in LINE["_chain"]["links"]}

#: The station the tag map calls FILL01, and the table the generator writes it
#: to. Read from the map rather than written here, so renaming a station in the
#: pack cannot leave this file quietly testing nothing.
OBJECT = next(m.object for m in load_tag_map(PACK / "tag_map.json")
              if m.equipment == "FILL01")

#: Midnight of the shift. The chain's windows are seconds from the start of
#: the line's own eight hours, so this is the one place wall clock and line
#: time meet. A fixed date and not "today": a test whose answer depends on
#: which day it runs is a test that will go red on a Sunday.
T0 = datetime(2026, 10, 8, 0, 0, 0)

#: The stretch of the shift written into the database. The window under test is
#: 06:00-07:30; an hour either side gives the chart a baseline to be flat
#: against and the window room not to be clamped to the first reading.
WRITTEN = (timedelta(hours=5), timedelta(hours=8))


def _stamp(value) -> datetime:
    """A point's `t`, however the service handed it over: these reads are
    called in-process here and over HTTP by the screen, and one of those two
    turns a datetime into a string."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _link3_tags() -> dict[str, dict]:
    return {record["tag"]: record
            for record in LINKS[3]["records"] if record["kind"] == "tag"}


@pytest.fixture(scope="module")
def replay(tmp_path_factory) -> Path:
    """The line's own eight hours, generated the way the lab generates them."""
    from fsmes.sim.generate import generate

    out = tmp_path_factory.mktemp("chain") / "replay"
    generate(PACK / "line.json", out, write_docs=False)
    return out


@pytest.fixture(scope="module")
def plant(replay):
    """FILL01 as the chain describes it, with the shift's own readings in it.

    Master data from the pack (`fsmes pack apply`'s own seeder), tag units from
    the generated manifest, readings from the generated CSV, and the two
    records the key names outside the tags: the changeover of link 1 and the
    chiller order of link 2, assigned and never started.
    """
    with pytest.MonkeyPatch.context() as env:
        # The tag manifest is where a unit comes from, and both chain tags
        # declare degC - which is why they share one axis rather than being
        # drawn against two scales of the same thing. Config, not code.
        env.setenv("MES_REPLAY_DIR", str(replay))
        env.setenv("MES_TAG_MAP_FILE", str(PACK / "tag_map.json"))
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        config.get_settings.cache_clear()
        db_module.get_engine.cache_clear()
        db_module.get_sessionmaker.cache_clear()

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        session = Session(engine, expire_on_commit=False)
        cycles = {m.equipment: m.cycle_seconds
                  for m in load_tag_map(PACK / "tag_map.json")}
        masterdata.seed(session, PACK / "masterdata", cycles)
        session.flush()

        filler = session.query(Equipment).filter_by(code="FILL01").one()

        wanted = set(_link3_tags())
        rows = []
        with (replay / f"{OBJECT}.csv").open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                second = timedelta(seconds=int(row["TSec"]))
                if not WRITTEN[0] <= second <= WRITTEN[1]:
                    continue
                for name in wanted:
                    rows.append(TagValue(equipment_id=filler.id,
                                         tag=f"FILL01.{name}", ts=T0 + second,
                                         value_num=float(row[name])))
        assert rows, f"the generator wrote no {sorted(wanted)} rows for {OBJECT}"
        session.add_all(rows)

        # Link 1's changeover, and the machine running either side of it. The
        # panel says what the machine was doing at the second that was clicked,
        # and a window with no state rows at all would answer "unknown" - true,
        # but not the plant the key describes.
        setup = LINKS[1]["records"][0]["window_s"]
        session.add_all([
            EquipmentState(equipment_id=filler.id, state=EquipmentStateName.RUNNING,
                           started_at=T0 + WRITTEN[0],
                           ended_at=T0 + timedelta(seconds=setup[0])),
            EquipmentState(equipment_id=filler.id, state=EquipmentStateName.SETUP,
                           reason="Changeover", reason_code="changeover",
                           started_at=T0 + timedelta(seconds=setup[0]),
                           ended_at=T0 + timedelta(seconds=setup[1])),
            EquipmentState(equipment_id=filler.id, state=EquipmentStateName.RUNNING,
                           started_at=T0 + timedelta(seconds=setup[1]),
                           ended_at=T0 + WRITTEN[1]),
        ])

        # Link 2: the condenser clean, raised in the first minutes of the shift,
        # given to somebody, and never started because it needs the line
        # stopped. The one record that turns "the product went cold" into a
        # cause a person can act on.
        plan = session.query(MaintenancePlan).filter_by(
            code=LINKS[2]["records"][0]["plan"]).one()
        session.add(MaintenanceOrder(
            code="MO-CHAIN-CHILLER", equipment_id=filler.id, plan_id=plan.id,
            kind=MaintenanceKind.PREVENTIVE, status=MaintenanceStatus.ASSIGNED,
            summary=plan.name, reason="needs the line stopped - no window between orders",
            raised_at=T0 + timedelta(minutes=3), started_at=None,
            completed_at=None))
        session.commit()
        yield session

        session.close()
        engine.dispose()

    config.get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.get_sessionmaker.cache_clear()


@pytest.fixture()
def window() -> tuple[datetime, datetime]:
    """06:00-07:30, which is the window the handoff names: the cold ramp of
    link 3 with half an hour of flat morning in front of it to be flat
    against."""
    return T0 + timedelta(hours=6), T0 + timedelta(hours=7, minutes=30)


# --------------------------------------------- both tags, one axis, one window


def test_the_two_tags_of_link_three_come_back_on_one_axis(plant, window):
    """Both are measured in degrees, so one scale is the honest picture: a
    reader lays one line over the other and reads the twenty minutes between
    them off the clock. Two scales here would draw the same units twice and
    the lag would be unreadable."""
    envelope = trend.series(plant, "FILL01", tags=list(_link3_tags()),
                            window=window)

    assert [s["label"] for s in envelope["series"]] == list(_link3_tags())
    assert len(envelope["axes"]) == 1, envelope["axes"]
    assert envelope["axes"][0]["unit"] == "degC"
    assert sorted(envelope["axes"][0]["tags"]) == sorted(_link3_tags())
    assert envelope["window"]["named"] == "between"
    assert envelope["window"]["start"] == window[0]
    assert envelope["window"]["end"] == window[1]


def test_the_chart_draws_the_cause_rising_and_the_effect_falling(plant, window):
    """The move the key asserts, judged the way the key says to judge it.

    `_how_a_tag_record_is_judged` in the plant's own file: the mean over the
    record's own window against the mean over what came BEFORE it, in the
    tag's own units, in the named direction. Mean against baseline and not
    first bucket against last, because one of these records is a step and one
    is a ramp and the last bucket of the ramp is already recovering - a test
    that read the ends of this window would say the product never went cold.

    Measured on the drawn points, not on the stored rows: what is pinned is
    that both moves survive the bucketing the chart does to fit ninety minutes
    on a screen, which is the thing a reader is actually looking at.

    One margin is worth knowing about. The chiller's own window runs to the end
    of the shift and this chart stops at 7:30, so inside the stretch a reader
    can see at 7:30 it clears the key's one degree by about a hundredth of one
    - it is still climbing. That is not flakiness: the shift is one seed and
    one script, so the figure is the same on every run, and if somebody changes
    the line so the climb starts later this test is supposed to go red. The
    numbers are printed either way.
    """
    envelope = trend.series(plant, "FILL01", tags=list(_link3_tags()),
                            window=window)
    drawn = {s["label"]: s for s in envelope["series"]}
    said = []

    for name, record in _link3_tags().items():
        begins = T0 + timedelta(seconds=record["window_s"][0])
        points = [p for p in drawn[name]["points"] if p["mean"] is not None]
        inside = [p["mean"] for p in points if _stamp(p["t"]) >= begins]
        before = [p["mean"] for p in points if _stamp(p["t"]) < begins]
        assert inside, f"{name} drew nothing inside its own window"
        assert before, (f"{name}'s window starts at or before this chart's, so "
                        f"there is no baseline to judge the move against - the "
                        f"key scores that null rather than guessing")
        moved = fmean(inside) - fmean(before)
        said.append(f"{name} {moved:+.2f} {drawn[name]['unit']} "
                    f"({fmean(before):.2f} -> {fmean(inside):.2f})")
        if record["direction"] == "up":
            assert moved >= record["by"], said[-1]
        else:
            assert moved <= -record["by"], said[-1]

    # Printed so a person reading a failure - or a pass - can see the size of
    # the move rather than only that it was big enough.
    print(" · ".join(said))


def test_the_chart_says_how_many_readings_it_drew_as_how_many_points(plant, window):
    """An arbitrary window has to be honest about its own cost. Ninety minutes
    of a tag that arrives every second is more points than a chart can carry,
    so it is bucketed - and a mean read as a reading is the whole of what this
    sentence prevents."""
    envelope = trend.series(plant, "FILL01", tags=list(_link3_tags()),
                            window=window)

    assert envelope["bucket_seconds"]
    assert "readings are drawn as" in envelope["drawn_note"]


# ------------------------------------- the panel on a point inside the cold ramp


def test_a_point_inside_the_cold_ramp_names_the_chiller_order_nobody_started(
        plant, window):
    """THE WALK. A reader who knows only that the product went cold clicks a
    reading inside the ramp, and the panel beside it names the maintenance
    order that was open on this machine all shift, its state, and why it is in
    that state. That is link 3 reaching link 2 without anybody typing a query.
    """
    ramp = LINKS[3]["window_s"]
    inside = T0 + timedelta(seconds=(ramp[0] + ramp[1]) / 2)
    assert window[0] <= inside <= window[1]

    panel = trend.dossier(plant, "FILL01", tag="ProductTemp", at=inside,
                          window=window, bucket_seconds=30)

    assert panel["reading"]["unit"] == "degC"
    assert panel["reading"]["bucket"]["n"] > 0
    assert panel["machine"]["state"]["state"] == "running"

    orders = panel["maintenance"]["orders"]
    assert [o["code"] for o in orders] == ["MO-CHAIN-CHILLER"]
    assert orders[0]["status"] == "assigned"
    # Never started: the only true record of nobody having gone is an absence,
    # which is what link 2 asks the records for in so many words.
    assert orders[0]["started_at"] is None
    assert "stopped" in (orders[0]["reason"] or "")
    assert panel["maintenance"]["total"] == 1


def test_the_panel_carries_the_machines_other_tags_at_that_instant(plant, window):
    """The cause is on the same machine as the effect, so the other tag's last
    word at that second is on the panel - which is how a reader who clicked
    ProductTemp finds ChillerOutletTemp without knowing to look for it."""
    ramp = LINKS[3]["window_s"]
    inside = T0 + timedelta(seconds=(ramp[0] + ramp[1]) / 2)
    panel = trend.dossier(plant, "FILL01", tag="ProductTemp", at=inside,
                          window=window, bucket_seconds=30)

    named = {row["tag"]: row for row in panel["tags"]["tags"]}
    assert "ChillerOutletTemp" in named
    assert named["ChillerOutletTemp"]["unit"] == "degC"
    assert named["ChillerOutletTemp"]["value"] is not None
    # Every list states its total, including the tags nobody drew.
    assert panel["tags"]["total"] >= panel["tags"]["shown"]


def test_the_panel_refuses_a_second_the_chart_is_not_showing(plant, window):
    """A panel that quietly widened to answer about a second outside the chart
    would answer a question nobody asked - and the maintenance order raised at
    the start of the shift is exactly the answer a narrowed window loses."""
    with pytest.raises(Invalid):
        trend.dossier(plant, "FILL01", tag="ProductTemp",
                      at=T0 + timedelta(hours=9), window=window)
