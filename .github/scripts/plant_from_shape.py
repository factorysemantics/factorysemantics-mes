#!/usr/bin/env python3
"""Prove the whole shape path from a built wheel, on Linux and on Windows, with
the network off.

`plant_from_wheel.py` beside this proves the wheel starts *our* bottling line.
This proves the thing a customer actually does, which has four moves and three
machines in it:

1. `pipx install factorysemantics-mes` on a PC that may reach the internet
   once and never again;
2. `fsmes skills unpack plant-from-your-mes ./assistant` - the assistant folder
   comes out of the installed package, offline;
3. the folder is carried to the MES box, which has **no fsmes and no AI on it**,
   and the three commands in `SKILL.md` are run there by a bare `python`,
   writing one `plant-shape.toml`;
4. `fsmes demo from-shape plant-shape.toml --name acme --buffer 20 --yes` back
   on the first PC, and a plant shaped like theirs is on the dashboard.

So this script makes two virtual environments. One has the wheel in it and
plays the engineer's PC. The other has **nothing** in it and plays the MES box:
every skill command runs under that one, which is the only honest way to prove
the scripts are stdlib-only. If a script ever grows an import of `fsmes`, or of
anything off PyPI, this job goes red on both runners.

It runs the whole path twice, for the two reference shapes:

* the **flat** MES - one line, one straight routing, one tag folder per machine,
  no routing table at all. A format-1 shape.
* the **grouped** MES - two areas with cells under them, tags under
  channel/device folders with two machines sharing a device, and a routing table
  with two routings that meet at a shared machine. A format-2 shape.

Both come out of `tests/unknown_mes.py`, a SQL MES invented for this project
with table and column names deliberately unlike anything in `src/fsmes`.
Building that database is fixture setup - it stands in for a plant's MES, which
already exists - and it is the only step here that uses the checkout.

**The network off after install.** Every command after the install runs with
the proxy variables pointed at the discard port, loopback excepted, and the
script proves that by trying one outbound request itself and requiring it to
fail. That catches a converter that phones home through urllib, requests or
httpx - the ways this codebase makes a web request - while leaving the plant's
own loopback sockets alone, which is what it has to do: the dashboard, the OPC
server and the database all talk over 127.0.0.1.

Facts, never seconds: every wait is a poll for a state with a generous ceiling,
and the times printed are information for the Result, not gates.

Usage:  python .github/scripts/plant_from_shape.py --wheel dist/<name>.whl
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

SKILL = "plant-from-your-mes"
#: Where in the wheel the assistant folders live.
SHIPPED = f"fsmes/data/skills/{SKILL}"
#: Files the folder is useless without. Named here rather than globbed so that
#: a `force-include` line somebody forgets is a red job and not a quiet gap.
MUST_CARRY = ("SKILL.md", "FORMAT.md", "scripts/profile.py", "scripts/explain.py",
              "scripts/leakcheck.py", "example/plant-shape.toml",
              "example/plant-shape-grouped.toml", "queries/mssql/05d_routings.sql")
#: `[serve]` in a pack built from a shape - see `fsmes.shape.pack`. Named here
#: so a pack that quietly moves its port fails loudly instead of being tested
#: wherever it went.
API = ("127.0.0.1", 8010)
OPC = ("127.0.0.1", 4841)
#: What a shape-built plant calls its finished goods and its counted
#: characteristic. Generic by design: nothing here is derived from the source
#: MES's own words, which is the whole point of the leak check.
MATERIAL = "FG_01"
COUNTED = "sent_back"
SIGN_IN = {"code": "SCOTT", "password": "operator"}
BUFFER = 20
#: Read a child's output as the words it wrote. `text=True` alone decodes in the
#: console's code page, which on Windows is cp1252, and the CLI writes UTF-8
#: (2026-10-12, #163).
AS_WORDS = {"encoding": "utf-8", "errors": "replace"}

#: The two reference shapes, each the whole path end to end.
SHAPES = (
    {"style": "flat", "name": "acme",
     "why": "one line, one straight routing, one tag folder per machine",
     "areas": 1, "routings": 1, "shared_nodes": 0},
    {"style": "grouped", "name": "brightwater",
     "why": "two areas, a routing that is not one straight line, "
            "two machines under one OPC node",
     "areas": 2, "routings": 2, "shared_nodes": 2},
)


def say(what: str) -> None:
    print(what, flush=True)


def fail(why: str) -> None:
    say(f"::error::{why}")
    sys.exit(1)


def run(what: list[str], *, cwd: Path, env: dict | None = None,
        timeout: int = 1800, allow_failure: bool = False) -> subprocess.CompletedProcess:
    """Run a command, print everything it said, and stop the job if it failed."""
    shown = " ".join(Path(what[0]).name if i == 0 else w for i, w in enumerate(what))
    say(f"  $ {shown}")
    done = subprocess.run(what, cwd=cwd, env=env, capture_output=True,
                          timeout=timeout, **AS_WORDS)
    for stream in (done.stdout, done.stderr):
        if stream and stream.strip():
            say("\n".join(f"    | {line}" for line in stream.rstrip().splitlines()))
    if done.returncode != 0 and not allow_failure:
        fail(f"`{shown}` exited {done.returncode}")
    return done


# ------------------------------------------------------------------- the wheel


def the_wheel_carries_the_folder(wheel: Path, repo: Path) -> None:
    """`unzip -l` by another name, plus the #163 rule: the copy is a copy.

    The folder in the wheel is made at build time from `skills/<name>/`. Two
    copies of anything drift, so this compares them file by file and byte by
    byte, and says to add the `force-include` line when the wheel is short.
    """
    say(f"wheel: {wheel.name}, {wheel.stat().st_size:,} bytes")
    with zipfile.ZipFile(wheel) as zf:
        names = sorted(zf.namelist())
    carried = [n for n in names if n.startswith(f"{SHIPPED}/")]
    say(f"assistant files in the wheel: {len(carried)} of {len(names)} files, under {SHIPPED}/")
    missing = [f for f in MUST_CARRY if f"{SHIPPED}/{f}" not in carried]
    if missing:
        fail(f"the wheel does not carry {', '.join(missing)}. Add them to "
             f"[tool.hatch.build.targets.wheel.force-include] in pyproject.toml.")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(tmp)
        theirs = repo / "skills" / SKILL
        ours = Path(tmp) / SHIPPED
        wanted = sorted(p.relative_to(theirs).as_posix()
                        for p in theirs.rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts)
        got = sorted(p.relative_to(ours).as_posix()
                     for p in ours.rglob("*") if p.is_file())
        if wanted != got:
            fail(f"the wheel's copy of the assistant folder is not the checkout's. "
                 f"In skills/{SKILL}/ only: {sorted(set(wanted) - set(got)) or 'none'}. "
                 f"In the wheel only: {sorted(set(got) - set(wanted)) or 'none'}. "
                 f"Add it to [tool.hatch.build.targets.wheel.force-include].")
        differ = [f for f in wanted
                  if not filecmp.cmp(theirs / f, ours / f, shallow=False)]
        if differ:
            fail(f"{len(differ)} file(s) differ between skills/{SKILL}/ and the "
                 f"wheel: {differ}")
        say(f"  {len(wanted)} files, byte-for-byte the checkout's skills/{SKILL}/")


# -------------------------------------------------------------- the two machines


def venv_bin(venv: Path, command: str) -> Path:
    """`Scripts` on Windows, `bin` everywhere else."""
    if sys.platform == "win32":
        return venv / "Scripts" / f"{command}.exe"
    return venv / "bin" / command


def make_venv(where: Path, *, wheel: Path | None) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(where)], check=True)
    if wheel is None:
        return where
    python = venv_bin(where, "python")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                   check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", str(wheel)], check=True)
    return where


def the_mes_box_has_no_fsmes(python: Path) -> None:
    """The second venv is the MES box, and the MES box has nothing on it.

    Asserted rather than assumed: a bare venv on a runner with a user
    site-packages directory, or with `PYTHONPATH` set by a previous step, is not
    bare, and a skill script that imported `fsmes` would then pass here and fail
    at the customer.
    """
    probe = subprocess.run([str(python), "-c", "import fsmes"],
                           capture_output=True, **AS_WORDS)
    if probe.returncode == 0:
        fail("the venv standing in for the MES box can import fsmes, so running the "
             "skill scripts under it proves nothing about a box that has no fsmes")
    say("  `import fsmes` fails there, as it does on a plant's MES box")
    listed = subprocess.run([str(python), "-m", "pip", "freeze"],
                            capture_output=True, **AS_WORDS)
    installed = [line for line in listed.stdout.splitlines() if line.strip()]
    say(f"  installed packages: {len(installed)}"
        + (f" ({', '.join(installed)})" if installed else " - nothing but the standard library"))


# ------------------------------------------------------------- the network, off


def offline(base: dict) -> dict:
    """The environment every command after the install runs in.

    Pointing the proxy variables at the discard port is what urllib, requests
    and httpx all honour, and loopback is excluded because the plant is made of
    loopback: the dashboard, the OPC server and the database all talk over it.
    This does not stop a raw socket to the outside; `prove_the_network_is_off`
    below says exactly what it does stop, and the suite's own leak check is what
    proves the shape carries nothing worth sending.
    """
    dead = "http://127.0.0.1:9"
    environment = {**base}
    for key in ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
        environment[key] = dead
        environment[key.upper()] = dead
    environment["no_proxy"] = "127.0.0.1,localhost,::1"
    environment["NO_PROXY"] = environment["no_proxy"]
    # Inherited from the runner, a key would turn on screens this check does not
    # look at and does not pay for.
    environment.pop("ANTHROPIC_API_KEY", None)
    return environment


def prove_the_network_is_off(python: Path, environment: dict) -> None:
    code = ("import urllib.request as u\n"
            "try:\n"
            "    u.urlopen('https://pypi.org/simple/', timeout=20).read(16)\n"
            "except Exception as exc:\n"
            "    print(f'refused: {type(exc).__name__}')\n"
            "else:\n"
            "    print('REACHED PYPI')\n")
    done = subprocess.run([str(python), "-c", code], env=environment,
                          capture_output=True, timeout=120, **AS_WORDS)
    said = done.stdout.strip()
    if "REACHED PYPI" in said or not said.startswith("refused"):
        fail(f"the network is not off: a child with this environment said {said!r}")
    say(f"  an outbound https request from this environment {said}")


# ---------------------------------------------------------------- the dashboard


def ask(path: str, token: str | None = None, timeout: float = 30):
    url = f"http://{API[0]}:{API[1]}{path}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    # This script's own requests must not go through the dead proxy either.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8"))


def tell(path: str, body: dict, timeout: float = 30):
    url = f"http://{API[0]}:{API[1]}{path}"
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8"))


def until(what: str, check, *, ceiling: float, every: float = 3.0):
    started = time.monotonic()
    last = ""
    while time.monotonic() - started < ceiling:
        try:
            got = check()
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            got, last = None, f"{type(exc).__name__}: {exc}"
        if got:
            say(f"  {what}: yes, after {time.monotonic() - started:.0f} s")
            return got
        time.sleep(every)
    fail(f"{what}: not after {ceiling:.0f} s. Last thing seen: {last or 'nothing'}")
    raise AssertionError  # unreachable; `fail` exits


def port_answers(where: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(where, timeout=5):
            return True
    except OSError:
        return False


def what_is_on_screen(shape: dict, speed: float) -> None:
    """Every fact the handoff asks of a running plant, asked of this one."""
    until("the dashboard answers", lambda: ask("/health").get("status") == "ok",
          ceiling=300)
    token = str(until("sign-in works",
                      lambda: tell("/auth/login", SIGN_IN).get("token"), ceiling=120))

    nodes = ask("/masterdata/equipment", token)
    units = sorted(n["code"] for n in nodes if n["level"] == "work_unit"
                   and n["code"].startswith("ST_"))
    everywhere = sorted(n["code"] for n in nodes if n["level"] == "area")
    # Seeding a pack also makes the rows its maintenance and dispatch master
    # data refer to, so the plant's own areas are the ones the converter named.
    # `AR_` is the shape path's own prefix; every other area on the list came
    # from somewhere else and is counted but not asserted on.
    areas = [code for code in everywhere if code.startswith("AR_")]
    say(f"  stations on the plant: {len(units)} work unit(s) - {', '.join(units)}")
    say(f"  areas: {len(areas)} from the shape, {len(everywhere)} in all - "
        f"{', '.join(everywhere)}")
    if len(units) != 6:
        fail(f"expected the unknown MES's six machines as work units, got {units}")
    if len(areas) != shape["areas"]:
        fail(f"the {shape['style']} shape should give {shape['areas']} area(s) of its "
             f"own, got {len(areas)} of {len(everywhere)}: {everywhere}")

    if not port_answers(OPC):
        fail(f"nothing is listening on the OPC port {OPC[0]}:{OPC[1]}")
    say(f"  the OPC server on {OPC[1]} answers")

    def watching():
        health = ask("/health")
        seen = health.get("watching") or {}
        if seen.get("connected") and seen["connected"] == seen.get("machines"):
            say(f"  watching {seen['connected']} of {seen['machines']} machines")
            return health
        return None

    until("the floor is watching every machine", watching, ceiling=420)

    def a_counted_lot():
        chart = ask(f"/quality/spc/{MATERIAL}/{COUNTED}", token, timeout=60)
        if (chart.get("n") or 0) >= 1:
            say(f"  {chart['n']} lot(s) counted, {chart.get('inspected')} units looked "
                f"at, {chart.get('rejected')} sent back")
            return chart
        return None

    until(f"a quality_counts row is on the p chart (replay speed {speed:g}x)",
          a_counted_lot, ceiling=600)


# ------------------------------------------------------------------ the pack


def what_the_pack_says(shape: dict, pack: Path) -> None:
    """The three things only the pack can show: the routings, the tag folders,
    and the list of what the shape carried and the plant did not use."""
    import tomllib

    described = tomllib.loads((pack / "plant.toml").read_text(encoding="utf-8"))
    dropped = described.get("from_shape", {}).get("dropped", [])
    asked = described.get("from_shape", {}).get("asked", [])
    if not dropped:
        fail("[from_shape].dropped is empty; every field the shape carries and the "
             "plant cannot use has to be named with a reason")
    say(f"  [from_shape].dropped: {len(dropped)} field(s), each with a reason")
    say(f"  [from_shape].asked: {asked}")
    if [a.get("what") for a in asked] != ["buffer between each pair of machines"]:
        fail(f"the buffer is the one question this path asks; plant.toml says {asked}")

    routings = json.loads((pack / "masterdata" / "routings.json").read_text(encoding="utf-8"))
    firsts = {r["operations"][0]["equipment"] for r in routings if r.get("operations")}
    say(f"  routings.json: {len(routings)} of {len(routings)} - "
        + "; ".join(f"{r['code']}: "
                    + " -> ".join(op["equipment"] for op in r["operations"])
                    for r in routings))
    if len(routings) != shape["routings"]:
        fail(f"the {shape['style']} shape should give {shape['routings']} routing(s), "
             f"got {len(routings)}")
    if shape["routings"] > 1 and len(firsts) < 2:
        fail("two routings that both start at the same machine are one straight line "
             f"twice over, not a branching plant: {firsts}")

    tags = json.loads((pack / "tag_map.json").read_text(encoding="utf-8"))
    machines = tags.get("machines", [])
    folders: dict[tuple, list[str]] = {}
    for entry in sorted(machines, key=lambda m: m["equipment"]):
        folders.setdefault(tuple(entry.get("group") or ()), []).append(entry["equipment"])
    shared = {path: codes for path, codes in folders.items() if len(codes) > 1}
    say(f"  tag_map.json: {len(folders)} OPC folder(s) over {len(machines)} "
        f"machines, {len(shared)} holding more than one machine")
    for path, codes in sorted(shared.items()):
        say(f"    {'/'.join(path) or '(the root)'}: {', '.join(codes)}")
    if len(shared) != shape["shared_nodes"]:
        fail(f"the {shape['style']} shape should put {shape['shared_nodes']} OPC "
             f"folder(s) over more than one machine, got {len(shared)}")


# ---------------------------------------------------------------------- the path


def the_three_commands(bare_python: Path, folder: Path, database: Path,
                       work: Path, environment: dict) -> Path:
    """`SKILL.md`'s three commands, run on the box that has no fsmes on it.

    Run from `work` rather than from the assistant folder so that nothing here
    depends on the scripts being next to their own output, which is how a
    person actually does it.
    """
    scripts = folder / "scripts"
    mapping = work / "mapping.toml"
    shape = work / "plant-shape.toml"
    run([str(bare_python), str(scripts / "profile.py"), "--sqlite", str(database),
         "--propose-mapping", str(mapping)], cwd=work, env=environment)
    # The proposal is a draft a person is meant to correct. Nobody corrects it
    # here, which is the stronger claim: it was right from table and column
    # names alone on a schema this project has never read.
    run([str(bare_python), str(scripts / "profile.py"), "--sqlite", str(database),
         "--mapping", str(mapping), "--out", str(shape)], cwd=work, env=environment)
    run([str(bare_python), str(scripts / "explain.py"), str(shape)],
        cwd=work, env=environment)
    # Not one of the three, and run anyway: `profile.py` refuses to write on a
    # hit, so this re-reads the written file against the database it came from.
    leaked = run([str(bare_python), str(scripts / "leakcheck.py"), "--sqlite",
                  str(database), "--shape", str(shape)], cwd=work, env=environment)
    if "passed" not in leaked.stdout:
        fail("the leak check did not say it passed over the shape file it wrote")
    if not shape.is_file():
        fail(f"the three commands wrote no {shape}")
    say(f"  wrote {shape.name}, {shape.stat().st_size:,} bytes")
    return shape


def one_shape_all_the_way(shape: dict, *, repo: Path, work: Path, fsmes: Path,
                          fsmes_python: Path, bare_python: Path, folder: Path,
                          environment: dict, speed: float) -> None:
    style, name = shape["style"], shape["name"]
    say(f"\n================ the {style} MES: {shape['why']} ================")

    say(f"== fixture: a {style} SQL MES this project has never read ==")
    database = work / f"{style}.db"
    # The only step that uses the checkout, and the only one that needs fsmes:
    # the invented MES's numbers are replayed through the product's own
    # generator so the bottling line is genuinely in there to be recovered.
    # A customer's MES is simply already there.
    run([str(fsmes_python), str(repo / "tests" / "unknown_mes.py"), str(database), style],
        cwd=work)

    say("== the three commands in SKILL.md, on a box with no fsmes ==")
    shape_file = the_three_commands(bare_python, folder, database, work, environment)

    say("== the pack, read before anything is started ==")
    pack = work / f"pack-{style}"
    run([str(fsmes), "shape", "plant", str(shape_file), "--out", str(pack),
         "--name", name, "--buffer", str(BUFFER)], cwd=work, env=environment)
    run([str(fsmes), "pack", "check", str(pack)], cwd=work, env=environment)
    what_the_pack_says(shape, pack)

    say("== one command, and a plant shaped like theirs ==")
    home = work / f"home-{style}"
    started = {**environment, "FSMES_HOME": str(home)}
    began = time.monotonic()
    run([str(fsmes), "demo", "from-shape", str(shape_file), "--name", name,
         "--buffer", str(BUFFER), "--yes", "--speed", str(speed)],
        cwd=work, env=started, timeout=2700)
    say(f"first start took {time.monotonic() - began:.0f} s on {sys.platform} "
        f"(it generates the shift once)")
    try:
        say("== what is on screen ==")
        what_is_on_screen(shape, speed)
    finally:
        say("== stopping ==")
        run([str(fsmes), "demo", name, "stop"], cwd=work, env=started, timeout=600)
    for where, what in ((API, "the dashboard"), (OPC, "the OPC server")):
        if port_answers(where):
            fail(f"{what} is still listening on {where[1]} after stop")
    say("  both ports are back")


def print_in_the_words_the_plant_used() -> None:
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
    parser.add_argument("--only", choices=[s["style"] for s in SHAPES],
                        help="just one of the two reference shapes")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    wheel = Path(args.wheel).resolve()
    if not wheel.is_file():
        fail(f"no wheel at {wheel}")

    say("== what the wheel carries ==")
    the_wheel_carries_the_folder(wheel, repo)

    work = Path(tempfile.mkdtemp(prefix="fsmes-shape-")).resolve()
    say(f"\n== a clean machine: {work} ==")
    if repo in work.parents:
        fail(f"{work} is inside the checkout at {repo}; this proves nothing")

    say("== the engineer's PC: a fresh venv with the wheel in it ==")
    engineer = make_venv(work / ".venv-fsmes", wheel=wheel)
    fsmes = venv_bin(engineer, "fsmes")
    fsmes_python = venv_bin(engineer, "python")
    reported = subprocess.run([str(fsmes), "--version"], check=True, cwd=work,
                              capture_output=True, **AS_WORDS)
    say(f"  installed: {reported.stdout.strip()}")

    say("== the MES box: a fresh venv with nothing in it ==")
    mes_box = make_venv(work / ".venv-bare", wheel=None)
    bare_python = venv_bin(mes_box, "python")
    the_mes_box_has_no_fsmes(bare_python)

    say("== the network, off, for everything from here on ==")
    environment = offline(dict(os.environ))
    prove_the_network_is_off(fsmes_python, environment)

    say("== the assistant folder, out of the installation, offline ==")
    listed = run([str(fsmes), "skills", "list"], cwd=work, env=environment)
    if "inside this installation" not in listed.stdout:
        fail("`fsmes skills list` did not say the folder came out of the installation; "
             "it may have found a checkout above the working directory instead")
    run([str(fsmes), "skills", "unpack", SKILL, str(work / "assistant")],
        cwd=work, env=environment)
    folder = work / "assistant" / SKILL
    if not (folder / "SKILL.md").is_file():
        fail(f"no SKILL.md under {folder} after unpack")
    written = sorted(p.relative_to(folder).as_posix()
                     for p in folder.rglob("*") if p.is_file())
    say(f"  {len(written)} file(s) written to {folder}")
    missing = [f for f in MUST_CARRY if f not in written]
    if missing:
        fail(f"unpacked folder is missing {', '.join(missing)}")

    wanted = [s for s in SHAPES if args.only in (None, s["style"])]
    for shape in wanted:
        one_shape_all_the_way(shape, repo=repo, work=work, fsmes=fsmes,
                              fsmes_python=fsmes_python, bare_python=bare_python,
                              folder=folder, environment=environment, speed=args.speed)

    say(f"\n{len(wanted)} of {len(SHAPES)} reference shape(s) went from an unread SQL "
        f"MES to a running plant on {sys.platform}, with the network off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
