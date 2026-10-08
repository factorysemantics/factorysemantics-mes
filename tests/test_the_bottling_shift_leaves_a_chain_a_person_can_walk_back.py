"""Four records, four different books, one story - and the answer key to mark it.

Scott, 2026-10-08: *"An extremely complex quality concern. Correlates to OPC
tags. Tags correlate to some maintenance event. Maintenance event correlates
to some production event. How could a complex reasoning problem first be
simulated and then tested?"*

The bottling shift (`labs/multiplant/bottling/line.json`) plants the chain:
the 4:40 changeover runs the filler faster, the chiller's condenser clean has
been sitting in the backlog unstarted all shift, the chilled water leaving the
chiller climbs, the product it chills drifts two degrees cold, and the bottles
filled in that half hour are a millimetre and a half low. Beside it the line
writes the answer down - `_chain`, four links, each naming the window it
happens in and the record it leaves.

Two halves here, and nothing in either is a number this file chose:

  **the key against the line**  every window, station, tag, plan and
  characteristic the key names is one this pack really has, and every move it
  claims is in the generated CSV, measured by the key's own rule.

  **the key against a scorer**  `fsmes.sim.score.score_chain` marked against
  hand-built evidence, because the three answers it has to be able to give -
  recorded, not recorded, and nobody looked - are exactly what a run on a real
  plant cannot be relied on to produce on demand.

The full-shift replay that fetches the real records from a real plant is
`fsmes score bottling`, which starts a server and plays eight hours; it is not
here, and this is the fast half the handoff asked for. **Nothing in this file
listens on a port or writes outside `tmp_path`.**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fsmes.sim import generate
from fsmes.sim.score import CHAIN_RECORDS, chain_metrics, score_chain
from fsmes.sim.truth import load_truth, station_to_equipment

ROOT = Path(__file__).resolve().parents[1]
BOTTLING = ROOT / "labs" / "multiplant" / "bottling"
LINE = BOTTLING / "line.json"
TAG_MAP = BOTTLING / "tag_map.json"
PLANS = BOTTLING / "masterdata" / "maintenance_plans.json"
SPECS = BOTTLING / "masterdata" / "quality_specs.json"

#: What the generator writes before a station's own analogs, in order.
FIXED_COLUMNS = ["TSec", "State", "GoodCount", "ScrapCount", "TotalCount",
                 "AlarmWord", "CycleTimeMs", "RunMinutes", "ReadyBit"]

#: The MES state a changeover is published in. The scorer's own constant, so a
#: plant that renamed it fails here rather than silently scoring nothing.
SETUP = "setup"


# ----------------------------------------------------------------- the shift


@pytest.fixture(scope="module")
def line() -> dict:
    return generate.load_config(LINE)


@pytest.fixture(scope="module")
def chain(line) -> dict:
    assert line.get("_chain"), (
        "the bottling shift is supposed to carry its chain's answer key in "
        "`_chain`; without it nothing downstream has anything to mark")
    return line["_chain"]


@pytest.fixture(scope="module")
def rows(line) -> dict[str, list[list]]:
    """The shift, simulated once for the whole file. Two seconds and no disk."""
    return generate.simulate(line)


def station_of(equipment: str) -> str:
    """The station a machine code belongs to, read off the pack's tag map."""
    mapping = station_to_equipment(TAG_MAP)
    for station, code in mapping.items():
        if code == equipment:
            return station
    raise AssertionError(f"the pack's tag map knows no machine {equipment!r}")


def header_for(line: dict, station: str) -> list[str]:
    cfg = next(s for s in line["stations"] if s["name"] == station)
    return [*FIXED_COLUMNS, *[a["name"] for a in generate.station_analogs(cfg)]]


def series(line: dict, rows: dict, station: str, column: str) -> list[tuple[int, float]]:
    """(second, value) for one column, skipping the empty cells a quiet tag
    leaves - a gap is missing data, and averaging over it as if it were a
    number is the thing this product refuses everywhere else."""
    header = header_for(line, station)
    at = header.index(column)
    out = []
    for row in rows[station]:
        value = row[at]
        if value is None or value == "":
            continue
        out.append((int(row[0]), float(value)))
    return out


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


# ------------------------------------------- the key names things this pack has


