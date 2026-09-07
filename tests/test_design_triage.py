"""Design feedback becoming work.

The chat captures ideas; a Claude Code session judges them. These pin the
promises that make that safe to run unattended-ish: an idea is never judged
twice, an interrupted run loses nothing, a verdict always finds its way back
to the person who raised it, a branch is never called finished, and none of
it touches plant data.
"""

import pytest

from fsmes.services import design
from fsmes.services import design_triage as triage

# Captured before the autouse fixture redirects it at a tmp directory.
REAL_BACKLOG = triage.BACKLOG


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "STORE", tmp_path / "design.db")
    monkeypatch.setattr(triage, "STATE", tmp_path / "triage-state.json")
    monkeypatch.setattr(triage, "BACKLOG", tmp_path / "backlog")
    yield


def a_conversation(message="the orders screen needs a material filter",
                   route="/dashboard/orders") -> int:
    cid = design.start(route=route, plant="machining.db", who="SCOTT", title=message)
    design.add_turn(cid, "user", message)
    design.add_turn(cid, "assistant", "You would want a filter bar.", model="qwen3:8b")
    return cid


# --------------------------------------------------------------- the mark

def test_an_idea_is_offered_for_triage_once():
    """Re-running the pipeline with nothing new must do nothing at all."""
    cid = a_conversation()
    waiting = triage.pending()
    assert [c["id"] for c in waiting] == [cid]

    triage.advance(cid, waiting[0]["last_turn"])
    assert triage.pending() == []


def test_a_run_that_dies_half_way_still_offers_what_it_never_reached():
    """The reason the mark is per conversation rather than one global number."""
    first, second = a_conversation(), a_conversation("the machine cards do not scale")
    waiting = {c["id"]: c for c in triage.pending()}

    triage.advance(second, waiting[second]["last_turn"])   # then the run dies

    assert [c["id"] for c in triage.pending()] == [first]


def test_the_mark_never_moves_backwards():
    """Re-reading an old conversation must not make newer turns look unjudged."""
    cid = a_conversation()
    triage.advance(cid, 99)
    triage.advance(cid, 2)
    assert triage.marks()[str(cid)] == 99


def test_a_judged_conversation_can_be_read_again_deliberately():
    cid = a_conversation()
    triage.advance(cid, triage.pending()[0]["last_turn"])
    assert triage.pending() == []
    assert [c["id"] for c in triage.pending(everything=True)] == [cid]


def test_new_turns_on_an_old_conversation_come_back():
    """Scott replying to a verdict is new work, not a closed matter."""
    cid = a_conversation()
    triage.advance(cid, triage.pending()[0]["last_turn"])
    design.add_turn(cid, "user", "what about a saved view?")

    waiting = triage.pending()
    assert [t["text"] for t in waiting[0]["turns"]] == ["what about a saved view?"]


# --------------------------------------------------------------- the verdict

def test_a_verdict_lands_back_in_the_conversation_that_produced_it():
    """An idea answered somewhere else is one the person who had it never
    hears about again."""
    cid = a_conversation()
    triage.record_verdict(cid, "Building this — branch design/orders-filters.")

    turns = design.transcript(cid)["turns"]
    assert turns[-1]["text"].startswith("Building this")
    assert turns[-1]["model"] == triage.TRIAGE_MODEL


def test_a_verdict_is_not_dressed_up_as_the_chat_model():
    """The transcript must not imply the on-device model made this call."""
    cid = a_conversation()
    triage.record_verdict(cid, "Rejected.")
    assert triage.TRIAGE_MODEL != design.LOCAL_MODEL


# --------------------------------------------------------------- the notes

def test_an_unknown_status_is_refused_rather_than_written():
    """A typo that quietly does nothing is worse than a refusal — the same
    rule the sweep knobs follow."""
    with pytest.raises(ValueError, match="unknown status"):
        triage.write_note(slug="x", title="X", status="approvedish",
                          conversation=1, body="")


def test_a_branch_is_never_called_done():
    """`built` exists so a backlog cannot claim unreviewed work is finished."""
    assert triage.UNMERGED in triage.STATUSES
    assert triage.UNMERGED != "done"


def test_a_note_carries_what_it_came_from():
    cid = a_conversation()
    path = triage.write_note(
        slug="orders-filters", title="Filter orders by material and date",
        status="approved", conversation=cid, route="/dashboard/orders",
        plant="machining.db", turns=[1, 2], summary="Already an open plan item.",
        body="## Assessment\n\nThe API has the filters; the screen does not.")

    meta = triage.front_matter(path.read_text(encoding="utf-8"))
    assert meta["status"] == "approved"
    assert meta["conversation"] == str(cid)
    assert meta["turns"] == "[1, 2]"
    assert meta["route"] == "/dashboard/orders"
    assert "the screen does not" in path.read_text(encoding="utf-8")


def test_rewriting_a_note_keeps_the_date_the_idea_arrived():
    """Status changes over the life of an idea; when it was raised does not."""
    triage.write_note(slug="i", title="I", status="approved", conversation=1, body="")
    path = triage.note_path("i")
    original = path.read_text(encoding="utf-8").replace("created: 20", "created: 19", 1)
    path.write_text(original, encoding="utf-8")

    triage.write_note(slug="i", title="I", status="built", conversation=1,
                      body="", branch="design/i")
    meta = triage.front_matter(path.read_text(encoding="utf-8"))
    assert meta["created"].startswith("19")
    assert meta["branch"] == "design/i"


def test_the_backlog_lists_ideas_and_skips_its_own_readme():
    (triage.BACKLOG).mkdir(parents=True, exist_ok=True)
    (triage.BACKLOG / "README.md").write_text("---\ntitle: not an idea\n---\n", encoding="utf-8")
    triage.write_note(slug="a", title="A", status="approved", conversation=1, body="")

    assert [n["slug"] for n in triage.notes()] == ["a"]


def test_the_backlog_lives_in_the_repository():
    """Versioned beside the code it causes, not in a vault that would need
    two-way sync for every status change."""
    assert REAL_BACKLOG.parts[-3:] == ("docs", "design", "backlog")
    assert (REAL_BACKLOG.parents[2] / "pyproject.toml").is_file()


# --------------------------------------------------------------- isolation

def test_triage_never_writes_to_the_plants_database():
    import fsmes.domain  # noqa: F401  (registers every table)
    from fsmes.db import Base

    assert not any(
        "triage" in t.lower() or "backlog" in t.lower() for t in Base.metadata.tables), (
        "the plant schema must know nothing about design triage")
