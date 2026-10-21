#!/usr/bin/env python3
"""Prove a built wheel brings the bottling plant up, on Linux and on Windows,
with no checkout in sight.

`demo_from_wheel.sh` beside this proves the *twin* runs from a wheel. This
proves the thing we actually demonstrate: the six-station bottling line, its
eight-hour shift, the simulated floor and the dashboard - from
`pip install` and one command, in a directory that holds none of this
repository's files.

It is Python rather than a shell script because it has to be the same check on
both runners. A plant PC is a Windows PC (`docs/operate/compatibility.md`
promises it), nobody on this project owns one, and a bash-only check on
ubuntu-latest is how `start_new_session=True` sat in `fsmes plant` for weeks
doing nothing on the platform it mattered on.

What it asserts, in order, and why each one:

* the wheel carries both packs, and no `out/` CSV - the replay is generated
  from a seed, and a shipped replay would be a second copy of a thing
  `line.json` already states (it is 9.7 MB against a 2 MB wheel);
* the shipped packs are byte-for-byte the lab's - the wheel's copies are
  copies, never a second source of truth;
* after one command: sign-in works, the three bottling specifications are
  there including the counted one, the OPC replay port answers, the floor is
  watching with every machine connected and a check recorded, and at least one
  counted lot has been inspected;
* `fsmes demo bottling stop` puts the ports back.

Facts, never seconds: every wait below is a poll for a state with a generous
ceiling, and the times it prints are information for the Result, not gates. A
shared runner makes an absolute bound flaky and says nothing about the code.

Usage:  python .github/scripts/plant_from_wheel.py --wheel dist/<name>.whl
"""

from __future__ import annotations

import argparse
import contextlib
import filecmp
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PLANT = "bottling"
#: `[serve]` in `labs/multiplant/bottling/plant.toml`. Named here rather than
#: parsed so that a pack that quietly changes its port fails this check loudly
#: instead of testing whatever it moved to.
API = ("127.0.0.1", 8010)
OPC = ("127.0.0.1", 4841)
MATERIAL = "FG-BOTTLE"
COUNTED = "sent_back"
SIGN_IN = {"code": "SCOTT", "password": "operator"}
#: Where in the wheel the packs live.
SHIPPED = "fsmes/data/packs"
PACKS = ("bottling", "machining")
#: How to read what the plant prints, and how to print it again. The CLI
#: writes UTF-8 - `instance 492c5047... written to ...` has an ellipsis in it -
#: and `text=True` alone decodes a child in the console's code page, which on
#: Windows is cp1252: the first run of this check on windows-latest logged
#: that ellipsis as the three cp1252 characters its UTF-8 bytes spell - the
#: plant's own words, read wrongly by this script and written back out again
#: (2026-10-12). Say the encoding and the words survive the trip to the log.
AS_WORDS = {"encoding": "utf-8", "errors": "replace"}


def say(what: str) -> None:
    print(what, flush=True)


def fail(why: str) -> None:
    say(f"::error::{why}")
    sys.exit(1)


# ------------------------------------------------------------------- the wheel


def read_the_wheel(wheel: Path) -> list[str]:
    """What the wheel carries under `fsmes/data/packs`, and what it must not."""
    size = wheel.stat().st_size
    say(f"wheel: {wheel.name}, {size:,} bytes")
    with zipfile.ZipFile(wheel) as zf:
        names = sorted(zf.namelist())
    shipped = [n for n in names if n.startswith(f"{SHIPPED}/")]
    say(f"pack files in the wheel: {len(shipped)} of {len(names)} files")
    for pack in PACKS:
        wanted = [f"{SHIPPED}/{pack}/{f}" for f in
                  ("plant.toml", "line.json", "floor.json", "tag_map.json")]
        missing = [w for w in wanted if w not in shipped]
        if missing:
            fail(f"the wheel does not carry {pack}: missing {', '.join(missing)}")
        masterdata = [n for n in shipped if n.startswith(f"{SHIPPED}/{pack}/masterdata/")]
        if not masterdata:
            fail(f"the wheel carries {pack} with no masterdata")
        say(f"  {pack}: 4 pack files + {len(masterdata)} master-data files")
    replay = [n for n in shipped if "/out/" in n or n.lower().endswith(".csv")]
    if replay:
        fail("the wheel carries generated line data, which is generated from a "
             f"seed and must not be shipped: {', '.join(replay[:5])}")
    say("  no out/ and no CSV under the packs: the replay is generated, not shipped")
    return shipped


