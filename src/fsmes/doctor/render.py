"""The file itself: Markdown, for a person first and an agent second.

Three rules shape every line below.

**A person reads it.** It opens with what it is and what it is not, because
the first question anybody asks of a file they are about to paste into a chat
window is *what is in here*. Nothing is a wall of JSON. The order is the order
a support call takes: what is installed, what is running, what died, what the
log said, and then - beside an incumbent MES - how long this one has been
watching and how well it matched.

**An agent reads it next.** One heading per section, named the same way every
time, so "Processes" and "Scorecard over time" can be found by name rather
than by position. The numbers are in tables, the logs in fenced blocks.

**It fits on a screen's worth of scrolling.** The budget is 400 lines for a
one-plant home, and the logs are what threaten it: six logs of forty lines is
the whole budget by itself. So identical consecutive lines are collapsed with
a count - the OPC agent writes a heartbeat every eight seconds - and if the
file is still over the line, each log carries fewer lines and says so. The
alternative is a file nobody reads to the end, and the end is where the
questions are.

Nothing here reads the disk and nothing here decides what is safe to print:
every string it is given has already been through `scrub`, and the whole file
goes through `leak.verdict` before it is written.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from fsmes import migration
from fsmes.doctor import gather, scrub

#: The handoff's number, and the reason for the trimming below.
BUDGET = 400

#: What this file is, in one line, before anything else in it.
WHAT_IT_IS = (
    "This file describes one installation of FactorySemantics MES: what is "
    "installed, what is running, and what its logs said, with every value "
    "replaced by its shape. It is not a copy of any of your data - no tag "
    "name, order number, product, lot, person, host name or database URL is "
    "in it - and it names the plants in this home and nothing else of yours."
)

#: The sentence the second half prints instead of padding.
NOT_IN_SHADOW = "not in shadow mode — nothing to report yet"


def _span(seconds: float) -> str:
    """A duration in the words a person would use for it."""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} minutes"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def _share(value: float | None) -> str:
    return "not measured" if value is None else f"{value * 100:.1f}%"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _mode(name: str, value: str | None, default: str) -> str:
    """One mode, and whether this plant chose it or the product did.

    A mode nothing set is not a mode nobody knows: it is the default, and
    saying which is the difference between a fact and a blank.
    """
    if value is None or value == "":
        return f"- {name}: `{default}` — nothing sets it, so the product's default"
    return f"- {name}: `{scrub.scrub_text(str(value))}`"


# ----------------------------------------------------------------- the halves


def _installation(report: gather.Report) -> list[str]:
    install = report.install
    # The home is the one path spelled out rather than called `<home>`: a
    # person has to find it, and every other path in this file is written
    # relative to it.
    home = scrub.shape_path(install.home)
    fleet = scrub.shape_path(install.fleet_file, home=install.home)
    out = [
        "## This installation",
        "",
        f"- fsmes **{install.version}**",
        f"- installed with: {install.how}",
        f"- typed as: `{install.invoked_as}`",
        f"- `fsmes` on this PATH: {install.executable_on_path}",
        f"- Python {install.python}, at `{install.interpreter}`",
        f"- OS: {install.os_said}",
        f"- home: `{home}`" + ("" if install.home_exists
                               else " — **this directory does not exist**"),
        f"- fleet file: `{fleet}`" + ("" if install.fleet_exists
                                      else " — **no fleet file: this home has no plant yet**"),
        f"- modules present: {', '.join(install.modules) if install.modules else 'none (kernel only)'}",
    ]
    if not install.skill_folder:
        out.append("- the leak check's own folder is **not** in this installation "
                   "(see the line at the top of this file)")
    for line in report.trouble:
        out.append(f"- **trouble:** {line}")
    out.append("")
    return out


def _the_plants(report: gather.Report) -> list[str]:
    """The list, with its total, before any one plant's detail."""
    out = ["## Plants in this home", ""]
    if not report.plants:
        if report.asked_for:
            out += [f"No plant called `{report.asked_for}` is in this home."]
            if report.home_has:
                out += ["", f"This home has {len(report.home_has)} "
                            f"{'plant' if len(report.home_has) == 1 else 'plants'}: "
                        + ", ".join(f"`{n}`" for n in report.home_has)
                        + ". Run `fsmes doctor` with no `--plant` for all of them."]
            out.append("")
        else:
            out += ["None. This home has no plant in it yet, so everything below this "
                    "section is about the installation only.", ""]
        return out
    out.append(f"{len(report.plants)} "
               f"{'plant' if len(report.plants) == 1 else 'plants'}"
               + (", of the ones this run was asked about" if report.asked_for else "")
               + ":")
    out.append("")
    out.append("| plant | answering | processes alive | shadow mode |")
    out.append("| --- | --- | --- | --- |")
    for plant in report.plants:
        shadow = "off" if plant.shadow.on is False else ("on" if plant.shadow.on else "unknown")
        out.append(f"| `{plant.name}` | {_yes(plant.answering)} | "
                   f"{len(plant.alive)} of {len(plant.processes)} | {shadow} |")
    out.append("")
    return out


