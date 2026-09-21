"""What a person is shown before they sign, and the walk through it.

The panel that tells an approver a draft is waiting shipped without the half
that matters: a row, a name, and a button that put the plant's vocabulary in
front of every operator. Signing that is signing blind. The design said so
before it was built - *what the person is shown before they sign: the diff,
the coverage, what is retired and how many records carry it* - and this is
that sentence, one function per clause.

Two things live here and they are deliberately separate:

* **A reviewer** answers, for one kind of waiting item, *what would change*.
  It returns the draft against the revision it would supersede as a list of
  changes in plain words, plus the facts an approver needs to weigh them: how
  large the list is now and after, how much recorded history already carries
  the code, who drafted it and on whose behalf, and the one click that puts
  back what the plant has today.
* **The walk** turns any reviewer's answer into a guide the floor assistant
  plays - one step per change, the approve control last. It reads nothing but
  the change rows, so a second kind joins by writing a reviewer and never
  touches this half.

Nothing here calls a model. The steps are generated from the draft's own diff
and the words are the plant's own; decisions 0031 and 0032 stay in force. A
walk that a model narrated would be a walk that could be wrong about what is
about to be signed, which is the one place in this product that cannot be.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.services import NotFound

#: The screen a review is read on. Every generated step falls back to it, and
#: the walk names it as its home, so a walk that has crossed onto another
#: screen can be brought back to the panel that signs.
PAGE = "/dashboard"

#: The anchors the review panel carries, and what each is for. They are in
#: `web/index.html`; a test holds them to that, exactly as it holds the
#: built-in guides.
CHANGE_ANCHOR = "review-change"       # one per change row, in order
APPROVE_ANCHOR = "review-approve"     # the control that signs it

#: The operator's own screen, and the control on it that a downtime
#: vocabulary is: the list an operator picks a stop from. A change to the
#: vocabulary is read there, in front of the real select, rather than
#: described on the review panel - an approver is being asked what this does
#: to the floor, and the floor is one screen away.
STATION_PAGE = "/dashboard/station"

#: The two halves of that control, and the field each belongs to: the word on
#: the list, and the sentence the screen shows beside the word once it is
#: chosen. A step that changes the sentence rings the sentence.
STATION_ANCHORS = {                             # both in `web/station.html`
    "name": "station-reason-code",
    "description": "station-reason-help",
    "status": "station-reason-code",
}


# --------------------------------------------------------------- the kinds


@dataclass(frozen=True)
class Kind:
    """One kind of thing that waits for somebody.

    `waiting` answers the queue; `review` answers one row of it. A second
    kind - a document, a trigger, a setpoint adjustment, a design note - is
    one entry in `KINDS` with those two functions. It writes its own diff and
    inherits the walk.
    """

    name: str
    #: What it is called in a sentence a person reads.
    label: str
    #: What a caller must hold to see it waiting, and to open it.
    capability: str
    waiting: Callable[[Session, int], tuple[list[dict], int]]
    review: Callable[[Session, str, int], dict]


def capabilities() -> dict[str, str]:
    """`{kind: capability}` - what the pending-approvals endpoint gates on."""
    return {kind.name: kind.capability for kind in KINDS.values()}


def review(session: Session, kind: str, code: str, revision: int) -> dict:
    """The whole substance of one waiting item, with its walk attached."""
    known = KINDS.get(kind)
    if known is None:
        raise NotFound(f"nothing here reviews a {kind!r}")
    out = known.review(session, code, revision)
    out["walkthrough"] = walkthrough(out)
    return out


# ------------------------------------------------------- the plant's words


def _change(field: str, label: str, before, after, *, note: str | None = None,
            page: str | None = None, anchor: str | None = None) -> dict:
    """One line of a diff, in the words the screen will use.

    `page` and `anchor` are the extension point for a kind whose change has a
    control of its own somewhere in the product: name them and the step points
    at that control instead of at the review panel's own row.
    """
    row = {"field": field, "label": label, "before": before, "after": after}
    if note:
        row["note"] = note
    if page:
        row["page"] = page
    if anchor:
        row["anchor"] = anchor
    return row


def _count(number: int, noun: str) -> str:
    """`1 recorded interval`, `2 recorded intervals`. The plant's counts are
    read by people, and a panel that says "1 reasons" is a panel somebody
    stops trusting about the numbers that matter."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _stop(text) -> str:
    """One of the plant's own values, with one full stop after it and never
    two. A description that ends in a sentence already has its punctuation;
    a name does not."""
    text = str(text).rstrip()
    return text if text.endswith((".", "!", "?", ":", ";")) else f"{text}."


