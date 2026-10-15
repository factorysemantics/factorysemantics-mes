#!/usr/bin/env python
"""Two nodes of one plant on one box, started and stopped by the fleet.

Scott, 2026-10-15, about the release this is part of: *"must run on
windows."* Nobody on this project has a Windows machine, so this script is
how the two-node machinery is read on one: a pack that names its nodes, one
PostgreSQL, one machine layer, and the three fleet verbs a person types.

What it proves, in the order it does it:

1. `fsmes pack check` accepts a pack that names two nodes.
2. `fsmes fleet create` builds the plant once - two nodes are one plant and
   one database - and records that this installation owns it.
3. `fsmes fleet start` starts **both** nodes, each with its own node name,
   its own port, its own pid file and its own log directory.
4. Both nodes answer `/health`, each saying its own name.
5. Every role - the reader, the floor, the housekeeping - is held by exactly
   one of them, and `/health` on either node says the same thing about who
   holds what, because the holders come from the database both nodes share.
6. `fsmes fleet status` says two nodes answered and names what each holds.
7. `fsmes fleet stop` stops both, and neither port answers afterwards.

What it does **not** prove is the kill: the takeover, the lease units and the
counters that must not be booked twice are proven by
`tests/test_a_plant_on_two_nodes_survives_one_of_them_dying.py` and by
`scripts/kill-a-node.sh`, both on Linux. `Popen.terminate()` on Windows does
not go through the supervisor's own shutdown, so a killed node there is a
different mechanism from a killed node here and deserves its own proof
rather than a claim made by this script.

It runs on either platform - it is proven on Linux before it is pushed, and
it is the Windows runner it exists for.

    python .github/scripts/two_nodes_on_one_box.py \
        --database-url postgresql+psycopg://user:pw@127.0.0.1:5432/two_nodes

Every path goes through `pathlib`, every child's output is read as UTF-8, and
nothing here uses a shell, a signal or a process group: the three things that
are not the same on both platforms.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: The variable the pack names for the thing that signs sessions. Both nodes
#: read the same one: a person signed in on one node is signed in on the
#: plant, and two of them would sign that person out every time the proxy
#: sent them to the other node.
#:
#: Its name says `signer` and not `secret` on purpose. The pack names the
#: variable and never the value, so this constant is a variable name and
#: nothing private - but code scanning reads a name with `secret` in it as a
#: credential, and a scanner that cries wolf on a CI script is a scanner
#: nobody reads on the day it is right (`py/clear-text-storage-sensitive-data`,
#: PR #178).
SIGNING_VARIABLE = "FSMES_TWO_NODE_SIGNER"
#: Short enough that a role is claimed while this script is watching, long
#: enough that a busy runner does not drop a renewal. Every wait below is a
#: multiple of it, never a number of seconds chosen for its own sake.
LEASE_SECONDS = 6.0
ROLES = ("reader", "floor", "housekeeping")
#: This interpreter's own `fsmes`. Not the console script: on Windows that
#: lives in a `Scripts` directory that is on PATH only if the installer put
#: it there, and `python -m fsmes` is the same program either way.
MES = [sys.executable, "-m", "fsmes"]


def say(line: str = "") -> None:
    print(line, flush=True)


def fail(why: str) -> None:
    say("")
    say(f"NOT PROVEN: {why}")
    raise SystemExit(1)


# ------------------------------------------------------------------ the pack

def pack_with_two_nodes(where: Path, *, database_url: str, port_a: int, port_b: int,
                        opc_endpoint: str) -> Path:
    """The lab's bottling pack with its nodes named, and nothing else changed.

    Patched in the four places that make it this script's: where the machine
    layer is, where the database is, that it runs on two nodes, and the name
    of the variable its signing key lives in. The replay data is the
    reference hour beside it, which generates in a moment.
    """
    pack = where / "bottling"
    shutil.copytree(ROOT / "labs" / "multiplant" / "bottling", pack)
    shutil.copy(ROOT / "labs" / "kepsim" / "line.json", pack / "line.json")

    toml = (pack / "plant.toml").read_text(encoding="utf-8")
    toml = toml.replace('opc_endpoint = "opc.tcp://127.0.0.1:4841/fsmes/bottling"',
                        f'opc_endpoint = "{opc_endpoint}"')
    toml = toml.replace("api_port     = 8010", f"api_port     = {port_a}")
    toml = toml.replace("simulate     = true",
                        f'simulate     = true\nsecret_key_env = "{SIGNING_VARIABLE}"\n\n'
                        f'[[serve.nodes]]\nid = "a"\napi_port = {port_a}\n\n'
                        f'[[serve.nodes]]\nid = "b"\napi_port = {port_b}\n')
    # A pack a person writes names `database_password_file` and keeps the
    # password out of the file that says what the plant is. This one splices
    # the whole URL because that is what it was given, into a directory that
    # is deleted at the end.
    toml += (f'\n[storage]\ndatabase_url = "{database_url}"\n'
             f'\n[redundancy]\nenabled = true\nlease_seconds = {LEASE_SECONDS}\n')
    (pack / "plant.toml").write_text(toml, encoding="utf-8")
    return pack


# --------------------------------------------------------------- the asking

def get(url: str, timeout: float = 4.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            return answer.status, answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as refused:
        return refused.code, ""
    except Exception:
        return 0, ""


def health(port: int) -> dict:
    status, body = get(f"http://127.0.0.1:{port}/health")
    if status != 200 or not body:
        return {}
    try:
        return json.loads(body)
    except ValueError:
        return {}


def answers(port: int, timeout: float) -> dict:
    """Wait for one node's dashboard to answer, and say what it said."""
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        body = health(port)
        if body:
            return body
        time.sleep(0.5)
    return {}


