"""An SPC signal does something, and an inspection reaches the chart.

The gap these pin, found by the lab's third round on 2026-09-14: the four
Western Electric rules ran only when somebody opened the chart, and nothing
read what they returned; and the station inspection path wrote no quality
check, so a scrap burst's readings never reached the rules at all.

Decision record 0027. Each test is named after the behaviour it pins.
"""

from sqlalchemy import func, select

from fsmes.db import utcnow
from fsmes.domain import NcDisposition, NonConformance, QualityCheck, SpcSignal
from fsmes.services import quality, serialization, spc

# Twelve readings is the fewest the chart will draw limits from, so a steady
# run of that length is the "nothing is wrong" baseline every test starts from.
STEADY = [11.0 + (i % 3 - 1) * 0.02 for i in range(30)]


def _record(session, values, characteristic="brix", **kwargs):
    signals = []
    for value in values:
        _check, _nc, raised = quality.record_check(
            session, material_code="FG-COLA", characteristic=characteristic,
            value=value, actor="test", **kwargs)
        signals.extend(raised)
    session.flush()
    return signals


def _ncs(session):
    return list(session.scalars(select(NonConformance).order_by(NonConformance.id)))


def _spc_ncs(session):
    return [nc for nc in _ncs(session) if (nc.evidence or {}).get("source") == "spc"]


# ------------------------------------------------------- the rule acts

def test_a_steady_process_raises_no_hold(session):
    """The baseline. A chart that raises a hold on a process behaving itself
    is a chart people switch off."""
    assert _record(session, STEADY) == []
    assert _spc_ncs(session) == []


def test_a_point_beyond_three_sigma_raises_a_hold_without_anyone_opening_the_chart(session):
    """Nobody calls `spc.chart` here. The rule fires on the write, which is
    the whole point: at two in the morning nobody has the screen open."""
    _record(session, STEADY)
    raised = _record(session, [11.9])
    assert [s["rule"] for s in raised] == [1]
    holds = _spc_ncs(session)
    assert len(holds) == 1
    assert holds[0].code == raised[0]["nonconformance"]


def test_the_hold_says_which_rule_and_which_readings(session):
    """A supervisor's first question is *why do you think so*. It is answered
    on the record, not by asking them to re-derive it."""
    _record(session, STEADY)
    _record(session, [11.9])
    hold = _spc_ncs(session)[-1]
    assert "rule 1" in hold.description
    assert "brix" in hold.description
    evidence = hold.evidence
    assert evidence["rule"] == 1
    assert evidence["characteristic"] == "brix"
    assert evidence["what"] == "a point beyond three sigma"
    assert evidence["window"]["points"][-1]["value"] == 11.9
    # The limits as they stood when it fired. Recomputing them later gives
    # different numbers, and then nobody can see what the MES acted on.
    assert evidence["window"]["upper"] > evidence["window"]["centre"]


def test_a_hold_a_rule_raised_is_open_because_nobody_has_picked_it_up(session):
    """Decision 0024: a step nobody took is null, not the system's name. A
    machine raising a record has not reviewed it."""
    _record(session, STEADY)
    _record(session, [11.9])
    hold = _spc_ncs(session)[-1]
    assert hold.status.value == "open"
    assert hold.reviewed_by is None
    assert hold.raised_by == "spc"


def test_judging_the_same_window_again_raises_nothing_new(session):
    """Evaluating the rules on every write is only safe if a window that has
    already spoken stays quiet. The window's identity is the readings it
    judged, so the second look finds the first one's record."""
    _record(session, STEADY)
    _record(session, [11.9])
    signal = session.scalars(select(SpcSignal).order_by(SpcSignal.id)).first()
    before = len(_ncs(session))
    count = session.scalar(select(func.count(SpcSignal.id)))

    assert spc.evaluate(session, signal.spec, since_id=signal.check_id) == []
    assert len(_ncs(session)) == before
    assert session.scalar(select(func.count(SpcSignal.id))) == count


