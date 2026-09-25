"""The plant's own quality numbers, and the two facts that stopped being
judgments in code.

Eleven literals from the configuration audit of 2026-09-21, made the plant's.
Every one of them ships the value that was in the source, so the first thing
this file pins is that **nothing moved** — a plant that writes no key behaves
exactly as it did. The rest is each number doing what it says when a plant
does write one, and the two things that turned out not to be the plant's at
all but the gauge's and the material's.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from fsmes.domain import Gauge
from fsmes.services import coa, gauges, quality, serialization, spc

STEADY = [11.0 + (i % 3 - 1) * 0.02 for i in range(30)]


def _severities(session):
    """The two words the product writes, on this plant's list."""
    from fsmes.services import severities

    for code, name in (("minor", "Minor"), ("major", "Major")):
        severities.define(session, code=code, name=name, actor="QE")
        severities.approve(session, code, 1, actor="ADMIN")
    session.flush()


def _record(session, values, characteristic="brix"):
    raised = []
    for value in values:
        raised.extend(quality.record_check(
            session, material_code="FG-COLA", characteristic=characteristic,
            value=value, actor="test")[2])
    session.flush()
    return raised


def _set(monkeypatch, **values):
    """This plant's settings, as a plant that wrote the keys would have them.

    Patched on the cached Settings object rather than through the environment
    because `get_settings` is cached for the life of the process, which is the
    right thing for a plant and the wrong thing for a test that wants to ask
    what a different plant would do.
    """
    from fsmes.config import get_settings

    settings = get_settings()
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value, raising=False)


# ------------------------------------- nothing moved, which is the point


def test_every_quality_key_ships_the_literal_that_was_in_the_source():
    """Rule one of the audit, as one test. A plant that configures nothing
    gets exactly what it got before any of these keys existed, and these are
    the numbers that were in the source on 2026-09-21."""
    from fsmes.config import Settings

    shipped = Settings()
    assert shipped.hold_rules() == (1, 2, 3, 4)
    assert shipped.major_rules() == (1,)
    assert shipped.quality_cpk_capable == 1.33
    assert shipped.quality_cpk_marginal == 1.0
    assert shipped.quality_spc_min_points == 12
    assert shipped.quality_spc_history == 200
    assert shipped.quality_gauge_ratio_adequate == 10
    assert shipped.quality_gauge_ratio_floor == 4
    assert shipped.quality_gauge_default_interval_days == 365
    assert shipped.quality_coa_serials_listed == 200
    assert shipped.quality_serial_digits == 6
    assert shipped.quality_nc_code_prefix == "NC"
    assert shipped.quality_containment_max_depth == 6


def test_the_services_agree_with_the_constants_they_replaced(session):
    """Each service keeps the literal as its own named fallback, so the
    product still says what it does in one place even when nothing is
    configured. A drift between the two would be the setting quietly moving
    a plant that never asked for it.

    Read with a session since 2026-09-24, because these are read from the
    plant's own `plant_settings` table before the environment. On a plant with
    no rows there - which is what this test's database is - every one of them
    still answers the literal above, and that is the whole point of the test.
    """
    assert spc.min_points(session) == spc.MIN_POINTS == 12
    assert spc.history(session) == spc.HISTORY == 200
    assert spc.cpk_bars(session) == (spc.CPK_CAPABLE, spc.CPK_MARGINAL) == (1.33, 1.0)
    assert spc.hold_rules(session) == spc.HOLD_RULES == (1, 2, 3, 4)
    assert spc.major_rules(session) == spc.MAJOR_RULES == (1,)
    assert gauges.ratios(session) == (gauges.RATIO_ADEQUATE, gauges.RATIO_FLOOR) == (10.0, 4.0)
    assert gauges.default_interval_days(session) == gauges.DEFAULT_INTERVAL_DAYS == 365
    assert coa.serials_listed(session) == coa.SERIALS_LISTED == 200
    assert serialization.max_depth(session) == serialization.MAX_DEPTH == 6
    assert serialization.serial_digits(session) == serialization.SERIAL_DIGITS == 6
    assert quality.nc_code_prefix(session) == quality.NC_CODE_PREFIX == "NC"


def test_every_shipped_default_is_inside_the_range_the_checker_enforces():
    """A range that refused the value the product ships would be a range that
    refused a plant for behaving exactly as the product does."""
    from fsmes.config import Settings
    from fsmes.pack.check import QUALITY_RANGES

    shipped = Settings()
    for name, (low, high, _why) in QUALITY_RANGES.items():
        value = getattr(shipped, f"quality_{name}")
        assert value > low, name
        if high is not None:
            assert value <= high, name


# ---------------------------------------------------- the SPC numbers


