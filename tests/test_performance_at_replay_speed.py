"""A replayed plant counts faster than its clock, and no screen prints the lie.

On the morning of 2026-09-18 a bottling plant that had been replaying a
recorded line at 10x overnight showed **performance 881 %** and **OEE 980 %**
on its dashboard, and 800-odd per cent on one machine's page. The maintainer
read it as an error. It was one, twice over:

1. The counts arrive at the line's pace and every duration the MES measures is
   wall clock, so performance — the one OEE factor that divides a count by a
   duration — came out by the replay factor. The MES is *told* that factor
   (`MES_SIM_SPEED`, which `fsmes fleet start --speed` puts into the
   environment of every process of that plant, the API included), so it can
   restate the run time on the line's clock instead of printing the ratio of
   two clocks. See `fsmes.services.line_clock`.
2. Where the counts still will not fit inside the run time — a real finding,
   and the one decision 0026 is about — the MES printed the ratio as a
   percentage anyway on every surface except the analysis waterfall. A
   percentage on a screen is read as a measurement of the machine, and that
   one measures two of the MES's own records disagreeing. So there is no
   figure: the disagreement is named, both its numbers with it, and the ratio
   is kept under a name that says what it is.

One test per surface, because the gap was never in the rule — it was in how
far the rule reached.
"""

import os
import re
from datetime import timedelta

import pytest

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.domain import EquipmentState, EquipmentStateName, ProductionLog
from fsmes.services import analysis, equipment, line_clock, masterdata, workorders
from fsmes.services import oee as oee_rules

#: The demo line's mixer, rated at 4.0 s a unit.
MACHINE = "MIX01"
RATED = 4.0
RAN_MINUTES = 30
#: What the machine makes in thirty *line* minutes at its rated cycle.
AT_RATE = int(RAN_MINUTES * 60 / RATED)          # 450


#: Everything a plant's own configuration decides, and nothing the suite
#: itself depends on. Applying a pack configures the process it is applied in
#: (`fsmes.pack.apply` updates the environment on purpose), so a suite that
#: has applied one hands the next test a plant somebody else configured -
#: different modules mounted, a coverage floor set, a different name in
#: `/metrics`. These tests are about what one plant prints, so they start
#: from one nobody else has configured.
_KEPT = {"MES_PLANT_TIMEZONE", "MES_DATABASE_URL", "MES_TEST_DATABASE_URL"}


@pytest.fixture(autouse=True)
def _a_plant_nobody_else_configured(monkeypatch):
    for name in [k for k in os.environ if k.startswith("MES_") and k not in _KEPT]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def at_speed(monkeypatch):
    """Start this plant the way `fsmes fleet start --speed N` starts one.

    Only ever called before a test signs a client in, or from a test that
    signs nobody in: clearing the settings cache builds a new `Settings`, and
    a plant with no `MES_SECRET_KEY` gets a fresh one, which signs out
    whoever was holding a token from the last one.
    """

    def set_it(speed: float | None):
        if speed is None:
            monkeypatch.delenv("MES_SIM_SPEED", raising=False)
        else:
            monkeypatch.setenv("MES_SIM_SPEED", str(speed))
        get_settings.cache_clear()

    yield set_it
    get_settings.cache_clear()


def _ran_and_made(session, units: int, minutes: int = RAN_MINUTES) -> None:
    """The mixer ran for `minutes` of *wall* clock and counted `units`."""
    mixer = masterdata.get_equipment(session, MACHINE)
    now = utcnow()
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=now - timedelta(minutes=minutes + 5),
                               ended_at=now - timedelta(minutes=5)))
    order = workorders.create(session, code="WO-REPLAY", material_code="FG-COLA",
                              quantity=units)
    session.flush()
    session.add(ProductionLog(work_order_id=order.id, equipment_id=mixer.id,
                              good_qty=units, scrap_qty=0,
                              ts=now - timedelta(minutes=10)))
    session.flush()


# --------------------------------------------------------------- the two clocks


