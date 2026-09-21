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

#: The screen a review is read on. Every generated step names it, so a walk
#: that is picked up after a page load lands back where the review is.
PAGE = "/dashboard"

#: The anchors the review panel carries, and what each is for. They are in
#: `web/index.html`; a test holds them to that, exactly as it holds the
#: built-in guides.
CHANGE_ANCHOR = "review-change"       # one per change row, in order
APPROVE_ANCHOR = "review-approve"     # the control that signs it


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
                  "goes on showing it under its name.")))
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
            changes.append(_change(field, label, before or None, after or None))

    on_the_list = reasons_service.catalog(session)
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
