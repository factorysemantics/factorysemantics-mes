#!/usr/bin/env python3
"""`fsmes doctor` on the three states, with a secret planted where it would leak.

The file `doctor` writes is the one thing in this product that a person is
told to paste somewhere else - to an agent, or to this project. So the
promise on its first line, *no tag name, order number, product, lot, person,
host name or database URL is in it*, is checked here the only way a promise
like that can be: plant words that could only have come from the plant, run
the command, and grep the file for them.

The planted words are the handoff's own: a tag named `SECRET_TAG_XYZ` and an
order `WO-LEAK-1`, written into the log of a process that has died - which is
the one place in the file a plant's own words are quoted at all - together
with a traceback that carries them. This machine's host name and the OS user
who ran the command are checked for too: nobody notices their own user name
in a file they are pasting.

The states, in the order a person meets them:

* a home with **no plant** - the first half, and a file that says so;
* a **running** plant with `MES_SHADOW=true`, which is the only way the
  second half's "in shadow mode" is reached, because no file on this machine
  can turn shadow mode on and a running plant is the authority on its own;
* a **half-dead** plant - the API killed, the rest alive - where the file has
  to name the process that is gone and its log, the same as `status`;
* a **stopped** plant, with the planted words now in every log.

And on each file: the leak check's verdict is on it, it is inside its own
400-line budget, and `python -m fsmes doctor` answers on a PC where the
console script is not on PATH.

On ubuntu-latest and on windows-latest. Every path in the file is written by
`pathlib`, every log is read and written as UTF-8, and the whole thing is
processes and console output - which is where Windows differs, and nobody on
this project has a Windows machine to find out on.

Usage:  python .github/scripts/doctor_writes_nothing_of_yours.py --wheel dist/<name>.whl
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plant_from_wheel import (
    API,
    AS_WORDS,
    PLANT,
    fail,
    install,
    print_in_the_words_the_plant_used,
    say,
    the_port_is_back,
    until,
    venv_bin,
)

#: The words that must never come out the other side. A tag and an order,
#: because those are the two a plant has thousands of and cares most about.
TAG = "SECRET_TAG_XYZ"
ORDER = "WO-LEAK-1"

#: The process whose log the planted words go into: the one that is killed.
THE_ONE_THAT_ANSWERS = "the dashboard and the API"

#: What the file promises about itself, and may never be written without.
PASSED = "leak check: passed"

#: The file's own budget, from `fsmes.doctor.render.BUDGET`. Said here as a
#: number because this script runs against an installed wheel and imports
#: nothing from it.
BUDGET = 400


def run(command: list[str], **how) -> subprocess.CompletedProcess:
    """A command, with its output read as words and printed as it stands."""
    done = subprocess.run(command, capture_output=True, timeout=2700, **AS_WORDS, **how)
    for line in ((done.stdout or "") + (done.stderr or "")).splitlines():
        say(f"    | {line}")
    return done


def health_says_ok() -> bool:
    url = f"http://{API[0]}:{API[1]}/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as answer:
            return json.loads(answer.read().decode("utf-8")).get("status") == "ok"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def the_processes_of(home: Path) -> list[dict]:
    """What the plant recorded itself as, beside its pid file."""
    recorded = home / "data" / f"{PLANT}.procs"
    if not recorded.is_file():
        fail(f"{recorded} is not there: a started plant records what each of its "
             "processes is, and doctor cannot name a process without it")
    return json.loads(recorded.read_text(encoding="utf-8"))


#: One of this product's own modules, as a traceback in a log would name it.
#: The file keeps a frame whose file is one this machine ships, and nothing
#: else - so the path planted here has to be a real installed path, which is
#: the only thing `doctor` will recognise.
OUR_MODULE = "fsmes/integrations/opc/agent.py"


def where_the_package_is(python: str) -> Path:
    """The directory this product is installed in, asked of the installation."""
    done = subprocess.run(
        [python, "-c", "import fsmes, pathlib; print(pathlib.Path(fsmes.__file__).parent.parent)"],
        capture_output=True, timeout=300, **AS_WORDS)
    where = Path((done.stdout or "").strip())
    if done.returncode != 0 or not where.is_dir():
        fail(f"could not ask the installation where it is: {done.stdout}{done.stderr}")
    return where


def plant_the_words_in(log: Path, module: Path) -> None:
    """Write lines no scrub may carry into a log `doctor` will read.

    Three shapes, because the scrub has three paths through a log: a
    structured line whose values are a tag and an order; a structured line
    with a traceback in it, which is the one thing the file keeps more of;
    and console text, which cannot be read key by key at all.
    """
    lines = [
        json.dumps({"timestamp": "2026-10-14T06:30:00.000000Z", "level": "info",
                    "logger": "opc.agent", "event": "read a tag",
                    "tag": TAG, "order": ORDER, "value": 712.5}),
        json.dumps({"timestamp": "2026-10-14T06:30:01.000000Z", "level": "error",
                    "logger": "opc.agent", "event": "could not read",
                    "tag": TAG, "order": ORDER,
                    "exception": [{"exc_type": "TimeoutError",
                                   "exc_value": f"no answer for {TAG} on {ORDER}",
                                   "frames": [{"filename": str(module),
                                               "lineno": 412, "name": "read_batch",
                                               "locals": {"tag": TAG, "order": ORDER}}]}]}),
        "Traceback (most recent call last):",
        f'  File "{module}", line 412, in read_batch',
        f"    value = client.read({TAG})",
        f"RuntimeError: the session for {ORDER} is gone",
    ]
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    say(f"  planted {TAG} and {ORDER} in {log.name}")


def doctor(fsmes: str, work: Path, home: Path, out: Path, *more: str) -> str:
    """Run `doctor`, time it, and hold the file to every promise it makes."""
    environment = {**os.environ, "FSMES_HOME": str(home)}
    started = time.monotonic()
    done = run([fsmes, "doctor", "--out", str(out), *more], cwd=work, env=environment)
    took = time.monotonic() - started
    if done.returncode != 0:
        fail(f"`fsmes doctor` exited {done.returncode}")
    if not out.is_file():
        fail(f"`fsmes doctor` said nothing was wrong and wrote no {out}")
    text = out.read_text(encoding="utf-8")
    lines = text.splitlines()
    say(f"  {out.name}: {len(lines)} lines, written in {took:.1f}s")

    if PASSED not in text:
        fail(f"{out.name} does not carry the leak check's verdict: a file whose first "
             "line promises a check that did not pass is worse than no file")
    if len(lines) > BUDGET:
        fail(f"{out.name} is {len(lines)} lines; the budget is {BUDGET}, because a file "
             "nobody reads is a file nobody pastes")
    try:
        who = getpass.getuser()
    except Exception:
        who = ""
    for word, what in ((TAG, "a tag planted in a log"),
                       (ORDER, "an order planted in a log"),
                       (socket.gethostname(), "this machine's host name"),
                       (who, "the user who ran the command")):
        if word and word in text:
            fail(f"{what} ({word!r}) is in {out.name}. That is the whole promise on the "
                 "first line of the file, and it is the one thing this check exists for")
    say(f"  nothing of the plant, the machine or the person is in {out.name}")
    return text


def must_say(text: str, where: str, wanted: list[tuple[str, str]]) -> None:
    for phrase, why in wanted:
        if phrase not in text:
            fail(f"{where} never said {phrase!r}: {why}")


def main() -> int:
    print_in_the_words_the_plant_used()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, help="the built wheel to prove")
    parser.add_argument("--speed", type=float, default=60.0,
                        help="replay speed; 60 plays a scripted hour in a minute")
    args = parser.parse_args()

    wheel = Path(args.wheel).resolve()
    if not wheel.is_file():
        fail(f"no wheel at {wheel}")

    work = Path(tempfile.mkdtemp(prefix="fsmes-doctor-")).resolve()
    home = work / "home"
    say(f"== a clean machine: {work} ==")
    environment = {**os.environ, "FSMES_HOME": str(home), "MES_SHADOW": "true"}
    environment.pop("ANTHROPIC_API_KEY", None)

    venv = install(wheel, work)
    fsmes = str(venv_bin(venv, "fsmes"))
    python = str(venv_bin(venv, "python"))

    package = where_the_package_is(python)

    say("== a home with no plant in it ==")
    empty = doctor(fsmes, work, home, work / "no-plant.md")
    must_say(empty, "no-plant.md", [
        ("## This installation", "the first half is written whatever else is missing"),
        ("This home has no plant in it yet",
         "a home with no plant says so rather than printing empty sections"),
        ("nothing to report yet",
         "the second half stops when there is nothing measured, and does not pad"),
    ])

    say(f"== a running plant, watching only: fsmes demo {PLANT} ==")
    if run([fsmes, "demo", PLANT, "--speed", str(args.speed)],
           cwd=work, env=environment).returncode != 0:
        fail(f"`fsmes demo {PLANT}` exited non-zero before anything was broken")
    until("the dashboard answers", health_says_ok, ceiling=300)
    running = doctor(fsmes, work, home, work / "running.md")
    must_say(running, "running.md", [
        ("shadow mode: **on**",
         "MES_SHADOW was set in the shell that started the plant, and a running plant "
         "is the only thing that knows it"),
        ("asked the plant, which is running",
         "where the answer came from is said, because a stopped plant cannot be asked"),
        ("## Performance, toward migration", "the second half is reached in shadow mode"),
        ("What is not built yet",
         "the file ends in the same words the assistant uses for stage 4"),
    ])

    say("== half killing it: the dashboard and API process only ==")
    recorded = the_processes_of(home)
    api = [r for r in recorded if r["role"] == THE_ONE_THAT_ANSWERS]
    if len(api) != 1:
        fail(f"expected exactly one process recorded as {THE_ONE_THAT_ANSWERS!r}; "
             f"got {json.dumps(recorded)}")
    pid = int(api[0]["pid"])
    say(f"  kill {pid}")
    os.kill(pid, signal.SIGTERM)
    until("the dashboard has stopped answering", lambda: not health_says_ok(), ceiling=120)
    # Now that nothing holds it open, the words go into the log of the process
    # that died - which is the log a person is about to be told to read.
    # `<plant>.procs` records the log by its name; the directory is the
    # plant's own, which is where `fsmes.standalone` puts it.
    written_to = Path(api[0]["log"])
    if not written_to.is_absolute():
        written_to = home / "logs" / PLANT / written_to
    if not written_to.is_file():
        fail(f"the plant recorded {api[0]['log']!r} as its log and {written_to} is not "
             "a file; doctor is about to send a person to read it")
    plant_the_words_in(written_to, package / OUR_MODULE)
    half_dead = doctor(fsmes, work, home, work / "half-dead.md")
    must_say(half_dead, "half-dead.md", [
        ("**gone**", "the process that died is named as gone, the same as `status`"),
        (THE_ONE_THAT_ANSWERS, "named by what it is for, not by its pid alone"),
        ("api.jsonl", "and by the log that will say why it went"),
        (f"fsmes demo {PLANT} stop", "with the command to type next"),
        ("TimeoutError", "a traceback still says which exception it was"),
        (OUR_MODULE, "and in which of this product's own modules"),
        ("RuntimeError", "including a crash that never reached the JSON at all, "
                         "written into the same file as console text"),
    ])
    if '"locals"' in half_dead:
        fail("a traceback's locals are in half-dead.md; every variable in scope at a "
             "failure in this product is an order, a tag or a row")

    say(f"== stopped: fsmes demo {PLANT} stop ==")
    stopped_command = run([fsmes, "demo", PLANT, "stop"], cwd=work, env=environment)
    if stopped_command.returncode != 0:
        fail(f"`fsmes demo {PLANT} stop` exited {stopped_command.returncode}")
    the_port_is_back(API, "the dashboard")
    logs = home / "logs" / PLANT
    # The console logs last, so that they are among the most recently written
    # and `doctor` reads them: a stopped plant has no record of its processes
    # any more, and what it reads then is the newest files in the directory.
    for log in [*sorted(logs.glob("*.jsonl")), *sorted(logs.glob("*.log"))]:
        plant_the_words_in(log, package / OUR_MODULE)
    stopped = doctor(fsmes, work, home, work / "stopped.md")
    must_say(stopped, "stopped.md", [
        ("### Processes", "a stopped plant still has logs, and they are what is left"),
        ("Nothing is running for this file",
         "a log with no process behind it says so rather than reading as live"),
        ("RuntimeError",
         "a traceback in console text keeps its skeleton too, and console text is "
         "where an import-time crash lands and never reaches the JSON"),
    ])

    say("== and asked for a plant this home does not have ==")
    missing = doctor(fsmes, work, home, work / "missing.md", "--plant", "machining")
    must_say(missing, "missing.md", [
        ("No plant called `machining`",
         "a name typed on the command line is said back as it was typed, not shaped"),
        (f"`{PLANT}`", "with the names this home does have"),
    ])

    say("== on a PC where the console script is not on PATH ==")
    as_a_module = run([python, "-m", "fsmes", "doctor", "--out", str(work / "module.md")],
                      cwd=work, env={**os.environ, "FSMES_HOME": str(home)})
    if as_a_module.returncode != 0:
        fail("`python -m fsmes doctor` did not answer; on a PC where the Scripts folder "
             "is not on PATH that is the only way in")
    if PASSED not in (work / "module.md").read_text(encoding="utf-8"):
        fail("`python -m fsmes doctor` wrote a file with no verdict on it")

    say(f"\n`fsmes doctor` on {sys.platform} described a home with no plant, a running "
        f"plant in shadow mode, a half-dead one and a stopped one. {TAG} and {ORDER} "
        "were in the logs it read every time, and in none of the five files it wrote.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