def the_copies_are_copies(repo: Path, wheel: Path) -> None:
    """The shipped packs against `labs/multiplant/<name>`, file by file.

    A pack in two places is a pack that drifts. This is the test that fails
    when somebody edits one of them - and, just as importantly, when somebody
    adds a file to a lab pack and does not add it to the wheel's list in
    `pyproject.toml`, which hatchling's `force-include` will not do for them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(tmp)
        for pack in PACKS:
            theirs = repo / "labs" / "multiplant" / pack
            ours = Path(tmp) / SHIPPED / pack
            wanted = sorted(
                p.relative_to(theirs).as_posix() for p in theirs.rglob("*")
                if p.is_file() and "out" not in p.relative_to(theirs).parts)
            got = sorted(p.relative_to(ours).as_posix()
                         for p in ours.rglob("*") if p.is_file())
            if wanted != got:
                only_lab = sorted(set(wanted) - set(got))
                only_wheel = sorted(set(got) - set(wanted))
                fail(f"{pack}: the wheel's copy is not the lab's. "
                     f"In labs/multiplant/{pack} but not the wheel: "
                     f"{only_lab or 'none'}. In the wheel only: {only_wheel or 'none'}. "
                     f"Add it to [tool.hatch.build.targets.wheel.force-include].")
            differ = [f for f in wanted
                      if not filecmp.cmp(theirs / f, ours / f, shallow=False)]
            if differ:
                fail(f"{pack}: {len(differ)} file(s) differ between "
                     f"labs/multiplant/{pack} and the wheel: {differ}")
            say(f"  {pack}: {len(wanted)} files, byte-for-byte the lab's")


# -------------------------------------------------------------------- the plant


def venv_bin(venv: Path, command: str) -> Path:
    """Where a venv keeps its console scripts. `Scripts` on Windows, `bin`
    everywhere else - the assumption `.venv/bin` is the one this check exists
    to stop anybody making again."""
    if sys.platform == "win32":
        return venv / "Scripts" / f"{command}.exe"
    return venv / "bin" / command


def install(wheel: Path, work: Path) -> Path:
    venv = work / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, cwd=work)
    python = venv_bin(venv, "python")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                   check=True, cwd=work)
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", str(wheel)],
                   check=True, cwd=work)
    return venv


def ask(path: str, token: str | None = None, timeout: float = 15) -> dict:
    url = f"http://{API[0]}:{API[1]}{path}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8"))


def tell(path: str, body: dict, timeout: float = 15) -> dict:
    url = f"http://{API[0]}:{API[1]}{path}"
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8"))


def until(what: str, check, *, ceiling: float, every: float = 3.0):
    """Poll until `check` returns something truthy, and say how long it took.

    The ceiling is a ceiling, not a measurement: it is there so a job that will
    never pass ends, and it is set well above anything a loaded runner has
    done. What is asserted is the state, never the clock.
    """
    started = time.monotonic()
    last = ""
    while time.monotonic() - started < ceiling:
        try:
            got = check()
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            got, last = None, f"{type(exc).__name__}: {exc}"
        if got:
            took = time.monotonic() - started
            say(f"  {what}: yes, after {took:.0f} s")
            return got
        time.sleep(every)
    fail(f"{what}: not after {ceiling:.0f} s. Last thing seen: {last or 'nothing'}")


def port_answers(where: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(where, timeout=5):
            return True
    except OSError:
        return False


def the_port_is_back(where: tuple[str, int], what: str, *, ceiling: float = 30) -> None:
    """Wait for a stopped plant's socket to close, the way the way up waits.

    `stop` returning is not the same thing as the socket being gone: on Windows
    a listening socket can outlive the process that owned it by a moment, and
    one bare connect straight after `stop` failed three CI jobs on 2026-10-16
    on plants that had stopped perfectly well. So this is the same poll for a
    state as everything on the way up. The ceiling is a grace period, not a
    measurement: nothing here asserts a clock.
    """
    until(f"{what} has stopped listening on {where[1]}",
          lambda: not port_answers(where), ceiling=ceiling, every=0.5)


def the_plant_is_what_the_lab_runs(speed: float) -> None:
    """Every fact the handoff asks for, asked of the running plant."""
    until("the dashboard answers", lambda: ask("/health").get("status") == "ok",
          ceiling=300)

    signed_in = until("sign-in works", lambda: tell("/auth/login", SIGN_IN).get("token"),
                      ceiling=120)
    token = str(signed_in)

    specs = ask("/quality/specs", token)
    items = specs.get("items", [])
    counted = [i for i in items if i["characteristic"] == COUNTED and i["counted"]]
    say(f"  specifications on {MATERIAL}: {specs.get('total')} of "
        f"{specs.get('total')}, "
        f"{', '.join(sorted(i['characteristic'] for i in items))}")
    if specs.get("total") != 3 or not counted:
        fail(f"expected the three bottling specifications including a counted "
             f"{COUNTED!r}; got {json.dumps(specs)[:400]}")

    if not port_answers(OPC):
        fail(f"nothing is listening on the OPC replay port {OPC[0]}:{OPC[1]}")
    say(f"  the OPC replay port {OPC[1]} answers")

    def watching() -> dict | None:
        health = ask("/health")
        seen = health.get("watching") or {}
        floor = health.get("floor") or {}
        if seen.get("connected") and seen["connected"] == seen.get("machines") \
                and floor.get("last_check_recorded"):
            say(f"  watching {seen['connected']} of {seen['machines']} machines; "
                f"the floor last recorded a check at {floor['last_check_recorded']}")
            return health
        return None

    until("the floor is watching every machine and recording checks", watching,
          ceiling=420)

    def a_counted_lot() -> dict | None:
        chart = ask(f"/quality/spc/{MATERIAL}/{COUNTED}", token, timeout=30)
        if (chart.get("n") or 0) >= 1:
            say(f"  {chart['n']} lot(s) inspected, {chart.get('inspected')} units "
                f"looked at, {chart.get('rejected')} sent back")
            return chart
        return None

    until(f"a lot has been counted on the p chart (replay speed {speed:g}x)",
          a_counted_lot, ceiling=600)


def print_in_the_words_the_plant_used() -> None:
    """Let this script print what it read. Python writes stdout in the console's
    code page on Windows, where an ellipsis read correctly would raise
    `UnicodeEncodeError` on its way to the log."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(**AS_WORDS)


