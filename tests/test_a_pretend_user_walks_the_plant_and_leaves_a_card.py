"""`fsmes walk`: a pretend supervisor or operator walks a lab plant and
leaves a one-screen card saying where they got stuck.

The walk itself is a headless Claude run driving a browser, and is proved on
a running plant, not here. What is tested here are the promises the
machinery makes, with neither Playwright nor Claude present: the persona
files load and say what the handoff asked; a walk's result becomes exactly
the card; a walk that never said done or stuck is not written up as done;
the kill switch and the one-try-a-day rule are honoured; the step cap is
enforced by the browser side, not by the prompt; the headless run gets no
API key, no password, no shell and no checkout; and the walker's accounts
are a supervisor and an operator, never an admin.
"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from fsmes import plant
from fsmes.sim import walk, walk_browser


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(walk, "BASE_DIR", tmp_path / "walk")
    monkeypatch.setattr(walk, "STATE", tmp_path / "walk" / "state.json")
    monkeypatch.setattr(walk, "OFF_SWITCH", tmp_path / "walk.off")
    monkeypatch.delenv("MES_WALK", raising=False)
    yield


# ------------------------------------------------------------------ personas

def test_there_are_three_personas_and_the_supervisors_first_task_is_the_xlsx_export():
    names = walk.personas()
    assert names == ["engineer", "operator", "supervisor"], f"{len(names)} persona(s): {names}"
    loaded = {n: walk.load(n) for n in names}
    # The supervisor and the operator walk five tasks each; the engineer walks
    # the ones Scott said "walk it" to from his own complaints (2026-11-10).
    assert [len(loaded[n].tasks) for n in ("operator", "supervisor")] == [5, 5]
    assert [t.slug for t in loaded["engineer"].tasks] == ["set-up-tags-in-ui", "replay-real-opc-into-test"]
    supervisor = loaded["supervisor"]
    assert supervisor.tasks[0].slug == "export-quality-xlsx"
    assert supervisor.tasks[0].task == "Export the last two months of quality checks as an xlsx file."


def test_each_persona_signs_in_as_its_own_lab_account_with_the_role_that_job_holds():
    roles = {code: role for code, _name, _password, role in plant.LAB_USERS}
    assert roles[walk.load("supervisor").account] == "supervisor"
    assert roles[walk.load("operator").account] == "operator"
    # The engineer who sets up tags and connections holds the keys in a real
    # plant; the walk has to reach the same screens that person would.
    assert roles[walk.load("engineer").account] == "admin"
    assert len({walk.load(n).account for n in walk.personas()}) == 3


def test_a_persona_file_missing_a_field_is_refused_and_the_field_is_named(tmp_path):
    (tmp_path / "x.yaml").write_text(
        "persona: x\nrole: r\naccount: A\npassword_env: P\nwho: w\n"
        "tasks:\n  - slug: s\n    task: do it\n", encoding="utf-8")
    with pytest.raises(walk.WalkError, match="task 1 is missing expect"):
        walk.load("x", walks=tmp_path)


def test_an_unknown_persona_is_refused_with_the_ones_there_are():
    with pytest.raises(walk.WalkError, match="3 known: engineer, operator, supervisor"):
        walk.load("plant-manager")


def test_the_password_comes_from_the_environment_when_it_is_set_and_the_lab_default_when_not(monkeypatch):
    monkeypatch.delenv("FSMES_WALK_PASSWORD", raising=False)
    lab = dict((c, p) for c, _n, p, _r in plant.LAB_USERS)["WALK-SUP"]
    assert walk.password_for("WALK-SUP", "FSMES_WALK_PASSWORD") == lab
    monkeypatch.setenv("FSMES_WALK_PASSWORD", "from-the-env")
    assert walk.password_for("WALK-SUP", "FSMES_WALK_PASSWORD") == "from-the-env"


# ------------------------------------------------------------------ the card

RESULT = {
    "card": "2026-11-09-export-quality-xlsx",
    "persona": "supervisor",
    "slug": "export-quality-xlsx",
    "task": "Export the last two months of quality checks as an xlsx file.",
    "plant": "http://127.0.0.1:8110",
    "outcome": "stuck",
    "happened": ["I went to Quality and picked two months.",
                 "I looked for an export button; there is none.",
                 "I could copy the table by hand, not as a file."],
    "stuck_where": "Quality, inspection history: no export",
    "hurdles": "The date filter hides behind a When button.",
    "expected": "An Export button beside the filters.",
    "risk": "UI",
    "steps": 4,
    "shots": ["2026-11-09-export-quality-xlsx-before-1.png",
              "2026-11-09-export-quality-xlsx-before-4.png"],
}

BEFORE = ("**Before:** ![before](shots/2026-11-09-export-quality-xlsx-before-1.png) · where it stopped: "
          "![where it stopped](shots/2026-11-09-export-quality-xlsx-before-4.png)")

CARD = """---
card: 2026-11-09-export-quality-xlsx
persona: supervisor
plant: http://127.0.0.1:8110
status: new
risk: UI
pr:
---
# Export the last two months of quality checks as an xlsx file.

