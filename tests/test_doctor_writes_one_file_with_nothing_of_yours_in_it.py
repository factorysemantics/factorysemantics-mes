"""`fsmes doctor`: what it must say, and what it must never say.

The file exists so that the answer to "my plant stopped" is *run this, paste
the file* instead of *find your log folder, open run-api.jsonl, paste the last
lines*. It is only worth running if two things hold at once: it says enough
for somebody to diagnose the installation from it alone, and it carries
nothing of the plant that wrote it. Both halves are asserted here, on homes
built the way the product builds them - a pack, a fleet file, an ownership
record, logs on the disk.

A planted tag name (`SECRET_TAG_XYZ`) and a planted order (`WO-LEAK-1`) run
through every one of these homes. Any test that finds either of them in a
written file is the only kind of failure in this file that is not a bug in a
sentence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fsmes import migration, standalone
from fsmes.doctor import gather, leak, render, scrub
from fsmes.fleet import owned as ownership

#: The two values the CI job greps for. They are planted in a pack's label,
#: in a log line's values, and in a traceback's message.
TAG = "SECRET_TAG_XYZ"
ORDER = "WO-LEAK-1"


# --------------------------------------------------------------- homes to read


def a_pack(home: Path, name: str, *, port: int = 8765, env: dict | None = None) -> Path:
    pack = standalone.packs_dir(home) / name
    (pack / "masterdata").mkdir(parents=True, exist_ok=True)
    (pack / "plant.toml").write_text(
        f'[pack]\nformat = 1\n\n[plant]\nname = "{name}"\nlabel = "{TAG} line"\n\n'
        f'[serve]\napi_host = "127.0.0.1"\napi_port = {port}\n'
        f'opc_endpoint = "opc.tcp://127.0.0.1:{port + 100}/fsmes/{name}"\n',
        encoding="utf-8")
    return pack


def a_home(tmp_path: Path, *, name: str | None = "bottling", port: int = 8765) -> Path:
    """A home with one plant in it that nothing is running, or with none at all."""
    home = tmp_path / "fsmes-home"
    (home / "data").mkdir(parents=True, exist_ok=True)
    if name is None:
        return home
    pack = a_pack(home, name, port=port)
    standalone.fleet_file(home).write_text(
        f'packs = ["packs/{name}"]\n', encoding="utf-8")
    data = standalone.data_dir(home)
    host, user = ownership.here()
    ownership.remember(data, ownership.Entry(
        name=name, pack=str(pack), instance_id="a-test-instance", data_dir=str(data),
        api_host="127.0.0.1", api_port=port, control="local",
        created_at="2026-10-14T00:00:00Z", created_on_host=host, created_by_user=user,
        product_version="0.0.0", pack_fingerprint=""))
    (data / f"{name}.instance").write_text("a-test-instance", encoding="utf-8")
    return home


def a_log(home: Path, plant: str, file: str, lines: list[dict | str]) -> Path:
    where = home / "logs" / plant
    where.mkdir(parents=True, exist_ok=True)
    path = where / file
    path.write_text("".join(
        (json.dumps(line) if isinstance(line, dict) else line) + "\n" for line in lines),
        encoding="utf-8")
    return path


def a_line(**kwargs) -> dict:
    """One structlog line, with this plant's own words in its values."""
    return {"level": "info", "logger": "fsmes.operations",
            "timestamp": "2026-10-14T00:00:00.123456Z", **kwargs}


def processes_were_recorded(home: Path, plant: str, rows: list[dict]) -> None:
    """The two files `fsmes demo <plant>` writes when it starts a plant."""
    data = standalone.data_dir(home)
    (data / f"{plant}.pids").write_text(
        " ".join(str(row["pid"]) for row in rows), encoding="utf-8")
    (data / f"{plant}.procs").write_text(json.dumps(rows), encoding="utf-8")


def a_pid_that_is_gone() -> int:
    """A pid nothing holds: one this run started and waited for.

    Not a large number chosen by hand - a pid above this kernel's maximum is
    not a pid Windows would agree about, and this has to be the same fact on
    both.
    """
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def the_skills_own_pattern_of():
    """`pattern_of` as the shape skill itself spells it, out of its own script.

    `profile.py` is standalone by design, so it is executed rather than
    imported - the same way `leak.leakcheck` executes its sibling.
    """
    import types

    from fsmes import skills

    text = skills.read_file("plant-from-your-mes", "scripts/profile.py")
    assert text, "a release that ships doctor ships the skill it borrows from"
    module = types.ModuleType("profile_from_the_skill")
    module.__dict__["__file__"] = "skills/plant-from-your-mes/scripts/profile.py"
    exec(compile(text, "profile.py", "exec"), module.__dict__)
    return module.pattern_of


