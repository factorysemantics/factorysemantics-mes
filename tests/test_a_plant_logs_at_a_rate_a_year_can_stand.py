"""How much a running plant may say about itself, and the ceiling on it.

MEASURED, 2026-09-18. A lab plant's console log held 6,348,536 lines and
1.1 GB after six hours at replay speed 10 - about ten gigabytes a day, on
a box with 22 GB free. The last 150,000 lines of it covered two and a half
minutes and carried **386 actual log events**: 970 lines a second, of
which 967 were the rendering of the other three. One `database is locked`
came out as a 282-line rich traceback, boxes and locals and source
excerpts, ten times a second.

So there are two separate things to hold, and this file holds both.

A BUDGET, because a plant that says too much says nothing: the rate at
which a healthy plant logs, pinned against what its own writers produce.

A CEILING, because a plant is a thing that is left running and the number
that matters when it goes wrong is not the rate but the disk. Four files
of 50 MB, and `fsmes.fleet.logsink` explains why a rotating handler cannot
be the answer when four processes share one file.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from fsmes.fleet import logsink

# THE BUDGET. Per plant-hour, at the lab's replay speed of 10 - so a real
# plant at speed 1 has ten times the room. Two numbers because either one
# alone can be gamed: a thousand lines of traceback is few lines and much
# disk, and a million one-word lines is the reverse.
#
# 20,000 lines is about twelve a second at replay speed 10, which is more
# than a six-machine plant has to say about itself and a two-hundredth of
# what the lab plant was producing. 4 MB is 200 bytes a line at that rate.
# The point of the numbers is not precision, it is that a regression of
# the kind measured - a factor of 175 - cannot pass them.
LINES_PER_PLANT_HOUR = 20_000
BYTES_PER_PLANT_HOUR = 4_000_000


class _Meter(logging.Handler):
    """Counts what a plant would write: records, and rendered bytes."""

    def __init__(self) -> None:
        super().__init__()
        self.records = 0
        self.bytes = 0
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.records += 1
        try:
            self.bytes += len(self.format(record)) + 1
        except Exception:
            self.bytes += 200


@pytest.fixture()
def a_plant_on_a_file(tmp_path, monkeypatch):
    from fsmes import config
    from fsmes import db as db_module
    from test_a_plant_books_while_its_dashboard_is_read import CI_HISTORY, seed

    cached = (config.get_settings, db_module.get_engine, db_module.get_sessionmaker)
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'plant.db').as_posix()}")
    for c in cached:
        c.cache_clear()
    seed(CI_HISTORY)
    yield tmp_path
    db_module.get_engine().dispose()
    for c in cached:
        c.cache_clear()


def test_a_plant_running_normally_logs_inside_its_hourly_budget(a_plant_on_a_file, monkeypatch):
    """The plant's own writers, at the lab's rate, measured against a
    handler that counts what they would have written.

    Scaled from the run to an hour and compared with the budget, so the
    test states a number a plant engineer can hold the product to rather
    than an arbitrary count that happens to pass today.
    """
    import structlog

    from fsmes.integrations.opc import agent
    from fsmes.logging import setup_logging
    from test_a_plant_books_while_its_dashboard_is_read import run_the_plant

    # A plant routes structlog through stdlib logging; the suite does not,
    # so the measurement has to set the plant's own logging up or it would
    # be counting nothing and passing. The module's logger is re-bound
    # because structlog caches a logger on its first use, and in a test
    # process that first use may already have happened.
    root = logging.getLogger()
    handlers, before = root.handlers[:], root.level
    meter = _Meter()
    try:
        setup_logging("INFO", a_plant_on_a_file / "logs", "budget")
        monkeypatch.setattr(agent, "log", structlog.get_logger("opc.agent"))
        root.addHandler(meter)
        tally = run_the_plant(seconds=4.0, floor=False)
    finally:
        root.handlers[:] = handlers
        root.setLevel(before)

    assert tally.batches > 0, "the plant did nothing; the measurement means nothing"
    assert meter.records > 0, "nothing was logged at all; the budget would pass on silence"
    hours = tally.seconds / 3600
    lines_per_hour = meter.records / hours
    bytes_per_hour = meter.bytes / hours
    assert lines_per_hour <= LINES_PER_PLANT_HOUR, (
        f"{lines_per_hour:,.0f} lines an hour, budget {LINES_PER_PLANT_HOUR:,}; "
        f"{meter.records} records in {tally.seconds:.1f}s")
    assert bytes_per_hour <= BYTES_PER_PLANT_HOUR, (
        f"{bytes_per_hour / 1e6:.1f} MB an hour, budget {BYTES_PER_PLANT_HOUR / 1e6:.1f} MB")


def test_a_failure_is_one_line_and_not_a_picture_of_the_stack(monkeypatch):
    """The agent's failures come at the plant's rate. One line each, with
    the machine and the quantities on it; the stack goes to DEBUG, where a
    plant that is not being debugged never renders it."""
    from fsmes.integrations.opc import agent

    said = []
    monkeypatch.setattr(agent.log, "error", lambda event, **kw: said.append(("error", event, kw)))
    monkeypatch.setattr(agent.log, "debug", lambda event, **kw: said.append(("debug", event, kw)))

    agent._failed("failed to book production", RuntimeError("database is locked"),
                  equipment="FILL01", good=9, units_owed=9)

    assert [level for level, _e, _k in said] == ["error", "debug"], said
    _level, event, fields = said[0]
    assert event == "failed to book production"
    assert fields["equipment"] == "FILL01" and fields["units_owed"] == 9, fields
    assert "database is locked" in fields["error"]
    assert "exc_info" not in fields, "the line a plant engineer reads carries no stack"
    assert said[1][2]["exc_info"] is not None, "DEBUG still gets the whole stack"


def test_the_plain_traceback_is_what_a_plant_renders_unless_it_is_debugging(tmp_path):
    """One `database is locked` as a rich traceback is 282 lines of boxes,
    locals and source. Ten a second of those is how 1.1 GB happened."""
    import structlog

    from fsmes.logging import setup_logging

    handlers = logging.getLogger().handlers[:]
    level = logging.getLogger().level
    try:
        for asked, expected in (("INFO", structlog.dev.plain_traceback), ("DEBUG", None)):
            setup_logging(asked, tmp_path / "logs", "traceback-check")
            console = [h for h in logging.getLogger().handlers
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler)]
            assert console, "the console handler is where a plant log comes from"
            renderer = console[0].formatter.processors[-1]
            used = renderer._exception_formatter
            if expected is None:
                assert used is not structlog.dev.plain_traceback, "DEBUG keeps the full picture"
            else:
                assert used is expected, f"{asked} must render a plain traceback"
    finally:
        root = logging.getLogger()
        root.handlers[:] = handlers
        root.setLevel(level)


def test_the_plants_own_calls_to_itself_are_not_an_access_line_each():
    """A plant that simulates its own floor polls its own API about once a
    second. Those lines record this process asking itself a question;
    anybody else's request is still logged, because on a real plant that is
    who is using it."""
    from fsmes.logging import _NotThePlantTalkingToItself

    drop = _NotThePlantTalkingToItself({"127.0.0.1", "10.1.2.3"})

    def line(client: str) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                                 '%s - "%s %s HTTP/%s" %d',
                                 (client, "GET", "/dashboard/summary", "1.1", 200), None)

    assert not drop.filter(line("127.0.0.1:44850")), "the plant polling itself"
    assert not drop.filter(line("10.1.2.3:51000")), "the plant's own published address"
    assert drop.filter(line("10.9.9.9:51000")), "somebody's browser, which is worth a line"


def test_the_console_log_has_a_ceiling_and_keeps_the_most_recent(tmp_path):
    """A plant left running for a month must not fill the disk. The sink
    rolls at its cap and keeps a fixed number of files, so the space a
    plant's log can occupy is a number you can write down: 200 MB."""
    path = Path(tmp_path) / "plant.log"
    lines = b"".join(b"line %04d\n" % n for n in range(400))   # 4,000 bytes
    # A hundred bytes at a time, the way a plant's processes write it.
    written = logsink.pump(io.BytesIO(lines), path, max_bytes=1000, keep=3, chunk=100)

    assert written == 400 * 10
    assert path.exists(), "the newest file is the one the plant is writing to"
    rolled = sorted(p.name for p in Path(tmp_path).iterdir())
    assert rolled == ["plant.log", "plant.log.1", "plant.log.2", "plant.log.3"], rolled
    total = sum(p.stat().st_size for p in Path(tmp_path).iterdir())
    assert total <= 4 * 1000 + 100, f"{total} bytes kept, cap is four files of 1000"
    # The roll happens once a file is full, so `plant.log` is empty here and
    # the plant's last words are at the top of the pile rather than in it.
    newest = path if path.stat().st_size else path.with_suffix(path.suffix + ".1")
    assert b"line 0399" in newest.read_bytes(), "what it keeps is the most recent"
    assert b"line 0000" not in b"".join(q.read_bytes() for q in Path(tmp_path).iterdir()), (
        "the oldest fell off the end, which is what a ceiling means")


def test_the_ceiling_is_fixed_rather_than_something_a_plant_can_raise():
    """A plant that could be configured to fill its own disk still can."""
    assert logsink.MAX_BYTES * (logsink.KEEP + 1) == 200_000_000
