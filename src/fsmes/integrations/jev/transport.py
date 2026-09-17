"""Where a judgment question actually goes.

Two implementations, one interface, the same shape as
`fsmes.integrations.uns.transport`:

* `SdkTransport` - the real service, over `typesafe-sdk`. The SDK lives in
  the `[jev]` extra, because a plant PC that will never ask a hosted model
  anything should not carry its client (the same reason `mqtt`, `mcp` and
  `agent` are extras).
* whatever a test injects - the interface is one method, so every test in
  this repository runs the asked path without a key, a network or an SDK.

There is no log-mode transport of the `LogTransport` kind. A judgment that
was not asked has no answer, and an invented one would be exactly the lie
the typed questions exist to remove; the caller's own deterministic answer
is the fallback, and the record says the question was not asked.

**Nothing may escape this file.** A judgment gates nothing and may cost
nothing (decision 0031), and on 2026-09-17 the first real call proved that
is not free: the SDK's class had a different name from the one this file
assumed, and the `AttributeError` killed the scoring of a run that had
already succeeded. So every failure - a missing attribute, a bad argument,
a refused key, a service that is down, an answer shaped wrong - leaves here
as a `JevUnavailable` whose message is the sentence a record can print, and
the caller writes "not asked (that sentence)" and carries on with what it
already had.

The SDK surface below was read from `typesafe-sdk 0.6.0` as installed, not
from a description of it: `TypeSafeClient(api_key=..., model=..., retry=...,
timeout=..., base_url=...)`, `client.system_one(state=..., questions={name:
question})` with `Noul`/`Score`/`Choice` question objects, and a
`SystemOneResponse` carrying `model`, `usage` and `answers` keyed by the
names asked. A `Choice` takes `criteria` as a mapping of option name to its
description and answers with `choice`, `confidence` and `probabilities`
keyed by option name. The test that pins it
(`tests/test_jev_sdk_surface.py`) drives the real client through a mock HTTP
transport, so a change in that shape fails a test instead of a run.
"""

from __future__ import annotations

from typing import Protocol

import structlog

from fsmes import shadow

log = structlog.get_logger("jev.transport")

#: The one name the key is read from. Said in one place so every sentence
#: about it and the documentation cannot drift apart.
KEY_SETTING = "MES_JEV_API_KEY"

#: Said in one place, because there is one way to get the client.
NO_SDK = (
    "The judgment model's client is an optional extra. Install it with "
    "`pip install 'factorysemantics-mes[jev]'`. Nothing needs it: every "
    "caller keeps the answer it computes without one."
)

#: How much of a failure's own words a record keeps. Long enough to name the
#: thing that went wrong, short enough that a run card stays readable.
REASON_CHARACTERS = 300


class JevUnavailable(RuntimeError):
    """The judgment was not asked, and this is the sentence that says why.

    Its message is already the whole reason, so a caller writing a record
    prints it as it stands rather than naming this class again.
    """


class JevTransport(Protocol):
    def ask(self, *, state: str, questions: list[dict], model: str) -> dict:
        """Answers for one batch of questions about one state, or raise.

        `questions` is this package's own shape, not the SDK's - one dict
        per question with `name`, `kind` (`noul`, `score` or `choice`),
        `text` and, for a score, its ordered `levels`, or for a choice, its
        unordered `options`. The answer is this package's own shape too::

            {"model": "<the version actually served>",
             "request_id": "<the provider's id, or ''>",
             "usage": {"input_tokens": int|None, "output_tokens": int|None},
             "answers": {"<name>": {"probability": 0.0..1.0,
                                    "level": "<for a score or a choice>",
                                    "expected_score": <for a score>,
                                    "confidence": 0.0..1.0 or None,
                                    "probabilities": {...} or None}}}

        Raising is the whole error contract. The caller records that the
        question was not asked and keeps what it already had.
        """
        ...


