"""A characteristic the plant counts rather than measures.

The identity of the feature, pinned where it can break. Three things make a
counted characteristic different from both charts this product drew before
it, and each of them is the kind of mistake nobody would ever see on the
screen:

* the **record** is two whole numbers and a list of what was wrong - how many
  were looked at, how many were sent back, which defects - and not a reading
  of anything. Ten thousand bottles inspected is one row. A row per unit with
  "pass" in it was refused in decision 0027 and is refused here;
* the **limits move with the lot**. Sigma at a point is
  `sqrt(p̄(1-p̄)/nᵢ)` for *that point's own* n, so a lot of a hundred gets
  wide limits and a lot of two and a half thousand gets narrow ones. Every
  figure below was worked out by hand before it was written down, which is
  the only way a test of arithmetic is worth having;
* **one rule runs.** Rules 2, 3 and 4 count points inside sigma bands whose
  width changes with every lot, and rule 5 judges a range a share has none
  of. The chart says which rules it did not run and why, and it gives no
  capability figure at all.

Decision record 0041, amending 0040. Each test is named after the behaviour
it pins.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import NonConformance, QualityCheck, QualityCount
from fsmes.services import Invalid, plant_settings, quality, spc

# ------------------------------------------------------------- the fixture
#
# Ten inspected lots, built so every figure can be checked on paper.
#
# The sizes are perfect squares and all divisible by a hundred, and every lot
# sends back exactly a tenth of what it looked at. So:
#
#   pooled centre p-bar = 1110 / 11100            = 0.1 exactly
#   sqrt(p-bar * (1 - p-bar)) = sqrt(0.09)        = 0.3 exactly
#   sigma at n            = 0.3 / sqrt(n)
#
# which comes out exact at every size the fixture uses:
#
#     n     sigma     lower    upper
#   100     0.03      0.01     0.19
#   400     0.015     0.055    0.145
#   900     0.01      0.07     0.13
#  1600     0.0075    0.0775   0.1225
#  2500     0.006     0.082    0.118
#
# Note what that says: the same share - a tenth sent back - sits a third of
# the way to the limit on a lot of a hundred and dead on the centre of a lot
# of two and a half thousand, and one straight pair of lines across both
# would have flagged the small lots and excused the big ones. That is the
# error this whole module exists to prevent.
SIZES = [900, 400, 2500, 900, 1600, 100, 900, 400, 900, 2500]
SETTLED = [(n, n // 10) for n in SIZES]
CENTRE = 0.1
#: The sizes above, with the band each one gets at a centre of 0.1. Worked
#: out by hand in the table above and written down once.
BANDS = {100: (0.03, 0.01, 0.19), 400: (0.015, 0.055, 0.145),
         900: (0.01, 0.07, 0.13), 1600: (0.0075, 0.0775, 0.1225),
         2500: (0.006, 0.082, 0.118)}

#: The small lot that goes wrong, and the compensation that keeps the centre
#: exactly where it was. 20 of 100 is a share of 0.2, past that lot's own
#: upper limit of 0.19; ten fewer rejects on the last lot of 2500 puts it at
#: 0.096, which is well inside its own [0.082, 0.118]. The totals are
#: unchanged, so the centre line and every band stay exactly as above and the
#: only thing that moved is the one lot.
SMALL_LOT, BIG_LOT = 5, 9
HIGH = [*SETTLED]
HIGH[SMALL_LOT] = (100, 20)
HIGH[BIG_LOT] = (2500, 240)

#: And the other direction. Nothing sent back out of a hundred is a share of
#: nought, below that lot's own lower limit of 0.01 - which is a signal and
#: not good news: either the process genuinely changed or the inspection
#: missed things.
LOW = [*SETTLED]
LOW[SMALL_LOT] = (100, 0)
LOW[BIG_LOT] = (2500, 260)

#: Ten lots of eighty-one, nine of them sending back eight and one sending
#: back nine. The centre is 81/810 = 0.1 exactly again, and sigma is
#: 0.3 / 9 = 1/30, so three sigma is 0.1 and the lower limit lands exactly on
#: nought. No lot, however clean, can fire rule 1 downward on this chart.
TINY = [(81, 8)] * 9 + [(81, 9)]


def _spec(session, *, characteristic="sent_back", counted=True, **kw):
    """A counted characteristic on the demo plant's finished good.

    The demo plant deliberately has none: whether a characteristic is counted
    is master data, and seeding one onto this material would change the
    charts a dozen other tests assert on. So each test here writes its own.
    """
    return quality.create_spec(
        session, material_code="FG-COLA", characteristic=characteristic,
        unit="", counted=counted, actor="test", **kw)


def _count(session, spec, n, k, *, lot="L-01", defects=None, **kw):
    """One inspected lot through the write path."""
    count, nc, signals = quality.record_count(
        session, material_code="FG-COLA", characteristic=spec.characteristic,
        inspected=n, rejected=k, lot_ref=lot, defects=defects, actor="QI-DAY",
        **kw)
    session.flush()
    return count, nc, signals


def _lots(session, spec, pairs):
    """One count per `(inspected, rejected)` pair, through the write path."""
    signals = []
    for i, (n, k) in enumerate(pairs, start=1):
        _c, _nc, raised = _count(session, spec, n, k, lot=f"L-{i:02d}")
        signals.extend(raised)
    return signals


def _chart(session, spec=None):
    return spc.chart(session, "FG-COLA", (spec or _lookup(session)).characteristic)


def _lookup(session, characteristic="sent_back"):
    return quality.get_spec(session, "FG-COLA", characteristic)


def _spc_ncs(session):
    return [nc for nc in session.scalars(select(NonConformance).order_by(NonConformance.id))
            if (nc.evidence or {}).get("source") == "spc"]


def _lower_the_bar(session, points: int) -> None:
    """Draw limits from `points` lots rather than this plant's twelve.

    The number is the plant's (`[quality] spc_min_points`) and moving it is a
    thing a plant may do, so a test about ten lots may do it too.
    """
    plant_settings.write(session, domain="quality", key="spc_min_points",
                         written=str(points), actor="test")
    session.flush()


def _ten(session, pairs=None):
    """The fixture: a counted spec, a bar of ten, and ten lots on it."""
    _lower_the_bar(session, 10)
    spec = _spec(session)
    signals = _lots(session, spec, pairs or SETTLED)
    return spec, signals


# ------------------------------------------- the declaration on the spec

def test_a_specification_that_says_counted_is_charted_as_a_p_chart(session):
    """One word on the specification decides it, exactly as 0040 decided the
    other two. There is no chart-type field for anybody to set to something
    the data cannot support."""
    assert spc.chart_kind(_spec(session)) == "p"


def test_a_specification_that_says_nothing_is_charted_exactly_as_it_always_was(session):
    """Which is every specification on every plant today. The field is new,
    so null has to go on meaning *measured*."""
    spec = _spec(session, characteristic="cap_torque", counted=None,
                 min_value=1.0, max_value=2.0)
    assert spec.counted is None
    assert spc.chart_kind(spec) == "imr"


def test_a_counted_characteristic_is_not_also_inspected_five_at_a_time(session):
    """Refused where it is written, not where it is drawn. A count has no
    readings to average, so there would be no subgroup to chart - and two
    fields that can disagree about what a chart is are how a chart comes to
    lie."""
    with pytest.raises(Invalid) as refused:
        _spec(session, sample_size=5)
    assert "no readings to average" in str(refused.value)


def test_a_counted_characteristic_carries_no_specification_limits(session):
    """A minimum and a maximum are a tolerance on a measurement. What is
    recorded here is how many of how many went back, and a limit in a unit of
    measure has nothing to judge."""
    with pytest.raises(Invalid) as refused:
        _spec(session, min_value=0.0, max_value=0.05)
    assert "no minimum or maximum" in str(refused.value)


def test_a_counted_chart_has_no_unit_and_no_specification_lines(session):
    """A share has no unit of measure. Empty rather than "%", because the
    numbers plotted are shares and labelling them with a percent sign is how
    a chart comes to be drawn a hundred times too high."""
    _ten(session)
    chart = _chart(session)
    assert chart["unit"] == ""
    assert (chart["lower_spec"], chart["upper_spec"]) == (None, None)
    assert chart["sample_size"] is None, "stated as null, not left out"


# ------------------------------------------------ the count is the record

def test_ten_thousand_bottles_looked_at_is_one_row(session):
    """The whole shape of the feature in one assertion. Two whole numbers,
    one row, and not one reading anywhere - decision 0027 refused a
    `quality_checks` row per unit and the refusal stands."""
    spec = _spec(session)
    count, _nc, _signals = _count(session, spec, 10000, 120)
    assert list(session.scalars(select(QualityCount))) == [count]
    assert (count.inspected, count.rejected) == (10000, 120)
    assert session.scalars(select(QualityCheck)).all() == [], "nothing was measured"


def test_a_count_names_the_lot_it_was_taken_on_in_the_plants_own_words(session):
    """`lot_ref` is text because text is what the plant has. A count nobody
    can tie back to product is a number with no subject."""
    spec = _spec(session)
    count, _nc, _signals = _count(session, spec, 900, 90, lot="WO-1007-Q03")
    assert count.lot_ref == "WO-1007-Q03"
    assert count.lot_id is None, "this plant does not ship lots; that is not an error"


def test_a_count_with_no_lot_name_is_refused(session):
    with pytest.raises(Invalid) as refused:
        _count(session, _spec(session), 900, 90, lot="   ")
    assert "number with no subject" in str(refused.value)


def test_more_sent_back_than_were_looked_at_is_refused_with_both_numbers(session):
    """A share above one is not a number this process can produce: it is a
    typo, or two stretches added up wrong. Refused rather than stored, so no
    dot ever lands off the top of a chart nobody can explain."""
    with pytest.raises(Invalid) as refused:
        _count(session, _spec(session), 100, 101)
    said = str(refused.value)
    assert "101" in said and "100" in said and "above one" in said


def test_a_count_of_nothing_looked_at_is_refused(session):
    """Nought inspected has no share in it. On the simulated floor this is a
    quarter of an hour when the machine was stopped, and a point at nought
    rejects for it would say the plant was perfect while it was down."""
    with pytest.raises(Invalid) as refused:
        _count(session, _spec(session), 0, 0)
    assert "not a lot anybody inspected" in str(refused.value)


def test_a_count_posted_to_a_measured_characteristic_is_refused_and_names_the_way_in(session):
    """The commonest mistake a bench terminal can make, answered with the
    route that would have worked."""
    spec = _spec(session, characteristic="fill_height", counted=None,
                 min_value=139.0, max_value=145.0)
    with pytest.raises(Invalid) as refused:
        _count(session, spec, 900, 90)
    said = str(refused.value)
    assert "measured, not counted" in said
    assert "/quality/checks" in said and "/quality/samples" in said


def test_a_breakdown_left_out_is_not_broken_down_and_not_nothing_found(session):
    """Two different facts, and this product does not turn the first into the
    second. Null is nobody coded the rejects; the empty list is nobody found
    anything to code."""
    spec = _spec(session)
    plain, _nc, _s = _count(session, spec, 900, 90, lot="L-01")
    none_found, _nc, _s = _count(session, spec, 900, 0, lot="L-02", defects=[])
    assert plain.defects is None
    assert none_found.defects == []


def test_a_breakdown_that_repeats_a_code_is_added_up_rather_than_stored_twice(session):
    """Two rows saying `low_fill: 2` and `low_fill: 3` behind one dot is a
    list a reader has to do arithmetic on before they can read it."""
    count, _nc, _s = _count(session, _spec(session), 900, 90, defects=[
        {"code": "low_fill", "count": 2}, {"code": "cap_skew", "count": 4},
        {"code": "low_fill", "count": 3}])
    assert count.defects == [{"code": "low_fill", "count": 5},
                             {"code": "cap_skew", "count": 4}]


def test_a_defect_listed_with_nought_of_it_is_refused(session):
    """Leave out a defect nobody found. A breakdown that lists every code the
    plant has with nought against most of them is a table nobody reads."""
    with pytest.raises(Invalid) as refused:
        _count(session, _spec(session), 900, 90,
               defects=[{"code": "glass_chip", "count": 0}])
    assert "leave out a defect nobody found" in str(refused.value)


def test_a_breakdown_may_add_to_more_than_were_sent_back(session):
    """One bottle can be short-filled and badly capped at once. Counting
    defects is not counting units and this product does not pretend
    otherwise."""
    count, _nc, _s = _count(session, _spec(session), 900, 10, defects=[
        {"code": "low_fill", "count": 8}, {"code": "cap_skew", "count": 7}])
    assert sum(d["count"] for d in count.defects) == 15 > count.rejected


# --------------------------------------------------------- the arithmetic

def test_the_centre_is_the_pooled_share_and_not_the_mean_of_the_shares(session):
    """Nine small lots sending back one in fifty and one big lot sending back
    nearly one in five. Pooled - every reject over every unit looked at -
    this process sends back one in ten, and that is the line a reader is owed.
    The mean of the ten shares is 0.036, which would draw a centre line this
    process has never run at: it weighs a lot of a hundred as heavily as a lot
    of nine hundred."""
    _lower_the_bar(session, 10)
    spec = _spec(session)
    _lots(session, spec, [(100, 2)] * 9 + [(900, 162)])
    control = _chart(session, spec)["control"]
    assert control["centre"] == 0.1
    assert control["centre"] != pytest.approx(0.036), "the mean of the shares"
    assert (control["inspected"], control["rejected"]) == (1800, 180)


def test_each_lot_gets_limits_of_its_own_computed_from_its_own_size(session):
    """The arithmetic, at five different lot sizes, against figures worked
    out by hand. Every one of these lots sent back exactly a tenth, and the
    chart judges each of them against a different pair of lines."""
    spec, _ = _ten(session)
    points = _chart(session, spec)["points"]
    for point in points:
        sigma, lower, upper = BANDS[point["n"]]
        assert point["p"] == CENTRE
        assert point["sigma"] == pytest.approx(sigma)
        assert point["lower"] == pytest.approx(lower)
        assert point["upper"] == pytest.approx(upper)
    widest = next(p for p in points if p["n"] == 100)
    narrowest = next(p for p in points if p["n"] == 2500)
    assert widest["upper"] - widest["lower"] > 5 * (
        narrowest["upper"] - narrowest["lower"]), (
        "a lot of a hundred is judged far more loosely than a lot of 2500")


def test_there_is_no_one_pair_of_limits_on_this_chart_and_it_says_so(session):
    """Nulls rather than an average of the per-point ones, which is a pair of
    lines no lot was ever judged against."""
    spec, _ = _ten(session)
    control = _chart(session, spec)["control"]
    assert control["limits_per_point"] is True
    assert (control["upper"], control["lower"]) == (None, None)
    assert len(control["limits"]) == 10


def test_a_chart_says_how_many_were_looked_at_and_how_many_went_back(session):
    """Every list states its total, and here the totals are the record the
    chart is drawn from - a share without the number it came out of cannot be
    read."""
    spec, _ = _ten(session)
    chart = _chart(session, spec)
    assert (chart["n"], chart["inspected"], chart["rejected"]) == (10, 11100, 1110)
    assert (chart["control"]["smallest_lot"], chart["control"]["largest_lot"]) == (100, 2500)


def test_a_lower_limit_sitting_at_nought_is_said_rather_than_drawn_as_a_line(session):
    """Ten lots of eighty-one at a centre of a tenth: three sigma is exactly
    a tenth, so the lower limit lands on nought. No lot however clean can be
    beyond it, and a reader is owed that in words rather than a line they
    might read as achievable."""
    _lower_the_bar(session, 10)
    spec = _spec(session)
    _lots(session, spec, TINY)
    chart = _chart(session, spec)
    control = chart["control"]
    assert control["centre"] == 0.1
    assert control["lower_at_zero"] == 10, "all ten of them"
    for point in chart["points"]:
        assert point["sigma"] == pytest.approx(1 / 30, abs=5e-7)
        assert (point["lower"], point["upper"]) == (0.0, 0.2)
        assert point["lower_at_zero"] is True


def test_lots_nothing_was_ever_sent_back_from_get_no_limits_and_a_sentence(session):
    """A centre of nought has no sigma to speak of, and a chart of it would
    claim every lot was exactly on target. Ten clean lots are ten clean lots
    and not a process under control."""
    _lower_the_bar(session, 10)
    spec = _spec(session)
    _lots(session, spec, [(900, 0)] * 10)
    chart = _chart(session, spec)
    assert chart["control"] is None
    assert chart["signals"] == [] and chart["stable"] is None
    assert len(chart["points"]) == 10, "the lots are still listed"
    assert all(p["upper"] is None for p in chart["points"])


def test_the_fewest_points_a_counted_chart_draws_from_counts_lots(session):
    """Eleven lots is eleven looks at the process however many bottles were
    in them, so the bar is counted in lots. Counting the units would draw
    limits off one morning's big lot."""
    spec = _spec(session)
    _lots(session, spec, SETTLED[:9])
    chart = _chart(session, spec)
    assert chart["n"] == 9 and chart["min_points"] == 12
    assert chart["control"] is None
    assert "9 inspected lot(s)" in chart["note"]
    assert "12" in chart["note"]


