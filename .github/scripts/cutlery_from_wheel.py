#!/usr/bin/env python3
"""Prove a built wheel brings the cutlery plant up, on Linux and on Windows,
with no checkout in sight.

`plant_from_wheel.py` beside this does the same for bottling; this is its
sibling for the 83-machine cutlery plant Scott shows from his own PC
(2026-11-12), and borrows its helpers rather than spelling them twice. One
plant at a time: the wheel-plant job runs this after bottling is down.

What it asserts, in order:

* the wheel carries the cutlery pack - plant.toml, line.json, floor.json,
  tag_map.json and masterdata/ - and none of the lab's scripts or its `out/`;
* the shipped files are byte-for-byte `labs/cutlery`'s;
* after `fsmes demo cutlery`: `/masterdata/equipment` lists 83 machines, the
  order book holds two orders on every line, the floor's OEE sort answers
  with every machine on it, and some machine has an OEE - a figure, not
  "too new" and not empty;
* `fsmes demo cutlery stop` puts both ports back before it returns.

Facts, never seconds: every wait is a poll for a state with a generous
ceiling, and the times printed are information, not gates.

Usage:  python .github/scripts/cutlery_from_wheel.py --wheel dist/<name>.whl
"""

from __future__ import annotations

import argparse
import collections
import filecmp
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("plant_from_wheel", HERE / "plant_from_wheel.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

PLANT = "cutlery"
#: `[serve]` in `labs/cutlery/plant.toml`, named rather than parsed so a pack
#: that moves its port fails here loudly.
base.API = ("127.0.0.1", 8040)
base.OPC = ("127.0.0.1", 4844)
base.PACKS = (PLANT,)
#: The files that are the pack. The rest of `labs/cutlery` is the lab's
#: tooling and a pack carries no code (decision 0022).
PACK_FILES = ("plant.toml", "line.json", "floor.json", "tag_map.json")
MACHINES = 83

say, fail, ask, tell, until = base.say, base.fail, base.ask, base.tell, base.until


def the_copies_are_copies(repo: Path, wheel: Path) -> None:
    theirs = repo / "labs" / PLANT
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(tmp)
        ours = Path(tmp) / base.SHIPPED / PLANT
        wanted = sorted([*PACK_FILES, *(p.relative_to(theirs).as_posix()
                                        for p in (theirs / "masterdata").rglob("*") if p.is_file())])
        got = sorted(p.relative_to(ours).as_posix() for p in ours.rglob("*") if p.is_file())
        if wanted != got:
            fail(f"{PLANT}: the wheel's copy is not the lab's. In labs/{PLANT} but not the "
                 f"wheel: {sorted(set(wanted) - set(got)) or 'none'}. In the wheel only: "
                 f"{sorted(set(got) - set(wanted)) or 'none'}.")
        differ = [f for f in wanted if not filecmp.cmp(theirs / f, ours / f, shallow=False)]
        if differ:
            fail(f"{PLANT}: {len(differ)} file(s) differ between labs/{PLANT} and the wheel: {differ}")
        scripts = [f for f in got if f.endswith(".py")]
        if scripts:
            fail(f"{PLANT}: the wheel carries the lab's scripts: {scripts}")
        say(f"  {PLANT}: {len(wanted)} files, byte-for-byte the lab's, no scripts")


def the_plant_is_the_lab_s(speed: float) -> None:
    until("the dashboard answers", lambda: ask("/health").get("status") == "ok", ceiling=300)
    token = str(until("sign-in works",
                      lambda: tell("/auth/login", base.SIGN_IN).get("token"), ceiling=120))

    equipment = ask("/masterdata/equipment", token)
    levels = collections.Counter(row["level"] for row in equipment)
    say(f"  /masterdata/equipment: {len(equipment)} rows - {levels['work_unit']} machines, "
        f"{levels['work_center']} lines, {levels['area']} areas, a site and an enterprise")
    if levels["work_unit"] != MACHINES:
        fail(f"expected {MACHINES} machines on /masterdata/equipment, got {levels['work_unit']}")

    book = ask("/workorders?limit=500", token)
    per_line = collections.Counter(order["line"] for order in book["items"])
    statuses = collections.Counter(order["status"] for order in book["items"])
    say(f"  order book: {book['total']} orders on {len(per_line)} lines, "
        f"{dict(sorted(statuses.items()))}")
    if book["total"] != len(book["items"]) or set(per_line.values()) != {2} \
            or len(per_line) != levels["work_center"]:
        fail(f"expected two orders on each of {levels['work_center']} lines; got {dict(per_line)}")

    if not base.port_answers(base.OPC):
        fail(f"nothing is listening on the OPC replay port {base.OPC[1]}")
    say(f"  the OPC replay port {base.OPC[1]} answers")

    def an_oee() -> dict | None:
        summary = ask(f"/dashboard/summary?machine_limit={MACHINES + 17}&machine_sort=oee",
                      token, timeout=60)
        page, plant = summary["machines_page"], summary["plant"]
        rated = [m for m in summary["machines"] if (m.get("oee") or {}).get("oee") is not None]
        if page.get("sort") == "oee" and page["total"] == MACHINES and rated:
            say(f"  the OEE sort answers: {page['total']} machines, {len(rated)} with an OEE; "
                f"plant OEE {plant.get('oee')} over {plant.get('oee_machines')} of "
                f"{plant.get('oee_machines_total')}; first: {rated[0]['code']} "
                f"{rated[0]['oee']['oee']:.3f}")
            return summary
        return None

    until(f"some machine has an OEE on the floor's OEE sort (replay speed {speed:g}x)",
          an_oee, ceiling=900, every=10)


def main() -> int:
    base.print_in_the_words_the_plant_used()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, help="the built wheel to prove")
    parser.add_argument("--speed", type=float, default=60.0)
    args = parser.parse_args()

    repo = HERE.parents[1]
    wheel = Path(args.wheel).resolve()
    if not wheel.is_file():
        fail(f"no wheel at {wheel}")

    say("== what the wheel carries ==")
    base.read_the_wheel(wheel)
    say("== the shipped cutlery pack against the lab's ==")
    the_copies_are_copies(repo, wheel)

    work = Path(tempfile.mkdtemp(prefix="fsmes-cutlery-")).resolve()
    if repo in work.parents:
        fail(f"{work} is inside the checkout at {repo}; this proves nothing")
    say(f"== a clean machine: {work} ==")
    environment = {**os.environ, "FSMES_HOME": str(work / "home")}
    environment.pop("ANTHROPIC_API_KEY", None)

    venv = base.install(wheel, work)
    fsmes = base.venv_bin(venv, "fsmes")

    say(f"$ fsmes demo {PLANT} --speed {args.speed:g}")
    started = time.monotonic()
    first = subprocess.run([str(fsmes), "demo", PLANT, "--speed", str(args.speed)],
                           cwd=work, env=environment, capture_output=True, timeout=2700,
                           **base.AS_WORDS)
    say(first.stdout)
    if first.stderr.strip():
        say(first.stderr)
    if first.returncode != 0:
        fail(f"`fsmes demo {PLANT}` exited {first.returncode}")
    say(f"first start took {time.monotonic() - started:.0f} s on {sys.platform}")

    try:
        say("== what is on screen ==")
        the_plant_is_the_lab_s(args.speed)
    finally:
        say("== stopping ==")
        stopped = subprocess.run([str(fsmes), "demo", PLANT, "stop"], cwd=work, env=environment,
                                 capture_output=True, timeout=600, **base.AS_WORDS)
        say(stopped.stdout + stopped.stderr)
        if stopped.returncode != 0:
            fail(f"`fsmes demo {PLANT} stop` exited {stopped.returncode}")

    base.the_ports_are_back_already("the stop")
    say(f"\nThe wheel brought {PLANT} up on {sys.platform} and put it back down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
