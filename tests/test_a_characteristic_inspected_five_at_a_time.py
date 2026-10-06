"""A characteristic can be inspected five pieces at a time.

The identity of the feature, pinned where it can break. Two things make a
sampled characteristic different from the one-piece-at-a-time chart this
product has always drawn, and each of them is the kind of mistake nobody
would ever see on the screen:

* the **arithmetic** is X-bar and R, not individuals and moving range, so the
  limits are `X̿ ± A2·R̄` and the sigma capability is computed from is
  `R̄ / d2`. Every figure below was worked out by hand from a published
  constants table before it was written down here, which is the only way a
  test of arithmetic is worth having;
* the **observation** is the sample, not the reading. The rules run once, on
  the mean. A single bottle that would have tripped rule one on its own does
  not, because nobody looked at one bottle.

Decision record 0040. Each test is named after the behaviour it pins.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import NonConformance, PlantSetting, QualityCheck, QualitySample, SpcSignal
from fsmes.services import Invalid, WrongSampleSize, plant_settings, quality, spc

# ------------------------------------------------------------- the fixture
#
# Ten samples of five, built so every figure can be checked on paper.
#
# Each sample is five readings evenly spread either side of its own mean:
# `m-1, m-0.5, m, m+0.5, m+1`. So every sample's range is exactly 2.0, and
# R-bar is exactly 2.0 however many of them there are. The ten means are
# 100.0 with a tenth either side in turn, and they sum to exactly 1000.0, so
# X-bar-bar is exactly 100.0.
#
# From the n = 5 row of the subgroup table (A2 = 0.577, D3 = 0, D4 = 2.114,
# d2 = 2.326):
#
#   centre        = 100.0
#   A2 * R-bar    = 0.577 * 2.0            = 1.154
#   upper, lower  = 100.0 +/- 1.154         = 101.154, 98.846
#   sigma of a mean = 1.154 / 3             = 0.3847  (to four places)
#   sigma within  = 2.0 / 2.326             = 0.8598  (to four places)
#   R chart       = centre 2.0, upper 2.114 * 2.0 = 4.228, lower 0
#
# Note what the two sigmas say: a mean of five varies less than a bottle
# does (0.3847 against 0.8598). Drawing one chart with the other's sigma is
# the error this whole module exists to prevent.
MEANS = [100.0, 100.1, 99.9, 100.0, 100.1, 99.9, 100.0, 100.1, 99.9, 100.0]
TWELVE = [*MEANS, 100.1, 99.9]


def sample_at(mean: float) -> list[float]:
    """Five readings whose mean is `mean` and whose range is exactly 2.0."""
    return [mean - 1.0, mean - 0.5, mean, mean + 0.5, mean + 1.0]


def _spec(session, *, size: int | None = 5, low=90.0, high=110.0,
          characteristic="fill_height"):
    """A sampled characteristic on the demo plant's finished good.

    The demo plant deliberately has none: a sampling plan is master data, and
    seeding one onto a characteristic fifteen other tests read would change
    the chart they assert on. So each test here writes its own.
    """
    return quality.create_spec(
        session, material_code="FG-COLA", characteristic=characteristic,
        unit="mm", min_value=low, max_value=high, sample_size=size)


def _take(session, spec, means, *, size=5):
    """One sample per mean, each of `size` readings, through the write path."""
    signals = []
    for mean in means:
        values = sample_at(mean) if size == 5 else [mean] * size
        _s, _c, _nc, raised = quality.record_sample(
            session, material_code="FG-COLA", characteristic=spec.characteristic,
            values=values, actor="test")
        signals.extend(raised)
    session.flush()
    return signals


def _lookup(session):
    return quality.get_spec(session, "FG-COLA", "fill_height")


def _spc_ncs(session):
    return [nc for nc in session.scalars(select(NonConformance).order_by(NonConformance.id))
            if (nc.evidence or {}).get("source") == "spc"]


def _lower_the_bar(session, points: int) -> None:
    """Draw limits from `points` samples rather than this plant's twelve.

    Twelve samples of five is sixty readings to write for a test about ten
    samples; the number is the plant's (`[quality] spc_min_points`) and
    moving it is a thing a plant may do, so a test may do it too.
    """
    plant_settings.write(session, domain="quality", key="spc_min_points",
                         written=str(points), actor="test")
    session.flush()


# -------------------------------------------------- a plan on the spec

def test_a_specification_with_no_plan_is_charted_exactly_as_it_always_was(session):
    """The promise to every characteristic already on every plant: null is
    *no plan stated*, and it draws the individuals chart it drew yesterday."""
    spec = _spec(session, size=None, characteristic="cap_torque")
    assert spec.sample_size is None
    assert spc.plan_size(spec) == 1
    assert spc.chart(session, "FG-COLA", "cap_torque")["kind"] == "imr"


def test_a_plan_of_one_means_one_piece_at_a_time_too(session):
    """Somebody will write 1 rather than leave it empty, and they mean the
    same thing by it."""
    assert spc.plan_size(_spec(session, size=1, characteristic="cap_torque")) == 1


def test_a_plan_bigger_than_this_product_can_chart_is_refused_when_it_is_written(session):
    """Not when somebody opens the chart and finds it blank. Eleven is past
    the end of the constants table, and the sentence says what is on offer."""
    with pytest.raises(Invalid) as refused:
        _spec(session, size=11, characteristic="cap_torque")
    assert "2 to 10" in str(refused.value)


# ------------------------------------------------ the sample is the record

def test_a_sample_of_five_is_five_stored_readings_tied_together(session):
    """Five readings, five rows in `quality_checks`, one `quality_samples`
    row over them. Everything in this product that counts checks or lists
    measurements goes on telling the truth, and the arithmetic that needs
    the subgroup gets it from the key."""
    spec = _spec(session)
    sample, checks, nc, _signals = quality.record_sample(
        session, material_code="FG-COLA", characteristic="fill_height",
        values=sample_at(100.0), actor="JO")
    session.flush()

    assert nc is None
    assert len(checks) == 5
    assert [c.sample_id for c in checks] == [sample.id] * 5
    stored = list(session.scalars(
        select(QualityCheck).where(QualityCheck.spec_id == spec.id)
        .order_by(QualityCheck.id)))
    assert [c.value for c in stored] == sample_at(100.0)
    assert session.get(QualitySample, sample.id).checked_by == "JO"


def test_five_readings_are_five_different_stored_readings_not_one_averaged_row(session):
    """The schema temptation: five numbers in one row. It would have been the
    smaller schema and the larger lie - the measurements card, the inspection
    history, the certificate and every count of *how many checks has this
    plant recorded* each read `quality_checks`."""
    _spec(session)
    _take(session, _lookup(session), [100.0, 100.1])
    assert session.scalar(select(QualityCheck).where(
        QualityCheck.value == 99.0)) is not None, "the lowest reading is on record"
    assert len(list(session.scalars(select(QualityCheck)))) == 10


def test_a_sample_with_the_wrong_number_of_readings_is_refused_with_both_numbers(session):
    """Fewer is not a smaller sample. A mean of three charted against limits
    built for five is wrong in a way nobody would see on the screen, so the
    refusal names the plan and what arrived."""
    _spec(session)
    with pytest.raises(WrongSampleSize) as refused:
        quality.record_sample(session, material_code="FG-COLA",
                              characteristic="fill_height",
                              values=[100.0, 100.5, 101.0], actor="test")
    said = str(refused.value)
    assert "5 pieces" in said and "3 reading" in said


def test_one_reading_of_a_sampled_characteristic_is_refused_and_names_the_plan(session):
    """A single bottle is not a point on a chart of means, and storing it as
    one would quietly widen every limit beside it. The sentence says where
    the five readings go instead."""
    _spec(session)
    with pytest.raises(Invalid) as refused:
        quality.record_check(session, material_code="FG-COLA",
                             characteristic="fill_height", value=100.0, actor="test")
    said = str(refused.value)
    assert "5 pieces at a time" in said and "/quality/samples" in said


def test_a_sample_posted_to_a_characteristic_inspected_one_at_a_time_is_refused(session):
    """And the other way round, with the way in named."""
    _spec(session, size=None, characteristic="cap_torque")
    with pytest.raises(Invalid) as refused:
        quality.record_sample(session, material_code="FG-COLA",
                              characteristic="cap_torque",
                              values=[1.0, 2.0], actor="test")
    assert "/quality/checks" in str(refused.value)


# --------------------------------------------------------- the arithmetic

def test_the_limits_of_ten_samples_of_five_are_the_ones_computed_by_hand(session):
    """The figures at the top of this file, to four places, from the n = 5
    row of the published table. If this test fails the chart is lying about
    where the process is, and no screen could tell."""
    _lower_the_bar(session, 10)
    spec = _spec(session)
    _take(session, spec, MEANS)

    chart = spc.chart(session, "FG-COLA", "fill_height")
    assert chart["kind"] == "xbar_r"
    assert chart["sample_size"] == 5
    assert (chart["n"], chart["readings"]) == (10, 50)

    control = chart["control"]
    assert control["centre"] == 100.0
    assert control["mean_range"] == 2.0
    assert (control["upper"], control["lower"]) == (101.154, 98.846)
    assert control["sigma"] == 0.3847, "three of these is A2 * R-bar, not three bottles"
    assert control["sigma_within"] == 0.8598, "R-bar over d2 - the process, not the mean"
    assert control["constants"] == {"a2": 0.577, "d3": 0.0, "d4": 2.114, "d2": 2.326}


def test_the_range_chart_is_the_second_chart_and_has_no_lower_limit_at_five(session):
    """Centre R-bar, upper D4 * R-bar. D3 is zero below n = 7, so there is no
    lower limit: five identical bottles are unremarkable, not a signal."""
    _lower_the_bar(session, 10)
    _take(session, _spec(session), MEANS)
    ranges = spc.chart(session, "FG-COLA", "fill_height")["control"]["range_chart"]
    assert ranges == {"centre": 2.0, "upper": 4.228, "lower": 0.0}


def test_capability_is_computed_from_the_spread_within_samples(session):
    """Not from the spread of the means, which is smaller by root five. A
    sampled chart that used the plotted points' sigma would claim a Cpk the
    process never had - here it would claim 3.877 as 8.665."""
    _lower_the_bar(session, 10)
    _take(session, _spec(session, low=90.0, high=110.0), MEANS)
    chart = spc.chart(session, "FG-COLA", "fill_height")
    cap = chart["capability"]
    assert cap["sigma"] == 0.8598, "R-bar over d2, the process's own spread"
    assert cap["cp"] == round(20 / (6 * 2.0 / 2.326), 3) == 3.877
    # The sigma of a mean of five is 0.3847. Had capability been computed from
    # it, Cp would read 8.665 - more than twice as capable as the process is.
    assert round(20 / (6 * chart["control"]["sigma"]), 3) == 8.665
    assert cap["readings"] == 50, "Pp and Ppk describe the pieces, not the means"


def test_the_fewest_points_a_sampled_chart_draws_from_counts_samples(session):
    """Twelve means twelve samples, not twelve bottles. Eleven samples is
    fifty-five readings and still not a chart, and the note says so in the
    word for what it counted."""
    spec = _spec(session)
    _take(session, spec, MEANS)
    chart = spc.chart(session, "FG-COLA", "fill_height")
    assert chart["control"] is None
    assert chart["readings"] == 50
    assert chart["note"].startswith("10 samples; control limits need at least 12")


def test_a_chart_carries_each_sample_with_its_mean_its_range_and_its_readings(session):
    """So a reader can see the five bottles behind a point without another
    round trip - the chart's tooltips and its empty state both name them
    before anybody opens the panel that holds the rest."""
    _lower_the_bar(session, 10)
    _take(session, _spec(session), MEANS)
    chart = spc.chart(session, "FG-COLA", "fill_height")
    first = chart["samples"][0]
    assert (first["n"], first["mean"], first["range"]) == (5, 100.0, 2.0)
    assert [r["value"] for r in first["readings"]] == sample_at(100.0)
    assert len(chart["samples"]) == 10
    assert [p["sample"] for p in chart["points"]] == [s["sample"] for s in chart["samples"]]


def test_samples_stored_under_a_different_plan_are_left_out_and_counted(session):
    """A plan that changes leaves samples of the old size behind. Charting
    those with this size's constants would produce limits that are simply
    wrong, so they are set aside - and the chart says how many rather than
    letting a reader wonder where they went."""
    _lower_the_bar(session, 3)
    spec = _spec(session, size=3)
    _take(session, spec, [100.0, 100.0, 100.5], size=3)
    spec.sample_size = 5
    session.flush()
    _take(session, spec, MEANS[:3])
    chart = spc.chart(session, "FG-COLA", "fill_height")
    assert chart["n"] == 3
    assert chart["set_aside"] == 3
    assert "3 stored sample(s) left out" in (chart.get("note") or chart["verdict"]["note"] or "")


# ------------------------------------------------- the rules run on the mean

def test_a_reading_that_would_trip_rule_one_alone_does_not_trip_it_in_a_sample(session):
    """The identity of the feature. 103.0 is more than three within-process
    sigma above the centre - on an individuals chart it is rule one. Here it
    is one bottle of five whose mean, 100.6, sits inside the limits of a
    chart of means, and nobody looked at one bottle. Nothing fires."""
    spec = _spec(session)
    _take(session, spec, TWELVE)
    _sample, _checks, nc, signals = quality.record_sample(
        session, material_code="FG-COLA", characteristic="fill_height",
        values=[100.0, 100.0, 100.0, 100.0, 103.0], actor="test")
    session.flush()

    control = spc.chart(session, "FG-COLA", "fill_height")["control"]
    assert 103.0 - control["centre"] > 3 * control["sigma_within"], "rule 1 on its own"
    assert control["lower"] < 100.6 < control["upper"], "settled as a mean of five"
    assert signals == []
    assert nc is None


def test_a_sample_whose_mean_is_beyond_the_limit_trips_rule_one_on_the_mean(session):
    """And the value the signal reports is the mean, because that is the
    point that went out."""
    spec = _spec(session)
    _take(session, spec, TWELVE)
    signals = _take(session, spec, [102.0], size=5)
    assert [s["rule"] for s in signals] == [1]
    assert signals[0]["value"] == 102.0
    assert len(_spc_ncs(session)) == 1


def test_one_sample_is_one_look_at_the_process_and_at_most_one_hold(session):
    """Five readings outside the specification are one finding, naming the
    sample and how many of its readings were outside - not five findings and
    not five visits to the rules."""
    calls = []
    _spec(session, low=99.5, high=100.5)
    _real = spc.evaluate

    def counted(session_, spec_, **kwargs):
        calls.append(kwargs.get("since_sample"))
        return _real(session_, spec_, **kwargs)

    import fsmes.services.spc as spc_module
    spc_module.evaluate = counted
    try:
        sample, checks, nc, _ = quality.record_sample(
            session, material_code="FG-COLA", characteristic="fill_height",
            values=sample_at(100.0), actor="test")
    finally:
        spc_module.evaluate = _real
    session.flush()

    assert calls == [sample.id], "once per sample, on the sample just taken"
    assert len(checks) == 5
    assert nc is not None
    assert len(list(session.scalars(select(NonConformance)))) == 1
    assert f"2 of 5 readings in sample {sample.id}" in nc.description
    assert nc.evidence["sample"] == sample.id
    assert nc.evidence["values"] == sample_at(100.0)


def test_a_sample_range_beyond_the_range_limit_is_rule_five(session):
    """The four rules judge where the mean sat, against limits computed from
    the mean range - so a sample spread much wider than the rest has widened
    those limits itself and can report a mean that looks perfectly settled
    inside limits it inflated. The spread is the finding.

    It is **rule 5**, the number a range beyond its upper limit has on either
    chart: the moving range between two readings on an individuals chart, the
    spread inside one sample here. One statement about a process, one number
    for it - a plant that wanted to be called about a range would otherwise
    have had to say so twice.

    Recorded on every plant whatever the plant holds on (decision 0036); the
    hold is the plant's choice, and this plant has not made it, so the firing
    is reported and reported as unheld.
    """
    spec = _spec(session)
    _take(session, spec, TWELVE)
    _s, _c, _nc, signals = quality.record_sample(
        session, material_code="FG-COLA", characteristic="fill_height",
        values=[95.0, 105.0, 100.0, 100.0, 100.0], actor="test")
    session.flush()

    assert [s["rule"] for s in signals] == [spc.RANGE_RULE] == [5]
    assert signals[0]["value"] == 10.0
    # The default plant holds on rules 1 to 4, so this one is drawn and
    # recorded and calls nobody - and says so rather than leaving a reader to
    # wonder whether anybody was told.
    assert 5 not in spc.hold_rules(session)
    assert signals[0]["held"] is False
    assert signals[0]["nonconformance"] is None
    assert session.scalar(select(SpcSignal).where(SpcSignal.rule == 5)) is not None, \
        "recorded on every plant: decision 0036 is literal"
    chart = spc.chart(session, "FG-COLA", "fill_height")
    assert chart["rules"] == [1, 2, 3, 4, 5]
    assert "always_hold_rules" not in chart, "no rule answers to nobody's list"


def test_a_plant_that_holds_on_rule_five_holds_on_a_sample_range(session):
    """The other half of the choice, and the one the bottling pack makes.

    `hold_rules` naming 5 is a plant saying *call me about a range beyond its
    limit*, and it means that on whichever chart the characteristic is on. The
    hold is the same kind any other rule raises, and its evidence names the
    sample and its five readings.
    """
    session.add(PlantSetting(section="quality", key="hold_rules",
                             value="1,2,3,4,5", set_by="test"))
    session.flush()
    plant_settings.forget(session)
    assert spc.hold_rules(session) == (1, 2, 3, 4, 5)

    spec = _spec(session)
    _take(session, spec, TWELVE)
    _s, _c, _nc, signals = quality.record_sample(
        session, material_code="FG-COLA", characteristic="fill_height",
        values=[95.0, 105.0, 100.0, 100.0, 100.0], actor="test")
    session.flush()

    ranged = [s for s in signals if s["rule"] == spc.RANGE_RULE]
    assert len(ranged) == 1
    assert ranged[0]["held"] is True
    nc = session.scalar(select(NonConformance).where(
        NonConformance.code == ranged[0]["nonconformance"]))
    assert nc is not None
    assert "rule 5" in nc.description
    assert nc.evidence["sample_size"] == 5
    assert len(nc.evidence["readings"]) == 5


def test_a_settled_run_of_samples_raises_nothing(session):
    """The baseline. A chart that holds a process behaving itself is a chart
    people switch off."""
    assert _take(session, _spec(session), TWELVE) == []
    assert _spc_ncs(session) == []


def test_the_hold_a_rule_raises_names_the_sample_and_all_five_readings(session):
    """A supervisor reading the hold should not have to go and find the
    bottles. The sample id, the five values, and the mean that went out."""
    spec = _spec(session)
    _take(session, spec, TWELVE)
    signals = _take(session, spec, [102.0], size=5)
    nc = _spc_ncs(session)[0]
    assert nc.code == signals[0]["nonconformance"]
    assert nc.evidence["sample_size"] == 5
    assert nc.evidence["sample"] == max(
        s.id for s in session.scalars(select(QualitySample)))
    assert nc.evidence["readings"] == sample_at(102.0)
    assert nc.evidence["mean"] == 102.0
    assert len(nc.evidence["checks"]) == 5


# ------------------------------------------------------- over the wire
#
# The simulated floor and a bench terminal both reach this plant over HTTP,
# so the status codes are part of the contract and not an implementation
# detail: 422 is "your sample is the wrong size, fix the request", 400 is
# "this characteristic is not inspected that way at all".

def test_a_sample_size_can_be_written_onto_a_specification_over_the_api(admin, session):
    """One field. The configuration screen posts the same body."""
    made = admin.post("/quality/specs", json={
        "material": "FG-COLA", "characteristic": "fill_height", "unit": "mm",
        "min": 139.0, "max": 145.0, "sample_size": 5})
    assert made.status_code == 201, made.text
    listed = admin.get("/quality/specs").json()
    mine = [s for s in listed["items"] if s["characteristic"] == "fill_height"]
    assert mine and mine[0]["sample_size"] == 5
    assert all(s["sample_size"] in (None, 1) for s in listed["items"]
               if s["characteristic"] != "fill_height"), "nothing else was given a plan"


def test_a_specification_with_no_sample_size_posted_stays_one_at_a_time(admin):
    """The field is new, so every caller that has never heard of it has to go
    on working, and the characteristic it writes has to chart as it did."""
    admin.post("/quality/specs", json={
        "material": "FG-COLA", "characteristic": "cap_torque", "unit": "Nm",
        "min": 1.0, "max": 2.0})
    listed = admin.get("/quality/specs").json()["items"]
    assert [s["sample_size"] for s in listed if s["characteristic"] == "cap_torque"] == [None]


def test_posting_a_sample_answers_with_the_two_figures_the_chart_plots(client, session):
    """A bench that posted a sample should not have to compute the mean and
    the range itself to know what it just put on the chart."""
    _spec(session)
    posted = client.post("/quality/samples", json={
        "material": "FG-COLA", "characteristic": "fill_height",
        "values": sample_at(100.0), "gauge": None})
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert (body["n"], body["mean"], body["range"]) == (5, 100.0, 2.0)
    assert len(body["checks"]) == 5 and len(set(body["checks"])) == 5
    assert body["non_conformance"] is None


def test_a_sample_of_the_wrong_size_is_a_422_with_a_plain_sentence(client, session):
    """Not a stack trace and not a silent average of three."""
    _spec(session)
    refused = client.post("/quality/samples", json={
        "material": "FG-COLA", "characteristic": "fill_height",
        "values": [100.0, 100.5, 101.0]})
    assert refused.status_code == 422, refused.text
    said = refused.json()["detail"]
    assert "5 pieces" in said and "3 reading" in said


def test_a_single_check_on_a_sampled_characteristic_is_a_400_naming_the_size(client, session):
    """The other way a caller gets it wrong, with the way in named."""
    _spec(session)
    refused = client.post("/quality/checks", json={
        "material": "FG-COLA", "characteristic": "fill_height", "value": 100.0})
    assert refused.status_code == 400, refused.text
    assert "5 pieces at a time" in refused.json()["detail"]


def test_the_chart_endpoint_says_which_kind_of_chart_this_is(client, session):
    """In a word, so a screen does not have to infer the chart type from
    which keys happen to be present - which is how means get drawn as if they
    were single readings."""
    _lower_the_bar(session, 10)
    _take(session, _spec(session), MEANS)
    body = client.get("/quality/spc/FG-COLA/fill_height").json()
    assert body["kind"] == "xbar_r"
    assert body["sample_size"] == 5
    assert (body["n"], body["readings"]) == (10, 50)
    assert len(body["samples"]) == 10


# ------------------------------------------------------- the migration
#
# Run on a real SQLite file, both ways. A plant upgrades by running this
# chain, and a plant that cannot roll a revision back is a plant nobody dares
# upgrade.

REVISION = "b9f31c7a4e50"
PARENT = "c7a2e94b16d3"


def _shape(url: str, revision: str) -> dict[str, list[str]]:
    from alembic import command

    from fsmes import schema as schema_mod

    command.upgrade(schema_mod.alembic_config(url), revision)
    return schema_mod.database_shape(url)


def test_the_migration_adds_three_nullable_things_and_takes_them_away_again(tmp_path):
    """Up: a column on the specification, a table for the samples, a key on
    the readings. Down: all three gone and the parent's schema back, because
    a revision that cannot be rolled back is one nobody dares run."""
    from alembic import command

    from fsmes import schema as schema_mod

    db = tmp_path / "sampled.db"
    url = f"sqlite:///{db}"
    before = _shape(url, PARENT)
    assert "quality_samples" not in before
    assert "sample_size" not in before["quality_specs"]

    after = _shape(url, REVISION)
    assert "quality_samples" in after
    assert "sample_size" in after["quality_specs"]
    assert "sample_id" in after["quality_checks"]

    command.downgrade(schema_mod.alembic_config(url), PARENT)
    assert schema_mod.database_shape(url) == before


def test_the_migration_backfills_nothing(tmp_path):
    """Every specification already on every plant keeps a null sample size and
    every reading keeps a null sample. Inferring either would be inventing a
    sampling plan nobody wrote down, and a chart drawn from an invented plan
    is wrong in exactly the way this feature exists to avoid."""
    import sqlite3

    from alembic import command

    from fsmes import schema as schema_mod

    db = tmp_path / "existing.db"
    url = f"sqlite:///{db}"
    command.upgrade(schema_mod.alembic_config(url), PARENT)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO materials (code, name, unit, type) "
                     "VALUES ('M', 'M', 'ea', 'finished_good')")
        conn.execute("INSERT INTO quality_specs (material_id, characteristic, unit, "
                     "min_value, max_value) VALUES (1, 'brix', 'Bx', 10.0, 12.0)")
        conn.execute("INSERT INTO quality_checks (spec_id, value, result, ts, checked_by) "
                     "VALUES (1, 11.0, 'pass', '2026-10-06T08:00:00', 'JO')")

    command.upgrade(schema_mod.alembic_config(url), REVISION)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT sample_size FROM quality_specs").fetchall() == [(None,)]
        assert conn.execute("SELECT sample_id FROM quality_checks").fetchall() == [(None,)]
        assert conn.execute("SELECT count(*) FROM quality_samples").fetchone() == (0,)
