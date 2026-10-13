"""The stop mix: a plant's reasons in the shares its own system recorded.

A look-alike plant whose every long stop says "mechanical" is not a
look-alike: the losses page is the page an engineer reads first, and the shape
file measured what proportion of stops carried which reason and what
proportion carried none at all. This is the arithmetic that turns those
shares into the words the simulated floor puts on stops, and the claim that a
pack written before the mix existed still behaves exactly as it did.
"""

from collections import Counter

from fsmes.sim.operations import MIX_PER_TURN, _stop_rotation

BOTTLING = {
    "micro_stop_under_s": 60,
    "micro_stop": "micro_stop",
    "changeover": "changeover",
    "longer_than_a_micro_stop": "mechanical",
    "mix": [
        {"reason": "mechanical", "share": 0.5},
        {"reason": "material", "share": 0.3},
        {"reason": "quality", "share": 0.2},
    ],
    "leave_unlabelled": 0.0,
}


def test_a_pack_with_no_mix_names_long_stops_the_way_it_always_did():
    """Both lab packs are this case, and neither may change."""
    assert _stop_rotation({}) == ()
    assert _stop_rotation({"longer_than_a_micro_stop": "mechanical"}) == ()
    assert _stop_rotation({"mix": []}) == ()


def test_the_reasons_come_out_in_the_shares_the_source_system_recorded():
    counted = Counter(_stop_rotation(BOTTLING))
    assert counted["mechanical"] == 50
    assert counted["material"] == 30
    assert counted["quality"] == 20
    assert None not in counted, "this plant labelled every stop it stopped for"


def test_the_two_reasons_a_stops_length_already_decides_are_not_drawn_again():
    """Changeover and the micro-stop word are assigned by how long the stop
    was. Putting them in the rotation as well would book them twice."""
    stops = dict(BOTTLING)
    stops["mix"] = [
        {"reason": "changeover", "share": 0.4},
        {"reason": "micro_stop", "share": 0.4},
        {"reason": "mechanical", "share": 0.2},
    ]
    assert set(_stop_rotation(stops)) == {"mechanical"}


def test_a_plant_that_leaves_stops_unexplained_leaves_them_unexplained():
    stops = dict(BOTTLING, leave_unlabelled=0.3)
    rotation = _stop_rotation(stops)
    counted = Counter(rotation)
    assert counted[None] == 30
    assert sum(count for reason, count in counted.items() if reason) == 70
    # Not in a block at the end: an engineer opening the timeline should not
    # find the last hour of the shift entirely unexplained.
    first_half = rotation[: len(rotation) // 2]
    assert first_half.count(None) >= 10


def test_a_reason_recorded_on_very_few_stops_is_still_on_the_list():
    """Rounding a 0.4 % category away would shorten the list operators pick
    from, which is a quieter lie than getting its share slightly wrong."""
    stops = dict(BOTTLING)
    stops["mix"] = [
        {"reason": "mechanical", "share": 0.8},
        {"reason": "material", "share": 0.196},
        {"reason": "tooling", "share": 0.004},
    ]
    counted = Counter(_stop_rotation(stops))
    assert counted["tooling"] == 1
    assert set(counted) == {"mechanical", "material", "tooling"}


def test_a_share_that_is_not_zero_leaves_at_least_one_stop_unexplained():
    stops = dict(BOTTLING, leave_unlabelled=0.001)
    assert None in _stop_rotation(stops)


def test_one_turn_is_a_hundred_stops_so_a_one_per_cent_category_survives():
    assert MIX_PER_TURN == 100
    assert len(_stop_rotation(BOTTLING)) == MIX_PER_TURN