def main() -> int:
    print_in_the_words_the_plant_used()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, help="the built wheel to prove")
    parser.add_argument("--speed", type=float, default=60.0,
                        help="replay speed; 60 plays a scripted hour in a minute")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    wheel = Path(args.wheel).resolve()
    if not wheel.is_file():
        fail(f"no wheel at {wheel}")

    say("== what the wheel carries ==")
    read_the_wheel(wheel)
    say("== the shipped packs against the lab's ==")
    the_copies_are_copies(repo, wheel)

    # A directory with none of this repository's files in it, and not inside
    # it either: an installation on a customer's machine has no `labs/`, no
    # `src/` to import instead of the installed package, and no fleet file.
    work = Path(tempfile.mkdtemp(prefix="fsmes-plant-")).resolve()
    home = work / "home"
    say(f"== a clean machine: {work} ==")
    if repo in work.parents:
        fail(f"{work} is inside the checkout at {repo}; this proves nothing")

    environment = {**os.environ, "FSMES_HOME": str(home)}
    # Inherited from the runner, a key would turn on screens this check does
    # not look at and does not pay for.
    environment.pop("ANTHROPIC_API_KEY", None)

    venv = install(wheel, work)
    fsmes = venv_bin(venv, "fsmes")
    reported = subprocess.run([str(fsmes), "--version"], check=True, cwd=work,
                              capture_output=True, **AS_WORDS)
    say(f"installed: {reported.stdout.strip()}")

    say("== one command ==")
    say(f"$ fsmes demo {PLANT} --speed {args.speed:g}")
    started = time.monotonic()
    first_start = subprocess.run(
        [str(fsmes), "demo", PLANT, "--speed", str(args.speed)],
        cwd=work, env=environment, capture_output=True, timeout=2700, **AS_WORDS)
    took = time.monotonic() - started
    say(first_start.stdout)
    if first_start.stderr.strip():
        say(first_start.stderr)
    if first_start.returncode != 0:
        fail(f"`fsmes demo {PLANT}` exited {first_start.returncode}")
    say(f"first start took {took:.0f} s on {sys.platform} "
        f"(this includes generating the eight-hour shift, which happens once)")

    try:
        say("== what is on screen ==")
        the_plant_is_what_the_lab_runs(args.speed)
    finally:
        say("== stopping ==")
        stopped = subprocess.run([str(fsmes), "demo", PLANT, "stop"],
                                 cwd=work, env=environment,
                                 capture_output=True, timeout=600, **AS_WORDS)
        say(stopped.stdout + stopped.stderr)
        if stopped.returncode != 0:
            fail(f"`fsmes demo {PLANT} stop` exited {stopped.returncode}")

    for where, what in ((API, "the dashboard"), (OPC, "the OPC server")):
        the_port_is_back(where, what)
    say("  both ports are back")

    say(f"\nThe wheel brought {PLANT} up on {sys.platform} and put it back down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
