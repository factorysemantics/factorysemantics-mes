"""The check that a written setpoint was followed waits on the plant's own clock.

The wait is three time constants of the tag's lag, and the lag is counted on
the line's clock. A plant replaying its line at ten times the wall clock
(`fsmes fleet start --speed 10`, which puts `MES_SIM_SPEED` into every
process) passes those three time constants in a tenth of the wall time, so
the check comes a tenth as late. A real plant runs at 1x, and there nothing
changes: the check falls due at the write plus the wait, to the second.
"""

from datetime import datetime, timedelta

import pytest

from fsmes.config import get_settings
from fsmes.db import utcnow
from fsmes.services import adjustments

CAT = {
    "MIX01": {
        "tags": ["Temperature", "TemperatureSP"],
        "meta": {
            "Temperature": {"kind": "pv", "unit": "°C", "writable": False},
            # A two-minute lag: three of them is six minutes, as on the
            # cutlery pack's fork mould.
            "TemperatureSP": {"kind": "sp", "unit": "°C", "min": 40.0, "max": 80.0,
                              "drives": "Temperature", "lag_s": 120.0, "writable": True},
        },
        "analog": "Temperature",
    }
}


@pytest.fixture()
def at_speed(monkeypatch):
    """Start this plant the way `fsmes fleet start --speed N` starts one."""

    def set_it(speed: float | None):
        if speed is None:
            monkeypatch.delenv("MES_SIM_SPEED", raising=False)
        else:
            monkeypatch.setenv("MES_SIM_SPEED", str(speed))
        get_settings.cache_clear()

    yield set_it
    get_settings.cache_clear()


def _written(session, seconds_ago: float):
    rec = adjustments.propose(session, equipment_code="MIX01", tag="TemperatureSP", value=60.0,
                              rationale="cool it", cat=CAT)
    adjustments.approve(session, rec.code, actor="eng")
    adjustments.mark_written(session, rec.code, 60.0)
    rec.written_at = utcnow() - timedelta(seconds=seconds_ago)
    session.flush()
    return rec


def test_at_normal_speed_the_check_falls_due_at_the_write_plus_the_wait_to_the_second(session, at_speed):
    at_speed(None)
    written = datetime(2026, 11, 28, 2, 0, 0)
    assert adjustments.verification_due_at(written, 360.0) == written + timedelta(seconds=360)
    assert adjustments.verification_due_at(written, 360.0, 1.0) == datetime(2026, 11, 28, 2, 6, 0)

    rec = _written(session, seconds_ago=359)
    assert rec.verify_after_seconds == 360.0, "three time constants of the tag's own lag"
    assert adjustments.due_for_verification(session) == [], "a second early is not due"
    rec.written_at = utcnow() - timedelta(seconds=361)
    session.flush()
    assert [d["code"] for d in adjustments.due_for_verification(session)] == [rec.code]
    assert adjustments.out(rec)["verify_due_at"] == rec.written_at + timedelta(seconds=360)


def test_at_ten_times_the_clock_the_check_lands_about_thirty_six_seconds_after_the_write(session, at_speed):
    at_speed(10)
    rec = _written(session, seconds_ago=34)
    assert rec.verify_after_seconds == 360.0, "the wait itself is still six minutes of the line"
    assert adjustments.due_for_verification(session) == [], "34 s of wall clock is 340 s of the line"
    rec.written_at = utcnow() - timedelta(seconds=37)
    session.flush()
    assert [d["code"] for d in adjustments.due_for_verification(session)] == [rec.code]
    assert adjustments.out(rec)["verify_due_at"] == rec.written_at + timedelta(seconds=36)


def test_a_nonsense_speed_is_read_as_real_time_rather_than_used_as_a_divisor():
    written = datetime(2026, 11, 28, 2, 0, 0)
    assert adjustments.verification_due_at(written, 360.0, 0.0) == written + timedelta(seconds=360)
    assert adjustments.verification_due_at(written, 360.0, -3.0) == written + timedelta(seconds=360)