def test_the_rule_is_arithmetic_and_has_no_opinion_about_the_plant():
    """The pure function, on its own. A machine that made exactly what its
    rating allows for the run time is 1.0 whichever clock it is measured on,
    as long as both numbers are on the same one."""
    wall = oee_rules.performance(RATED, AT_RATE * 10, RAN_MINUTES * 60, replay_factor=1.0)
    assert wall.value is None and wall.outruns_run_time, "ten times the work in the same seconds"
    assert wall.ratio == pytest.approx(10.0, rel=0.01)

    line = oee_rules.performance(RATED, AT_RATE * 10, RAN_MINUTES * 60, replay_factor=10.0)
    assert line.value == pytest.approx(1.0, rel=0.01), "the same counts, on the line's clock"
    assert line.note is None


def test_a_plant_replaying_at_ten_times_reports_performance_on_the_lines_clock(session, at_speed):
    """Six wall minutes of a recorded half hour. The machine kept its rating;
    the screen said 1,000 % because it divided the line's units by the wall's
    seconds."""
    at_speed(10)
    _ran_and_made(session, units=AT_RATE * 10 - 50)

    result = equipment.oee(session, equipment_code=MACHINE, hours=8.0)
    assert result["performance"] == pytest.approx(0.99, rel=0.02)
    assert result["counts_outrun_run_time"] is False
    assert result["performance_note"] is None
    assert result["clock"]["factor"] == 10
    # The durations stay on the clock this MES measured, with the factor
    # beside them: a reader converts, and nothing is silently restated.
    assert result["runtime_seconds"] == pytest.approx(RAN_MINUTES * 60, abs=60)


def test_the_same_plant_at_one_times_has_nothing_to_say_about_its_clock(session, at_speed):
    """Every real plant. The factor is 1.0, the conversion is the identity,
    and no screen carries a replay bar it would have to explain."""
    at_speed(None)
    _ran_and_made(session, units=AT_RATE)

    result = equipment.oee(session, equipment_code=MACHINE, hours=8.0)
    assert result["clock"] is None
    assert line_clock.factor() == 1.0
    assert result["performance"] == pytest.approx(1.0, rel=0.02)


def test_a_machine_that_made_exactly_its_rating_reports_it_rather_than_flickering(session, at_speed):
    """Run time is a sum of measured intervals, so an exact fit comes back a
    few millionths either side of 1.0 depending on how the database subtracts
    two timestamps. A finding that appears and disappears on the clock's last
    digit is not a finding."""
    at_speed(None)
    _ran_and_made(session, units=AT_RATE)

    result = equipment.oee(session, equipment_code=MACHINE, hours=8.0)
    assert result["performance"] == pytest.approx(1.0, abs=0.0002)
    assert result["counts_outrun_run_time"] is False
    assert result["performance_note"] is None


def test_a_speed_that_makes_no_sense_is_treated_as_real_time(at_speed):
    """A divisor is not the place to find out that a setting was mistyped."""
    at_speed(0)
    assert line_clock.factor() == 1.0
    at_speed(-4)
    assert line_clock.factor() == 1.0


def test_the_clock_does_not_rescue_counts_that_outrun_the_run_time(session, at_speed):
    """Restating the run time is not a cap by another name. A machine that
    counted twice what thirty *line* minutes allow still has a disagreement,
    and it is still named rather than printed."""
    at_speed(10)
    _ran_and_made(session, units=AT_RATE * 20)

    result = equipment.oee(session, equipment_code=MACHINE, hours=8.0)
    assert result["performance"] is None and result["oee"] is None
    assert result["counts_outrun_run_time"] is True
    assert result["performance_ratio"] == pytest.approx(2.0, rel=0.02)
    note = result["performance_note"]
    assert "counted work will not fit inside the run time" in note
    # And the sentence says which clock its run time is on, so a reader can
    # check the arithmetic against the run time on the same screen.
    assert "on the line's clock" in note and "10x" in note


# ------------------------------------------------------- one test per surface


def _a_machine_counting_past_its_run_time(session):
    _ran_and_made(session, units=AT_RATE * 2)