def test_the_chain_is_four_links_in_order_each_saying_what_it_is(chain):
    """A key a person reads top to bottom: links numbered from one, each with
    its own plain sentence, in the order the story happened."""
    links = chain["links"]
    assert [link["link"] for link in links] == list(range(1, len(links) + 1))
    assert len(links) == 4, f"the handoff asked for four links; the key has {len(links)}"
    for link in links:
        assert link["what"].strip(), f"link {link['link']} does not say what it is"
        assert link["shows_as"].strip(), (
            f"link {link['link']} does not say what it looks like in the records")
        assert link["records"], f"link {link['link']} names no record, so nothing can find it"


def test_every_record_the_key_names_is_a_kind_the_scorer_can_read(chain):
    """A key may name a record this scorer has never heard of - and then the
    scorer says so rather than calling the link missing. This asserts the
    bottling key stays inside the vocabulary, so a green chain here means the
    records were really looked for."""
    for link in chain["links"]:
        for record in link["records"]:
            assert record["kind"] in CHAIN_RECORDS, (
                f"link {link['link']} names a {record['kind']!r} record; the scorer "
                f"reads {sorted(CHAIN_RECORDS)}")


def test_every_window_in_the_key_lies_inside_the_shift(chain, line):
    """A window that ran past the end of the shift would be a link no replay
    could ever record, and the key would be asking for the impossible."""
    duration = int(line["duration_s"])
    for link in chain["links"]:
        windows = [link["window_s"]] + [
            r["window_s"] for r in link["records"] if r.get("window_s")]
        for start, end in windows:
            assert 0 <= start < end <= duration, (
                f"link {link['link']} names the window {start}..{end}s, and the "
                f"shift is {duration}s long")


def test_every_machine_tag_plan_and_characteristic_in_the_key_is_real(chain, line):
    """The key names a machine code, a tag, a maintenance plan and a quality
    characteristic. Each one is checked against the pack that has to hold it,
    because a key naming a plan nobody seeded would score "not recorded"
    forever and read as a broken MES."""
    plans = {p["code"] for p in json.loads(PLANS.read_text(encoding="utf-8"))}
    specs = {(s["material"], s["characteristic"])
             for s in json.loads(SPECS.read_text(encoding="utf-8"))}
    for link in chain["links"]:
        for record in link["records"]:
            kind = record["kind"]
            if kind in ("stop", "tag", "maintenance"):
                station = station_of(record["equipment"])
                if kind == "tag":
                    assert record["tag"] in header_for(line, station), (
                        f"{record['equipment']} publishes no {record['tag']}")
            if kind == "maintenance":
                assert record["plan"] in plans, (
                    f"the pack seeds no maintenance plan {record['plan']!r}")
            if kind in ("sample", "finding"):
                assert (record["material"], record["characteristic"]) in specs, (
                    f"the pack holds no spec for {record['material']} "
                    f"{record['characteristic']}")


def test_the_key_says_which_spc_rule_fires_first(chain):
    """The handoff asked for it, and a reader assumes it is rule 1 because a
    low mean feels extreme. It is not: a millimetre and a half is under three
    sigma of a sample mean, so the rule that catches it is the two-of-three
    beyond two sigma. The key has to say so in words."""
    assert "_first_rule" in chain and str(chain["_first_rule"]).strip()
    assert "2" in str(chain["_first_rule"])


def test_the_key_says_what_it_does_not_claim(chain):
    """An answer key that only claims is a key nobody can trust at the edges.
    This one names the three things it deliberately does not promise."""
    assert chain["_what_this_key_does_not_claim"]


# -------------------------------------- the key against the generated records


def test_the_changeover_leaves_the_filler_in_setup_for_the_whole_interval(chain, line, rows):
    """Link 1's first record: the setup interval itself. Read out of the key's
    own window, not out of a number in this file."""
    record = next(r for link in chain["links"] for r in link["records"]
                  if r["kind"] == "stop")
    assert record["state"] == SETUP
    station = station_of(record["equipment"])
    start, end = record["window_s"]
    states = {row[1] for row in rows[station] if start <= row[0] < end}
    assert states == {5}, (
        f"the filler is meant to be in changeover for all of {start}..{end}s; "
        f"the shift has it in states {sorted(states)}")