# ------------------------------------------------------------- the rules

def test_a_settled_run_of_lots_raises_nothing(session):
    """Ten lots of different sizes all sending back a tenth. The limits move
    with every point and nothing fires, which is the baseline every test
    below is read against."""
    spec, signals = _ten(session)
    chart = _chart(session, spec)
    assert signals == [] and chart["signals"] == []
    assert chart["stable"] is True
    assert _spc_ncs(session) == []


def test_a_lot_beyond_its_own_upper_limit_trips_rule_one(session):
    """Twenty of a hundred against that lot's own upper limit of 0.19. The
    totals are unchanged from the settled run, so the centre line and every
    band are identical and the only thing that moved is this lot."""
    spec, _raised = _ten(session, HIGH)
    chart = _chart(session, spec)
    assert [s["rule"] for s in chart["signals"]] == [1]
    fired = chart["signals"][0]
    assert fired["index"] == SMALL_LOT
    assert fired["value"] == 0.2
    assert "beyond its own upper limit" in fired["what"]
    assert chart["control"]["centre"] == CENTRE, "the centre did not move"
    assert chart["stable"] is False


def test_a_lot_below_its_own_lower_limit_is_a_signal_too(session):
    """Nothing sent back out of a hundred, against a lower limit of 0.01.
    Not good news dressed up: either something changed for the better and the
    chart should be recentred, or the inspection missed things."""
    spec, _signals = _ten(session, LOW)
    chart = _chart(session, spec)
    fired = chart["signals"]
    assert [s["rule"] for s in fired] == [1]
    assert fired[0]["index"] == SMALL_LOT and fired[0]["value"] == 0.0
    assert "below its own lower limit" in fired[0]["what"]