def test_no_figure_above_one_hundred_per_cent_reaches_the_machine_page(session, client):
    """`/kpis/oee/{code}` — what the machine page draws its bars from."""
    _a_machine_counting_past_its_run_time(session)

    said = client.get(f"/kpis/oee/{MACHINE}").json()
    assert said["performance"] is None and said["oee"] is None
    assert "counted work will not fit inside the run time" in said["performance_note"]
    assert "no performance figure to print" in said["performance_note"]
    assert said["performance_ratio"] > 1.0, "the measurement is kept, not thrown away"


def test_no_figure_above_one_hundred_per_cent_reaches_the_analysis_screen(session, client):
    """`/analysis/oee` — the waterfall, and the line rollup above it."""
    _a_machine_counting_past_its_run_time(session)

    said = client.get("/analysis/oee?hours=8").json()
    station = next(s for s in said["stations"] if s["code"] == MACHINE)
    assert station["performance"] is None and station["oee"] is None
    assert station["counts_outrun_run_time"] is True
    assert said["stations_counts_outrun"] == 1
    # The rollup states its total: which stations the line figure covers.
    assert said["stations_rated"] == len([s for s in said["stations"] if s["oee"] is not None])
    assert said["line_oee"] is None or said["line_oee"] <= 1.0


def test_no_figure_above_one_hundred_per_cent_reaches_the_dashboard(session, client):
    """`/dashboard/summary` — the screen a person opens first, and the one
    that printed 980 %. Both the plant tile and the machine card."""
    _a_machine_counting_past_its_run_time(session)

    said = client.get("/dashboard/summary").json()
    plant = said["plant"]
    assert plant["oee"] is None or plant["oee"] <= 1.0
    # The mean says how many machines it covers, and how many it does not.
    assert plant["oee_machines"] <= plant["oee_machines_total"]
    assert plant["oee_counts_outrun"] == 1

    card = next(m for m in said["machines"] if m["code"] == MACHINE)["oee"]
    assert card["performance"] is None and card["oee"] is None
    assert "counted work will not fit inside the run time" in card["performance_note"]


def test_the_plant_tile_says_how_many_machines_its_mean_covers(session, client):
    """An average over four of twenty-four machines is a different claim from
    an average over twenty-four, and the tile used to say neither."""
    said = client.get("/dashboard/summary").json()["plant"]
    assert said["oee_machines_total"] == said["machines_total"]
    assert said["oee_machines"] == 0, "nothing has run in this plant yet"
    assert said["oee"] is None, "and zero is not the answer to that"


def test_metrics_exports_no_performance_and_no_oee_series(session, anon):
    """Not a gap: a scrape is read without the sentence beside it, and both of
    these are figures the MES withholds with a sentence. Pinned so nobody adds
    one without deciding to."""
    _a_machine_counting_past_its_run_time(session)

    body = anon.get("/metrics").text
    assert "mes_equipment_performance" not in body
    assert "mes_equipment_oee" not in body
    assert "mes_plant_oee" not in body
    assert "mes_equipment_coverage" in body, "what it does export is still there"


def test_metrics_say_the_replay_factor_of_a_replayed_plant(session, anon, at_speed):
    """A fleet scraped into one Prometheus must be able to tell a plant whose
    hour is an hour from one whose hour is six minutes."""
    at_speed(10)
    body = anon.get("/metrics").text
    assert re.search(r'^mes_replay_factor\{plant="[^"]+"\} 10$', body, re.MULTILINE), body

    at_speed(None)
    assert "mes_replay_factor" not in anon.get("/metrics").text


def test_health_says_which_clock_this_plant_is_on(session, anon, at_speed):
    """It rides on `/health` for the reason shadow mode does: the endpoint
    everything already asks is the one that reaches every reader — the screens'
    bar, a console, a person with curl."""
    at_speed(10)
    said = anon.get("/health").json()
    assert said["replay"]["factor"] == 10
    assert "10x wall clock" in said["replay"]["means"]
    assert "1x" in said["replay"]["standing_plant"]

    at_speed(None)
    assert anon.get("/health").json()["replay"] is None