def test_a_plant_that_insists_on_more_readings_draws_no_limits_until_it_has_them(
        session, monkeypatch):
    _set(monkeypatch, quality_spc_min_points=25)
    _record(session, STEADY)  # thirty readings, and it wants twenty-five
    assert spc.chart(session, "FG-COLA", "brix")["control"] is not None

    _set(monkeypatch, quality_spc_min_points=40)
    chart = spc.chart(session, "FG-COLA", "brix")
    assert chart["control"] is None
    # And it says why, in its own number rather than the product's.
    assert "40 to mean anything" in chart["note"]
    assert chart["min_points"] == 40


def test_the_cpk_bar_a_plant_sets_is_the_one_the_verdict_and_the_screen_use(
        session, monkeypatch):
    """The Cpk itself never moves - it is arithmetic. What moves is the
    English word beside it, and the browser used to hold its own copy of the
    bar to colour the verdict, so a plant that moved it got a green figure
    under a sentence calling it marginal."""
    _record(session, STEADY)
    chart = spc.chart(session, "FG-COLA", "brix")
    cpk = chart["capability"]["cpk"]
    assert chart["cpk_capable"] == 1.33

    _set(monkeypatch, quality_cpk_capable=cpk + 1, quality_cpk_marginal=cpk - 0.01)
    stricter = spc.chart(session, "FG-COLA", "brix")
    assert stricter["capability"]["cpk"] == cpk, "the arithmetic is not the plant's"
    assert "marginal" in stricter["verdict"]
    # The bar the browser paints by comes back with the figure it judges.
    assert stricter["cpk_capable"] == cpk + 1


def test_which_rule_is_a_major_finding_is_the_plants_triage_policy(
        session, monkeypatch):
    """Rule 1 alone by default, which is what the source did. A plant that
    treats a four-of-five trend as major changes only its own triage queue -
    the rule numbers and the record are identical."""
    from fsmes.domain import NonConformance

    _severities(session)
    _set(monkeypatch, quality_major_rules="")
    _record(session, STEADY)
    _record(session, [11.9])
    session.flush()
    raised = [nc for nc in session.scalars(select(NonConformance))
              if (nc.evidence or {}).get("source") == "spc"]
    assert len(raised) == 1 and raised[0].severity == "minor"


def test_a_shorter_history_is_the_window_the_rules_and_the_chart_both_read(
        session, monkeypatch):
    _record(session, STEADY)
    assert spc.chart(session, "FG-COLA", "brix")["n"] == 30
    _set(monkeypatch, quality_spc_history=15)
    assert spc.chart(session, "FG-COLA", "brix")["n"] == 15


# ------------------------------------------------------- the gauge numbers


def test_a_plant_that_follows_ansi_rather_than_aiag_calls_a_four_to_one_gauge_adequate(
        session, monkeypatch):
    """AIAG says 10:1 and ANSI Z540 says 4:1. Both plants are right, nothing
    off-plant reads the word `adequate`, and a plant follows one standard for
    every gauge it owns."""
    gauges.register(session, code="CAL-1", name="Calipers", resolution=0.01)
    session.flush()
    verdict = gauges.resolution_check(session, "CAL-1", tolerance=0.05)
    assert verdict["adequate"] is False and verdict["usable"] is True
    assert verdict["ratio_adequate"] == 10 and verdict["ratio_floor"] == 4

    _set(monkeypatch, quality_gauge_ratio_adequate=4.0, quality_gauge_ratio_floor=2.0)
    theirs = gauges.resolution_check(session, "CAL-1", tolerance=0.05)
    assert theirs["adequate"] is True
    # The ratio is the same measurement either way: only the word moved.
    assert theirs["ratio"] == verdict["ratio"]
    assert "4 to one is this plant's working rule" not in theirs["verdict"]


def test_a_gauge_registered_without_an_interval_takes_the_plants_house_answer(
        session, monkeypatch):
    """The 365 was in three places - the service, the API schema and the
    browser's form. Now it is one key, and the form sends nothing."""
    _set(monkeypatch, quality_gauge_default_interval_days=180)
    gauge = gauges.register(session, code="CAL-2", name="Micrometer")
    session.flush()
    assert gauge.interval_days == 180
    # And a gauge somebody gives an interval keeps their answer, not the
    # plant's: the per-gauge column was always the engineer's.
    theirs = gauges.register(session, code="CAL-3", name="Gauge block", interval_days=90)
    assert theirs.interval_days == 90