def written(tmp_path: Path, home: Path, **kwargs):
    from fsmes import doctor

    return doctor.write(tmp_path / "doctor.md", home=home, **kwargs)


@pytest.fixture(autouse=True)
def _one_home_at_a_time(monkeypatch, tmp_path):
    """`gather` points the fleet environment at the home it was given.

    It is the same variable every other command reads, which is the point -
    and a test that left it set would hand the next test this one's fleet.
    """
    from fsmes import plant as plants

    monkeypatch.setenv(standalone.HOME_ENV, str(tmp_path / "fsmes-home"))
    monkeypatch.delenv(plants.REGISTRY_ENV, raising=False)
    yield
    plants.environment.cache_clear()


# ------------------------------------------------------------- the three states


def test_a_home_with_no_plant_gets_the_first_half_and_is_told_there_is_no_plant(tmp_path):
    home = a_home(tmp_path, name=None)
    out = written(tmp_path, home)
    text = out.path.read_text(encoding="utf-8")

    assert "## This installation" in text
    assert "no fleet file: this home has no plant yet" in text
    assert "0 plants" in text or "no plant" in text
    assert render.NOT_IN_SHADOW in text, "the second half still has to say why it is empty"


def test_a_stopped_plant_has_its_logs_read_and_is_not_called_running(tmp_path):
    home = a_home(tmp_path)
    a_log(home, "bottling", "api.jsonl", [a_line(event="shutting down")])
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert "### Processes" in text
    assert "no pid file" in text
    assert "### Log — api.jsonl" in text, "a log with no process is headed by its file name"
    assert "shutting down" in text


def test_a_half_dead_plant_names_the_process_that_is_gone_and_the_log_that_will_say_why(tmp_path):
    home = a_home(tmp_path)
    import os

    a_log(home, "bottling", "api.jsonl", [a_line(event="the API fell over")])
    processes_were_recorded(home, "bottling", [
        {"pid": os.getpid(), "role": "the log sink", "log": "sink.log"},
        {"pid": a_pid_that_is_gone(), "role": "the dashboard and the API", "log": "api.jsonl"},
    ])
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert "1 of 2 alive." in text
    assert "| the dashboard and the API | **gone**" in text
    assert "api.jsonl" in text, "the log of what died is the one thing a person needs"
    assert "fsmes demo bottling stop" in text, "the same answer `status` gives"


def test_doctor_asked_for_a_plant_this_home_does_not_have_says_so(tmp_path):
    home = a_home(tmp_path)
    text = written(tmp_path, home, plant="machining").path.read_text(encoding="utf-8")

    assert "No plant called `machining` is in this home." in text
    assert "This home has 1 plant: `bottling`" in text, (
        "the name they meant is the next thing they need")


# ------------------------------------------------------------------ the scrub


def test_a_tag_name_and_an_order_number_planted_in_a_log_are_not_in_the_file(tmp_path):
    home = a_home(tmp_path)
    a_log(home, "bottling", "api.jsonl", [
        a_line(event="inspected", tag=TAG, order=ORDER, value=502.3),
        a_line(event="staged material", component="ACME-BOTTLE-1L", lot="LOT-99"),
    ])
    out = written(tmp_path, home)
    text = out.path.read_text(encoding="utf-8")

    assert TAG not in text
    assert ORDER not in text
    assert "ACME" not in text
    assert "inspected" in text, "the message template is the whole point of keeping the line"
    assert '"tag"' in text and '"order"' in text, "the keys say what kind of thing went"
    assert out.verdict.startswith("leak check: passed")