def test_a_rule_does_not_fire_backwards_over_readings_nobody_was_worried_about(session):
    """One wild reading moves the centre line, and every settled reading
    behind it is suddenly on one side of it. A rule fires on a reading, and
    the reading has to be a new one."""
    _record(session, STEADY)
    raised = _record(session, [11.9])
    assert {s["rule"] for s in raised} == {1}


def test_an_excursion_that_lasts_is_one_hold_not_twenty(session):
    """Twenty readings beyond three sigma is one thing that went wrong. A
    record per reading is a list nobody reads."""
    _record(session, STEADY)
    _record(session, [11.9, 12.0, 11.95, 12.1, 11.88])
    assert len(_spc_ncs(session)) == 1
    # Every firing is still recorded — the hold is what does not repeat.
    assert session.scalar(select(func.count(SpcSignal.id))) >= 2


def test_a_rule_fires_again_once_the_hold_has_been_dispositioned(session):
    """Somebody decided what happened to the material; the next excursion is
    a new finding, not the old one."""
    _record(session, STEADY)
    _record(session, [11.9])
    first = _spc_ncs(session)[-1]
    quality.disposition_nc(session, first.code, disposition=NcDisposition.SCRAP,
                           reason="two crates held", actor="SCOTT")
    session.flush()
    _record(session, [12.4])
    assert len(_spc_ncs(session)) == 2


def test_the_chart_endpoint_still_computes_the_rules(session):
    """The GET is unchanged: taking the rules off it would move where the
    truth is rather than adding to it."""
    _record(session, STEADY)
    _record(session, [11.9])
    out = spc.chart(session, "FG-COLA", "brix")
    assert out["stable"] is False
    assert 1 in {s["rule"] for s in out["signals"]}


def test_fewer_than_twelve_readings_raises_nothing(session):
    """Control limits from six points move with every reading, so a rule on
    them is a rule about nothing."""
    assert _record(session, [11.0, 13.0, 9.6, 11.2]) == []
    assert _spc_ncs(session) == []


def test_a_hold_names_no_station_when_a_person_measured(session):
    """Null there means *not recorded*. Deriving a station from the order's
    route would name a machine nobody stood at."""
    _record(session, STEADY)
    raised = _record(session, [11.9])
    assert raised[0]["equipment"] is None
    assert _spc_ncs(session)[-1].evidence["equipment"] is None


def test_the_station_that_measured_is_carried_when_it_is_known(session):
    _record(session, STEADY, equipment_code="MIX01")
    raised = _record(session, [11.9], equipment_code="MIX01")
    assert raised[0]["equipment"] == "MIX01"
    assert _spc_ncs(session)[-1].evidence["equipment"] == "MIX01"


# --------------------------------------------- inspections reach the chart

def _pieces(session, values, material="FG-COLA", equipment="MIX01", start=1):
    """Station events the way the OPC agent hands them over."""
    now = utcnow()
    return [{"kind": "piece", "equipment": equipment, "seq": start + i, "ts": now,
             "serial": f"P-{start + i:09d}", "material": material, "order": None,
             "passed": True, "fail_mask": 0,
             "attributes": ["brix", "Gloss"], "values": [value, 80.0]}
            for i, value in enumerate(values)]


def test_an_inspected_characteristic_with_a_specification_becomes_a_check(session):
    """The chart reads `quality_checks`. A station measuring into
    `unit_inspections` alone is invisible to every rule there is."""
    out = serialization.ingest_inspections(session, _pieces(session, [11.0, 11.02, 10.98]))
    session.flush()
    assert out["checks"] == 3
    checks = list(session.scalars(select(QualityCheck)))
    assert [c.spec.characteristic for c in checks] == ["brix", "brix", "brix"]
    assert all(c.equipment_id is not None for c in checks)


