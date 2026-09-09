"""The floor assistant.

Guide mode makes a promise the rest of the product does not: that it will
point at a real control on a real screen. A guide whose anchor has been
renamed highlights nothing, or worse, highlights the wrong thing and teaches
someone the wrong habit. Nothing compiles these pages, so this is the check
that keeps the promise true.
"""

import re
from pathlib import Path

import pytest

from fsmes.services import assistant
from fsmes.services import capabilities as caps

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

PAGE_FILES = {
    "/dashboard": "index.html",
    "/dashboard/orders": "orders.html",
    "/dashboard/station": "station.html",
    "/dashboard/quality": "quality.html",
    "/dashboard/analysis": "analysis.html",
    "/dashboard/admin": "admin.html",
    "/dashboard/instructions": "instructions.html",
}


def anchors_on(page: str) -> set[str]:
    html = (WEB / PAGE_FILES[page]).read_text(encoding="utf-8")
    return set(re.findall(r'data-assist="([^"]+)"', html))


# ------------------------------------------------------- guides are honest

@pytest.mark.parametrize("guide", assistant.GUIDES, ids=lambda g: g["id"])
def test_every_step_points_at_a_control_that_exists(guide):
    for i, step in enumerate(guide["steps"], 1):
        assert step["page"] in PAGE_FILES, f"{guide['id']} step {i}: unknown page {step['page']}"
        present = anchors_on(step["page"])
        assert step["anchor"] in present, (
            f"{guide['id']} step {i} points at data-assist={step['anchor']!r} "
            f"which {PAGE_FILES[step['page']]} does not have"
        )


@pytest.mark.parametrize("guide", assistant.GUIDES, ids=lambda g: g["id"])
def test_every_guide_requires_a_capability_the_product_knows(guide):
    assert guide["needs"] in caps.CAPABILITIES, f"{guide['id']} needs an unknown capability"


@pytest.mark.parametrize("guide", assistant.GUIDES, ids=lambda g: g["id"])
def test_every_step_explains_itself(guide):
    for step in guide["steps"]:
        assert step["title"] and step["body"], f"{guide['id']} has a step with no words"


def test_guide_ids_are_unique():
    ids = [g["id"] for g in assistant.GUIDES]
    assert len(ids) == len(set(ids))


# ------------------------------------------- guides respect what you may do

def test_an_inspector_is_only_offered_what_they_can_finish():
    """Teaching someone a task they will be refused at the last step is worse
    than saying it is not theirs to do."""
    inspector = set(caps.BUILTIN_ROLES["quality_inspector"]["capabilities"])
    offered = {g["id"] for g in assistant.visible_guides(inspector)}
    assert "record-check" in offered
    assert "book-production" not in offered
    assert "add-person" not in offered


def test_an_admin_is_offered_everything():
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    assert len(assistant.visible_guides(admin)) == len(assistant.GUIDES)


def test_a_viewer_is_offered_only_the_reading_guides():
    """Reading a procedure is reading. Someone who may look at the plant may
    also look up how a job is supposed to be done."""
    viewer = set(caps.BUILTIN_ROLES["viewer"]["capabilities"])
    offered = {g["id"] for g in assistant.visible_guides(viewer)}
    assert offered == {"order-progress", "why-stopped", "find-instruction"}
    assert all(g["needs"] == "plant.read" for g in assistant.visible_guides(viewer))


# ------------------------------------------------------------ routing works

def test_routing_falls_back_to_words_when_the_model_is_silent(monkeypatch):
    """Ollama being down must not take the assistant with it. A crude match is
    recoverable - the person reads the title and closes it - but no assistant
    at all is not."""
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: None)
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    guide = assistant.route("how do I record a quality inspection measurement", admin)
    assert guide and guide["id"] == "record-check"


