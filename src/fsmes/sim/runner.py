"""One scored run: a fresh plant, a scripted hour, a scorecard, then nothing.

The run is ephemeral by construction - its own database, its own ports from a
reserved range, torn down when scoring is finished. That is what lets a sweep
run twelve of these at once without any of them noticing each other, and it is
the same isolation-by-construction the multi-plant lab proved.

The MES is fed and read only through OPC UA and its HTTP API. Nothing here
touches the database directly, so a scenario this runner cannot express is a
gap in the product's own surface - which is the point.
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fsmes import plant as plants
from fsmes.sim.score import score_run
from fsmes.sim.truth import load_truth

# Reserved so an ephemeral run can never collide with the persistent plants on
# 8010/8020 and 4841/4842 - nor with the fleet console, which is a
# long-running process a person leaves open and whose port was inside this
# range until 2026-09-14. `fsmes.fleet.console.PORT` must stay outside it,
# and `tests/test_fleet_console.py` is what makes that a rule rather than a
# coincidence: a run that takes a console's port replaces the page somebody
# is watching with a plant's sign-in screen, and nothing says it happened.
API_RANGE = (8100, 8199)
OPC_RANGE = (4900, 4999)

def _whoami() -> str:
    try:
        return getpass.getuser()
    except Exception:                 # no passwd entry (a container, a service)
        return "unknown"


#: Where one run tells the others which ports it has taken. A port is claimed
#: for the whole of a run, not for the instant it was probed.
#:
#: Per user, not per machine: the lock coordinates *these* runs, and the bind
#: probe already covers everything else on the box. A directory shared between
#: two accounts would hand the second one a permission error instead of a port.
PORT_LOCKS = Path(tempfile.gettempdir()) / f"fsmes-ports-{_whoami()}"

#: A run that has held a port for longer than this is dead. The longest thing
#: the lab runs is an hour of line time at 10x - ten minutes - and the whole
#: ephemeral-plant machinery exists to be torn down, so an hour is generous by
#: a wide margin. A lock older than this is reclaimed rather than wedging the
#: range permanently when a run is killed.
PORT_LOCK_STALE_S = 3600.0


@dataclass
class Reservation:
    """A port this machine has promised to one run, and how to give it back."""

    port: int
    lock: Path

    def release(self) -> None:
        self.lock.unlink(missing_ok=True)


def bindable(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def reserve_port(low: int, high: int, what: str = "port") -> Reservation:
    """Take a port for the length of a run, not for the length of a probe.

    Probing with a bind and closing the socket again says a port was free a
    moment ago, which is a different claim from "this port is mine". Two runs
    started seconds apart both probed 8100, both found it free, and the second
    one's plant came up on a port the first one's API was about to take - so a
    read in the middle of a run answered *connection refused*. Seen on this
    box on 2026-09-14 when a two-plant starter ran beside other ephemeral
    plants; alone, the same plan was clean.

    So the claim is written down. `O_EXCL` is the atomic primitive on both
    platforms (`fsmes.core.oplock` settled that already, and this MES runs on
    plant PCs), the holder writes its pid, and a lock left by a killed run is
    reclaimed once it is stale. The bind probe still happens, for everything
    on this machine that is not one of these runs.
    """
    from fsmes.core.oplock import WriterBusy, claim_lock

    held, busy = 0, 0
    for port in range(low, high + 1):
        lock = PORT_LOCKS / f"{port}.lock"
        try:
            claim_lock(lock, PORT_LOCK_STALE_S)
        except WriterBusy:
            held += 1
            continue
        if not bindable(port):
            # Something that is not one of these runs is listening - a
            # standing plant, somebody's editor, the console. Give the claim
            # straight back rather than holding a port we cannot use.
            lock.unlink(missing_ok=True)
            busy += 1
            continue
        return Reservation(port=port, lock=lock)
    raise RuntimeError(
        f"No free {what} in {low}-{high}: {held} held by other runs on this machine, "
        f"{busy} in use by something else.")


def reserve_ports() -> tuple[Reservation, Reservation]:
    """The two ports one ephemeral plant needs, or neither of them.

    A run that took an API port and then found no OPC port would leave the
    first one claimed by a run that never started. It would be reclaimed when
    it went stale, but an hour of a range quietly shrinking is not a thing to
    leave for the staleness rule to mop up.
    """
    api = reserve_port(*API_RANGE, "API port")
    try:
        return api, reserve_port(*OPC_RANGE, "OPC port")
    except BaseException:
        api.release()
        raise


def _login(base: str, code: str = "SCOTT", password: str = "operator",
           attempts: int = 30) -> str:
    body = json.dumps({"code": code, "password": password}).encode()
    last = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(
                f"{base}/auth/login", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r)["token"]
        except (urllib.error.URLError, OSError, KeyError) as exc:
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"Could not sign in to {base}: {last}")


def _get(base: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{base}{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)




# The agent cannot sample faster than this usefully, and a real plant would
# never be asked to.
MIN_PUBLISH_MS = 50
DEFAULT_PUBLISH_MS = 500


def publish_interval_ms(truth: dict, speed: float) -> int:
    """Sample fast enough to see the briefest thing the script does.

    Replay compresses line time, so the shortest scripted event decides how
    often the agent must look. At 60x a 12-second jam lasts 200 ms of wall
    clock; sampled every 500 ms it is invisible, and scoring it as "missed"
    would blame the MES for the harness's own blind spot.
    """
    durations = [(e.end_s - e.start_s) for e in truth["events"]
                 if e.start_s is not None and e.end_s is not None]
    if not durations:
        return DEFAULT_PUBLISH_MS
    from fsmes.sim.score import SAMPLES_TO_RESOLVE
    needed_s = min(durations) / (SAMPLES_TO_RESOLVE * speed)
    return max(MIN_PUBLISH_MS, min(DEFAULT_PUBLISH_MS, int(needed_s * 1000)))


# The most machines one timeline request will draw (the route's own cap).
TIMELINE_PAGE = 60


def _timeline_for(base: str, token: str, hours: float, codes: list[str]) -> dict:
    """The state timeline of exactly the machines the script names.

    Asked for by name and in pages, because the endpoint is scoped for a
    screen - one line, a screenful of machines - and a factory has several
    lines. Unnamed, it returned the most populated line: four of five
    scripted breakdowns were on machines the scorer never received, and it
    called them missed. Machines absent from the merged result stay absent,
    and the scorer scores them unknown.
    """
    codes = [c for c in codes if c]
    if not codes:
        return _get(base, f"/analysis/timeline?hours={hours:.4f}", token)
    merged: dict = {"machines": [], "window": None, "lines": []}
    for i in range(0, len(codes), TIMELINE_PAGE):
        page = codes[i:i + TIMELINE_PAGE]
        part = _get(base, f"/analysis/timeline?hours={hours:.4f}&limit={TIMELINE_PAGE}"
                          f"&equipment={','.join(page)}", token)
        merged["machines"].extend(part.get("machines", []))
        w = part.get("window") or {}
        if w.get("start") and w.get("end"):
            have = merged["window"]
            merged["window"] = {
                "start": min(have["start"], w["start"]) if have else w["start"],
                "end": max(have["end"], w["end"]) if have else w["end"],
            }
        if part.get("line") and part["line"] not in merged["lines"]:
            merged["lines"].append(part["line"])
    if merged["window"]:
        span = (datetime.fromisoformat(merged["window"]["end"])
                - datetime.fromisoformat(merged["window"]["start"]))
        merged["window"]["hours"] = round(span.total_seconds() / 3600, 4)
    merged["machines_shown"] = len(merged["machines"])
    merged["machines_total"] = len(codes)
    return merged


def log_files(path: Path) -> list[Path]:
    """A component's structured log and the rotated files before it, oldest
    first: component.jsonl.5 ... component.jsonl.1, component.jsonl. A busy
    hour writes more than one file, and the first of them holds the start."""
    rotated = sorted((p for p in path.parent.glob(path.name + ".*") if p.suffix.lstrip(".").isdigit()),
                     key=lambda p: -int(p.suffix.lstrip(".")))
    return [*rotated, path]


def _json_lines(path: Path) -> list[dict]:
    """The structured log of one component, rotated files included, or
    nothing if it kept none."""
    text = ""
    for part in log_files(path):
        try:
            text += part.read_text(encoding="utf-8", errors="replace")
            if not text.endswith("\n"):
                text += "\n"
        except OSError:
            continue
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# How long after its first report the agent's lag is startup, not steady state.
AGENT_WARMUP_S = 60.0


def _steady(reports: list[dict]) -> list[dict]:
    """The agent's reports after its warm-up, by their own timestamps; all of
    them when the timestamps cannot be read."""
    stamps = []
    for r in reports:
        try:
            stamps.append(datetime.fromisoformat(str(r.get("timestamp")).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            return reports
    if not stamps:
        return reports
    start = min(stamps)
    return [r for r, t in zip(reports, stamps, strict=True) if (t - start).total_seconds() >= AGENT_WARMUP_S]


def pipeline_report(log_dir: Path, interval_s: float,
                    speed: float | None = None, wall_s: float | None = None) -> dict:
    """How far the harness's own pipeline fell behind, from what the replay
    and the agent wrote about themselves.

    A scorecard compares what the MES recorded against a scripted clock. If
    the replay could not keep that clock, or the agent booked readings long
    after they arrived, the comparison is about the pipeline and not about
    the MES - and the card has to say so rather than hand the blame on.
    `sustained` means neither fell more than one sampling interval behind,
    which is the condition under which the card's stated resolution is true:
    False as soon as either is known to have, True only when both are known
    to have kept up, None while either is unknown (a component that kept no
    log is unknown, not fine). `replay_sustainable_speed` is the speed the
    replay actually achieved, so the next run can ask for one it can keep.
    """
    replay = _json_lines(Path(log_dir) / "opc-replay.jsonl")
    agent = _json_lines(Path(log_dir) / "opc-agent.jsonl")

    behind = [r for r in replay if r.get("event") == "replay cannot sustain the requested speed"]
    replay_behind = (max((float(r.get("worst_seconds") or r.get("behind_seconds") or 0.0)
                          for r in behind), default=0.0)
                     if replay else None)

    ingestion = [r for r in agent if r.get("event") == "agent ingestion"]
    agent_lag = max((float(r.get("lag_max_s") or 0.0) for r in ingestion), default=None)
    # The agent's first minute is the subscription burst: nine hundred tags
    # answering at once, a backlog it drains in seconds. A lag there says
    # nothing about the hour that follows, so the verdict rests on the
    # steady reports and the startup lag is stated beside it.
    steady = _steady(ingestion)
    agent_lag_steady = max((float(r.get("lag_max_s") or 0.0) for r in steady), default=agent_lag)
    agent_backlog = max((int(r.get("backlog_peak") or 0) for r in ingestion), default=None)
    agent_busy = max((float(r.get("busy") or 0.0) for r in ingestion), default=None)
    agent_readings = sum(int(r.get("readings") or 0) for r in ingestion) if ingestion else None

    known = [x for x in (replay_behind, agent_lag_steady) if x is not None]
    if any(x > interval_s for x in known):
        sustained = False
    elif len(known) == 2:
        sustained = True
    else:
        sustained = None
    sustainable = None
    if replay_behind and speed and wall_s:
        sustainable = round(speed * wall_s / (wall_s + replay_behind), 1)
    return {
        "interval_s": interval_s,
        "replay_max_behind_s": replay_behind,
        "replay_sustainable_speed": sustainable,
        "agent_max_lag_s": agent_lag_steady,
        "agent_max_lag_s_including_startup": agent_lag,
        "agent_warmup_s": AGENT_WARMUP_S,
        "agent_max_backlog": agent_backlog,
        "agent_busy_share": agent_busy,
        "agent_readings": agent_readings,
        "sustained": sustained,
    }


def withhold_verdict(card: dict, why: str) -> dict:
    """Turn a card's verdicts into unknowns, keeping the evidence.

    A run whose own pipeline fell behind is an instrument out of calibration:
    its per-event detail is still worth reading, its headline numbers are
    not worth trending. Principle 4 - unknown, never a misleading number.
    """
    for fault in card.get("faults", []):
        if fault.get("detected") is not None:
            fault["detected"] = fault["recall"] = fault["detected_seconds"] = None
            fault["unknown_because"] = why
    for stop in card.get("planned_stops", []):
        if stop.get("observed"):
            stop["observed"] = False
            stop["unknown_because"] = why
    metrics = card.get("metrics", {})
    metrics.update(breakdown_recall=None, planned_stop_misclassified=None,
                   idle_stop_misclassified=None,
                   faults_scored=0, planned_stops_scored=0, idle_stops_scored=0)
    card["verdict_withheld"] = why
    return card


def _now_mes() -> datetime:
    """Now, in the frame the MES records timestamps in.

    The MES stores naive UTC (`fsmes.db.utcnow`). Stamping a naive *local*
    time here silently shifted every scored window by the machine's UTC
    offset - five hours on this box - and every check came back "not
    observed". Same clock, different representation; the bug was invisible
    because the seconds and minutes matched.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _await_replay(log_path: Path, timeout: float = 45.0, marker: str = "replay online") -> datetime:
    """Block until the replay reports the marker; return that instant."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if marker in log_path.read_text(errors="replace", encoding="utf-8"):
                return _now_mes()
        except OSError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"Replay never reported '{marker}' (see {log_path})")


# How long the replay holds the line still after coming online, so the agent
# has subscribed before the first piece is made. Wall seconds.
REPLAY_HOLD_S = 6.0


def scored_run(
    name: str,
    cfg: dict,
    root: Path,
    speed: float = 60.0,
    line_json: Path | None = None,
    settle: float = 8.0,
    keep_evidence: bool = False,
    triage_log: bool = True,
    echo=print,
    # Where the run's database is. None (the default) is a fresh SQLite file
    # in the run's working directory; a URL is a database the caller made
    # for this run - the large plant's PostgreSQL - which the caller also
    # drops, since a run that made a database it cannot see should not.
    database_url: str | None = None,
    # Called with (base_url, token) once the plant has booted and signed
    # in, before the scripted hour is left to run. The one hook a scenario
    # needs to seed anything the init script (which runs before the API
    # exists) cannot reach through the database directly - workforce and
    # quality-measurement breadth, for the mega-factory spike, added
    # through the public API like every other scenario actor per the
    # dogfood rule. None (the default) changes nothing for an existing run.
    post_boot: Callable[[str, str], None] | None = None,
    # Called with (base_url, token) after the scripted hour, while the plant
    # is still answering and before anything is torn down. Whatever it returns
    # lands on the card as `recorded`. The scorecard reads the three views it
    # scores against; a caller that needs more of the MES's own answer - the
    # lab, which puts bookings and OEE beside the truth - asks for it here
    # rather than keeping a plant alive to ask later. None changes nothing.
    collect: Callable[[str, str], dict] | None = None,
    # Settings this run's plant should have that its pack does not carry.
    # The lab turns the design chat on this way, and tells the plant which
    # experiment it belongs to, without writing either into a pack - a pack
    # is what a plant is, and "part of run 2026-09-14-one-line-bad-hour" is
    # not a fact about a plant. Applied last, so a caller can override the
    # runner's own settings deliberately rather than by accident of ordering.
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Run one plant through its scripted hour and score what it reported."""
    line = Path(line_json) if line_json else root / Path(cfg["replay_dir"]).parent / "line.json"
    if not line.is_file():
        raise FileNotFoundError(
            f"No line description at {line}. The scorer needs it for ground truth."
        )
    truth = load_truth(line, root / cfg["tag_map"])
    duration = truth["duration_s"]
    if not duration:
        raise ValueError(f"{line} does not declare duration_s")

    publish_ms = publish_interval_ms(truth, speed)
    # Claimed for the whole run, and given back in the `finally` below. Two
    # runs started seconds apart used to probe the same port, both find it
    # free, and one of them then read *connection refused* in the middle of
    # its own hour.
    api, opc = reserve_ports()
    api_port, opc_port = api.port, opc.port
    workdir = Path(tempfile.mkdtemp(prefix=f"fsmes-run-{name}-"))
    db = (workdir / "run.db").as_posix()

    env = plants.plant_env(name, cfg, root, speed)
    env.update(
        MES_DATABASE_URL=database_url or f"sqlite:///{db}",
        # Loopback, deliberately. A persistent lab plant is worth reaching from
        # a phone; a run that exists for sixty seconds is not, and inheriting
        # the plant's tailnet bind left the runner dialling an address its own
        # API was not listening on.
        MES_API_HOST="127.0.0.1",
        MES_API_PORT=str(api_port),
        MES_OPC_ENDPOINT=f"opc.tcp://127.0.0.1:{opc_port}/fsmes/{name}-run",
        MES_LOG_DIR=str(workdir / "logs"),
        MES_SIM_SPEED=str(speed),
        MES_OPC_PUBLISH_MS=str(publish_ms),
        MES_REPLAY_HOLD_S=str(REPLAY_HOLD_S),
    )
    env.update({str(k): str(v) for k, v in (extra_env or {}).items()})
    base = f"http://127.0.0.1:{api_port}"
    mes = plants.fsmes_bin()
    procs: list[subprocess.Popen] = []
    log = (workdir / "run.log").open("ab")

    try:
        echo(f"  seeding an ephemeral {name} (api {api_port}, opc {opc_port})")
        subprocess.run([mes, "init-db"], cwd=root, env=env, check=True,
                       stdout=subprocess.DEVNULL)
        # A plant described by a pack is seeded by applying it; a lab tool
        # that composes a configuration in code may still hand this an `init`
        # script, which is what the two scale labs' generators do. A pack can
        # never name one - decision 0022, held by `fsmes pack check`.
        if cfg.get("init"):
            seed = subprocess.run([os.sys.executable, cfg["init"]], cwd=root, env=env,
                                  capture_output=True, text=True)
        else:
            seed = subprocess.run([mes, "pack", "apply", str(cfg["pack"])], cwd=root, env=env,
                                  capture_output=True, text=True)
        if seed.returncode != 0:
            raise RuntimeError(f"seed failed: {(seed.stderr or seed.stdout).strip()}")
        for code, full, password, role in plants.LAB_USERS:
            subprocess.run([mes, "add-user", code, full, password, "--role", role],
                           cwd=root, env=env, capture_output=True)

        echo(f"  replaying {duration}s of line time at {speed}x "
             f"(~{duration / speed:.0f}s wall clock), sampling every {publish_ms}ms")
        # Which data this run replays, in the run's own log. A card that does
        # not say what it replayed cannot be told apart from a card about a
        # different hour, which is how a sweep came to print one run three
        # times and call it a comparison.
        echo(f"  replaying from {env.get('MES_REPLAY_DIR') or 'the plant default'}")
        procs.append(subprocess.Popen([mes, "run-opc-sim", "--replay"], cwd=root,
                                      env=env, stdout=log, stderr=subprocess.STDOUT))

        # Simulated second zero is the moment the replay says it is live, not
        # the moment the process was forked - the OPC server takes about a
        # second to build its address space, and at 60x that second is a whole
        # minute of line time. Waiting for the log line pins t0 to the event
        # instead of to a guess.
        _await_replay(workdir / "run.log", timeout=45.0)

        procs.append(subprocess.Popen([mes, "run-opc-agent"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen([mes, "run-api"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT))
        # The replay held the line still while the agent subscribed; simulated
        # second zero is its first tick, which it announces.
        t0 = _await_replay(workdir / "run.log", timeout=REPLAY_HOLD_S + 30.0, marker="replay ticking")

        token = _login(base)
        # The people part of the plant - operators booking, inspectors
        # recording the dimensional checks, a supervisor closing what they
        # raise - runs through the scored hour like it does on a standing
        # plant, so what is scored is the whole plant and not the machines
        # alone. It signs in over HTTP, so it starts once the API answers.
        procs.append(subprocess.Popen([mes, "run-operations"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT))
        if post_boot is not None:
            post_boot(base, token)
        elif cfg.get("post_boot"):
            # The registry's own post-boot script: the same hook as data, so
            # a scenario's workforce and quality system arrive through the
            # API whether the run was started by a script, the CLI or the
            # sim MCP. It lives beside the plant until teardown - the
            # mega-factory's keeps its inspectors recording checks for the
            # whole hour - and its output lands in the run log.
            procs.append(subprocess.Popen([os.sys.executable, cfg["post_boot"]], cwd=root,
                                          env=env, stdout=log, stderr=subprocess.STDOUT))
        time.sleep(duration / speed + settle)

        hours = max(0.05, (duration / speed + settle) / 3600.0)
        timeline = _timeline_for(base, token, hours, truth.get("equipment") or [])
        downtime = _get(base, f"/analysis/downtime?hours={hours:.4f}", token)
        oee = _get(base, f"/analysis/oee?hours={hours:.4f}", token)

        card = score_run(truth, timeline, t0, speed,
                         observe_interval_s=publish_ms / 1000.0)
        card["plant"] = name
        # The data actually replayed, read off the environment the replay
        # process was given rather than off the caller's intention.
        card["replay_dir"] = env.get("MES_REPLAY_DIR")
        card["line_json"] = str(line)
        # Whether the harness itself kept up. A miss on a run whose own
        # pipeline fell behind is not evidence about the MES, and the card
        # must not read as if it were.
        pipeline = pipeline_report(workdir / "logs", publish_ms / 1000.0,
                                   speed=speed, wall_s=duration / speed)
        card["pipeline"] = pipeline
        card["metrics"]["pipeline_sustained"] = pipeline["sustained"]
        if pipeline["sustained"] is False:
            why = (f"the harness fell behind its own {publish_ms} ms sample: replay "
                   f"{pipeline['replay_max_behind_s']} s, agent {pipeline['agent_max_lag_s']} s")
            withhold_verdict(card, why)
            hint = (f"; the replay sustained about {pipeline['replay_sustainable_speed']}x"
                    if pipeline["replay_sustainable_speed"] else "")
            echo(f"  verdict withheld - {why}{hint}")
        # A fault the MES recorded only after its window is a pipeline that
        # was minutes behind, not an MES that got it wrong; the headline is
        # withheld and the reason named, as for a harness that fell behind.
        if card.get("late_faults"):
            late = ", ".join(f"{f['equipment']} {f['lag_sim_seconds']:.0f} s late" for f in card["late_faults"])
            why = f"the MES recorded a fault only after its window had passed ({late}): behind, not wrong"
            withhold_verdict(card, why)
            echo(f"  verdict withheld - {why}")
        if collect is not None:
            card["recorded"] = collect(base, token)
        card["oee_reported"] = {k: v for k, v in oee.items() if k != "machines"}
        card["downtime_reported"] = {
            "total_seconds": downtime.get("total_seconds"),
            "reasons": downtime.get("reasons"),
            "unlabelled_share": downtime.get("unlabelled_share"),
        }
        # Same frame the MES records in, like every other stamp here.
        card["scored_at"] = _now_mes().isoformat()
        card["evidence_dir"] = str(workdir) if keep_evidence else None

        # Read the log before the evidence is discarded. The scorecard answers
        # the questions we thought to ask; this is the pass that looks for what
        # nobody asserted.
        if triage_log:
            from fsmes.sim import triage as triage_mod

            card["triage"] = triage_mod.triage(card, triage_mod.read_log(workdir))
            found = card["triage"].get("findings", [])
            if found:
                echo(f"  triage: {len(found)} finding(s), worst "
                     f"{card['triage'].get('worst')}")

        return card

    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        log.close()
        api.release()
        opc.release()
        # A swept run keeps its evidence; the results store records where
        # it is so a retention pass can find it later.
        if not keep_evidence:
            shutil.rmtree(workdir, ignore_errors=True)