def test_the_same_share_is_a_signal_on_a_big_lot_and_nothing_on_a_small_one(session):
    """The whole reason this chart has its own shape, in one pair of charts.
    Fifteen in a hundred and three hundred and seventy-five in two and a half
    thousand are the same share. On a lot of 2500 the upper limit is about
    0.123 and 0.15 is a signal; on a lot of 100 it is about 0.190 and 0.15 is
    an ordinary morning. One straight pair of lines across both would have
    called them the same, which is how a p chart drawn as an individuals
    chart sends a quality engineer to the wrong machine."""
    _lower_the_bar(session, 10)
    big = _spec(session, characteristic="sent_back_big")
    _lots(session, big, [(2500, 250)] * 9 + [(2500, 375)])
    small = _spec(session, characteristic="sent_back_small")
    _lots(session, small, [(2500, 250)] * 9 + [(100, 15)])

    on_the_big = spc.chart(session, "FG-COLA", "sent_back_big")
    on_the_small = spc.chart(session, "FG-COLA", "sent_back_small")
    assert on_the_big["points"][-1]["p"] == on_the_small["points"][-1]["p"] == 0.15
    assert on_the_big["points"][-1]["upper"] == pytest.approx(0.1234, abs=0.0005)
    assert on_the_small["points"][-1]["upper"] == pytest.approx(0.1903, abs=0.0005)
    assert [s["rule"] for s in on_the_big["signals"]] == [1]
    assert on_the_small["signals"] == [], "inside its own limits, and said so"