def _sentence(change: dict) -> str:
    """What the coach card says about one change. Deterministic, and built
    from the two values themselves - never a summary of them."""
    before, after = change.get("before"), change.get("after")
    if before in (None, ""):
        text = f"The draft says: {_stop(after)} The plant says nothing about this today."
    elif after in (None, ""):
        text = f"Today: {_stop(before)} The draft leaves it empty."
    else:
        text = f"Today: {_stop(before)} The draft: {_stop(after)}"
    note = change.get("note")
    return f"{text} {note}" if note else text


def walkthrough(reviewed: dict) -> dict:
    """A guide built from a draft's own diff: one step per change, the button
    that signs it last.

    The same `{id, title, steps}` the assistant plays for an authored guide
    and for a supervisor's recording, so there is one guide runner in this
    product and not two. What a generated guide adds is `nth`: the review
    panel renders one row per change and they all carry the same anchor,
    because the number of them is not known until the draft is read.
    """
    steps: list[dict] = []
    for index, change in enumerate(reviewed.get("changes") or []):
        step = {
            "page": change.get("page", PAGE),
            "anchor": change.get("anchor", CHANGE_ANCHOR),
            "title": change["label"],
            "body": _sentence(change),
        }
        if "anchor" not in change:
            step["nth"] = index
        steps.append(step)

    supersedes = reviewed.get("supersedes")
    undo = (f"Leaving it unsigned changes nothing on the floor. Putting revision "
            f"{supersedes['revision']} back is one click, on this same panel, "
            f"before or after you sign." if supersedes else
            "Leaving it unsigned changes nothing on the floor. There is no earlier "
            "revision to put back: this is the first one.")
    steps.append({
        "page": PAGE,
        "anchor": APPROVE_ANCHOR,
        "title": "Sign it, or leave it waiting",
        "body": f"This is the button that puts it in force. {undo}",
    })

    return {
        "id": f"review:{reviewed['kind']}:{reviewed['code']}:{reviewed['revision']}",
        "title": f"Before you sign {reviewed['code']} revision {reviewed['revision']}",
        "steps": steps,
        "generated": True,
        # Where the signing happens, so a walk that has crossed onto the
        # floor's own screen can be brought back to it in one move. A walk
        # that strands somebody in front of the control they just read is a
        # walk that stopped short of the thing it was for.
        "home": PAGE,
        "home_label": "Back to the review",
    }


# ------------------------------------------------- the downtime vocabulary


def _headline(row) -> str:
    """The dry run's one line for a vocabulary: what this draft would do to
    what the plant has already recorded."""
    if row.retires:
        return ("retires a code that labels "
                f"{_count(row.labels_intervals or 0, 'recorded interval')}")
    return ("a change to a reason already on the list" if row.revision > 1
            else "a new reason for the list")


