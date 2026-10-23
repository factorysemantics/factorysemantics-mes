#!/usr/bin/env python3
"""Make the state the first Windows user was stuck in, and get out of it.

2026-10-13, 07:56, from his phone: "Says 2 PID are alive, 7964 and 3940. But
it can't talk to them." His plant had half died. `fsmes demo bottling status`
told him two pid numbers; `fsmes demo bottling stop` refused to touch a plant
it could not talk to, and the flag that would have stopped it lives on a
different command. There was no way out that the product had told him about.

This check makes that exact state on purpose - start the plant from a built
wheel, then kill the one process everything else needs, the dashboard and API
- and then asserts that the product now talks a person out of it:

* `status` says the plant is not answering, names the process that is gone by
  what it is for, and prints the log that will say why it went. The log it
  names has to exist, which is what ties the roles `fsmes.plant` records to
  the files its processes actually write;
* `status` ends with the command to type, and never names `--force`, which is
  a flag on another command;
* the OPC server is still alive while all that is true - this is a half-dead
  plant, not a stopped one;
* `fsmes demo bottling stop` recovers it with no flag, exits 0, and says what
  it found;
* `fsmes demo bottling` brings the plant back up afterwards;
* `fsmes demo bottling stop --force` - the flag `fsmes fleet stop` puts in a
  person's head, and the one he typed on 2026-10-15 - is taken, answered in a
  sentence that names the command it belongs to, and stops the plant anyway.

And, because the same person's first question was that `fsmes` was not on
PATH at all: `python -m fsmes --version` answers in the same venv.

On ubuntu-latest and on windows-latest. A plant PC is a Windows PC, nobody on
this project owns one, and both halves of this - the process roles and the
words - are about processes and consoles, which is where Windows differs.

Facts, never seconds: every wait is a poll for a state with a generous
ceiling, and `os.kill(pid, SIGTERM)` is the kill on both platforms (on Windows
CPython turns it into `TerminateProcess`, which is what `fsmes.plant` already
relies on).

Usage:  python .github/scripts/recover_a_half_dead_plant.py --wheel dist/<name>.whl
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# One spelling of "install a wheel into a fresh venv on either platform", and
# one of "poll for a state". A second copy of either is a copy that drifts.
from plant_from_wheel import (
    API,
    AS_WORDS,
    OPC,
    PLANT,
    fail,
    install,
    port_answers,
    print_in_the_words_the_plant_used,
    say,
    the_port_is_back,
    until,
    venv_bin,
)

#: The process this check kills: the one thing anything would need to answer,
#: and the one that was certainly gone on his PC.
THE_ONE_THAT_ANSWERS = "the dashboard and the API"


def run(command: list[str], **how) -> subprocess.CompletedProcess:
    """A command, with its output read as words and printed as it stands."""
    done = subprocess.run(command, capture_output=True, timeout=2700, **AS_WORDS, **how)
    said = (done.stdout or "") + (done.stderr or "")
    for line in said.splitlines():
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
             "processes is, and nothing downstream can name a process without it")
    return json.loads(recorded.read_text(encoding="utf-8"))


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

    work = Path(tempfile.mkdtemp(prefix="fsmes-half-dead-")).resolve()
    home = work / "home"
    say(f"== a clean machine: {work} ==")
    environment = {**os.environ, "FSMES_HOME": str(home)}
    environment.pop("ANTHROPIC_API_KEY", None)

    venv = install(wheel, work)
    fsmes = str(venv_bin(venv, "fsmes"))
    python = str(venv_bin(venv, "python"))

    # His first question, in one line: a PC where the console script is not on
    # PATH still has a way to run the package.
    as_a_module = run([python, "-m", "fsmes", "--version"], cwd=work)
    if as_a_module.returncode != 0 or not as_a_module.stdout.strip().startswith("fsmes "):
        fail("`python -m fsmes --version` did not answer; on a PC where the Scripts "
             "folder is not on PATH that is the only way in")

    say(f"== one command: fsmes demo {PLANT} --speed {args.speed:g} ==")
    if run([fsmes, "demo", PLANT, "--speed", str(args.speed)],
           cwd=work, env=environment).returncode != 0:
        fail(f"`fsmes demo {PLANT}` exited non-zero before anything was broken")
    until("the dashboard answers", health_says_ok, ceiling=300)

    say("== half killing it: the dashboard and API process only ==")
    recorded = the_processes_of(home)
    told = ", ".join(str(r["role"]) + " (pid " + str(r["pid"]) + ")" for r in recorded)
    say(f"  the plant recorded {len(recorded)} processes: {told}")
    api = [r for r in recorded if r["role"] == THE_ONE_THAT_ANSWERS]
    if len(api) != 1:
        fail(f"expected exactly one process recorded as {THE_ONE_THAT_ANSWERS!r}; "
             f"got {json.dumps(recorded)}")
    pid = int(api[0]["pid"])
    say(f"  kill {pid}")
    os.kill(pid, signal.SIGTERM)
    until("the dashboard has stopped answering", lambda: not health_says_ok(), ceiling=120)
    if not port_answers(OPC):
        fail(f"the OPC server on {OPC[1]} went too; this check needs a half-dead plant, "
             "not a stopped one")
    say(f"  the OPC server on {OPC[1]} is still alive: the plant is alive and silent")

    say(f"== what a person is told: fsmes demo {PLANT} status ==")
    status = run([fsmes, "demo", PLANT, "status"], cwd=work, env=environment)
    said = status.stdout + status.stderr
    for wanted, why in (
        ("is not answering", "the plant has to be described as not answering"),
        (f"gone: {THE_ONE_THAT_ANSWERS}", "the process that is gone has to be named by "
                                          "what it is for, not by its pid alone"),
        ("still alive:", "what is still holding the ports has to be named too"),
        (f"fsmes demo {PLANT} stop", "the last thing on screen has to be the command "
                                     "to type next"),
    ):
        if wanted not in said:
            fail(f"`fsmes demo {PLANT} status` never said {wanted!r}: {why}")
    if "--force" in said:
        fail("the easy path named `--force`, which is a flag on `fsmes fleet stop`; "
             "that is the dead end this whole change exists to remove")

    # The log it sent him to read has to be a file that is really there.
    named = [word.strip() for line in said.splitlines() if "api.jsonl" in line
             for word in line.split() if word.strip().endswith("api.jsonl")]
    if not named:
        fail("status named no log for the process that is gone; the one thing his own "
             "machine could tell him was in a log")
    log = Path(named[0])
    if not log.is_file():
        fail(f"status sent a person to {log}, which is not a file")
    say(f"  the log it names exists: {log} ({log.stat().st_size:,} bytes)")

    say(f"== the way out: fsmes demo {PLANT} stop ==")
    stopped = run([fsmes, "demo", PLANT, "stop"], cwd=work, env=environment)
    if stopped.returncode != 0:
        fail(f"`fsmes demo {PLANT} stop` exited {stopped.returncode} on a plant this "
             "installation made on this machine; that is the dead end he hit")
    recovery = stopped.stdout + stopped.stderr
    if "was not answering" not in recovery or "still alive" not in recovery:
        fail("stop recovered the plant without saying what it found; a command that "
             f"silently does more than it was asked is worse: {recovery!r}")
    for where, what in ((API, "the dashboard"), (OPC, "the OPC server")):
        the_port_is_back(where, what)
    say("  both ports are back")

    say(f"== and again: fsmes demo {PLANT} --speed {args.speed:g} ==")
    again = run([fsmes, "demo", PLANT, "--speed", str(args.speed)], cwd=work, env=environment)
    if again.returncode != 0:
        fail(f"`fsmes demo {PLANT}` exited {again.returncode} after the recovery")
    try:
        until("the dashboard answers again", health_says_ok, ceiling=300)
    finally:
        # Taken down with the word he actually typed on 2026-10-15. The plant
        # is up and answering here, so the flag is the only thing under test.
        last = run([fsmes, "demo", PLANT, "stop", "--force"], cwd=work, env=environment)

    say(f"== the word he typed: fsmes demo {PLANT} stop --force ==")
    guessed = (last.stdout or "") + (last.stderr or "")
    if last.returncode != 0:
        fail(f"`fsmes demo {PLANT} stop --force` exited {last.returncode}; the flag is "
             "taken and answered, never refused - refusing it is what cost him "
             "2026-10-13 and again 2026-10-15")
    if "No such option" in guessed:
        fail(f"typer refused the flag `fsmes fleet stop` had put in his head: {guessed!r}")
    if "stop needs no --force here" not in guessed:
        fail("the flag was swallowed and nothing said why it was not needed; the "
             f"sentence is the whole reason for accepting it: {guessed!r}")
    for where, what in ((API, "the dashboard"), (OPC, "the OPC server")):
        the_port_is_back(where, what)
    say("  the flag was answered in a sentence and the plant still went down")

    say(f"\nA half-dead {PLANT} on {sys.platform} was described, recovered and started "
        "again, with no flag and nothing killed by hand - and the flag he guessed was "
        "answered in words instead of refused.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