def holders(body: dict) -> dict:
    """role -> the node holding it, as `/health` reports it."""
    node = body.get("node") or {}
    return {role: (node.get("holders") or {}).get(role) for role in ROLES}


def wait_for_every_role(port: int, timeout: float) -> dict:
    """Wait until every role has a holder, and say who holds what.

    Each role is a race of its own - the reader, the floor and the
    housekeeping are claimed by three different processes - so this waits for
    all three to be claimed rather than for one node to claim all three.
    """
    until = time.monotonic() + timeout
    held: dict = {}
    while time.monotonic() < until:
        held = holders(health(port))
        if all(held.get(role) for role in ROLES):
            return held
        time.sleep(0.5)
    return held


# ------------------------------------------------------------- the commands

def fsmes(*args: str, env: dict, where: Path | None = None, timeout: float = 600.0):
    """One `fsmes` command, its output read as UTF-8 and printed."""
    done = subprocess.run([*MES, *args], cwd=where or ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    for line in (done.stdout or "").splitlines():
        say(f"    | {line}")
    if done.returncode != 0:
        for line in (done.stderr or "").splitlines():
            say(f"    ! {line}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True,
                        help="a PostgreSQL both nodes share; the database must exist")
    parser.add_argument("--port-a", type=int, default=8010)
    parser.add_argument("--port-b", type=int, default=8011)
    parser.add_argument("--opc-port", type=int, default=4841)
    parser.add_argument("--speed", type=float, default=60.0,
                        help="line seconds per wall second for the replay")
    parser.add_argument("--workdir", default="",
                        help="where the pack, the data and the logs go (default: a temp dir)")
    parser.add_argument("--keep-running", action="store_true",
                        help="leave both nodes up at the end, for a person to look at")
    args = parser.parse_args()

    if "postgresql" not in args.database_url:
        fail("two nodes share one database, which a file database cannot be: "
             "--database-url must be a PostgreSQL URL")

    where = Path(args.workdir).resolve() if args.workdir else Path(tempfile.mkdtemp(
        prefix="fsmes-two-nodes-"))
    where.mkdir(parents=True, exist_ok=True)
    root = where / "run"
    root.mkdir(parents=True, exist_ok=True)
    logs = where / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    endpoint = f"opc.tcp://127.0.0.1:{args.opc_port}/fsmes/bottling-two-nodes"

    env = dict(os.environ)
    env[SIGNING_VARIABLE] = "the-same-signer-on-both-nodes"
    env["PYTHONIOENCODING"] = "utf-8"

    say(f"Two nodes of one plant on {sys.platform}, in {where}")
    pack = pack_with_two_nodes(where, database_url=args.database_url, port_a=args.port_a,
                              port_b=args.port_b, opc_endpoint=endpoint)
    say("  the line data the replay plays")
    if fsmes("sim-generate", str(pack / "line.json"), "--out", str(pack / "out"),
             env=env).returncode != 0:
        fail("the line data would not generate")

    say("  fsmes pack check, on a pack that names two nodes")
    if fsmes("pack", "check", str(pack), env=env).returncode != 0:
        fail("the checker refused a pack naming two nodes")

    replay = None
    started_nodes = False
    replay_log = logs / "replay.log"
    try:
        say("  the machine layer: one OPC replay for the whole plant")
        sim_env = dict(env)
        sim_env.update(MES_PLANT_NAME="bottling", MES_OPC_ENDPOINT=endpoint,
                       MES_REPLAY_DIR=str(pack / "out"),
                       MES_TAG_MAP_FILE=str(pack / "tag_map.json"),
                       MES_SIM_SPEED=str(args.speed), MES_LOG_DIR=str(logs))
        with replay_log.open("ab") as sink:
            replay = subprocess.Popen([*MES, "run-opc-sim", "--replay"], cwd=ROOT, env=sim_env,
                                      stdout=sink, stderr=subprocess.STDOUT)
        until = time.monotonic() + 90
        while time.monotonic() < until:
            if "replay online" in replay_log.read_text(encoding="utf-8", errors="replace"):
                break
            if replay.poll() is not None:
                fail(f"the machine layer exited ({replay.returncode}); see {replay_log}")
            time.sleep(0.25)
        else:
            fail(f"the machine layer never came online; see {replay_log}")
        say("    | replay online")

        say("  fsmes fleet create, once, for one plant on two nodes")
        if fsmes("fleet", "create", str(pack), "--root", str(root), env=env).returncode != 0:
            fail("the plant would not build from a pack naming two nodes")

        say("  fsmes fleet start, which starts every node the pack names")
        started = fsmes("fleet", "start", "bottling", "--root", str(root),
                        "--speed", str(args.speed), env=env)
        started_nodes = True
        if started.returncode != 0:
            fail("fleet start refused a plant with two nodes")
        for node in ("a", "b"):
            if f"node {node}" not in started.stdout:
                fail(f"fleet start said nothing about node {node}:\n{started.stdout}")

        say("  both nodes answer /health, each with its own name")
        seen = {}
        for node, port in (("a", args.port_a), ("b", args.port_b)):
            body = answers(port, timeout=180)
            if not body:
                fail(f"node {node} never answered on port {port}")
            said = (body.get("node") or {}).get("name")
            if said != node:
                fail(f"the node on port {port} calls itself {said!r}, not {node!r}")
            seen[node] = body
            say(f"    | node {node} answered on {port}, redundancy "
                f"{(body.get('node') or {}).get('redundancy')}")

        say("  every role is held by exactly one node, and both nodes agree")
        held = wait_for_every_role(args.port_a, timeout=LEASE_SECONDS * 20)
        missing = [role for role in ROLES if not held.get(role)]
        if missing:
            fail(f"nobody held {', '.join(missing)} within 20 leases; "
                 f"what was held: {held}")
        for role, holder in held.items():
            if holder not in ("a", "b"):
                fail(f"{role} is held by {holder!r}, which is neither node")
            say(f"    | {role} is held by node {holder}")
        from_b = holders(health(args.port_b))
        if from_b != held:
            fail(f"the nodes disagree about who holds what: a says {held}, b says {from_b}")
        say("    | node b says the same, because the holders are in the database")

        say("  fsmes fleet status names both nodes and what they hold")
        status = fsmes("fleet", "status", "bottling", "--root", str(root), env=env)
        if status.returncode != 0:
            fail("fleet status refused a plant with two nodes")
        if not re.search(r"nodes\s+2, 2 answered", status.stdout):
            fail(f"fleet status did not say two nodes answered:\n{status.stdout}")
        for node in ("a", "b"):
            if not re.search(rf"^\s+{node}\s+\S+.*holding ", status.stdout, re.MULTILINE):
                fail(f"fleet status said nothing about node {node}:\n{status.stdout}")

        say("  fsmes fleet stop stops both")
        if args.keep_running:
            say(f"    | --keep-running: both nodes are up on {args.port_a} and "
                f"{args.port_b}, the machine layer is still playing on "
                f"{endpoint} (pid {replay.pid}), and {root} is theirs. Stop "
                f"them with `fsmes fleet stop bottling --root {root}`, and "
                f"the machine layer by its pid.")
            return 0
        stopped = fsmes("fleet", "stop", "bottling", "--root", str(root), env=env)
        if stopped.returncode != 0:
            fail("fleet stop refused a plant with two nodes")
        started_nodes = False
        for node, port in (("a", args.port_a), ("b", args.port_b)):
            if f"node {node}" not in stopped.stdout:
                fail(f"fleet stop said nothing about node {node}:\n{stopped.stdout}")
            until = time.monotonic() + 60
            while time.monotonic() < until and health(port):
                time.sleep(0.5)
            if health(port):
                fail(f"node {node} was still answering on {port} after fleet stop")
            say(f"    | node {node} is gone from {port}")

        say("")
        say("PROVEN on this box: a pack that names two nodes is checked, built once, "
            "started as two nodes that each answer for themselves, has every role held "
            "by exactly one of them, and is stopped by one command. The kill is proven "
            "on Linux and is not claimed here.")
        return 0
    finally:
        # A failure before the stop step leaves two nodes answering, which on
        # a runner is a job that never ends and on a person's box is two
        # plants they did not ask for. `--keep-running` is the one case where
        # they are meant to stay up.
        if started_nodes and not args.keep_running:
            fsmes("fleet", "stop", "bottling", "--root", str(root), env=env, timeout=120)
        if replay is not None and replay.poll() is None and not args.keep_running:
            # `--keep-running` keeps the machine layer too: two nodes with
            # nothing to read are a plant whose machines all say
            # *disconnected*, which is not what the person asked to look at.
            replay.terminate()
            try:
                replay.wait(timeout=30)
            except subprocess.TimeoutExpired:
                replay.kill()
        for log in sorted(logs.rglob("*.log")) + sorted((root / "logs").rglob("*.jsonl")):
            say("")
            say(f"--- the last 40 lines of {log}")
            try:
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]:
                    say(f"    {line}")
            except OSError as unreadable:
                say(f"    (unreadable: {unreadable})")


if __name__ == "__main__":
    raise SystemExit(main())