def test_one_lot_is_one_look_at_the_process_and_at_most_one_hold(session):
    """However many bottles were in it. Ten thousand units sent back through
    one count is one finding for a person to work through, not ten
    thousand."""
    spec, _ = _ten(session)
    _count(session, spec, 100, 20, lot="L-11")
    raised = _spc_ncs(session)
    assert len(raised) == 1, [nc.code for nc in raised]
    assert raised[0].evidence["rule"] == 1


def test_a_lot_that_fires_twice_is_recorded_once(session):
    """The same points are the same finding. A chart read twice, or a second
    lot arriving behind the first, does not raise the hold again."""
    spec, _ = _ten(session)
    _count(session, spec, 100, 20, lot="L-11")
    first = len(_spc_ncs(session))
    spc.evaluate(session, _lookup(session))
    session.flush()
    assert len(_spc_ncs(session)) == first


def test_the_hold_a_rule_raises_names_the_lot_and_what_was_wrong_with_it(session):
    """What a person opening the finding needs: which lot, how many of how
    many, and which defects - because "0.2 sent back" sends nobody anywhere
    and "sixteen of them short-filled" sends them to the filler."""
    spec, _ = _ten(session)
    _count(session, spec, 100, 20, lot="WO-1007-Q11",
           defects=[{"code": "low_fill", "count": 16}, {"code": "cap_skew", "count": 4}])
    nc = _spc_ncs(session)[-1]
    assert nc.evidence["lot"] == "WO-1007-Q11"
    window = nc.evidence["window"]
    assert (window["inspected"], window["rejected"]) == (100, 20)
    assert window["defects"] == [{"code": "low_fill", "count": 16},
                                 {"code": "cap_skew", "count": 4}]
    # And the lines this lot was judged against, not the chart's own, because
    # on a p chart they are not the same thing.
    assert window["lower"] == pytest.approx(0.01, abs=0.002)
    assert window["upper"] == pytest.approx(0.19, abs=0.002)
    assert "20 sent back of 100 looked at" in nc.description


