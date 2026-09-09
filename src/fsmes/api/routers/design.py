"""The design conversation, endpoint side.

Off unless MES_DESIGN_CHAT is set. A customer's MES should not accumulate our
design notes, and a plant floor is not a place to be billing an API per
message.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from fsmes.api.deps import DbDep, UserDep, require
from fsmes.config import get_settings
from fsmes.services import auth, design

router = APIRouter()

WEB_DIR = Path(__file__).resolve().parents[1].parent / "web"


class ChatIn(BaseModel):
    message: str
    route: str
    screen: str | None = None
    # What the screen is actually showing - its rendered text, the payload it
    # fetched, and the filters in force. This is the whole point: a critique
    # of a screen nobody can see is a guess.
    visible: str | None = None
    data: str | None = None
    filters: dict | None = None
    conversation: int | None = None


@router.get("/status")
def status(user: UserDep) -> dict:
    """Whether this surface is on, and which model would answer."""
    return {
        "enabled": design.enabled(),
        "model": design.CLAUDE_MODEL if design.claude_available() else design.LOCAL_MODEL,
        "claude": design.claude_available(),
        "note": (
            "Claude answers design questions." if design.claude_available()
            else "No ANTHROPIC_API_KEY on this machine, so the on-device model "
                 "answers. It is a weaker design partner; set the key to use Claude."
        ),
    }


@router.post("/chat", dependencies=[require("audit.read")])
def chat(body: ChatIn, user: UserDep, db: DbDep) -> dict:
    """Discuss the screen the person is looking at."""
    if not design.enabled():
        return {"error": "The design surface is off. Set MES_DESIGN_CHAT=1 to enable it."}

    role = auth.current_role(db, user) or user["role"]
    settings = get_settings()

    # Big payloads get shrunk on-device before they go anywhere. Paying a
    # frontier model to read forty kilobytes of repeated JSON is a waste.
    context = {
        "route": body.route,
        "screen": body.screen or body.route,
        "filters": body.filters,
        "visible": design.compress(body.visible or "", "screen's rendered text"),
        "data": design.compress(body.data or "", "screen's underlying data"),
        "who": f"{user['sub']} ({role})",
    }

    conversation = body.conversation
    if conversation is None:
        conversation = design.start(
            route=body.route,
            plant=str(settings.database_url).rsplit("/", 1)[-1],
            who=user["sub"],
            title=body.message)

    past = design.history(conversation)
    design.add_turn(conversation, "user", body.message, context=context)

    # The local model decides whether this is worth a paid one. Which model
    # is about to answer also picks the system prompt: Claude gets the
    # opinionated-collaborator brief, the on-device model gets the narrower
    # capture-only one - see design.build_prompt.
    design_question = design.is_design_question(body.message)
    use_claude = design_question and design.claude_available()
    system, messages = design.build_prompt(
        body.message, context, past, design.read_source(body.route, WEB_DIR),
        claude=use_claude)

    if use_claude:
        try:
            text, model = design.ask_claude(system, messages)
        except Exception as exc:      # a key problem must not lose the message
            text, model = (
                f"Claude could not be reached ({type(exc).__name__}). "
                f"Falling back to the on-device model.\n\n"
                + design.ask_local(system, messages)[0],
                design.LOCAL_MODEL)
    else:
        text, model = design.ask_local(system, messages)

    design.add_turn(conversation, "assistant", text, model=model)
    return {
        "conversation": conversation,
        "say": text,
        "model": model,
        "routed": "design" if design_question else "operation",
        "hint": (
            None if design_question else
            "That reads like an operating question rather than a design one - "
            "the Assistant button answers those on-device, for nothing."
        ),
    }


@router.get("/conversations", dependencies=[require("audit.read")])
def list_conversations(route: str | None = None, limit: int = 40) -> dict:
    """Every design conversation, newest first. Nothing is thrown away - a
    decision argued out on Tuesday and lost by Friday is worse than one never
    made."""
    return {"conversations": design.conversations(route, limit)}


@router.get("/conversations/{conversation_id}", dependencies=[require("audit.read")])
def read_conversation(conversation_id: int) -> dict:
    found = design.transcript(conversation_id)
    return found or {"error": f"no conversation {conversation_id}"}