def test_how_much_warning_a_gauge_wants_is_the_gauges_own(session):
    """A quarterly calibration wants a fortnight and an annual one wants two
    months, and one plant owns both - so it is a column, not a key. Thirty is
    what every gauge starts with, which is the number the browser had
    invented for itself."""
    today = date.today()
    gauges.register(session, code="CAL-YEAR", name="Annual", interval_days=365,
                    warn_days=60)
    gauges.register(session, code="CAL-QTR", name="Quarterly", interval_days=90)
    for code, days_ago in (("CAL-YEAR", 320), ("CAL-QTR", 45)):
        gauge = session.scalar(select(Gauge).where(Gauge.code == code))
        gauge.last_calibrated = today - timedelta(days=days_ago)
    session.flush()

    register = {g["code"]: g for g in gauges.register_list(session, today)["gauges"]}
    # 45 days out, inside its 60-day window.
    assert register["CAL-YEAR"]["warn_days"] == 60
    assert register["CAL-YEAR"]["due_soon"] is True
    # 45 days out, outside its 30-day window - the same number of days, a
    # different answer, which is the whole reason this is on the gauge.
    assert register["CAL-QTR"]["warn_days"] == 30
    assert register["CAL-QTR"]["due_soon"] is False
    assert gauges.register_list(session, today)["due_soon"] == 1


def test_the_gauge_register_answers_due_soon_so_the_browser_stops_deciding_it(
        admin, session):
    """The server returned only `days_until_due` and `overdue`, so the shop
    floor's definition of *due soon* lived in JavaScript at three separate
    places on one screen."""
    gauges.register(session, code="CAL-4", name="Calipers")
    session.flush()
    body = admin.get("/quality/gauges").json()
    assert "due_soon" in body
    assert all("due_soon" in row and "warn_days" in row for row in body["gauges"])


# ------------------------------------------- the certificate and the serials


def test_a_material_is_counted_in_pieces_because_it_says_so_not_because_of_its_code(
        session):
    """The audit's found bug #1: `services/coa.py` computed pallet capability
    only for materials whose code started `UT-`, so any plant not numbering
    its pieces that way got an empty capability block and no error anywhere."""
    from fsmes.services import masterdata

    piece = masterdata.create_material(session, code="WIDGET-1", name="A widget",
                                       counted_in_pieces=True)
    plain = masterdata.create_material(session, code="WIDGET-2", name="Another")
    session.flush()
    assert piece.counted_in_pieces is True
    assert plain.counted_in_pieces is False
    # Nothing in the product reads the prefix any more.
    source = (__import__("pathlib").Path(coa.__file__)).read_text(encoding="utf-8")
    assert 'startswith("UT-")' not in source


def test_a_plant_that_lists_fewer_serials_says_how_many_more_there_are(
        session, monkeypatch):
    """The certificate truncates; what it must never do is truncate quietly.
    Two hundred was the product's answer and is still the default - what
    changed is that a plant whose customer wants every unit listed, or whose
    certificate has to stay printable, can say so."""
    from fsmes.db import utcnow
    from fsmes.domain import ProductionSource
    from fsmes.services import execution, workorders

    code = "WO-SERIALS"
    workorders.create(session, code=code, material_code="FG-COLA", quantity=6, actor="test")
    workorders.release(session, code, "test")
    for index in range(6):
        serialization.produce(session, material_code="FG-COLA", order_code=code,
                              serial=f"BTL-{index}", actor="test")
    execution.report(session, equipment_code="MIX01", good=6, source=ProductionSource.OPC)
    session.flush()

    data = coa.gather(session, code)
    assert len(data["serials"]) == 6
    rendered = coa.render(data, issued_by="test", issued_at=utcnow(), revision=1,
                          supersedes=None)
    assert "BTL-5" in rendered and "more" not in rendered.split("## Serialised units")[1][:400]

    _set(monkeypatch, quality_coa_serials_listed=2)
    assert coa.serials_listed(session) == 2
    # Re-gathered, because the number the certificate cut its list to is
    # carried with the data rather than read again while rendering: a
    # certificate says what was gathered.
    shorter = coa.render(coa.gather(session, code), issued_by="test",
                         issued_at=utcnow(), revision=2, supersedes=1)
    assert "BTL-0" in shorter and "BTL-5" not in shorter
    assert "… 4 more" in shorter


def test_a_plant_numbers_its_own_serials_and_the_separator_stays_the_products(
        session, monkeypatch):
    """Eight digits or six affects only the plant's own labels and scanners.
    The hyphen does not: `next_serial`'s recovery scan reads `PREFIX-digits`
    to find where a plant's numbering had already got to, and a plant that
    changed the separator would start again at one over serials it had
    already issued."""
    assert serialization.next_serial(session, "F") == "F-000001"
    _set(monkeypatch, quality_serial_digits=8)
    assert serialization.next_serial(session, "K") == "K-00000001"
    # And the counter a plant already has keeps counting, whatever the width.
    assert serialization.next_serial(session, "F") == "F-00000002"