def test_the_speed_a_fleet_is_started_with_reaches_the_process_that_answers_the_screens(tmp_path):
    """The whole of how the MES knows. `fsmes fleet start --speed 10` starts
    four processes for a plant and the replay is only one of them; the one
    that answers `/dashboard` is `run-api`, and it is told the same speed
    through the same environment. A speed that reached the replay alone would
    leave the MES dividing the line's counts by the wall's seconds with no way
    to know it was doing so."""
    from fsmes import plant as plants

    env = plants.plant_env("bottling", {}, tmp_path, speed=10)
    assert env["MES_SIM_SPEED"] == "10"
    # And nothing is set when nobody asked for a speed: a real plant's clock
    # is the line's, and a default here would be a claim about the plant.
    assert "MES_SIM_SPEED" not in plants.plant_env("bottling", {}, tmp_path)


def test_the_console_carries_the_replay_factor_of_a_plant_that_answered(tmp_path):
    """The fleet console shows no OEE by design (decision 0023), so what it
    owes a reader is the one fact that changes how every figure inside that
    plant should be read."""
    from fsmes.fleet import console as fleet_console
    from fsmes.fleet import observe

    base = "http://127.0.0.1:8110"
    replaying = {"status": "ok", "plant": "bottling", "shadow": False,
                 "replay": {"factor": 10.0, "means": "replaying at 10x",
                            "standing_plant": "a plant meant to be looked at runs at 1x"}}

    def health(_base, **_kw):
        return observe.Answer(base, True, body=replaying, status=200)

    def pack(_base, **_kw):
        return observe.Answer(base, False, why="not asked here")

    watching = fleet_console.Console(tmp_path, health=health, pack=pack)
    row = watching._one(fleet_console.Listed(name="bottling", base=base))
    assert row["replay"]["factor"] == 10.0
    assert row["shadow"] is False, "a replayed plant is not a shadowed one"

    # And a plant whose clock is the line's says nothing, rather than 1.
    def real(_base, **_kw):
        return observe.Answer(base, True, body={"status": "ok", "plant": "bottling",
                                                "shadow": False, "replay": None}, status=200)

    quiet = fleet_console.Console(tmp_path, health=real, pack=pack)
    assert quiet._one(fleet_console.Listed(name="bottling", base=base))["replay"] is None


# ------------------------------------------------------------ nothing is lost


def test_the_units_are_never_moved_or_dropped_to_make_the_figures_fit(session, at_speed):
    """House rule 1. Withholding a figure is not the same as editing the
    plant: every unit the machine counted is still counted, in good_qty, in
    quality, and in the ratio that is kept beside the missing figure."""
    _a_machine_counting_past_its_run_time(session)

    result = equipment.oee(session, equipment_code=MACHINE, hours=8.0)
    assert result["good_qty"] == AT_RATE * 2
    assert result["quality"] == 1.0
    assert result["availability"] is not None
    assert result["runtime_seconds"] > 0


def test_the_loss_in_units_is_priced_on_the_clock_the_units_are_on(session, at_speed):
    """A loss measured in units is a rate in disguise. At 10x the machine had
    ten line-seconds of run time for every second this MES watched, so the
    units that run time could have made are ten times what the wall clock
    would price them at."""
    at_speed(10)
    _ran_and_made(session, units=AT_RATE * 10 - 50)

    station = next(s for s in analysis.oee_breakdown(session, hours=8)["stations"]
                   if s["code"] == MACHINE)
    # Thirty line-minutes at 4 s is 4,500 units of capacity in six wall
    # minutes. Priced on the wall clock the loss would read 4,050 units short
    # of a machine that was 50 units short.
    assert station["loss"]["performance_units"] == pytest.approx(50, abs=5)
    assert station["performance"] == pytest.approx(0.99, rel=0.02)


def test_a_line_with_nothing_wrong_with_it_is_untouched(session, at_speed):
    """The change is for the case that could not be stated honestly, and only
    that case. A station inside its rating still reports its figure."""
    at_speed(None)
    _ran_and_made(session, units=300)

    result = equipment.oee(session, equipment_code=MACHINE, hours=8.0)
    assert result["performance"] == pytest.approx(4.0 * 300 / 1800, rel=0.02)
    assert result["performance_ratio"] == result["performance"]
    assert result["counts_outrun_run_time"] is False
    assert result["performance_note"] is None
    assert result["oee"] is not None