def reason(exc: BaseException, *, secret: str = "") -> str:
    """One failure, as the sentence a record prints.

    Names the class as well as the message: "not asked (connection refused)"
    and "not asked (AttributeError: ...)" are a service being down and this
    code being wrong, and a reader six weeks later needs to tell them apart.
    A `JevUnavailable` already carries its own sentence and is used as it
    stands.

    `secret` is removed if it appears at all. Nothing in the SDK puts the key
    in an exception - the key travels as a header, and the SDK redacts those
    from its own logging - but "nothing puts it there" is an assumption about
    somebody else's code, and this is the one place it costs nothing to stop
    being an assumption.
    """
    if isinstance(exc, JevUnavailable):
        said = str(exc)
    else:
        message = str(exc).strip() or "no message"
        said = f"{type(exc).__name__}: {message}"[:REASON_CHARACTERS]
        if _is_a_refused_key(exc):
            said += f"; the judgment service refused {KEY_SETTING}"
    return said.replace(secret, "[redacted]") if secret else said


def _is_a_refused_key(exc: BaseException) -> bool:
    """A key the service would not take, by the SDK's own class names.

    Read off the class name rather than by importing the SDK's exceptions:
    this runs on the failure path, where importing the SDK is exactly the
    thing that may have gone wrong.
    """
    name = type(exc).__name__.lower()
    return "authentication" in name or "permissiondenied" in name


def import_sdk():
    """The SDK, or an error that says how to get one."""
    try:
        import typesafe_sdk
    except ImportError as exc:
        raise JevUnavailable(NO_SDK) from exc
    if typesafe_sdk is None:  # the module hidden rather than absent
        raise JevUnavailable(NO_SDK)
    return typesafe_sdk


class SdkTransport:
    """The real service.

    The key is held privately and never printed: not by `repr`, not by the
    log line below, not in an error. `fsmes info` and every status line say
    whether a key is set, which is the only part of it anybody needs.

    The SDK's own defaults are overridden rather than inherited. Its default
    is a 10-second timeout with two retries, a 30-second worst case in front
    of a service whose median is about 100 ms; for a nightly triage pass that
    is half a minute of a build slot spent waiting for an answer nothing
    depends on. One attempt, five seconds, and a failure is recorded as "not
    asked".
    """

    def __init__(self, api_key: str, *, base_url: str = "",
                 timeout: float = 5.0, http_transport=None) -> None:
        if not api_key:
            raise ValueError("a judgment client needs a key; ask available() first")
        self._api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        # How a test drives the real SDK client without a network: an
        # `httpx2` transport the SDK accepts. Nothing in the product sets it,
        # and `from_settings` does not pass it.
        self._http_transport = http_transport

    def __repr__(self) -> str:  # never the key
        where = self.base_url or "the client's own default endpoint"
        return f"SdkTransport({where}, timeout={self.timeout}s, key=set)"

    def ask(self, *, state: str, questions: list[dict], model: str) -> dict:
        # Belt and braces. Shadow mode already refuses to build this at all;
        # refused again here so a caller that built one some other way
        # cannot get past it. Outside the catch below, because a refusal is
        # a decision this MES made rather than a failure of the service.
        shadow.guard("llm.jev", detail=f"{len(questions)} typed question(s), {len(state)} characters of state")
        try:
            return self._ask(state=state, questions=questions, model=model)
        except Exception as exc:  # the whole point: nothing escapes
            raise JevUnavailable(reason(exc, secret=self._api_key)) from exc

    def _ask(self, *, state: str, questions: list[dict], model: str) -> dict:
        """The call itself. Anything it raises is caught by `ask` above."""
        typesafe_sdk = import_sdk()
        service = typesafe_sdk.TypeSafeClient(
            api_key=self._api_key,
            model=model,
            # One attempt: `max_retries=0` is the SDK's own way of saying so,
            # and the policy's own deadline is set to the same timeout so a
            # retry policy cannot quietly reinstate the 30-second worst case.
            retry=typesafe_sdk.RetryPolicy(max_retries=0, timeout=self.timeout),
            timeout=self.timeout,
            base_url=self.base_url or None,
            transport=self._http_transport,
        )
        try:
            reply = service.system_one(
                state=state,
                questions={q["name"]: _as_sdk_question(typesafe_sdk, q) for q in questions},
                model=model,
            )
        finally:
            service.close()

        served = str(getattr(reply, "model", "") or "")
        if not served:
            # A judgment stored against a version nobody recorded cannot be
            # reproduced, and decision 0031 says the served version is part
            # of the answer. Refusing here is better than storing the
            # version we asked for and calling it the one we got.
            raise JevUnavailable(
                "the judgment service did not say which model version answered")
        answers = _read_answers(reply, questions)
        log.info("judgment answered", model=served, questions=len(questions),
                 state_characters=len(state))
        return {"model": served,
                "request_id": _request_id(reply),
                "usage": _usage(reply),
                "answers": answers}