def _processes(plant: gather.Plant) -> list[str]:
    out = ["### Processes", ""]
    for note in plant.notes:
        out += [note, ""]
    if not plant.processes:
        out += ["No process is recorded for this plant and its log directory is "
                      "empty or absent: nothing of it has run on this machine.", ""]
        return out
    recorded = [p for p in plant.processes if p.pid]
    if not recorded:
        return out
    out += [f"{len(plant.alive)} of {len(recorded)} alive.", "",
            "| pid | what it is | state | log |", "| --- | --- | --- | --- |"]
    for process in recorded:
        role = process.role or "not recorded by the release that started it"
        out.append(f"| {process.pid} | {role} | "
                   f"{'alive' if process.alive else '**gone**'} | `{process.log}` |")
    if plant.gone and plant.alive and not plant.answering:
        out += ["",
                f"That is a plant that is running and cannot be talked to. "
                f"`fsmes demo {plant.name} stop` stops what is left and clears the "
                f"record; `fsmes demo {plant.name}` starts it again. This is the same "
                f"answer `fsmes demo {plant.name} status` gives."]
    out.append("")
    return out


def _ports(plant: gather.Plant) -> list[str]:
    out = ["### Ports", ""]
    if not plant.ports:
        out += ["This plant's pack asks for no port this run could read.", ""]
        return out
    out += ["| what | address | answers |", "| --- | --- | --- |"]
    for port in plant.ports:
        where = port.host if port.host in ("127.0.0.1", "0.0.0.0", "localhost", "::") else "<host>"
        out.append(f"| {port.what} | `{where}:{port.port}` | {_yes(port.answers)} |")
    out.append("")
    return out


def _shadow(plant: gather.Plant) -> list[str]:
    shadow = plant.shadow
    state = "**on**" if shadow.on else "off"
    out = ["### Shadow mode", "",
           f"- shadow mode: {state} — {shadow.source}"]
    if shadow.on and shadow.closed is not None and shadow.total is not None:
        out.append(f"- {shadow.closed} of {shadow.total} outbound paths closed")
    elif not shadow.on:
        out.append("- this MES may act on its plant (`MES_SHADOW=true` to watch only)")
    out.append(_mode("ERP mode", shadow.erp_mode, "rest"))
    out.append(_mode("namespace (UNS) mode", shadow.uns_mode, "off"))
    out.append(_mode("inbound MQTT mode", shadow.inbound_mode, "off"))
    out.append("")
    return out


