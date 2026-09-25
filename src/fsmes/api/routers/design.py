"""The design conversation, endpoint side.

Off unless MES_DESIGN_CHAT is set. A customer's MES should not accumulate our
design notes, and a plant floor is not a place to be billing an API per
message.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from fsmes.api import deps
from fsmes.api.deps import UserDep, require
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
    # Echoed back from /design/status so the log can show the panel and the
    # plant agreeing about which run this is. Never trusted on its own.
    lab_run: str | None = None


@router.get("/status")
def status(user: UserDep) -> dict:
    """Whether this surface is on, and which model would answer."""
    lab = design.lab()
    return {
        "enabled": design.enabled(),
        "model": design.CLAUDE_MODEL if design.claude_available() else design.LOCAL_MODEL,
        "claude": design.claude_available(),
        # Present only on a plant an experiment started. The panel shows it so
        # the person typing knows the note is being filed against a run, and
        # sends it back with the message so the two agree in the log.
        "lab": lab,
        "note": (
            "Claude answers design questions." if design.claude_available()
            else "No ANTHROPIC_API_KEY on this machine, so the on-device model "
                 "answers. It is a weaker design partner; set the key to use Claude."
        ),
    }


def _numbers(db) -> dict:
    """What this plant gives its design chat: three budgets, three timeouts
    and the model's name.

    Read through `fsmes.services.plant_settings` - the row this plant's
    administrator saved, then the setting its pack compiled, then the literal
    the product ships - and gathered into one dict so the endpoint reads them
    all inside the single short session it is allowed to open.
    """
    from fsmes.services import plant_settings

    def admin(key):
        return plant_settings.setting(db, "admin", key)

    return {
        "compress": {
            "budget": int(admin("design_compress_budget")),
            "source_chars": int(admin("design_compress_source_chars")),
            "timeout": float(admin("design_compress_timeout_seconds")),
            "model": str(plant_settings.setting(db, "system", "local_model_name")),
        },
        "source_budget": int(admin("design_source_budget")),
        "classify_timeout": float(admin("design_classify_timeout_seconds")),
        "chat_timeout": float(admin("design_chat_timeout_seconds")),
        "model": str(plant_settings.setting(db, "system", "local_model_name")),
    }


@router.post("/chat", dependencies=[require("audit.read")])
def chat(body: ChatIn, user: UserDep) -> dict:
    """Discuss the screen the person is looking at.

    NO REQUEST SESSION. A request session lives until the response is sent,
    and on SQLite that is the plant's single write lock. This endpoint then
    spends up to four network calls inside it - two on-device compressions,
    a classification, and the frontier model - which on a lab plant meant
    minutes with nothing in the plant able to book anything, and a screen
    that answered `Could not reach the design surface: 500` because the
    design store's own write met the same lock. The one thing it needs from
    the plant database is read here and the session closed.
    """
    if not design.enabled():
        return {"error": "The design surface is off. Set MES_DESIGN_CHAT=1 to enable it."}

    with deps.short_read() as db:
        role = auth.current_role(db, user) or user["role"]
        # This plant's own budgets, timeouts and model name, read inside the
        # one short session this endpoint opens and carried past it. Nothing
        # below this line may hold a database session: four model calls follow,
        # and on SQLite a session held across them is the plant's single write
        # lock held across them.
        numbers = _numbers(db)
    settings = get_settings()

    # Big payloads get shrunk on-device before they go anywhere. Paying a
    # frontier model to read forty kilobytes of repeated JSON is a waste.
    context = {
        "route": body.route,
        "screen": body.screen or body.route,
        "filters": body.filters,
        "visible": design.compress(body.visible or "", "screen's rendered text", **numbers["compress"]),
        "data": design.compress(body.data or "", "screen's underlying data", **numbers["compress"]),
        "who": f"{user['sub']} ({role})",
    }

    # Which experiment this plant belongs to, if any. The run and the plant
    # come from the plant's own environment; the screen is the one tag only
    # the browser knows. A `lab_run` in the body that does not match the one
    # the plant was started with is ignored rather than honoured - a tag is
    # only worth having if it cannot be typed in.
    lab = design.lab()
    if lab and body.lab_run and body.lab_run != lab["run"]:
        lab = None

    conversation = body.conversation
    if conversation is None:
        conversation = design.start(
            route=body.route,
            plant=str(settings.database_url).rsplit("/", 1)[-1],
            who=user["sub"],
            title=body.message,
            lab_run=lab and lab["run"],
            lab_plant=lab and lab["plant"],
            screen=body.screen or body.route)

    past = design.history(conversation)
    design.add_turn(conversation, "user", body.message, context=context)

    # The local model decides whether this is worth a paid one. Which model
    # is about to answer also picks the system prompt: Claude gets the
    # opinionated-collaborator brief, the on-device model gets the narrower
    # capture-only one - see design.build_prompt.
    design_question = design.is_design_question(
        body.message, timeout=numbers["classify_timeout"], model=numbers["model"])
    use_claude = design_question and design.claude_available()
    system, messages = design.build_prompt(
        body.message, context, past,
        design.read_source(body.route, WEB_DIR, numbers["source_budget"]),
        claude=use_claude)

    if use_claude:
        try:
            text, model = design.ask_claude(system, messages)
        except Exception as exc:      # a key problem must not lose the message
            text, model = (
                f"Claude could not be reached ({type(exc).__name__}). "
                f"Falling back to the on-device model.\n\n"
                + design.ask_local(system, messages, timeout=numbers["chat_timeout"],
                                   model=numbers["model"])[0],
                numbers["model"])
    else:
        text, model = design.ask_local(system, messages, timeout=numbers["chat_timeout"],
                                       model=numbers["model"])

    design.add_turn(conversation, "assistant", text, model=model)
    return {
        "conversation": conversation,
        "say": text,
        "model": model,
        "routed": "design" if design_question else "operation",
        "lab": lab,
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