def _as_sdk_question(sdk, question: dict):
    """One of this package's questions, in the SDK's own types.

    The SDK's `Score` takes an ordered list of criteria, one per score from
    zero, and answers with a number in that range. This package's levels are
    named, so each criterion carries its name as well as its words and the
    name comes back in the answer's legend - which is what lets a score be
    read as `medium` rather than as `2`.
    """
    kind = question["kind"]
    if kind == "noul":
        return sdk.Noul(instructions=question["text"])
    if kind == "choice":
        # The SDK calls the options `criteria` too, as a mapping rather than
        # an ordered list: unordered is the whole difference between this and
        # a score, and the answer comes back keyed by the option's own name.
        return sdk.Choice(
            instructions=question["text"],
            criteria={name: description for name, description in question["options"]},
        )
    if kind == "score":
        return sdk.Score(
            instructions=question["text"],
            criteria=[{"level": name, "means": description}
                      for name, description in question["levels"]],
        )
    raise ValueError(f"unknown question kind {kind!r}")


def _read_answers(reply, questions: list[dict]) -> dict:
    """The SDK's answers, in this package's shape.

    Every question asked must come back, in the kind it was asked in. A
    battery that silently returns four answers out of six is the ambiguity
    this whole change exists to delete, so a missing one raises rather than
    reading as "no".
    """
    got = dict(getattr(reply, "answers", None) or {})
    out: dict[str, dict] = {}
    for question in questions:
        name = question["name"]
        answer = got.get(name)
        if answer is None:
            raise JevUnavailable(
                f"the judgment service did not answer {name!r}")
        out[name] = _one_answer(question, answer)
    return out


def _one_answer(question: dict, answer) -> dict:
    """One SDK answer, read as the kind of question that was asked."""
    kind = question["kind"]
    if kind == "noul":
        if not hasattr(answer, "noul"):
            raise JevUnavailable(
                f"{question['name']!r} was asked as a condition and came back "
                f"as {type(answer).__name__}")
        return {"probability": _as_float(answer.noul), "level": None,
                "expected_score": None, "confidence": None, "probabilities": None}
    if kind == "choice":
        if not hasattr(answer, "choice"):
            raise JevUnavailable(
                f"{question['name']!r} was asked as a choice and came back "
                f"as {type(answer).__name__}")
        options = [str(name) for name, _ in question["options"]]
        probabilities = {str(name): _as_float(p)
                         for name, p in (getattr(answer, "probabilities", None) or {}).items()
                         if str(name) in options}
        chosen = str(answer.choice)
        if chosen not in options:
            # An option nobody offered is not an answer to the question that
            # was asked. Better a recorded "not asked" than a label that is
            # not in the vocabulary quietly entering a confusion matrix.
            raise JevUnavailable(
                f"{question['name']!r} came back as {chosen!r}, which is not "
                f"one of the {len(options)} option(s) it was asked with")
        return {"probability": probabilities.get(chosen),
                "level": chosen,
                "expected_score": None,
                "confidence": _as_float(getattr(answer, "confidence", None)),
                "probabilities": probabilities or None}
    if kind == "score":
        if not hasattr(answer, "score"):
            raise JevUnavailable(
                f"{question['name']!r} was asked as a score and came back "
                f"as {type(answer).__name__}")
        levels = [str(name) for name, _ in question["levels"]]
        probabilities = {levels[int(at)]: _as_float(p)
                         for at, p in (getattr(answer, "probabilities", None) or {}).items()
                         if 0 <= int(at) < len(levels)}
        # The level with the most probability on it, which is a reading of
        # what came back and not a threshold: no number is compared with a
        # line, and the whole distribution is stored beside it. The expected
        # score is kept as the service gave it, because it can fall between
        # two levels and rounding it away would lose that.
        highest = max(probabilities, key=lambda name: probabilities[name]) if probabilities else None
        return {"probability": probabilities.get(highest) if highest else None,
                "level": highest,
                "expected_score": _as_float(answer.score),
                "confidence": _as_float(getattr(answer, "confidence", None)),
                "probabilities": probabilities or None}
    raise ValueError(f"unknown question kind {kind!r}")