def test_rules_two_to_five_are_not_run_and_the_chart_says_which_and_why(session):
    """Withheld in words on the chart itself, not left for a reader to notice
    that a rule never fires. Rules 2, 3 and 4 count points inside sigma bands
    whose width changes with every lot; rule 5 judges a range a share has
    none of."""
    spec, _ = _ten(session)
    chart = _chart(session, spec)
    assert chart["rules"] == [1]
    assert chart["rules_not_run"] == [2, 3, 4, 5]
    said = chart["rules_not_run_note"]
    assert "sigma changes with the size of each lot" in said
    assert "a share has none" in said


def test_a_plant_that_holds_on_a_rule_this_chart_cannot_run_holds_on_nothing(session):
    """A plant whose settings raise holds on rule 2 does not get a chart that
    quietly promotes rule 1 into its place, and does not get a hold list that
    claims a rule it never ran."""
    plant_settings.write(session, domain="quality", key="hold_rules",
                         written="2,5", actor="test")
    spec, _ = _ten(session, HIGH)
    chart = _chart(session, spec)
    assert chart["hold_rules"] == []
    assert [s["rule"] for s in chart["signals"]] == [1]
    assert _spc_ncs(session) == [], "recorded on the chart, and no hold raised"


def test_there_is_no_capability_figure_and_the_chart_says_why(session):
    """Not a dash and not a nought. Cp and Cpk compare a measured spread with
    the tolerance it was given, and a share of units sent back is neither a
    measurement nor inside a tolerance."""
    spec, _ = _ten(session)
    chart = _chart(session, spec)
    assert chart["capability"] is None
    assert "no Cp or Cpk here" in chart["capability_note"]


