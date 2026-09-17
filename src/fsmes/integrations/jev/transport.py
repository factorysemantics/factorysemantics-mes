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

**Nothing in this file has been run against the real service.** The SDK
surface assumed by `SdkTransport.ask` is the one described in the survey
(`docs/ai/JEV.md`): a client built with an API key, a batch of questions
evaluated over one state, typed answers back. If TypeSafe's client is
shaped differently, this is the one function that changes - which is why
the rest of the package talks to the interface and not to the SDK.
"""

from __future__ import annotations

from typing import Protocol

import structlog

from fsmes import shadow

log = structlog.get_logger("jev.transport")

#: Said in one place, because there is one way to get the client.
NO_SDK = (
    "The judgment model's client is an optional extra. Install it with "
    "`pip install 'factorysemantics-mes[jev]'`. Nothing needs it: every "
    "caller keeps the answer it computes without one."
)


class JevTransport(Protocol):
    def ask(self, *, state: str, questions: list[dict], model: str) -> dict:
        """Answers for one batch of questions about one state, or raise.

        `questions` is this package's own shape, not the SDK's - one dict
        per question with `name`, `kind` (`noul` or `score`), `text` and,
        for a score, its ordered `levels`. The answer is this package's own
        shape too::

            {"model": "<the version actually served>",
             "request_id": "<the provider's id, or ''>",
             "answers": {"<name>": {"probability": 0.0..1.0,
                                    "level": "<for a score>",
                                    "confidence": 0.0..1.0 or None,
                                    "probabilities": {...} or None}}}

        Raising is the whole error contract. The caller records that the
        question was not asked and keeps what it already had.
        """
        ...


def import_sdk():
    """The SDK, or an error that says how to get one."""
    try:
        import typesafe_sdk
    except ImportError as exc:
        raise RuntimeError(NO_SDK) from exc
    if typesafe_sdk is None:  # the module hidden rather than absent
        raise RuntimeError(NO_SDK)
    return typesafe_sdk


class SdkTransport:
    """The real service.

    The key is held privately and never printed: not by `repr`, not by the
    log line below, not in an error. `fsmes info` and every status line say
    whether a key is set, which is the only part of it anybody needs.

    The SDK's own defaults are overridden rather than inherited. Its
    documented default is a 10-second timeout with two retries, which is a
    30-second worst case in front of a service whose median is about 100 ms;
    for a nightly triage pass that is half a minute of a build slot spent
    waiting for an answer nothing depends on. One attempt, five seconds,
    and a failure is recorded as "not asked".
    """

    def __init__(self, api_key: str, *, base_url: str = "",
                 timeout: float = 5.0) -> None:
        if not api_key:
            raise ValueError("a judgment client needs a key; ask available() first")
        self._api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    def __repr__(self) -> str:  # never the key
        where = self.base_url or "the client's own default endpoint"
        return f"SdkTransport({where}, timeout={self.timeout}s, key=set)"

    def ask(self, *, state: str, questions: list[dict], model: str) -> dict:
        # Belt and braces. Shadow mode already refuses to build this at all;
        # refused again here so a caller that built one some other way
        # cannot get past it.
        shadow.guard("llm.jev", detail=f"{len(questions)} typed question(s), {len(state)} characters of state")
        typesafe_sdk = import_sdk()
        kwargs = {"api_key": self._api_key, "timeout": self.timeout, "max_retries": 0}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        service = typesafe_sdk.TypeSafe(**kwargs)
        asked = [_as_sdk_question(typesafe_sdk, q) for q in questions]
        reply = service.evaluate(model=model, state=state, questions=asked)
        answers = _read_answers(reply, questions)
        served = getattr(reply, "model", "") or ""
        if not served:
            # A judgment stored against a version nobody recorded cannot be
            # reproduced, and decision 0031 says the served version is part
            # of the answer. Refusing here is better than storing the
            # version we asked for and calling it the one we got.
            raise RuntimeError(
                "the judgment service did not say which model version answered")
        log.info("judgment answered", model=served, questions=len(questions),
                 state_characters=len(state))
        return {"model": served,
                "request_id": str(getattr(reply, "request_id", "") or ""),
                "answers": answers}


def _as_sdk_question(sdk, question: dict):
    """One of this package's questions, in the SDK's own types."""
    kind = question["kind"]
    if kind == "noul":
        return sdk.Noul(name=question["name"], description=question["text"])
    if kind == "score":
        return sdk.Score(name=question["name"], description=question["text"],
                         levels=[sdk.Level(name=name, description=description)
                                 for name, description in question["levels"]])
    raise ValueError(f"unknown question kind {kind!r}")


def _read_answers(reply, questions: list[dict]) -> dict:
    """The SDK's answers, in this package's shape.

    Every question asked must come back. A battery that silently returns
    four answers out of six is the ambiguity this whole change exists to
    delete, so a missing one raises rather than reading as "no".
    """
    by_name = {getattr(a, "name", None): a for a in getattr(reply, "answers", [])}
    out: dict[str, dict] = {}
    for question in questions:
        answer = by_name.get(question["name"])
        if answer is None:
            raise RuntimeError(
                f"the judgment service did not answer {question['name']!r}")
        out[question["name"]] = {
            "probability": _as_float(getattr(answer, "probability", None)),
            "level": getattr(answer, "level", None),
            "confidence": _as_float(getattr(answer, "confidence", None)),
            "probabilities": dict(getattr(answer, "probabilities", None) or {}) or None,
        }
    return out


def _as_float(value) -> float | None:
    return None if value is None else round(float(value), 4)
