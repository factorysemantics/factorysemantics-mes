"""A pretend user walks the plant and leaves a card saying where they got stuck.

Scott, 2026-11-08: "Claude walking the plant as a supervisor and leaving
cards" - what it replaces is him finding problems, reading screens and
re-testing, every night. So, per task:

1. A **persona** - a shift supervisor, a line operator - from
   `labs/walks/<persona>.yaml`: who they are, the lab account they sign in
   with, and five tasks in his words.
2. A **walk**: one headless Claude run (Sonnet, `claude -p`, the Claude Code
   login, never an API key) that plays the persona on a lab plant through a
   headless browser (`fsmes.sim.walk_browser`, the only tools it has). It
   stops when the task is done, when it is stuck, or at the step cap.
3. A **card**: `<out>/<date>-<slug>.md`, one screen, with the screenshots
   beside it in `<out>/shots/`. Three lines in the persona's words are the
   card; the walk section is the evidence; logs go to `<out>/logs/`, never
   into the card.

This is the old night shift's skeleton (`autoloop`: a kill switch, a state
file, a run) with its scoring, brief and self-merge taken out. A walk opens
no PR and merges nothing: a card waits for a judge, and the judge's PR for
Scott. And it is honest about absences the way the night shift was: a walk
that ended without a verdict says so on its card, it is never written up as
"done".

Kill switch: `touch ~/.local/share/fsmes/walk.off`, checked before every
walk.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WALKS = REPO / "labs" / "walks"
HOME = Path.home()
BASE_DIR = HOME / ".local" / "share" / "fsmes" / "walk"
STATE = BASE_DIR / "state.json"
OFF_SWITCH = HOME / ".local" / "share" / "fsmes" / "walk.off"

MAX_STEPS = 20
MODEL = "sonnet"
MAX_BUDGET_USD = 2.0          # per walk; a runaway walk stops at this, not at the week
WALK_TIMEOUT_S = 15 * 60

# What the headless run must not inherit: a key in the lab's env file is not
# the login the walks run on (the lab's key is refused since 2026-11-05), and
# the crew's own variables would tell the run it is a crew member.
DROPPED_ENV = ("ANTHROPIC_API_KEY", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CREW_ROLE")


class WalkError(ValueError):
    """A persona file or an account that cannot be walked, in words."""


@dataclass(frozen=True)
class Task:
    slug: str
    task: str
    expect: str


@dataclass(frozen=True)
class Persona:
    name: str
    role: str
    account: str
    password_env: str
    who: str
    tasks: tuple[Task, ...]

    def task(self, slug: str) -> Task:
        for t in self.tasks:
            if t.slug == slug:
                return t
        raise WalkError(f"{self.name} has no task {slug!r}; its {len(self.tasks)} "
                        f"task(s): {', '.join(t.slug for t in self.tasks)}")


# ------------------------------------------------------------------ personas

def personas(walks: Path | None = None) -> list[str]:
    """The persona names there are, from the files in `labs/walks/`."""
    return sorted(p.stem for p in (walks or WALKS).glob("*.yaml"))


def load(persona: str | Path, walks: Path | None = None) -> Persona:
    """Read one persona file, refusing one that is missing a field."""
    import yaml  # PyYAML arrives with uvicorn[standard], a runtime dependency

    path = Path(persona)
    if path.suffix != ".yaml":
        path = (walks or WALKS) / f"{persona}.yaml"
    if not path.is_file():
        known = personas(walks)
        raise WalkError(f"no persona {str(persona)!r}; {len(known)} known: {', '.join(known) or 'none'}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [k for k in ("persona", "role", "account", "password_env", "who", "tasks")
               if not raw.get(k)]
    if missing:
        raise WalkError(f"{path.name} is missing {', '.join(missing)}")
    tasks = []
    for n, t in enumerate(raw["tasks"], 1):
        gaps = [k for k in ("slug", "task", "expect") if not (t or {}).get(k)]
        if gaps:
            raise WalkError(f"{path.name}: task {n} is missing {', '.join(gaps)}")
        tasks.append(Task(str(t["slug"]), " ".join(str(t["task"]).split()),
                          " ".join(str(t["expect"]).split())))
    slugs = [t.slug for t in tasks]
    twice = sorted({s for s in slugs if slugs.count(s) > 1})
    if twice:
        raise WalkError(f"{path.name}: task slug(s) used twice: {', '.join(twice)}")
    return Persona(str(raw["persona"]), str(raw["role"]), str(raw["account"]),
                   str(raw["password_env"]), " ".join(str(raw["who"]).split()), tuple(tasks))


def password_for(account: str, password_env: str) -> str:
    """The account's password: the environment variable the persona names,
    else the lab-only default the lab seed gave that account. Never printed."""
    from fsmes import plant

    if os.environ.get(password_env):
        return os.environ[password_env]
    for code, _name, password, _role in plant.LAB_USERS:
        if code == account:
            return password
    raise WalkError(f"no password for {account}: {password_env} is not set and "
                    f"{account} is not a lab account")


# --------------------------------------------------------------- the switch

def disabled() -> str | None:
    """The kill switch, and why the walker honoured it."""
    if OFF_SWITCH.exists():
        return f"{OFF_SWITCH} exists - remove it to walk again"
    if os.environ.get("MES_WALK", "").strip().lower() in ("0", "off", "false"):
        return "MES_WALK=off in the environment"
    return None


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ the walk

def prompt_for(persona: Persona, task: Task, base: str, cap: int = MAX_STEPS) -> str:
    return "\n".join([
        f"You are a {persona.role} at a plant. {persona.who}",
        "",
        f"You are signed in to the plant's MES at {base} as yourself. Your boss",
        f"asked you, in these words: \"{task.task}\"",
        "",
        "Do it the way this person would. The walk tools are your hands and",
        "eyes on the screen and the only tools you have: start with `look`.",
        "You know the job, not the software - find your way by the menus and",
        "what the screens say, the way a person new to this MES would.",
        f"You have {cap} steps. Accounts, roles and settings are not yours to",
        "touch.",
        "",
        "When the task is done, or you are stuck - the screen does not have",
        "what you need, or it will not let you - call `finish`. Stuck is a good",
        "answer: an honest stuck is worth more than a done that is not. Do not",
        "say done unless you saw it done on the screen.",
        "",
        "Write `happened` as three short lines, first person, past tense, plain",
        "words, the way you would tell your boss, for example:",
        "\"I went to Quality and picked the last two months.\"",
        "\"I looked for an export button; there is none.\"",
        "\"I could copy the table by hand, but not as a file.\"",
    ])


def _command(prompt: str, mcp_config: Path) -> list[str]:
    return ["claude", "-p", prompt,
            "--model", MODEL,
            "--output-format", "json",
            "--no-session-persistence",
            "--tools", "",
            "--strict-mcp-config", "--mcp-config", str(mcp_config),
            "--allowedTools", "mcp__walk",
            "--permission-mode", "dontAsk",
            "--max-budget-usd", f"{MAX_BUDGET_USD:.2f}"]


def walk_one(persona: Persona, task: Task, base: str, out: Path, when: str,
             account: str | None = None, echo=print, runner=subprocess.run) -> dict:
    """One task, one headless run. Returns what the card is written from."""
    account = account or persona.account
    card_id = f"{when}-{task.slug}"
    shots, logs = out / "shots", out / "logs"
    shots.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    result_path = logs / f"{card_id}-result.json"
    # A second walk of the same task the same day (--again) starts clean: a
    # screenshot or a step left from the first would be evidence of a walk
    # this card is not about.
    result_path.unlink(missing_ok=True)
    (logs / f"{card_id}-steps.jsonl").unlink(missing_ok=True)
    for old in shots.glob(f"{card_id}-before-*.png"):
        old.unlink()

    secret = password_for(account, persona.password_env)
    # A directory of its own, outside every checkout: a run started inside a
    # repository reads that repository's CLAUDE.md, and the walker is a
    # person on the floor, not a member of whatever crew works there.
    scratch = Path(tempfile.mkdtemp(prefix="fsmes-walk-"))
    config = scratch / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"walk": {
        "command": sys.executable, "args": ["-m", "fsmes.sim.walk_browser"],
        "env": {"FSMES_WALK_BASE": base, "FSMES_WALK_ACCOUNT": account,
                "FSMES_WALK_SHOTS": str(shots), "FSMES_WALK_PREFIX": card_id,
                "FSMES_WALK_RESULT": str(result_path),
                "FSMES_WALK_LOG": str(logs / f"{card_id}-steps.jsonl"),
                "FSMES_WALK_STEPS": str(MAX_STEPS)}}}}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
    env["FSMES_WALK_SECRET"] = secret      # inherited by the browser server only

    echo(f"walking {persona.name}/{task.slug} on {base} as {account} "
         f"({MODEL}, at most {MAX_STEPS} steps)…")
    run_log: dict = {}
    why = ""
    try:
        done = runner(_command(prompt_for(persona, task, base), config), cwd=scratch,
                      env=env, capture_output=True, text=True, encoding="utf-8",
                      timeout=WALK_TIMEOUT_S)
        try:
            run_log = json.loads(done.stdout or "{}")
        except ValueError:
            run_log = {"raw": (done.stdout or "")[-4000:]}
        run_log["returncode"] = done.returncode
        run_log["stderr"] = (done.stderr or "")[-2000:]
        if done.returncode != 0:
            why = f"the run exited {done.returncode}"
    except subprocess.TimeoutExpired:
        why = f"the run hit the {WALK_TIMEOUT_S // 60}-minute timeout"
    except FileNotFoundError:
        why = "`claude` is not on PATH here - run from a login shell"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    (logs / f"{card_id}-run.json").write_text(json.dumps(run_log, indent=1), encoding="utf-8")

    try:
        verdict = json.loads(result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        verdict = None
    shot_names = sorted((p.name for p in shots.glob(f"{card_id}-before-*.png")),
                        key=lambda n: int(n.rsplit("-", 1)[1].split(".")[0]))
    result = {
        "card": card_id, "persona": persona.name, "slug": task.slug, "task": task.task,
        "plant": base, "account": account,
        "cost_usd": run_log.get("total_cost_usd"), "turns": run_log.get("num_turns"),
    }
    if verdict:
        result.update(outcome=verdict["outcome"], happened=verdict["happened"],
                      stuck_where=verdict.get("stuck_where", ""),
                      hurdles=verdict.get("hurdles", ""),
                      expected=verdict.get("expected") or task.expect,
                      risk=verdict.get("risk", "UI"), steps=verdict.get("steps"),
                      shots=verdict.get("shots") or shot_names)
    else:
        steps = len(shot_names)
        if not why:
            why = (f"it used all {MAX_STEPS} steps" if steps >= MAX_STEPS
                   else "it stopped without saying")
        result.update(outcome="unfinished", happened=[
            f"The walk ended before it said done or stuck: {why}.",
            f"It took {steps} step(s); the screenshots show how far it got.",
            "Nothing on this card is a finding yet.",
        ], stuck_where="", hurdles="", expected=task.expect, risk="UI",
            steps=steps, shots=shot_names)
    return result


# ------------------------------------------------------------------ the card

def card_text(result: dict) -> str:
    """The card, one screen, in the order the handoff set."""
    shots = result.get("shots") or []
    if shots:
        before = f"![before](shots/{shots[0]})"
        if len(shots) > 1:
            before += f" · where it stopped: ![where it stopped](shots/{shots[-1]})"
    else:
        before = "(no screenshot was taken)"
    outcome = result["outcome"]
    if outcome == "stuck" and result.get("stuck_where"):
        walked = f"stuck - {result['stuck_where']}"
    else:
        walked = outcome
    steps = result.get("steps")
    lines = [
        "---",
        f"card: {result['card']}",
        f"persona: {result['persona']}",
        f"plant: {result['plant']}",
        "status: waiting-on-scott",
        f"risk: {result.get('risk') or 'UI'}",
        "pr:",
        "---",
        f"# {result['task']}",
        "",
        f"**Before:** {before}",
        "",
        "**After:** (the judge adds it)",
        "",
        "## What happened",
        "",
        *[f"- {line}" for line in result["happened"][:3]],
        "",
        "## Walk",
        "",
        f"- **Walk:** {walked}"
        + (f" ({steps} of {MAX_STEPS} steps)" if steps is not None else ""),
        f"- **Hurdles:** {result.get('hurdles') or 'none said'}",
        f"- **Expected:** {result.get('expected') or 'not said'}",
        "",
    ]
    return "\n".join(lines)


def write_card(result: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result['card']}.md"
    path.write_text(card_text(result), encoding="utf-8")
    return path


# ------------------------------------------------------------------- the run

def run(persona_name: str, base: str, out: Path, task: str | None = None,
        account: str | None = None, again: bool = False, echo=print,
        runner=subprocess.run, when: str | None = None) -> list[Path]:
    """Walk one task, or every task a persona has. Returns the cards written.

    One try a day per task: a task already walked today is skipped unless
    `again` says to walk it twice. The kill switch is read before each walk,
    so touching it stops a run between two walks.
    """
    persona = load(persona_name)
    tasks = [persona.task(task)] if task else list(persona.tasks)
    when = when or datetime.now().strftime("%Y-%m-%d")
    base = base.rstrip("/")
    cards: list[Path] = []
    for t in tasks:
        why_not = disabled()
        if why_not:
            echo(f"walks disabled: {why_not}")
            break
        key = f"{persona.name}/{t.slug}"
        state = _state()
        if not again and state.get(key) == when:
            echo(f"  {key}: walked already today - --again to walk it twice")
            continue
        result = walk_one(persona, t, base, out, when, account=account, echo=echo,
                          runner=runner)
        card = write_card(result, out)
        state = _state()
        state[key] = when
        _save_state(state)
        (out / "logs" / f"{result['card']}.json").write_text(
            json.dumps(result, indent=1), encoding="utf-8")
        cost = result.get("cost_usd")
        spent = f"${cost:.2f}" if isinstance(cost, (int, float)) else "cost unknown"
        echo(f"  {result['outcome']:<10} {card}  ({spent})")
        cards.append(card)
    return cards