def _request_id(reply) -> str:
    """The provider's id for this call, or an empty string.

    The SDK raises rather than returning nothing when the header is absent,
    and a missing id is not a reason to lose an answer.
    """
    try:
        return str(reply.request_id or "")
    except Exception:  # an id nobody sent is not a failure
        return ""


def _usage(reply) -> dict:
    """What the call cost, in tokens, as far as the service said.

    Unknown is not zero (house rule 2): a count the service did not report
    is stored as `null`, so a month of these can be added up honestly or not
    at all.
    """
    usage = getattr(reply, "usage", None)
    return {"input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None)}


def _as_float(value) -> float | None:
    return None if value is None else round(float(value), 4)


# ------------------------------------------------- which version to pin to
#
# The service's model list and the version that answers are two different
# facts, and on 2026-09-17 they did not match: `models.list()` offered only
# `jev-latest` and `jev-preview`, while a call made as `jev-latest` answered
# as `jev-1.13.0` - which is then pinnable by name. So the only way to learn
# the name of the version to pin is to ask a question, and asking one costs
# a call. That is why `resolve` below is something a person asks for rather
# than something any of this does by itself.


#: The state `resolve` sends: one synthetic line about nothing, because the
#: point of the call is the version that answers, not the answer.
RESOLVE_STATE = "A build finished and logged nothing out of the ordinary."

#: The question it carries, because a question is required and this is the
#: cheapest true one.
RESOLVE_QUESTION = "This text describes a build that failed."


def list_models(api_key: str, *, base_url: str = "", timeout: float = 5.0,
                http_transport=None) -> list[dict]:
    """Every model this key may ask, as the service lists them.

    `http_transport` is how a test hands in an `httpx2` transport; left
    alone, this opens a connection to the service, which is why it is only
    ever reached from a command a person typed.
    """
    shadow.guard("llm.jev", detail="asking which model versions are served")
    try:
        typesafe_sdk = import_sdk()
        service = typesafe_sdk.TypeSafeClient(
            api_key=api_key, timeout=timeout, base_url=base_url or None,
            retry=typesafe_sdk.RetryPolicy(max_retries=0, timeout=timeout),
            transport=http_transport)
        try:
            listed = service.models.list()
        finally:
            service.close()
    except Exception as exc:  # a command prints this, never a traceback
        raise JevUnavailable(reason(exc, secret=api_key)) from exc
    return [{"name": str(getattr(m, "name", "")),
             "description": str(getattr(m, "description", "")),
             "release_date": str(getattr(m, "release_date", ""))}
            for m in getattr(listed, "models", ()) or ()]


def resolve_version(api_key: str, *, alias: str = "jev-latest", base_url: str = "",
                    timeout: float = 5.0, http_transport=None) -> dict:
    """Ask `alias` one throwaway question, and report the version that answered.

    One deliberate call, so a pin can be moved on purpose and the move
    recorded as a re-validation rather than discovered later in a record.
    """
    shadow.guard("llm.jev", detail=f"one question, to learn what {alias} answers as")
    try:
        typesafe_sdk = import_sdk()
        service = typesafe_sdk.TypeSafeClient(
            api_key=api_key, model=alias, timeout=timeout, base_url=base_url or None,
            retry=typesafe_sdk.RetryPolicy(max_retries=0, timeout=timeout),
            transport=http_transport)
        try:
            reply = service.system_one(
                state=RESOLVE_STATE,
                questions={"failed": typesafe_sdk.Noul(instructions=RESOLVE_QUESTION)},
                model=alias)
        finally:
            service.close()
    except Exception as exc:  # a command prints this, never a traceback
        raise JevUnavailable(reason(exc, secret=api_key)) from exc
    served = str(getattr(reply, "model", "") or "")
    if not served:
        raise JevUnavailable("the judgment service did not say which model version answered")
    return {"asked_as": alias, "served": served, "usage": _usage(reply),
            "request_id": _request_id(reply)}