def test_the_chart_says_what_a_lot_is_on_a_plant_that_does_not_ship_lots(session):
    """The screen prints this rather than an empty link where a lot number
    would be. The stretch is real and it has a name the plant gave it; it is
    simply not a shipped lot."""
    spec, _ = _ten(session)
    assert _chart(session, spec)["lot_note"] == (
        "a lot here is one inspection stretch on one order until this plant "
        "ships lots")


# ----------------------------------------------------------- over the wire
#
# The simulated floor and an inspection terminal both reach this plant over
# HTTP, so the status codes are part of the contract: 422 is "your count does
# not add up, fix the request", 400 is "this characteristic is not inspected
# that way at all", 403 is "you are not the person who records these".

def test_a_specification_can_be_marked_counted_over_the_api(admin):
    """One field, the same body the configuration screen posts."""
    made = admin.post("/quality/specs", json={
        "material": "FG-COLA", "characteristic": "sent_back", "unit": "",
        "counted": True})
    assert made.status_code == 201, made.text
    listed = admin.get("/quality/specs").json()["items"]
    mine = [s for s in listed if s["characteristic"] == "sent_back"]
    assert mine and mine[0]["counted"] is True
    assert all(not s["counted"] for s in listed
               if s["characteristic"] != "sent_back"), "nothing else was marked"


