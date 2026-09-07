"""The single-writer lock's contract: skip, don't corrupt; recover, don't wedge."""

from __future__ import annotations

import os
import time

import pytest

from fsmes.core.oplock import WriterBusy, single_writer


def _attempt(lock, stale_after_s=3600, busy_error=WriterBusy):
    """Try to take the lock and release it immediately.

    Exists so a refusal can be asserted with a plain call instead of a nested
    ``with`` — what the tests care about is whether the attempt was allowed,
    never what happens inside it.
    """
    with single_writer(lock, stale_after_s=stale_after_s, busy_error=busy_error):
        pass


def test_a_second_writer_is_refused_while_the_first_holds_the_lock(tmp_path):
    lock = tmp_path / "ingest.lock"
    with single_writer(lock, stale_after_s=3600), pytest.raises(WriterBusy):
        _attempt(lock)


def test_the_lock_is_released_when_the_block_finishes(tmp_path):
    lock = tmp_path / "ingest.lock"
    with single_writer(lock, stale_after_s=3600):
        assert lock.exists()
    assert not lock.exists()


def test_the_lock_is_released_even_when_the_block_raises(tmp_path):
    # A cadence that wedges after one unhandled error is worse than no lock:
    # nothing ingests again until somebody notices and deletes a file by hand.
    lock = tmp_path / "ingest.lock"
    with pytest.raises(ValueError), single_writer(lock, stale_after_s=3600):
        raise ValueError("the pipeline blew up")
    assert not lock.exists()


def test_a_lock_older_than_the_stale_window_is_reclaimed(tmp_path):
    lock = tmp_path / "ingest.lock"
    lock.write_text("99999 pretend-this-process-was-killed", encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock, (old, old))

    _attempt(lock)  # entering at all is the assertion: a dead holder cannot wedge us

    assert not lock.exists()


def test_a_fresh_lock_is_respected_even_though_a_stale_one_would_not_be(tmp_path):
    lock = tmp_path / "ingest.lock"
    lock.write_text("99999 still-running", encoding="utf-8")

    with pytest.raises(WriterBusy):
        _attempt(lock)


def test_a_caller_can_keep_its_own_exception_type(tmp_path):
    # Callers that already handle their own busy error should not have to change
    # their except clauses to adopt the shared lock.
    class IngestBusy(WriterBusy):
        pass

    lock = tmp_path / "ingest.lock"
    with single_writer(lock, stale_after_s=3600), pytest.raises(IngestBusy):
        _attempt(lock, busy_error=IngestBusy)