def _station_for(session, code: str) -> tuple[str, str] | None:
    """Where a change to this word is visible on the floor, and how to say so.

    `(url, sentence)`, or `None` when there is nowhere honest to stand.

    The vocabulary is plant-wide - every station offers the same list - so
    "which station" is not in the list itself. It is in the history: the
    machines that have actually recorded a stop under this code are the ones
    whose operators choose this word, and the busiest of them is where the
    change is most worth looking at. Deterministic, and computed from the
    plant's own records; nothing here proposes anything.

    `None` for a code with no recorded stop behind it - a brand-new reason
    nobody can have chosen yet, or one that has been on the list and never
    used. Pointing at a machine picked out of the equipment table would be
    inventing a place, and the review says it in words instead.
    """
    from fsmes.services import reasons as reasons_service

    machines = reasons_service.machines_labelling(session, code)
    if not machines:
        return None
    machine, _count = machines[0]
    where = (f"The walk shows this on {machine}, "
             + (f"the machine that has recorded {code}."
                if len(machines) == 1
                else f"the busiest of the {len(machines)} machines that have "
                     f"recorded {code}."))
    return f"{STATION_PAGE}?m={machine}&reason={code}", where


def _waiting_downtime_reasons(session: Session, limit: int) -> tuple[list[dict], int]:
    """Every downtime-reason draft waiting on an approver."""
    from fsmes.services import reasons as reasons_service

    rows, total = reasons_service.drafts(session, limit=limit)
    now = utcnow()
    items = [{
        "kind": "downtime_reason",
        "code": row.code,
        "revision": row.revision,
        "title": row.name,
        "headline": _headline(row),
        "drafted_by": row.created_by,
        "on_behalf_of": row.on_behalf_of,
        "drafted_at": row.created_at,
        # How long it has waited. The only column that changes with time, so a
        # forgotten draft reads as "eleven days" rather than falling off a list.
        "waiting_seconds": max((now - row.created_at).total_seconds(), 0.0),
        "review": f"/dashboard/pending-approvals/downtime_reason/{row.code}/{row.revision}",
        "approve": f"/equipment/downtime-reasons/{row.code}/approve/{row.revision}",
    } for row in rows]
    return items, total


