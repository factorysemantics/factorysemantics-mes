"""What is not built yet between shadow mode and a switch-over, in one place.

Shadow mode is the furthest this product goes toward replacing an incumbent
MES: it watches, it books nothing, and `fsmes shadow scorecard` says how well
what it saw matches what the old system recorded. Everything after that —
turning the old one off — is not built, and nobody has run shadow mode on a
real plant yet.

This module holds those words once. `fsmes doctor` prints them at the end of
the file a person pastes to their agent, and the assistant folder's stage 4
quotes the same block, so a person who reads both is told the same thing
twice rather than two different things once. If the answer changes, it
changes here.

Written as sentences rather than as a table because the reader is a plant
engineer deciding whether to trust this with their line, and the honest
answer is a paragraph, not a tick.
"""

from __future__ import annotations

#: The heading `doctor` and the assistant both use, so an agent can find the
#: block by name in either file.
HEADING = "What is not built yet"

#: The four gaps between shadow mode and a switch-over. Each is one line,
#: because each is one thing a person would otherwise discover on the day.
NOT_BUILT_YET: tuple[str, ...] = (
    "**Writing back to the PLC.** There is a guarded write queue and shadow "
    "mode closes it. Nothing beyond that queue exists: no sequence of writes, "
    "no interlock handling, no proof that a write landed. A line that is "
    "driven by this MES is not something this MES can do today.",
    "**History from the old MES.** Nothing imports it. Orders, operations, "
    "stops, measurements and their labels stay in the incumbent. This MES "
    "knows only what it has watched since you switched it on, and every "
    "figure it prints says so.",
    "**A cutover procedure.** There is no written sequence for turning the "
    "old system off and this one on: no dual-booking period, no reconciliation "
    "at the boundary, no rollback. Shadow mode and the scorecard are the "
    "evidence you would need before writing one. They are not the procedure.",
    "**Confirmations to an ERP that is in charge.** The ERP connectors read, "
    "and in shadow mode they write to a file rather than to the ERP. An ERP "
    "that expects this MES to confirm production back to it — and to be "
    "believed — is not a path that has been built or tested.",
)

#: The three questions to send the project with a doctor file. They are
#: questions rather than a survey because what migration needs to be is not
#: known yet: nobody has run shadow mode beside a real MES, and these are the
#: three answers that would decide the shape of the work.
QUESTIONS: tuple[str, ...] = (
    "What does your incumbent MES do that this one would have to do on day "
    "one — not everything it does, the part you could not run a shift without?",
    "What would you need to see before you let it book anything for real? A "
    "number of weeks in shadow mode, an agreement figure, a particular line, "
    "a particular shift?",
    "Which way does your ERP point: does it tell the plant what to make, or "
    "does it wait to be told what was made? And who is allowed to disagree "
    "with it?",
)

#: One sentence, so the block cannot be read as a roadmap.
CAVEAT = (
    "None of the four is on a date. They are written down so that a person "
    "evaluating this MES beside their own is not surprised later, and so that "
    "the three questions below get asked before the work is designed rather "
    "than after."
)


def as_markdown(level: int = 2) -> list[str]:
    """The block as Markdown lines, under a heading of the given level.

    One function rather than two formatters: the page `doctor` writes and any
    page the assistant quotes come out of this, so they cannot drift.
    """
    hashes = "#" * max(1, int(level))
    out = [f"{hashes} {HEADING}", ""]
    for line in NOT_BUILT_YET:
        out.append(f"- {line}")
    out += ["", CAVEAT, "", f"{hashes}# Three questions to send with this file", ""]
    for n, question in enumerate(QUESTIONS, start=1):
        out.append(f"{n}. {question}")
    return out