def _log(process: gather.Process, per_log: int) -> list[str]:
    """One log, collapsed and trimmed, in a fenced block."""
    # A recorded process is headed by what it is; a log found on the disk with
    # no process behind it is headed by its own file name, because that is
    # what a person will type and what an agent will be asked about.
    if process.pid:
        what = process.role or "a process whose role was not recorded"
    else:
        what = PurePosixPath(process.log).name
    out = [f"### Log — {what}", "", f"`{process.log}`", ""]
    if not process.pid:
        out += ["Nothing is running for this file; it is what the last run left "
                "behind.", ""]
    if not process.log_exists:
        out += ["This log is named by the plant's own record and is **not on the "
                      "disk**: either it was never written or something removed it.", ""]
        return out
    runs = scrub.collapse(list(process.lines))
    shown = runs[-per_log:]
    said = []
    if len(shown) < len(runs):
        said.append(f"the last {len(shown)} of {len(runs)} lines, trimmed to keep this "
                    f"file inside {BUDGET} lines")
    if process.truncated:
        said.append("the log itself is longer than this")
    if process.dropped:
        said.append(f"{process.dropped} console lines that were not part of a traceback "
                    f"were counted and dropped")
    if said:
        out += ["(" + "; ".join(said) + ")", ""]
    out.append("```")
    for line, count in shown:
        out.append(line if count == 1 else f"{line}    (x {count})")
    out += ["```", ""]
    return out


def _one_plant(plant: gather.Plant, per_log: int) -> list[str]:
    out = [f"## {plant.name}", ""]
    if plant.label:
        out.append(f"- what its pack calls it: {plant.label} *(shaped, like every other "
                   f"value in this file)*")
    out += [
        f"- this installation owns it: {_yes(plant.owned)}",
        f"- answering: {_yes(plant.answering)}" + (f" on `{plant.url}`" if plant.url else ""),
        f"- database: `{plant.database}`",
        "",
    ]
    out += _processes(plant)
    out += _ports(plant)
    out += _shadow(plant)
    for process in plant.processes:
        out += _log(process, per_log)
    return out


def _coverage(plant: gather.Plant) -> list[str]:
    out = ["### Coverage over that time", ""]
    account = plant.coverage
    if account is None or account.why_not:
        out += [(account.why_not if account else "not measured"), ""]
        return out
    out += [
        f"- machines on this plant: {account.machines}",
        f"- window: {_span(account.window_seconds)} of machine-time "
        f"({account.machines} machines over {_span((account.until - account.watching_since).total_seconds())})",
        f"- of that, observed: {_span(account.observed_seconds)} — "
        f"**{_share(account.share)}**",
    ]
    if account.by_cause:
        out += ["", "Where the rest of it went:", "",
                "| cause | time |", "| --- | --- |"]
        for cause, seconds in sorted(account.by_cause.items(), key=lambda kv: -kv[1]):
            out.append(f"| `{cause}` | {_span(seconds)} |")
    out += ["",
            "One figure for the whole plant, on purpose: a line per machine would "
            "print this plant's own codes for its machines. The coverage page "
            "(`docs/operate/coverage.md`) says what each cause means."]
    out.append("")
    return out


def _scorecards(plant: gather.Plant) -> list[str]:
    out = ["### Scorecard over time", ""]
    if not plant.scorecards:
        out += [
            "No scorecard has been kept under this home, so there is nothing to line "
            "up by date. One run of this writes one:",
            "",
            "```",
            "fsmes shadow scorecard --incumbent <what the incumbent exported> \\",
            "    --map <your mapping file> --json scorecard.json",
            "```",
            "",
            "It keeps a copy of its figures - counts, not orders - under "
            "`<home>/scorecards/<date>.json`, and the next run of `doctor` lines them "
            "up here.",
            "",
        ]
        return out
    out += [f"{len(plant.scorecards)} kept "
            f"{'scorecard' if len(plant.scorecards) == 1 else 'scorecards'}, oldest first. "
            f"Figures only: the orders and operations themselves stayed on this machine.",
            "",
            "| kept | fields compared | agreed | differed | not stated |",
            "| --- | --- | --- | --- | --- |"]
    for row in plant.scorecards:
        out.append(f"| {row.date} | {row.compared if row.compared is not None else '?'} "
                   f"| {row.agree if row.agree is not None else '?'} "
                   f"| {row.differ if row.differ is not None else '?'} "
                   f"| {row.not_stated if row.not_stated is not None else '?'} |")
    latest = plant.scorecards[-1]
    out += ["", "### Differences still open", ""]
    if latest.differ:
        out += [f"{latest.differ} of the {latest.compared} fields compared in the most "
                f"recent scorecard still differ. Which orders and operations they are on "
                f"is in that scorecard, on this machine; it is not in this file.", ""]
    else:
        out += ["The most recent kept scorecard reports no field still differing.", ""]
    if latest.cannot_tell:
        out += [f"That scorecard also names {latest.cannot_tell} "
                f"{'thing' if latest.cannot_tell == 1 else 'things'} it cannot tell you - "
                f"work only one side has, a field the export never carries, comparisons "
                f"that could not be made. The sentences name orders and columns, so they "
                f"are not kept under the home and are not in this file. They are in the "
                f"scorecard itself, under *what this cannot tell you*.", ""]
    return out


