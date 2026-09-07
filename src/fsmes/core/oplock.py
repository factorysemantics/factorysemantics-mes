"""Single-writer file lock, shared by every scheduled pipeline.

Ported from factorysemantics.com (``core/oplock.py``), where it was extracted
from the node's ingest so the event-lake ingest could share the same
discipline. The reasoning carries over unchanged: any pipeline on a fixed
cadence will eventually overrun its own schedule, and two overlapping runs
racing a watermark store or a curated directory is corruption, not slowness.

``O_EXCL`` create is the atomic primitive on both platforms (there is no
``fcntl`` on Windows, and this MES is expected to run on plant PCs). A lock
whose holder was killed would otherwise wedge the cadence permanently, so one
older than ``stale_after_s`` is reclaimed and the takeover is logged rather
than silently ignored.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("fsmes.oplock")


class WriterBusy(RuntimeError):
    """Another writer holds the lock.

    Expected on a fixed cadence when a cycle overruns — the caller skips rather
    than corrupting shared state.
    """


@contextmanager
def single_writer(
    lock_path: Path,
    stale_after_s: float,
    busy_error: type[WriterBusy] = WriterBusy,
) -> Iterator[None]:
    """Hold ``lock_path`` for the duration of the block, or raise ``busy_error``.

    ``busy_error`` lets a caller keep its own exception type so existing
    handlers do not need to change.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lock_path.stat().st_mtime
        if age < stale_after_s:
            raise busy_error(
                f"another run has held the lock for {age:.0f}s "
                f"({lock_path.name}) — skipping this cycle"
            ) from None
        log.warning("reclaiming lock abandoned %.0fs ago (holder died?)", age)
        lock_path.unlink(missing_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, f"{os.getpid()} {datetime.now(UTC).isoformat()}".encode())
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)