@pytest.mark.parametrize("link_no,tag", [(1, "CycleTimeMs"), (3, "ChillerOutletTemp"),
                                         (3, "ProductTemp")])
def test_each_tag_the_key_names_moved_the_way_the_key_says(chain, line, rows, link_no, tag):
    """The key's own rule, applied to the generated shift: the mean over the
    record's window against the mean over everything before it, in the named
    direction, by at least the amount named.

    The same rule reads a step and a ramp, which is why the key states it
    once: the cycle time drops at 4:40 and then holds flat, so there is no
    slope inside its window at all, while the chiller's outlet temperature
    climbs the whole way. First-against-last would score one of them and miss
    the other.
    """
    link = next(x for x in chain["links"] if x["link"] == link_no)
    record = next(r for r in link["records"] if r.get("tag") == tag)
    start, end = record["window_s"]
    readings = series(line, rows, station_of(record["equipment"]), tag)
    before = [v for t, v in readings if t < start]
    inside = [v for t, v in readings if start <= t < end]
    assert before and inside, f"{tag} has no readings to compare over {start}..{end}s"
    moved = mean(inside) - mean(before)
    wanted = float(record["by"])
    if record["direction"] == "down":
        assert -moved >= wanted, (
            f"{tag} is meant to sit at least {wanted} lower over {start}..{end}s; "
            f"it moved {moved:+.3f}")
    else:
        assert moved >= wanted, (
            f"{tag} is meant to sit at least {wanted} higher over {start}..{end}s; "
            f"it moved {moved:+.3f}")


def test_the_bottles_filled_in_the_cold_window_are_light_on_the_line_itself(chain, line, rows):
    """Link 4 in the one place the CSV can show it. Fill height is measured in
    the lab from the weight, so the chart and its finding belong to the replay;
    what the line itself carries is the weight, and it has to be low for the
    whole window the key gives the samples - otherwise the chart cannot be low
    either.
    """
    link = next(x for x in chain["links"] if x["link"] == 4)
    start, end = link["window_s"]
    weights = series(line, rows, "Refill", "FillWeight")
    cold = [v for t, v in weights if start <= t < end]
    warm = [v for t, v in weights if t < start]
    assert cold and warm
    assert mean(warm) - mean(cold) > 4.0, (
        "the cold half hour is meant to fill several grams light; it filled "
        f"{mean(warm) - mean(cold):.2f} g light")


def test_the_filler_publishes_a_process_alarm_while_the_chiller_climbs(chain, line, rows):
    """What a person watching the line actually sees first. The drift raises
    bit 1 of the alarm word on the station it is on - "a process value is
    drifting" - so the chain is not silent until the lab weighs a bottle. The
    key's own prose says this; here it is in the records.
    """
    record = next(r for link in chain["links"] for r in link["records"]
                  if r.get("tag") == "ChillerOutletTemp")
    start, end = record["window_s"]
    station = station_of(record["equipment"])
    drifting = [row for row in rows[station]
                if start <= row[0] < end and int(row[5]) & 0b10]
    assert drifting, "nothing on the filler says a process value is drifting"


# ------------------------------------------------- the scorer, on known evidence
#
# Hand-built evidence rather than a replay: a plant that answers every request
# correctly proves the happy path and nothing else, and the honesty of this
# section is entirely in what it says when it cannot answer.

T0 = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)


def _at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


ONE_LINK = {
    "name": "a tag that climbs",
    "links": [{
        "link": 1, "what": "the chiller heats up", "shows_as": "the trend climbs",
        "window_s": [100, 200],
        "records": [{"kind": "tag", "equipment": "FILL01", "tag": "ChillerOutletTemp",
                     "direction": "up", "by": 1.0, "window_s": [100, 200]}],
    }],
}


def _trend(points: list[tuple[float, float]]) -> dict:
    return {"equipment": "FILL01", "tag": "ChillerOutletTemp",
            "points": [{"t": _at(t), "mean": v, "min": v, "max": v, "n": 1}
                       for t, v in points]}