def test_a_traceback_keeps_the_exception_type_and_the_module_and_loses_the_values(tmp_path):
    home = a_home(tmp_path)
    a_log(home, "bottling", "opc-agent.jsonl", [{
        "event": "could not read a tag",
        "level": "error",
        "logger": "fsmes.opc.agent",
        "timestamp": "2026-10-14T00:00:00Z",
        "exception": [{
            "exc_type": "TimeoutError",
            "exc_value": f"reading {TAG} for {ORDER} timed out",
            "frames": [
                {"filename": str(Path("src/fsmes/opc/agent.py").resolve()),
                 "lineno": 412, "name": "read_batch",
                 "locals": {"tag": TAG, "order": ORDER}},
                {"filename": "/home/someone/their-own-script.py",
                 "lineno": 7, "name": "main", "locals": {}},
            ],
        }],
    }])
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert "TimeoutError" in text, "the exception type is what names the fault"
    assert "fsmes/opc/agent.py" in text and "412" in text, "our own module and line survive"
    assert TAG not in text and ORDER not in text
    assert "their-own-script" not in text, "somebody else's file is not ours to print"
    assert "locals_dropped" in text, "what was dropped is counted, not silently gone"


def test_a_traceback_in_console_text_keeps_its_skeleton(tmp_path):
    home = a_home(tmp_path)
    a_log(home, "bottling", "plant.log", [
        "Traceback (most recent call last):",
        f'  File "{Path("src/fsmes/opc/agent.py").resolve()}", line 412, in read_batch',
        f"    value = client.read({TAG})",
        f"RuntimeError: the session for {ORDER} is gone",
    ])
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert "Traceback (most recent call last):" in text
    assert "fsmes/opc/agent.py" in text and "read_batch" in text
    assert "RuntimeError" in text
    assert TAG not in text and ORDER not in text


def test_a_plant_name_is_the_one_word_of_yours_the_file_keeps(tmp_path):
    home = a_home(tmp_path, name="bottling")
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert "## bottling" in text, (
        "every command a person types next has the plant's name in it, so the name "
        "is in the file on purpose and is the only word of theirs that is")


def test_the_file_never_names_this_machine_or_this_user(tmp_path):
    import getpass
    import socket

    home = a_home(tmp_path)
    a_log(home, "bottling", "api.jsonl", [a_line(event="serving")])
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert socket.gethostname() not in text
    user = getpass.getuser()
    if len(user) > 3 and user not in scrub.vocabulary():
        assert user not in text


def test_nothing_is_written_when_the_check_finds_a_value_it_was_given(tmp_path, monkeypatch):
    home = a_home(tmp_path)
    a_log(home, "bottling", "api.jsonl", [a_line(event="inspected", tag=TAG)])

    whole = render.whole

    def with_a_leak(report, verdict):
        return whole(report, verdict) + f"\nand one line the scrub missed: {TAG}\n"

    monkeypatch.setattr(render, "whole", with_a_leak)
    out = tmp_path / "doctor.md"
    with pytest.raises(leak.Leak) as refused:
        written(tmp_path, home)

    assert not out.exists(), "a file that would leak is never on the disk"
    assert "NOT written" in str(refused.value)
    assert TAG in str(refused.value), "the command has to say which value it was"


def test_the_shape_of_a_value_is_the_shape_the_shape_skill_writes():
    """One idiom, not two: the scrub's shapes are the check's shapes.

    A value the scrub spells one way and the leak check another would be a
    check that is weaker than it looks.
    """
    assert leak.leakcheck() is not None, "a release that ships doctor ships the check"
    theirs = the_skills_own_pattern_of()
    for value in ("FILL01", "WO-ACME-4711", "502.3", "DAY", TAG, ORDER):
        assert scrub.pattern_of(value) == theirs(value)


# ---------------------------------------------------------------- the two halves


def test_the_second_half_says_there_is_nothing_to_report_when_shadow_mode_is_off(tmp_path):
    home = a_home(tmp_path)
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert "## Performance, toward migration" in text
    assert render.NOT_IN_SHADOW in text
    body = text.split("## Performance, toward migration", 1)[1]
    body = body.split("## What is not built yet", 1)[0]
    assert len(body.strip().splitlines()) <= 6, (
        "shadow mode off is four sentences and a stop, not a padded section")
    assert "### Coverage" not in body and "### Scorecard over time" not in body


def test_what_is_not_built_yet_is_printed_from_the_one_place_the_assistant_reads(tmp_path):
    home = a_home(tmp_path)
    text = written(tmp_path, home).path.read_text(encoding="utf-8")

    assert f"## {migration.HEADING}" in text
    for sentence in migration.NOT_BUILT_YET:
        assert sentence.split(".")[0] in text
    for question in migration.QUESTIONS:
        assert question in text