def _review_downtime_reason(session: Session, code: str, revision: int) -> dict:
    """One revision of one reason, against the one the plant is living with.

    The revision under review need not be a draft: undo is approving an older
    revision, and somebody about to do that is owed the same reading of what
    it would change as somebody signing a new one.
    """
    from fsmes.services import reasons as reasons_service

    row = next((r for r in reasons_service.revisions(session, code)
                if r.revision == revision), None)
    if row is None:
        raise NotFound(f"no downtime reason {code} revision {revision}")

    # What the plant is living with, and would stop living with. Not "the
    # previous revision by number": a plant that has undone something once is
    # living with an older number than the newest one.
    live = reasons_service.in_force(session, code)
    supersedes = live if (live is not None and live.id != row.id) else None

    on_the_list = reasons_service.catalog(session)

    # Where a person would see this change land, if there is such a place.
    # Two conditions, and both are about honesty rather than convenience: the
    # code has to be on the list the station screen draws today - otherwise
    # the walk would ring a select that does not contain the word it is
    # talking about - and some machine has to have recorded a stop under it,
    # or there is no floor to stand on. A code that fails either is described
    # on the review panel, exactly as it was before this existed.
    floor = _station_for(session, code) if code in on_the_list else None

    def on_the_floor(field: str, showing: bool = True) -> dict:
        """`page` and `anchor` for a field with a control of its own, or
        nothing at all - which is what makes a step stay on the review.

        `showing` is false when the control for this field has nothing in it
        on the screen today: the sentence beside a code is an empty paragraph
        until a plant writes one, and a ring around an empty paragraph is a
        line on the screen pointing at nothing. The step then rings the word
        itself, which is what a person is actually looking at.
        """
        if not floor or field not in STATION_ANCHORS:
            return {}
        anchor = STATION_ANCHORS[field if showing else "name"]
        return {"page": floor[0], "anchor": anchor}

    # Which machine, said once. It is the same answer for every row of one
    # diff, and a panel that repeats it under each change reads like two
    # different facts about two different machines.
    said_where = False

    def where() -> str | None:
        nonlocal said_where
        if not floor or said_where:
            return None
        said_where = True
        return floor[1]

    changes: list[dict] = []
    if row.retires:
        carried = reasons_service.intervals_labelled(session, code)
        changes.append(_change(
            "status", f"{code} leaves the list",
            "on the list, and choosable at the machine",
            "off the list; nothing new can be labelled with it",
            note=(f"{_count(carried, 'recorded interval')} already "
                  f"{'carries' if carried == 1 else 'carry'} this code and "
                  f"{'keeps' if carried == 1 else 'keep'} it. Retiring changes what "
                  "may be chosen next, never what was chosen before, so the pareto "
                  "goes on showing it under its name."
                  + (f" {where()}" if floor else "")),
            **on_the_floor("status")))
    elif supersedes is None:
        changes.append(_change(
            "code", f"{code} is a code the plant does not have", None, code,
            note="It is what the analysis groups on and what leaves the MES, so it "
                 "is the half of this that is hard to change later."))

    for field, label in (("name", f"What an operator reads on the {code} button"),
                         ("description", f"The sentence shown beside {code}")):
        before = getattr(supersedes, field, None) if supersedes else None
        after = getattr(row, field)
        if (before or "") != (after or "") and (before or after):
            changes.append(_change(
                field, label, before or None, after or None, note=where(),
                **on_the_floor(field, showing=bool(before))))

    total = len(on_the_list)
    if row.retires:
        after_total = total - 1 if code in on_the_list else total
    elif code in on_the_list:
        after_total = total
    else:
        after_total = total + 1

    carried = reasons_service.intervals_labelled(session, code)
    stated = row.labels_intervals
    now = utcnow()
    return {
        "kind": "downtime_reason",
        "label": "downtime reason",
        "code": code,
        "revision": revision,
        "title": row.name,
        "description": row.description,
        "status": row.status.value,
        "retires": row.retires,
        "headline": _headline(row),
        "drafted_by": row.created_by,
        "on_behalf_of": row.on_behalf_of,
        "drafted_at": row.created_at,
        "waiting_seconds": (max((now - row.created_at).total_seconds(), 0.0)
                            if row.status.value == "draft" else None),
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "changes": changes,
        # How big the plant's list is, and how big it would be. Counted, both
        # of them: a list that grows by one and a list that shrinks by one are
        # different acts and the panel says which this is.
        "coverage": {
            "vocabulary_total": total,
            "vocabulary_total_after": after_total,
            # The noun, already right for the count. The server owns the
            # plant's words here exactly as it owns the catalogue's.
            "of": "reason an operator may choose from" if total == 1
                  else "reasons an operator may choose from",
        },
        # What history already carries the code. Counted now, beside what the
        # drafter said when they wrote the draft - a draft that waited a week
        # was written against a smaller number, and an approver reading one
        # number would not know which.
        "affected": {
            "intervals_labelled": carried,
            "stated_in_the_draft": stated,
            "moved_since_the_draft": stated is not None and stated != carried,
        },
        # The revision it would supersede, whole, so undo is a click and not a
        # hunt through a history screen.
        "supersedes": reasons_service.out(supersedes) if supersedes else None,
        "approve": f"/equipment/downtime-reasons/{code}/approve/{revision}",
        "undo": (f"/equipment/downtime-reasons/{code}/approve/{supersedes.revision}"
                 if supersedes else None),
    }


#: Every kind of waiting item, and how to read one. One entry today - the
#: plant's downtime vocabulary. Work instructions, triggers, setpoint
#: adjustments and the design-chat notes each already have a lifecycle and a
#: screen; joining them is an entry here with a `waiting` and a `review`, and
#: the walk they inherit needs no change at all.
KINDS: dict[str, Kind] = {
    "downtime_reason": Kind(
        name="downtime_reason",
        label="downtime reason",
        capability="process.approve",
        waiting=_waiting_downtime_reasons,
        review=_review_downtime_reason,
    ),
}