def test_routing_never_offers_a_guide_the_person_cannot_use(monkeypatch):
    # The model is wrong and names an admin guide; an inspector must not get it.
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: "add-person")
    inspector = set(caps.BUILTIN_ROLES["quality_inspector"]["capabilities"])
    guide = assistant.route("add a new employee", inspector)
    assert guide is None or guide["needs"] in inspector


def test_a_question_that_matches_nothing_returns_no_guide(monkeypatch):
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: "NONE")
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    assert assistant.route("what is the weather", admin) is None


def test_the_answer_path_degrades_honestly(monkeypatch):
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: None)
    said = assistant.answer("what is our OEE", {}, {"plant.read"})
    assert "not answering" in said


# ---------------------------------------------------------------- endpoints

def test_the_guide_list_is_filtered_for_the_person_asking(sign_in):
    inspector = sign_in("QINSP", role="quality_inspector")
    titles = {g["id"] for g in inspector.get("/assist/guides").json()["guides"]}
    assert "record-check" in titles and "add-person" not in titles


def test_fetching_a_guide_you_may_not_use_is_refused(sign_in):
    inspector = sign_in("QINSP2", role="quality_inspector")
    out = inspector.get("/assist/guides/add-person").json()
    assert "error" in out


def test_the_widget_is_on_every_screen():
    """It is injected per page rather than by a shared layout, so it is
    exactly the kind of thing that gets forgotten on the next new screen."""
    for name in PAGE_FILES.values():
        html = (WEB / name).read_text(encoding="utf-8")
        assert "assist.js" in html, f"{name} has no assistant"
        assert "assist.css" in html, f"{name} has no assistant styles"


def test_facts_carrying_timestamps_do_not_break_the_answer(monkeypatch):
    """The facts come straight from the services and carry datetimes. A
    serialisation error here took down a whole answer for want of one date."""
    from datetime import datetime

    seen = {}
    monkeypatch.setattr(assistant, "_ask_model",
                        lambda prompt, **k: seen.setdefault("prompt", prompt) and "fine")
    said = assistant.answer(
        "what happened",
        {"open_nonconformances": [{"code": "NC-1", "raised": datetime(2026, 8, 31, 12, 0)}]},
        {"plant.read"},
    )
    assert said
    assert "NC-1" in seen["prompt"]


def test_a_question_about_what_a_procedure_says_is_answered_not_toured(monkeypatch):
    """Someone asking what the instruction says wants the answer. Routing that
    to a guide made the assistant feel like it was dodging the question."""
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: None)
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    question = "what does our procedure say about out of tolerance fill weight"
    assert assistant.route(question, admin) is None
    assert assistant.route("what is the fill weight tolerance", admin) is None


def test_asking_to_be_shown_still_gets_a_guide(monkeypatch):
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: None)
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    for phrasing in (
        "how do i record a quality inspection measurement",
        "show me how to record a quality inspection measurement",
    ):
        guide = assistant.route(phrasing, admin)
        assert guide and guide["id"] == "record-check", phrasing


def test_the_gate_holds_even_when_the_model_insists_on_a_guide(monkeypatch):
    """The model is an optimisation on top of the gate, never the gate.

    Asked to make this judgement itself, qwen3:8b kept routing "what does our
    procedure say about an out-of-tolerance reading" to a walkthrough, which
    reads as dodging the question.
    """
    monkeypatch.setattr(assistant, "_ask_model", lambda *a, **k: "find-instruction")
    admin = set(caps.BUILTIN_ROLES["admin"]["capabilities"])
    assert assistant.route("what does the procedure say about scrap", admin) is None
    assert assistant.route("how do i find the procedure for scrap", admin) is not None


def test_the_gate_recognises_the_ways_people_actually_ask():
    for phrasing in ("how do I record a check", "How to record a check",
                     "show me how to record a check", "where do I enter the value",
                     "steps to record a check"):
        assert assistant.wants_showing(phrasing), phrasing
    for phrasing in ("what is the fill weight spec", "is the line running",
                     "what does the procedure say", "who approved WI-FILL"):
        assert not assistant.wants_showing(phrasing), phrasing
