"""The demo has to be able to fail out loud.

`fsmes demo` is the release gate: CI installs the built wheel into an empty
directory and runs it, and a tag cannot publish unless the loop closes. That
only works if a demo that books nothing looks different from the outside to a
demo that books everything. It did not in 0.1.0 - the wheel carried no config
files, the simulator had no line, the order sat at `released`, and the command
still exited 0.

These pin the verdict the command's exit code is built on. They need no
simulator: the verdict is a decision about four answers, not a run.
"""

from fsmes.cli import _loop_verdict

COMPLETE = {
    "status": "completed",
    "completion": {"kind": "order_completion", "order": "WO-DEMO", "ordered_qty": 15.0,
                   "good_qty": 15.0, "scrap_qty": 0.0, "over_qty": 0.0, "lot": "LOT-FG-001"},
    "genealogy": {"consumed": [{"lot": "LOT-SUGAR-001"}], "produced": [{"lot": "LOT-FG-001"}]},
    "oee": {"availability": 0.9, "performance": 0.8, "quality": 1.0, "oee": 0.72},
}


def _verdict(**overrides):
    return _loop_verdict(**{**COMPLETE, **overrides})


def test_a_loop_that_closed_has_nothing_to_report():
    assert _verdict() is None


def test_an_order_that_never_completed_is_named_with_the_state_it_stopped_in():
    verdict = _verdict(status="released")
    assert verdict is not None and "released" in verdict


def test_the_wheel_that_shipped_as_0_1_0_would_have_been_caught():
    # Nothing ran, so nothing completed and nothing was confirmed. This is the
    # exact shape of the 0.1.0 demo, which exited 0 anyway.
    assert _loop_verdict("released", None, {}, {}) is not None


def test_a_completed_order_the_erp_was_never_told_about_is_not_a_closed_loop():
    """The order completion, not any confirmation. The operation
    confirmations go out first, so a run whose order was never closed to the
    ERP would otherwise have passed on the strength of those."""
    assert _verdict(completion=None) is not None


def test_a_completed_order_with_no_finished_lot_is_not_a_closed_loop():
    assert _verdict(genealogy={"consumed": [{"lot": "LOT-SUGAR-001"}], "produced": []}) is not None


def test_an_oee_that_honestly_reports_null_still_closes_the_loop():
    # A component that cannot be computed is reported as null on purpose.
    # Failing here would be asking the demo to guess a number.
    unknown = {"availability": None, "performance": None, "quality": None, "oee": None}
    assert _verdict(oee=unknown) is None


def test_an_oee_endpoint_that_did_not_answer_is_not_a_closed_loop():
    assert _verdict(oee={}) is not None