def test_a_containment_depth_a_plant_chooses_is_never_past_the_products_ceiling(
        session, monkeypatch):
    """It is the plant's packaging *and* the guard that stops a walk running
    away over a cycle in the data. A plant chooses the first; it does not get
    to switch off the second."""
    _set(monkeypatch, quality_containment_max_depth=7)
    assert serialization.max_depth(session) == 7
    _set(monkeypatch, quality_containment_max_depth=500)
    assert serialization.max_depth(session) == serialization.DEPTH_CEILING == 12
    _set(monkeypatch, quality_containment_max_depth=0)
    assert serialization.max_depth(session) == 1


def test_a_plant_that_calls_them_ncrs_calls_all_of_them_ncrs(session, monkeypatch):
    _set(monkeypatch, quality_nc_code_prefix="NCR")
    nc = quality.open_nc(session, description="Out of spec", severity="minor")
    session.flush()
    assert nc.code.startswith("NCR-")
    # The number's own width stays the product's: five digits, as it was.
    assert nc.code.split("-", 1)[1].isdigit() and len(nc.code.split("-", 1)[1]) == 5


# ------------------------------------------- the Configuration page


def test_every_quality_key_is_listed_on_the_configuration_page_with_its_value(
        admin):
    """Rule three of the audit: the PR that makes a setting configurable adds
    its `ConfigSection` in the same PR, never afterwards. So every key in the
    schema has a row somebody can find it on."""
    from fsmes.pack import format as fmt

    page = admin.get("/dashboard/config/quality/sections").json()
    listed = {key["key"] for row in page["items"] for key in row["pack_keys"]}
    expected = {f"[quality] {key.name}" for key in fmt.BY_SECTION["quality"].keys}
    assert listed == expected, "a [quality] key with no row is a key nobody can find"

    for row in page["items"]:
        for key in row["pack_keys"]:
            assert key["value"] != "" or key["key"].endswith("hold_rules")
            assert key["is_default"] is True, "a fresh plant runs on the defaults"
            assert key["about"], f"{key['key']} says nothing about itself"


def test_the_page_states_its_total_and_stays_inside_a_screenful(admin):
    """Rule two: a list is honest as a plain list up to twelve, and past
    twelve it needs search and a stated sort. Quality is at twelve, so this
    pins where the boundary is rather than where the list happens to be."""
    page = admin.get("/dashboard/config/quality/sections").json()
    assert page["total"] == len(page["items"]) == 12


def test_the_configuration_screen_searches_only_once_the_list_passes_twelve():
    """Below a screenful a search box is furniture: it costs a control and a
    focus trap and buys nothing, because the eye is already faster. Above it,
    a list without one reproduces what the free-text box did."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
              / "config.js").read_text(encoding="utf-8")
    assert "const SCREENFUL = 12;" in script
    assert "page.total > SCREENFUL" in script
    # And the sort is stated on the page rather than left to be inferred.
    assert "#section-sort" in script


def test_one_setting_can_be_pointed_at_by_name_in_the_address():
    """A page of boxes needs a way to say *this* box. The assistant's walk and
    the "what is it now" link after a change both arrive at
    `?setting=<key>`, and only the box that query names carries the anchor they
    point at - so a person who came here by hand sees the page they always saw.
    """
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
              / "config.js").read_text(encoding="utf-8")
    assert 'URLSearchParams(window.location.search).get("setting")' in script
    # The box, and the Save of the section it belongs to: the walk ends on the
    # button, and one Save serves one section.
    assert 'if (key.name === FOCUS) field.dataset.assist = "setting-in-focus";' in script
    assert 'button.dataset.assist = "setting-save-in-focus";' in script


@pytest.mark.parametrize("bad, expect", [
    ({"spc_min_points": 1}, "at least two readings"),
    ({"spc_history": 0}, "at least one reading"),
    ({"serial_digits": 40}, "between 1 and 12"),
    ({"containment_max_depth": 99}, "hard ceiling of twelve"),
    ({"nc_code_prefix": "ncr"}, "upper case"),
    ({"cpk_capable": 1.0, "cpk_marginal": 1.33}, "nothing would ever be called marginal"),
    ({"gauge_ratio_adequate": 4, "gauge_ratio_floor": 10},
     "nothing would ever be called usable but marginal"),
])
def test_a_number_that_would_make_its_own_judgment_meaningless_is_refused(bad, expect):
    """Ranges, not opinions. A plant that wants twenty-five readings behind
    its limits or a four-to-one gauge floor is answering its own question and
    is not second-guessed here; what is refused is a number that would leave
    the thing it decides unable to decide anything."""
    from fsmes.pack import check as checker

    class _Pack:
        def table(self, name):
            return bad if name == "quality" else {}

    found = [str(p) for p in checker._quality_numbers(_Pack())]
    assert any(expect in line for line in found), found
