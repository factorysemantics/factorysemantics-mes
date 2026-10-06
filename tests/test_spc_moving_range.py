"""The moving-range half of the chart: the series, its limits, and its rule.

Scott, 2026-10-06: *"so the current simulation runs an I chart. I want an IMR
chart like a real MES should have."* The mean moving range was always computed
here, because sigma is estimated from it. What was missing was the series it is
the mean of, drawn with its own centre line and its own upper limit, and the
one rule a moving-range chart has.

The numbers below are worked out by hand in the docstrings rather than read off
the code, because a test that recomputes what it is testing pins nothing.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import NonConformance, PlantSetting, QualitySpec, SpcSignal
from fsmes.services import plant_settings, quality, spc

#: Twelve readings - the fewest the chart will draw limits from - that step
#: 0.1 either way eleven times and then jump 0.7. Eleven moving ranges:
#: ten of 0.1 and one of 0.7.
TWELVE = [10.0, 10.1, 10.0, 10.1, 10.0, 10.1, 10.0, 10.1, 10.0, 10.1, 10.0, 10.7]

#: The same run without the jump, so the moving-range chart has nothing to say.
QUIET = [10.0, 10.1, 10.0, 10.1, 10.0, 10.1, 10.0, 10.1, 10.0, 10.1, 10.0, 10.1]


def _record(session, values, characteristic="brix"):
    raised = []
    checks = []
    for value in values:
        check, _nc, signals = quality.record_check(
            session, material_code="FG-COLA", characteristic=characteristic,
            value=value, actor="test")
        checks.append(check)
        raised.extend(signals)
    session.flush()
    return checks, raised


def _holds_on(session, written):
    """What this plant raises a hold on, written the way the Configuration page
    writes it - a database row, which is the layer a plant owns."""
    session.add(PlantSetting(section="quality", key="hold_rules",
                             value=written, set_by="test"))
    session.flush()
    plant_settings.forget(session)


def _spc_ncs(session):
    rows = session.scalars(select(NonConformance).order_by(NonConformance.id))
    return [nc for nc in rows if (nc.evidence or {}).get("source") == "spc"]


# ------------------------------------------------- the series and its limits


def test_the_moving_range_series_is_one_shorter_than_the_readings(session):
    """Twelve readings, eleven gaps. The first reading has nothing before it,
    and a zero invented for it would pull every limit on both charts down."""
    _record(session, TWELVE)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert moving["readings"] == 12
    assert moving["n"] == 11
    assert len(moving["points"]) == 11


def test_each_moving_range_names_the_two_readings_it_is_the_gap_between(session):
    """The later reading is the one a person can open, so it is the point's
    own `check`. The earlier one is named too: *which two?* is the first
    question, and "count back one dot" is not an answer."""
    checks, _ = _record(session, TWELVE)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    first = moving["points"][0]
    assert first["check"] == checks[1].id
    assert first["previous_check"] == checks[0].id
    last = moving["points"][-1]
    assert last["check"] == checks[11].id
    assert last["previous_check"] == checks[10].id


def test_the_centre_line_and_upper_limit_are_the_hand_computed_ones(session):
    """Eleven ranges: ten of 0.1 and one of 0.7, so 1.7 in total and a mean
    moving range of 1.7 / 11 = 0.154545..., which rounds to 0.1545.

    The upper limit is D4 times that mean, and D4 at n = 2 is 3.267:
    3.267 x 1.7 / 11 = 5.5539 / 11 = 0.5049. The lower limit is nought,
    because D3 at n = 2 is nought - and nought is written down rather than
    left out, because a chart with no lower line and a chart whose lower line
    is nought look the same drawn and are not the same claim.
    """
    _record(session, TWELVE)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert [p["range"] for p in moving["points"]] == [0.1] * 10 + [0.7]
    assert moving["centre"] == 0.1545
    assert moving["upper"] == 0.5049
    assert moving["lower"] == 0.0


def test_the_sigma_both_charts_use_is_the_mean_of_this_series(session):
    """Not a second opinion drawn beside the first. The individuals limits are
    three times the mean moving range over d2, and d2 at n = 2 is 1.128: the
    centre line of the moving-range chart is the number the individuals chart
    was already standing on."""
    _record(session, TWELVE)
    drawn = spc.chart(session, "FG-COLA", "brix")
    assert drawn["control"]["mean_moving_range"] == drawn["moving_range"]["centre"]
    assert drawn["control"]["sigma"] == pytest.approx(
        drawn["moving_range"]["centre"] / spc.D2_N2, abs=0.0005)


# ---------------------------------------------------------------- the rule


def test_the_range_beyond_the_upper_limit_is_the_one_that_is_flagged(session):
    """0.7 is beyond 0.5049 and each 0.1 is not, so one range of the eleven
    fires and it is the gap between the eleventh and twelfth readings. Pinned
    by *which readings*, not by how many signals there are: a count passes
    just as well when the wrong pair is flagged."""
    checks, _ = _record(session, TWELVE)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert [(s["previous_check"], s["check"]) for s in moving["signals"]] == [
        (checks[10].id, checks[11].id)]
    assert moving["signals"][0]["rule"] == spc.MR_RULE
    assert moving["signals"][0]["value"] == 0.7
    assert moving["stable"] is False


def test_a_run_with_no_jump_in_it_flags_no_range_at_all(session):
    """Eleven ranges of 0.1, a mean of 0.1, an upper limit of 0.3267. A chart
    that flagged one of these is a chart people switch off."""
    _record(session, QUIET)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert moving["centre"] == 0.1
    assert moving["upper"] == 0.3267
    assert moving["signals"] == []
    assert moving["stable"] is True
    assert "in control" in moving["verdict"]


def test_a_moving_range_firing_is_said_in_the_charts_own_verdict(session):
    """A process can sit inside the individuals limits and still jump further
    between consecutive readings than its own variation accounts for. The
    sentence says so rather than reporting *in control* about it."""
    _record(session, TWELVE)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert "out of control" in moving["verdict"]
    assert "of 11 ranges" in moving["verdict"]


# ------------------------------------------------- too few readings to judge


def test_fewer_readings_than_the_chart_draws_limits_from_draws_no_limits(session):
    """Eleven readings is one short of this plant's `spc_min_points`, and the
    moving-range half is as honest about that as the individuals half: no
    centre line, no upper limit, and the same sentence saying why."""
    _record(session, TWELVE[:11])
    drawn = spc.chart(session, "FG-COLA", "brix")
    moving = drawn["moving_range"]
    assert moving["centre"] is None
    assert moving["upper"] is None
    assert moving["lower"] is None
    assert moving["signals"] == []
    # Ten gaps exist and are drawn; what cannot be drawn is a limit to judge
    # them against. The series is still stated, with its total.
    assert moving["n"] == 10
    assert moving["note"] == drawn["note"]
    assert moving["verdict"] == drawn["note"]


def test_one_reading_has_no_moving_range_and_says_so(session):
    """Not a range of nought. Two readings make the first gap."""
    _record(session, [10.0])
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert moving["points"] == []
    assert moving["n"] == 0
    assert "two readings make the first one" in moving["verdict"]


# ------------------------------------------- what the plant chooses to hold on


def test_the_moving_range_rule_is_recorded_on_a_plant_that_has_not_asked_and_holds_nobody(session):
    """The shipped default: recorded like the other four, held by none.

    Decision 0036 is read literally - every rule is drawn *and recorded*, and
    the only thing a plant chooses is which of them raises a hold. The record
    has to be there or the panel behind a pink dot would say nothing fired on
    a flagged reading. What the default spares a plant is the morning of holds
    about jumps it already lived through, which is what would teach it to
    switch the feature off rather than to read it.
    """
    assert spc.MR_RULE not in spc.hold_rules(session)
    _record(session, TWELVE[:11])
    checks, raised = _record(session, [TWELVE[11]])

    mr = [s for s in raised if s["rule"] == spc.MR_RULE]
    assert len(mr) == 1
    # Stated, and stated as not held, rather than left out of the answer.
    assert mr[0]["held"] is False
    assert mr[0]["nonconformance"] is None

    row = session.scalar(select(SpcSignal).where(SpcSignal.rule == spc.MR_RULE))
    assert row is not None
    assert row.check_id == checks[0].id
    assert row.nonconformance_id is None
    # No hold anywhere on this plant for the rule it did not ask about.
    assert [nc for nc in _spc_ncs(session)
            if (nc.evidence or {}).get("rule") == spc.MR_RULE] == []


def test_nothing_earlier_than_the_reading_just_written_is_recorded(session):
    """No back-fill. A plant that upgrades starts recording moving-range
    signals from its next reading; the jumps in its history stay as they were
    judged at the time, which is not at all."""
    _record(session, TWELVE)
    before = [row.window_key for row in session.scalars(
        select(SpcSignal).where(SpcSignal.rule == spc.MR_RULE))]
    assert len(before) == 1
    # The one it recorded is the gap the twelfth reading made, not the ten
    # quiet gaps behind it - and running the rules again adds none of them.
    spec = session.scalar(select(QualitySpec).where(QualitySpec.characteristic == "brix"))
    spc.evaluate(session, spec)
    session.flush()
    assert [row.window_key for row in session.scalars(
        select(SpcSignal).where(SpcSignal.rule == spc.MR_RULE))] == before


def test_the_chart_flags_the_range_whatever_the_plant_holds_on(session):
    """Decision 0036: a rule a plant could switch off the chart would be a
    chart that lies. The firing above was recorded and nobody was called, and
    the dot is still red."""
    _record(session, TWELVE)
    moving = spc.chart(session, "FG-COLA", "brix")["moving_range"]
    assert len(moving["signals"]) == 1
    # And the screen can say why it opened nothing.
    assert moving["signals"][0]["held"] is False
    assert moving["signals"][0]["nonconformance"] is None


def test_a_plant_that_turns_the_rule_on_gets_a_hold_naming_both_readings(session):
    """The same kind of non-conformance every other rule raises, with the two
    readings the range is the gap between as its evidence.

    `hold_rules = [5]` is the plant in this test: the moving range is what it
    wants to be called about. A jump this size trips the individuals rules as
    well - at n = 2 one usually does - and they go on being drawn and recorded
    without calling anybody, which is what decision 0036 asks of a rule a
    plant has not chosen.
    """
    _holds_on(session, "5")
    assert spc.hold_rules(session) == (spc.MR_RULE,)
    checks, _ = _record(session, QUIET)
    _, raised = _record(session, [10.8])

    mr = [s for s in raised if s["rule"] == spc.MR_RULE]
    assert len(mr) == 1
    assert mr[0]["held"] is True
    assert mr[0]["nonconformance"] is not None
    assert all(s["held"] is False for s in raised if s["rule"] != spc.MR_RULE)

    row = session.scalar(select(SpcSignal).where(SpcSignal.rule == spc.MR_RULE))
    assert row.what == spc.MR_WHAT
    # Both readings, in order, on the record the MES acted on, and the limits
    # the range was judged against rather than the individuals chart's.
    assert [p["value"] for p in row.window["points"]] == [10.1, 10.8]
    assert row.window["points"][0]["check"] == checks[11].id
    assert row.window["centre"] == pytest.approx(1.8 / 12, abs=0.0001)

    hold = [nc for nc in _spc_ncs(session)
            if (nc.evidence or {}).get("rule") == spc.MR_RULE]
    assert len(hold) == 1
    assert "rule 5" in hold[0].description
    assert "mean moving range" in hold[0].description
    assert hold[0].code == mr[0]["nonconformance"]


def test_judging_the_same_pair_of_readings_again_raises_nothing_new(session):
    """A firing is identified by the readings it judged, so the same gap is
    the same finding however many times the rules are run over it."""
    _holds_on(session, "5")
    _record(session, QUIET)
    _record(session, [10.8])
    rows = select(SpcSignal).where(SpcSignal.rule == spc.MR_RULE)
    before = [row.window_key for row in session.scalars(rows)]
    assert len(before) == 1
    spec = session.scalar(select(QualitySpec).where(QualitySpec.characteristic == "brix"))
    spc.evaluate(session, spec)
    session.flush()
    assert [row.window_key for row in session.scalars(rows)] == before