def test_posting_a_count_answers_with_the_three_figures_the_chart_plots(client, session):
    """A terminal that posted a lot should not have to divide one number by
    another to know what it just put on the chart."""
    _spec(session)
    posted = client.post("/quality/counts", json={
        "material": "FG-COLA", "characteristic": "sent_back",
        "inspected": 900, "rejected": 90, "lot_ref": "WO-1007-Q01",
        "defects": [{"code": "cap_skew", "count": 60},
                    {"code": "low_fill", "count": 30}]})
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert (body["n"], body["k"], body["p"]) == (900, 90, 0.1)
    assert body["lot_ref"] == "WO-1007-Q01" and body["lot"] is None
    assert body["defects"] == [{"code": "cap_skew", "count": 60},
                               {"code": "low_fill", "count": 30}]
    assert body["non_conformance"] is None


def test_a_count_that_does_not_add_up_is_a_400_with_a_plain_sentence(client, session):
    """Not a stack trace and not a share above one quietly stored."""
    _spec(session)
    refused = client.post("/quality/counts", json={
        "material": "FG-COLA", "characteristic": "sent_back",
        "inspected": 100, "rejected": 140, "lot_ref": "L-01"})
    assert refused.status_code == 400, refused.text
    said = refused.json()["detail"]
    assert "140" in said and "100" in said and "above one" in said


def test_a_count_on_a_measured_characteristic_is_a_400_naming_the_way_in(client, session):
    _spec(session, characteristic="fill_height", counted=None,
          min_value=139.0, max_value=145.0)
    refused = client.post("/quality/counts", json={
        "material": "FG-COLA", "characteristic": "fill_height",
        "inspected": 900, "rejected": 90, "lot_ref": "L-01"})
    assert refused.status_code == 400, refused.text
    assert "/quality/checks" in refused.json()["detail"]


def test_recording_a_count_needs_the_right_to_record_quality_and_says_who_has_it(anon, session):
    """The same capability a reading needs - `quality.record` - because a
    count is a quality record. The refusal names the capability and who holds
    it, as every refusal on this plant does."""
    _spec(session)
    refused = anon.post("/quality/counts", json={
        "material": "FG-COLA", "characteristic": "sent_back",
        "inspected": 900, "rejected": 90, "lot_ref": "L-01"})
    assert refused.status_code in (401, 403), refused.text
    if refused.status_code == 403:
        assert "quality.record" in refused.text


def test_the_chart_endpoint_says_this_is_a_p_chart(client, session):
    """In a word, so a screen never has to infer the chart type from which
    keys happen to be present - which is how a share gets drawn as if it were
    a millimetre."""
    _ten(session)
    body = client.get("/quality/spc/FG-COLA/sent_back").json()
    assert body["kind"] == "p"
    assert (body["n"], body["inspected"], body["rejected"]) == (10, 11100, 1110)
    assert body["control"]["limits_per_point"] is True
    assert len(body["points"]) == 10
    assert all({"n", "k", "p", "upper", "lower", "sigma"} <= set(p) for p in body["points"])


# ---------------------------------------------------- a click on one lot

def test_a_click_on_a_lot_opens_which_defects_worst_first(client, session):
    """The first question a quality person asks of a dot on a p chart is not
    "how far out is it" but "which ones". So the dossier's first block is the
    breakdown, worst first."""
    spec, _ = _ten(session)
    count, _nc, _s = _count(session, spec, 100, 20, lot="WO-1007-Q11", defects=[
        {"code": "cap_skew", "count": 4}, {"code": "low_fill", "count": 16}])
    body = client.get(f"/quality/spc/FG-COLA/sent_back/lot/{count.id}").json()
    assert [d["code"] for d in body["defects"]["defects"]] == ["low_fill", "cap_skew"]
    assert body["defects"]["total"] == 2, "two codes, and the list says so"
    assert body["defects"]["coded"] == 20, "every unit sent back was coded"
    assert body["defects"]["rejected"] == 20
    assert body["defects"]["note"] is None, "nothing to reconcile"
    assert body["lot"]["lot_ref"] == "WO-1007-Q11"
    assert (body["lot"]["inspected"], body["lot"]["rejected"]) == (100, 20)
    assert body["chart"]["on_chart"] is True
    assert [s["rule"] for s in body["chart"]["signals"]] == [1]