def test_a_chain_nobody_collected_evidence_for_is_unknown_and_says_why():
    """The failure this section exists for. A run that fetched nothing has not
    discovered that the records are missing; it has not looked, and a zero
    there would be the lie the stop sections already refuse."""
    card = score_chain(ONE_LINK, {"machines": []}, T0, 1.0, None)
    link = card["links"][0]
    assert link["recorded"] is None
    assert "no chain evidence" in link["records"][0]["why"]
    metrics = chain_metrics(card)
    assert metrics["chain_links"] == 1
    assert metrics["chain_links_recorded"] is None
    assert metrics["chain_links_not_observed"] == 1
    assert metrics["chain_recorded"] is None


def test_a_tag_that_moved_the_way_the_key_says_is_recorded():
    evidence = {"tags": {"FILL01/ChillerOutletTemp": _trend(
        [(0, 3.2), (50, 3.2), (120, 4.6), (180, 5.1)])}}
    card = score_chain(ONE_LINK, {"machines": []}, T0, 1.0, evidence)
    link = card["links"][0]
    assert link["recorded"] is True
    assert link["records"][0]["moved_by"] == pytest.approx(1.65, abs=0.01)
    assert chain_metrics(card)["chain_recorded"] == 1.0


def test_a_tag_that_sat_still_is_not_recorded_and_says_by_how_much_it_missed():
    evidence = {"tags": {"FILL01/ChillerOutletTemp": _trend(
        [(0, 3.2), (50, 3.2), (120, 3.3), (180, 3.25)])}}
    card = score_chain(ONE_LINK, {"machines": []}, T0, 1.0, evidence)
    link = card["links"][0]
    assert link["recorded"] is False
    assert link["records"][0]["moved_by"] == pytest.approx(0.075, abs=0.01)
    assert chain_metrics(card)["chain_links_not_recorded"] == 1


def test_a_tag_with_nothing_before_the_window_is_unknown_rather_than_guessed():
    """A record whose window opens at the start of the run has no baseline. The
    honest answer is that the move cannot be measured, not that it did not
    happen - the mistake that would make an MES watching from one minute in
    look like an MES that lost the story."""
    evidence = {"tags": {"FILL01/ChillerOutletTemp": _trend([(120, 4.6), (180, 5.1)])}}
    card = score_chain(ONE_LINK, {"machines": []}, T0, 1.0, evidence)
    row = card["links"][0]["records"][0]
    assert row["recorded"] is None
    assert "BEFORE this window" in row["why"]


def test_a_tag_the_mes_holds_nothing_for_is_unknown_rather_than_missing():
    card = score_chain(ONE_LINK, {"machines": []}, T0, 1.0, {"tags": {}})
    row = card["links"][0]["records"][0]
    assert row["recorded"] is None
    assert "nobody asked" in row["why"]


def test_an_order_still_due_is_recorded_and_the_card_says_nobody_started_it():
    """Link 2's whole point. The record is an order at `due` with no start on
    it, and the card carries the second half as its own fact: "raised and
    still waiting" and "raised, done, re-baselined" are both real records, and
    only the key says which one this story wanted."""
    key = {"links": [{"link": 1, "what": "nobody cleaned the condenser",
                      "shows_as": "an order still due", "window_s": [0, 300],
                      "records": [{"kind": "maintenance", "equipment": "FILL01",
                                   "plan": "PM-FILL-CHILLER", "status": "due"}]}]}
    evidence = {"maintenance": {"complete": True, "total": 1, "items": [
        {"code": "MO-1", "plan": "PM-FILL-CHILLER", "equipment": "FILL01",
         "status": "due", "started_at": None}]}}
    card = score_chain(key, {"machines": []}, T0, 1.0, evidence)
    link = card["links"][0]
    assert link["recorded"] is True
    assert link["records"][0]["orders"] == ["MO-1"]
    assert link["records"][0]["ever_started"] == []
    assert "none against PM-FILL-CHILLER ever started" in link["records"][0]["why"]


def test_a_maintenance_list_that_came_back_in_part_cannot_answer_in_the_negative():
    """A list fetched one page of is not a list that proved an absence. Said
    out loud, because the alternative is an executor reading "not recorded"
    off a plant whose backlog simply had another page."""
    key = {"links": [{"link": 1, "what": "nobody cleaned the condenser",
                      "shows_as": "an order still due", "window_s": [0, 300],
                      "records": [{"kind": "maintenance", "equipment": "FILL01",
                                   "plan": "PM-FILL-CHILLER", "status": "due"}]}]}
    evidence = {"maintenance": {"complete": False, "total": 900, "items": []}}
    row = score_chain(key, {"machines": []}, T0, 1.0, evidence)["links"][0]["records"][0]
    assert row["recorded"] is None
    assert "came back in part" in row["why"]