{before}

**After:** (the judge adds it)

## What happened

- I went to Quality and picked two months.
- I looked for an export button; there is none.
- I could copy the table by hand, not as a file.

## Walk

- **Walk:** stuck - Quality, inspection history: no export (4 of 20 steps)
- **Hurdles:** The date filter hides behind a When button.
- **Expected:** An Export button beside the filters.
"""


def test_a_walk_result_becomes_exactly_the_card(tmp_path):
    card = CARD.replace("{before}", BEFORE)
    path = walk.write_card(RESULT, tmp_path)
    assert path == tmp_path / "2026-11-09-export-quality-xlsx.md"
    assert path.read_text(encoding="utf-8") == card


def test_the_card_has_no_log_or_tool_trace_in_it():
    text = walk.card_text(RESULT)
    assert "mcp__" not in text and "logs/" not in text
    assert len(text.splitlines()) < 40, "a card is one screen"


# ------------------------------------------------------------- the headless run

def fake_claude(verdict=None, shots=2, cost=0.07, seen=None):
    """Stands in for `claude -p`: plays the browser server's part by writing
    the screenshots and the verdict where the MCP config says, and answers
    the way `--output-format json` does."""
    def runner(cmd, cwd, env, **_kw):
        config_path = Path(cmd[cmd.index("--mcp-config") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        server = config["mcpServers"]["walk"]["env"]
        if seen is not None:
            seen.update(cmd=cmd, cwd=Path(cwd), env=env,
                        config_text=config_path.read_text(encoding="utf-8"))
        for n in range(1, shots + 1):
            Path(server["FSMES_WALK_SHOTS"], f"{server['FSMES_WALK_PREFIX']}-before-{n}.png"
                 ).write_bytes(b"png")
        if verdict:
            Path(server["FSMES_WALK_RESULT"]).write_text(json.dumps(verdict), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"total_cost_usd": cost, "num_turns": shots + 2, "result": "ok"}), "")
    return runner


DONE = {"outcome": "done", "happened": ["I opened the station.", "I booked 120 and 3.",
                                        "The screen said booked."],
        "stuck_where": "", "hurdles": "", "expected": "", "risk": "UI", "steps": 2,
        "shots": []}


def test_a_walk_that_never_said_done_or_stuck_is_written_up_as_unfinished(tmp_path):
    cards = walk.run("operator", "http://127.0.0.1:8110", tmp_path, task="book-output",
                     runner=fake_claude(verdict=None, shots=20), when="2026-11-09",
                     echo=lambda *_: None)
    text = cards[0].read_text(encoding="utf-8")
    assert "**Walk:** unfinished (20 of 20 steps)" in text
    assert "it used all 20 steps" in text
    assert "done" not in text.split("## Walk")[1].replace("done or stuck", "")


def test_a_finished_walk_writes_its_card_and_says_what_it_cost(tmp_path):
    said = []
    cards = walk.run("operator", "http://127.0.0.1:8110/", tmp_path, task="book-output",
                     runner=fake_claude(verdict=DONE, shots=2, cost=0.13),
                     when="2026-11-09", echo=said.append)
    text = cards[0].read_text(encoding="utf-8")
    assert "plant: http://127.0.0.1:8110\n" in text
    assert "**Walk:** done (2 of 20 steps)" in text
    assert "![where it stopped](shots/2026-11-09-book-output-before-2.png)" in text
    assert any("$0.13" in line for line in said)


def test_the_headless_run_is_sonnet_with_no_shell_no_api_key_and_no_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-refused")
    monkeypatch.setenv("CREW_ROLE", "executor")
    monkeypatch.setenv("FSMES_WALK_PASSWORD", "s3cret-walk")
    seen: dict = {}
    walk.run("supervisor", "http://127.0.0.1:8110", tmp_path, task="export-quality-xlsx",
             runner=fake_claude(verdict=DONE, seen=seen), when="2026-11-09",
             echo=lambda *_: None)
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__walk"
    assert "--strict-mcp-config" in cmd
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "CREW_ROLE" not in seen["env"]
    assert not (seen["cwd"] / ".git").exists() and seen["cwd"] != walk.REPO
    # The password reaches the browser server through the environment only:
    # never on the command line, never in the prompt, never in a file.
    assert "s3cret-walk" not in " ".join(cmd)
    assert "s3cret-walk" not in seen["config_text"]
    assert seen["env"]["FSMES_WALK_SECRET"] == "s3cret-walk"
    assert not seen["cwd"].exists(), "the run's scratch directory is removed afterwards"


# ------------------------------------------------------- switch and one a day

def test_the_kill_switch_stops_the_walks_before_any_run_starts(tmp_path):
    walk.OFF_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    walk.OFF_SWITCH.touch()
    said = []

    def never(*_a, **_k):
        raise AssertionError("claude was started with the kill switch on")

    cards = walk.run("supervisor", "http://127.0.0.1:8110", tmp_path, runner=never,
                     echo=said.append)
    assert cards == []
    assert any("walks disabled" in line and "walk.off" in line for line in said)


def test_mes_walk_off_in_the_environment_is_a_kill_switch_too(monkeypatch):
    monkeypatch.setenv("MES_WALK", "off")
    assert walk.disabled() == "MES_WALK=off in the environment"


def test_a_task_is_walked_once_a_day_unless_again_says_twice(tmp_path):
    runs = []

    def counting(*a, **k):
        runs.append(1)
        return fake_claude(verdict=DONE)(*a, **k)

    quiet = {"echo": lambda *_: None, "when": "2026-11-09", "task": "book-output"}
    walk.run("operator", "http://127.0.0.1:8110", tmp_path, runner=counting, **quiet)
    assert walk.run("operator", "http://127.0.0.1:8110", tmp_path, runner=counting, **quiet) == []
    walk.run("operator", "http://127.0.0.1:8110", tmp_path, runner=counting, again=True, **quiet)
    assert len(runs) == 2


def test_a_second_walk_the_same_day_leaves_no_screenshot_from_the_first(tmp_path):
    quiet = {"echo": lambda *_: None, "when": "2026-11-09", "task": "book-output"}
    walk.run("operator", "http://127.0.0.1:8110", tmp_path,
             runner=fake_claude(verdict=None, shots=5), **quiet)
    walk.run("operator", "http://127.0.0.1:8110", tmp_path, again=True,
             runner=fake_claude(verdict=None, shots=2), **quiet)
    left = sorted(p.name for p in (tmp_path / "shots").iterdir())
    assert left == ["2026-11-09-book-output-before-1.png", "2026-11-09-book-output-before-2.png"]


# ------------------------------------------------------------- the step cap

class FakeDriver:
    def __init__(self):
        self.acts = 0

    async def act(self, kind, **args):
        self.acts += 1
        return f"did {kind}"

    async def screenshot(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")


def test_the_browser_stops_acting_at_the_step_cap_and_still_takes_the_verdict(tmp_path):
    driver = FakeDriver()
    w = walk_browser.Walk(driver=driver, shots=tmp_path / "shots", prefix="d-s",
                          result_path=tmp_path / "result.json", cap=20)

    async def walk_on():
        out = []
        for _ in range(23):
            out.append(await w.step("click", target="Next"))
        return out

    said = asyncio.run(walk_on())
    assert driver.acts == 20, "no action past the cap reached the browser"
    assert "step cap (20 steps) is reached" in said[20][0] and said[20][1] is None
    assert len(list((tmp_path / "shots").iterdir())) == 20
    assert w.finish("stuck", "a\nb\nc", stuck_where="ran out of steps").startswith("Written")
    assert json.loads((tmp_path / "result.json").read_text())["steps"] == 20


def test_the_verdict_is_done_or_stuck_and_nothing_else(tmp_path):
    w = walk_browser.Walk(driver=FakeDriver(), shots=tmp_path, prefix="p",
                          result_path=tmp_path / "result.json")
    assert "nothing was written" in w.finish("probably fine", "a")
    assert not (tmp_path / "result.json").exists()


def test_administration_is_refused_whatever_the_role():
    assert walk_browser.forbidden("http://h:8110/admin/users")
    assert walk_browser.forbidden("http://h:8110/admin")
    assert not walk_browser.forbidden("http://h:8110/dashboard/quality")
    assert not walk_browser.forbidden("http://h:8110/administrative-notes")


def test_one_a_day_is_per_plant_so_bottling_and_machining_are_both_walked(tmp_path):
    quiet = {"echo": lambda *_: None, "when": "2026-11-09", "task": "book-output"}
    first = walk.run("operator", "http://127.0.0.1:8110", tmp_path / "a",
                     runner=fake_claude(verdict=DONE), **quiet)
    second = walk.run("operator", "http://127.0.0.1:8120", tmp_path / "b",
                      runner=fake_claude(verdict=DONE), **quiet)
    assert len(first) == len(second) == 1