def test_a_lot_nobody_broke_down_says_so_rather_than_showing_a_clean_lot(client, session):
    """Null is *not broken down*. A dossier that drew an empty table here
    would be claiming an inspector found nothing wrong with ninety bottles he
    sent back."""
    spec, _ = _ten(session)
    count, _nc, _s = _count(session, spec, 900, 90, lot="L-11")
    body = client.get(f"/quality/spc/FG-COLA/sent_back/lot/{count.id}").json()
    assert body["defects"]["defects"] == []
    assert body["defects"]["coded"] is None, "not nought: nobody coded them"
    assert body["defects"]["total"] == 0
    assert body["defects"]["rejected"] == 90
    assert "without a breakdown" in body["defects"]["note"]


def test_a_lot_carries_its_own_limits_and_the_centre_they_were_drawn_from(client, session):
    """So a reader looking at one lot can see which pair of lines it was
    judged against, without being handed a chart to find it in."""
    spec, _ = _ten(session)
    count, _nc, _s = _count(session, spec, 100, 20, lot="L-11")
    body = client.get(f"/quality/spc/FG-COLA/sent_back/lot/{count.id}").json()
    on_the_chart = body["chart"]
    assert on_the_chart["kind"] == "p"
    assert (on_the_chart["point"], on_the_chart["points"]) == (11, 11)
    assert on_the_chart["p"] == 0.2
    assert on_the_chart["centre"] == pytest.approx(CENTRE, abs=0.002)
    assert on_the_chart["upper"] == pytest.approx(0.19, abs=0.005)
    assert on_the_chart["lower_at_zero"] is False
    assert on_the_chart["capability"] is None
    assert "no Cp or Cpk here" in on_the_chart["capability_note"]
    assert on_the_chart["rules_not_run"] == [2, 3, 4, 5]


# ------------------------------------------------------------- the migration
#
# Run on a real SQLite file, both ways. A plant upgrades by running this
# chain, and a plant that cannot roll a revision back is a plant nobody dares
# upgrade.

REVISION = "d4e8a1c93b70"
PARENT = "a1c47f8b2d93"


def _shape(url: str, revision: str) -> dict[str, list[str]]:
    from alembic import command

    from fsmes import schema as schema_mod

    command.upgrade(schema_mod.alembic_config(url), revision)
    return schema_mod.database_shape(url)


def test_the_migration_adds_three_nullable_things_and_takes_them_away_again(tmp_path):
    """Up: a column on the specification, a table for the counts, a key on
    the signals. Down: all three gone and the parent's schema back, because a
    revision that cannot be rolled back is one nobody dares run."""
    from alembic import command

    from fsmes import schema as schema_mod

    db = tmp_path / "counted.db"
    url = f"sqlite:///{db}"
    before = _shape(url, PARENT)
    assert "quality_counts" not in before
    assert "counted" not in before["quality_specs"]

    after = _shape(url, REVISION)
    assert "quality_counts" in after
    assert "counted" in after["quality_specs"]
    assert "count_id" in after["spc_signals"]

    command.downgrade(schema_mod.alembic_config(url), PARENT)
    assert schema_mod.database_shape(url) == before


def test_the_migration_backfills_nothing(tmp_path):
    """Every specification on every plant keeps a null `counted` and goes on
    being charted as a measurement; no count is invented for any past
    inspection; no existing signal is given a lot. Scrap already recorded is
    not turned into inspection counts either - scrap is what a station threw
    away and a count is what somebody looked at, and the two have different
    denominators. Unknown is not zero."""
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
                     "VALUES (1, 11.0, 'pass', '2026-10-11T08:00:00', 'JO')")

    command.upgrade(schema_mod.alembic_config(url), REVISION)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT counted FROM quality_specs").fetchall() == [(None,)]
        assert conn.execute("SELECT count(*) FROM quality_counts").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM spc_signals "
                            "WHERE count_id IS NOT NULL").fetchone() == (0,)