def test_a_characteristic_with_no_specification_stays_an_inspection_and_says_so(session):
    """Unlabelled data is reported as unlabelled. `Gloss` has no
    specification in this plant, and silently dropping it is how a plant
    convinces itself it is measuring something it is not."""
    out = serialization.ingest_inspections(session, _pieces(session, [11.0]))
    assert out["uncharted"] == ["FG-COLA/Gloss"]


def test_a_reading_is_judged_by_the_specification_not_by_the_station(session):
    out = serialization.ingest_inspections(session, _pieces(session, [11.0, 12.5]))
    session.flush()
    assert out["checks"] == 2
    results = sorted(c.result.value for c in session.scalars(select(QualityCheck)))
    assert results == ["fail", "pass"]


def test_a_station_reading_opens_no_hold_of_its_own(session):
    """The station already judged the unit. A record per bad piece on a
    machine inspecting ten a second is noise a supervisor scrolls past."""
    serialization.ingest_inspections(session, _pieces(session, [12.5, 12.6, 12.7]))
    session.flush()
    assert _ncs(session) == []


def test_a_scrap_burst_at_a_station_trips_rule_one_and_raises_one_hold(session):
    """The whole loop, end to end: a station's readings become checks, the
    rules see them on the write, and a hold exists with the rule and the
    points on it — with nobody having opened a screen."""
    serialization.ingest_inspections(session, _pieces(session, STEADY))
    session.flush()
    out = serialization.ingest_inspections(
        session, _pieces(session, [11.9, 11.95, 12.0], start=100))
    session.flush()

    assert [s["rule"] for s in out["signals"]][:1] == [1]
    holds = _spc_ncs(session)
    assert len(holds) == 1
    assert holds[0].evidence["equipment"] == "MIX01"
    assert "rule 1" in holds[0].description


def test_replaying_the_same_station_events_writes_no_second_check(session):
    """A replayed event is a duplicate serial, and a duplicate serial is not a
    second measurement of anything."""
    events = _pieces(session, [11.0, 11.02])
    serialization.ingest_inspections(session, events)
    session.flush()
    again = serialization.ingest_inspections(session, events)
    session.flush()
    assert again["checks"] == 0
    assert session.scalar(select(func.count(QualityCheck.id))) == 2


# ------------------------------------------- what a plant does next is its own

def test_the_trigger_catalogue_offers_the_signal_tag(session):
    """A plant that wants a rule to stop a moulder writes a trigger. That
    decision is not in this code, which is house rule 4."""
    from fsmes.services import triggers

    assert spc.SIGNAL_TAG in triggers.EVENT_TAGS
    assert "rule number" in triggers.EVENT_TAGS[spc.SIGNAL_TAG]


def test_a_signal_reaches_the_triggers_on_the_station_that_measured():
    """The hold is already raised. This is the other half: the plant's own
    trigger sees the rule number on the machine whose reading tripped it."""
    from fsmes.integrations.opc import agent as agent_mod

    handler = agent_mod._Handler({}, inspections={})
    seen: list[tuple] = []
    handler.evaluator = type("E", (), {"observe": lambda _self, *a: seen.append(a) or []})()
    handler._signals([{"rule": 1, "what": "a point beyond three sigma", "equipment": "MIX01",
                       "material": "FG-COLA", "characteristic": "brix", "nonconformance": "NC-00001"}])
    assert seen == [("MIX01", "spc.signal", 1.0)]
    assert handler.inspection_stats["signals"] == 1


def test_a_signal_with_no_station_reaches_no_trigger():
    """Firing a trigger on a machine the MES is guessing at is worse than not
    firing one. The signal is still recorded and still logged."""
    from fsmes.integrations.opc import agent as agent_mod

    handler = agent_mod._Handler({}, inspections={})
    seen: list[tuple] = []
    handler.evaluator = type("E", (), {"observe": lambda _self, *a: seen.append(a) or []})()
    handler._signals([{"rule": 1, "what": "a point beyond three sigma", "equipment": None,
                       "material": "FG-COLA", "characteristic": "brix", "nonconformance": "NC-00001"}])
    assert seen == []
    assert handler.inspection_stats["signals"] == 1
