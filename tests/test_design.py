"""The design surface.

It exists so a critique of a screen can be made while looking at the screen,
instead of remembered badly in a terminal afterwards. These pin the three
things that make it safe to have: it is off unless asked for, it never lands
in plant data, and the local model decides what is worth paying for.
"""


import pytest

from fsmes.services import design


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "STORE", tmp_path / "design.db")
    yield


# ------------------------------------------------------------------- it is off

def test_the_surface_is_off_unless_asked_for(monkeypatch):
    """A customer's MES must not accumulate our design notes, and a plant floor
    is not a place to be billing an API per message."""
    monkeypatch.delenv("MES_DESIGN_CHAT", raising=False)
    assert design.enabled() is False
    monkeypatch.setenv("MES_DESIGN_CHAT", "1")
    assert design.enabled() is True


def test_a_disabled_surface_refuses_rather_than_answering(client, monkeypatch):
    monkeypatch.delenv("MES_DESIGN_CHAT", raising=False)
    out = client.post("/design/chat", json={"message": "hi", "route": "/dashboard"})
    assert out.status_code in (200, 403)
    if out.status_code == 200:
        assert "off" in out.json().get("error", "")


def test_it_never_writes_to_the_plants_database():
    """Design notes are development work, not manufacturing records. They keep
    their own database so a plant's audit trail stays about the plant."""
    import fsmes.domain  # noqa: F401  (registers every table)
    from fsmes.db import Base

    assert "design" in str(design.STORE)
    assert not any("design" in t.lower() for t in Base.metadata.tables), (
        "the plant schema must know nothing about design conversations")


# ------------------------------------------------------------------- routing

def test_an_operating_question_is_not_sent_to_a_paid_model(monkeypatch):
    """The on-device assistant answers those well and for nothing."""
    monkeypatch.setattr(design, "local_generate", lambda *a, **k: "OPERATION")
    assert design.is_design_question("how do I record a fill weight check?") is False


def test_a_design_question_is(monkeypatch):
    monkeypatch.setattr(design, "local_generate", lambda *a, **k: "DESIGN")
    assert design.is_design_question("how would a supervisor use this screen?") is True


def test_a_silent_local_model_defaults_to_design(monkeypatch):
    """This surface exists to be talked to about design, and the operator
    assistant is one click away."""
    monkeypatch.setattr(design, "local_generate", lambda *a, **k: None)
    assert design.is_design_question("anything at all") is True


# ------------------------------------------------------------------- context

def test_a_big_payload_is_shrunk_on_device_before_it_goes_anywhere(monkeypatch):
    """Paying a frontier model to read forty kilobytes of repeated JSON is a
    waste when there is a model on the box that can summarise it."""
    monkeypatch.setattr(design, "local_generate", lambda *a, **k: "60 machines, 3 down.")
    out = design.compress("x" * 40000, "screen data", budget=1000)
    assert "summarised on-device" in out
    assert len(out) < 1000


def test_a_small_payload_is_left_alone(monkeypatch):
    monkeypatch.setattr(design, "local_generate",
                        lambda *a, **k: pytest.fail("should not have been called"))
    assert design.compress("short", "screen data") == "short"


def test_truncation_says_that_it_truncated(monkeypatch):
    """Silently sending half the screen would make the answer wrong for a
    reason nobody could see."""
    monkeypatch.setattr(design, "local_generate", lambda *a, **k: None)
    out = design.compress("y" * 9000, "screen data", budget=500)
    assert "truncated from 9000" in out


def test_the_prompt_carries_the_screen_and_its_source():
    system, messages = design.build_prompt(
        "how would a supervisor use this?",
        {"screen": "the orders screen", "visible": "WO-4711 completed",
         "filters": {"status": "running"}, "data": "{...}"},
        [], "----- orders.js -----\nfunction renderList() {}")
    assert "300 people" in system, "it must judge against the real plant"
    said = messages[-1]["content"]
    assert "WO-4711 completed" in said
    assert "orders.js" in said
    assert '"status": "running"' in said


def test_the_local_model_gets_a_capture_only_prompt_not_the_collaborator_one():
    """qwen3:8b is the only model that actually answers on this deployment (no
    ANTHROPIC_API_KEY). Sending it the "opinionated collaborator" brief made
    it critique and propose designs of its own - noise that made transcripts
    harder to triage. It should acknowledge, clarify if needed, and stop."""
    system, _ = design.build_prompt(
        "the orders screen should show due dates",
        {"screen": "the orders screen"}, [], "", claude=False)
    assert "300 people" not in system, "that brief is Claude's, not the local model's"
    assert "opinionated collaborator" not in system
    assert "capture" in system.lower() or "record" in system.lower()


def test_claude_still_gets_the_full_collaborator_prompt_by_default():
    system, _ = design.build_prompt(
        "how would a supervisor use this?",
        {"screen": "the orders screen"}, [], "")
    assert "300 people" in system


def test_history_is_replayed_as_conversation():
    _, messages = design.build_prompt(
        "and what about the time range?",
        {"screen": "orders"},
        [{"role": "user", "text": "how would a supervisor use this?"},
         {"role": "assistant", "text": "They would need filters."}],
        "")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == "They would need filters."


# ------------------------------------------------------------------ archival

def test_every_conversation_is_kept_with_what_was_on_screen():
    """A design note read six months later is meaningless without the thing it
    was about."""
    cid = design.start("/dashboard/orders", "bottling.db", "SCOTT", "supervisor use?")
    design.add_turn(cid, "user", "how would a supervisor use this?",
                    context={"visible": "WO-4711"})
    design.add_turn(cid, "assistant", "They would need filters.", model="claude-opus-5")

    found = design.transcript(cid)
    assert found["route"] == "/dashboard/orders"
    assert [t["role"] for t in found["turns"]] == ["user", "assistant"]
    assert found["turns"][1]["model"] == "claude-opus-5"


def test_conversations_are_listed_newest_first_and_by_screen():
    design.start("/dashboard/orders", None, "SCOTT", "first")
    design.start("/dashboard/quality", None, "SCOTT", "second")
    assert design.conversations()[0]["title"] == "second"
    assert len(design.conversations(route="/dashboard/orders")) == 1


def test_asking_for_a_conversation_that_does_not_exist_is_not_a_crash():
    assert design.transcript(9999) is None


def test_design_chat_can_be_kept_local_even_with_a_key(monkeypatch):
    from fsmes.services import design
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(design, "claude_available", design.claude_available)  # the real one
    monkeypatch.setenv("MES_DESIGN_CLAUDE", "0")
    assert design.claude_available() is False
    monkeypatch.delenv("MES_DESIGN_CLAUDE")
    try:
        import anthropic  # noqa: F401
        assert design.claude_available() is True
    except ImportError:
        assert design.claude_available() is False