def test_two_kept_scorecards_on_two_dates_appear_as_two_rows(tmp_path):
    home = a_home(tmp_path)
    kept = home / gather.SCORECARDS
    kept.mkdir(parents=True)
    for day, agree in (("2026-10-01", 40), ("2026-10-08", 51)):
        (kept / f"{day}.json").write_text(json.dumps({
            "kind": "shadow_scorecard_figures",
            "written": day,
            "orders": {"both": 12},
            "operations": {"both": 30},
            "agreement": {"compared": 60, "agree": agree, "differ": 60 - agree,
                          "per_operation": [{"field": "good", "not_stated": 2}],
                          "per_order": []},
            "cannot_tell": {"total": 3},
        }), encoding="utf-8")

    rows = gather.scorecards_in(home, leak.Seen())
    assert [row.date for row in rows] == ["2026-10-01", "2026-10-08"]
    assert [row.agree for row in rows] == [40, 51]

    import dataclasses

    plant = dataclasses.replace(gather.one_plant(
        "bottling", {"pack": None, "env": {}}, home, leak.Seen()), scorecards=rows)
    said = "\n".join(render._scorecards(plant))
    assert "### Scorecard over time" in said
    assert said.count("| 2026-10-") == 2, "two days, two rows"
    assert "3 things it cannot tell you" in said


def test_a_home_with_no_kept_scorecard_prints_the_command_that_makes_one(tmp_path):
    home = a_home(tmp_path)
    plant = gather.one_plant("bottling", {"pack": None, "env": {}}, home, leak.Seen())
    said = "\n".join(render._scorecards(plant))

    assert "No scorecard has been kept" in said
    assert "fsmes shadow scorecard --incumbent" in said
    assert "--json" in said


# ------------------------------------------------------------------ the promises


def test_the_file_stays_inside_four_hundred_lines_on_a_one_plant_home(tmp_path):
    home = a_home(tmp_path)
    for file in ("api.jsonl", "opc-agent.jsonl", "operations.jsonl"):
        a_log(home, "bottling", file, [
            a_line(event="inspected", tag=f"FILL{n:02d}", order=f"WO-{n}", value=n + 0.5)
            for n in range(300)])
    processes_were_recorded(home, "bottling", [
        {"pid": a_pid_that_is_gone(), "role": "the dashboard and the API", "log": "api.jsonl"},
        {"pid": a_pid_that_is_gone(), "role": "the reader", "log": "opc-agent.jsonl"},
        {"pid": a_pid_that_is_gone(), "role": "the simulated floor", "log": "operations.jsonl"},
    ])
    out = written(tmp_path, home)

    assert out.lines <= render.BUDGET, (
        f"{out.lines} lines: a file nobody will read is a file nobody pastes")
    text = out.path.read_text(encoding="utf-8")
    # Three logs of 300 lines each is 900 lines of log for a 400-line file, so
    # most of it is not in there - and the file says so rather than leaving a
    # reader to think the plant only ever wrote 40 lines.
    assert "the log itself is longer than this" in text, "what was left out is said"


def test_doctor_starts_nothing_and_stops_nothing(tmp_path, monkeypatch):
    home = a_home(tmp_path)
    a_log(home, "bottling", "api.jsonl", [a_line(event="serving")])

    def refuse(*args, **kwargs):
        raise AssertionError("doctor started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    import os

    monkeypatch.setattr(os, "kill", refuse)
    written(tmp_path, home)


def test_doctor_finishes_well_inside_the_time_a_person_will_wait(tmp_path):
    """A comparison, not a clock: the ports it cannot reach are the budget.

    Two ports that do not answer is the state a person runs this in, and the
    one way this command can hang. The gate is that the whole run costs less
    than four times one port's own timeout - true on a slow shared runner,
    and false the moment a socket waits on the system default instead.
    """
    import time

    home = a_home(tmp_path)
    a_log(home, "bottling", "api.jsonl", [a_line(event="serving")])
    started = time.monotonic()
    written(tmp_path, home)
    took = time.monotonic() - started

    budget = 4 * (gather.PORT_TIMEOUT + gather.ASK_TIMEOUT)
    assert took < budget, f"{took:.2f}s, against a budget of {budget:.2f}s"