def _second_half(report: gather.Report) -> list[str]:
    out = ["## Performance, toward migration", ""]
    watching = report.in_shadow
    if not watching:
        out += [NOT_IN_SHADOW + ".", "",
                "Shadow mode is how this MES runs beside the one in charge: it watches, "
                "it books nothing, and `fsmes shadow scorecard` compares what it saw "
                "with what the incumbent recorded. No plant in this home is in it, so "
                "there is nothing measured to report. `docs/operate/shadow-mode.md` is "
                "the page.", ""]
        return out
    for plant in watching:
        out += [f"### How long shadow mode has been on — `{plant.name}`", "",
                "**Not recorded.** Nothing in this product writes down the day shadow "
                "mode was switched on, so this file cannot say how long it has been on "
                "and does not guess. What it can say is how long this MES has been "
                "watching the plant at all, which is the window every figure below is "
                "measured over:", ""]
        account = plant.coverage
        if account is not None and account.watching_since is not None:
            out += [f"- watching since {account.watching_since:%Y-%m-%d %H:%M}Z",
                    f"- until {account.until:%Y-%m-%d %H:%M}Z",
                    f"- that is {_span((account.until - account.watching_since).total_seconds())}",
                    ""]
        else:
            out += ["- not measured: this MES has no reading from this plant yet", ""]
        out += _coverage(plant)
        out += _scorecards(plant)
    return out


# ----------------------------------------------------------------- the whole


def _body(report: gather.Report, per_log: int) -> list[str]:
    out = ["## What this file is", "", WHAT_IT_IS, "",
           "Paste it where you want help: into your own agent, into a chat with us, "
           "into an issue. Nothing sends it anywhere by itself, and there is no "
           "command in this product that does.", ""]
    out += _installation(report)
    out += _the_plants(report)
    for plant in report.plants:
        out += _one_plant(plant, per_log)
    out += _second_half(report)
    out += migration.as_markdown(level=2)
    return out


def markdown(report: gather.Report) -> list[str]:
    """The whole file bar its first lines, inside the budget if it can be.

    Rendered more than once rather than trimmed in place: the only thing that
    gives is how much log each process carries, and the file says when it has
    given. Nothing is read from the disk again - this is the same gathered
    facts, printed shorter.
    """
    body = _body(report, gather.LOG_LINES)
    for fewer in (24, 14, 8, 4):
        if len(body) + 10 <= BUDGET:
            break
        body = _body(report, fewer)
    return body


def head(report: gather.Report, verdict: str) -> list[str]:
    """The first lines: the verdict, the version, the clock.

    The verdict is first because it is the line that says whether the rest of
    the file can be trusted to be free of your data, and a reader should not
    have to scroll for it.
    """
    return [
        "# fsmes doctor",
        "",
        f"`{verdict}`",
        "",
        f"fsmes {report.install.version} — written {report.local_clock} "
        f"({report.utc_clock})",
        "",
    ]


def whole(report: gather.Report, verdict: str) -> str:
    """The file, as text, ending in one newline."""
    return "\n".join([*head(report, verdict), *markdown(report)]).rstrip() + "\n"