def test_a_finding_is_matched_on_what_the_mes_wrote_down_not_on_its_words():
    """Two characteristics on one material, one finding each. Matching on the
    description would credit the chain with the wrong one; matching on the
    evidence the MES stored when it raised it cannot."""
    key = {"links": [{"link": 1, "what": "the fill height ran low",
                      "shows_as": "a finding opens", "window_s": [0, 300],
                      "records": [{"kind": "finding", "material": "FG-BOTTLE",
                                   "characteristic": "fill_height"}]}]}
    evidence = {"findings": {"complete": True, "total": 2, "items": [
        {"code": "NC-1", "created_at": _at(10),
         "evidence": {"material": "FG-BOTTLE", "characteristic": "fill_weight", "rule": 1}},
        {"code": "NC-2", "created_at": _at(20),
         "evidence": {"material": "FG-BOTTLE", "characteristic": "fill_height", "rule": 2}},
    ]}}
    row = score_chain(key, {"machines": []}, T0, 1.0, evidence)["links"][0]["records"][0]
    assert row["recorded"] is True
    assert row["findings"] == ["NC-2"]
    assert row["rules_fired"] == [2]


def test_a_characteristic_nobody_sampled_in_the_window_is_unknown():
    """No point on the chart inside the window is nothing to be right or wrong
    about. A plant whose lab was busy elsewhere for half an hour did not lose
    the story; nobody measured it."""
    key = {"links": [{"link": 1, "what": "the fill height ran low",
                      "shows_as": "low points on the chart", "window_s": [100, 200],
                      "records": [{"kind": "sample", "material": "FG-BOTTLE",
                                   "characteristic": "fill_height", "below": 140.84}]}]}
    evidence = {"charts": {"FG-BOTTLE/fill_height": {
        "kind": "xbar_r", "sample_size": 5,
        "points": [{"value": 142.0, "ts": _at(10)}]}}}
    row = score_chain(key, {"machines": []}, T0, 1.0, evidence)["links"][0]["records"][0]
    assert row["recorded"] is None
    assert "nobody measured" in row["why"]


def test_a_link_with_one_record_missing_is_not_recorded_whatever_else_it_has():
    """A chain is only as walked-back as its weakest record. Two records on
    one link, one found and one definitely absent: the link is not recorded,
    and the card still shows which half was there."""
    key = {"links": [{"link": 1, "what": "the chiller heats and the product cools",
                      "shows_as": "two tags move", "window_s": [100, 200],
                      "records": [
                          {"kind": "tag", "equipment": "FILL01", "tag": "ChillerOutletTemp",
                           "direction": "up", "by": 1.0, "window_s": [100, 200]},
                          {"kind": "finding", "material": "FG-BOTTLE",
                           "characteristic": "fill_height"}]}]}
    evidence = {
        "tags": {"FILL01/ChillerOutletTemp": _trend(
            [(0, 3.2), (50, 3.2), (120, 4.6), (180, 5.1)])},
        "findings": {"complete": True, "total": 0, "items": []},
    }
    card = score_chain(key, {"machines": []}, T0, 1.0, evidence)
    link = card["links"][0]
    assert link["recorded"] is False
    assert [row["recorded"] for row in link["records"]] == [True, False]


def test_a_line_with_no_chain_written_down_scores_an_absent_key_and_no_links():
    """Most lines have no chain, and the hour CI runs is one of them. The
    section says the key is absent rather than reporting a perfect score over
    nothing."""
    card = score_chain(None, {"machines": []}, T0, 1.0, None)
    assert card["key"] == "absent"
    assert card["links"] == []
    assert chain_metrics(card)["chain_links"] == 0


def test_the_shift_carries_its_key_through_to_ground_truth():
    """What joins the two halves of this file: the key a person edits in
    `line.json` is the key the scorer marks, carried by `load_truth` without
    being interpreted on the way."""
    truth = load_truth(LINE, TAG_MAP)
    assert truth["chain"]["links"][0]["link"] == 1
    assert len(truth["chain"]["links"]) == 4
