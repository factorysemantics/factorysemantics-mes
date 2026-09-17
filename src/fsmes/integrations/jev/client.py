"""Asking a fixed battery of typed questions, and keeping what came back.

A question here is a value, not a string built at the call site. It has a
name, the exact words it is asked in, and - because decision 0032 says so -
the class of state it sends, so what would leave the box is a property of
the question and can be read off the code rather than guessed from it.

Three kinds, which is all this model has:

* `Noul` - the probability that one named condition holds.
* `Score` - a position on ordered, described levels.
* `Choice` - one named option out of several that are not ordered, with a
  probability on every option. "Why did this machine stop" is that shape:
  starved and blocked and broken are not more and less of one thing.

**No question here has a threshold, and nothing here turns a probability
into a verdict.** The survey's own condition for one is a calibration plot
drawn on this project's data, and that plot does not exist yet; until it
does, "0.8 seemed reasonable" is not a threshold. So an answer is stored
with its probability and read by a person, and the deterministic answer
beside it is the one that is load-bearing.

Every answer carries its provenance: the model version **as served** (never
the one that was asked for), the request id, the hash of the question text
as it was actually asked, and when. A question's wording is part of its
identity - re-word it and last month's answers are answers to a different
question - so the hash is stored rather than trusted to a name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from fsmes import shadow
from fsmes.integrations.jev.transport import (
    KEY_SETTING,
    JevTransport,
    JevUnavailable,
    SdkTransport,
    reason,
)

log = structlog.get_logger("jev.client")

#: The one name the key is read from, from the module that says it, so every
#: sentence about it and the documentation cannot drift apart.
_KEY_SETTING = KEY_SETTING


@dataclass(frozen=True)
class Noul:
    """The probability that one named condition holds."""

    name: str
    text: str

    def as_question(self) -> dict:
        return {"name": self.name, "kind": "noul", "text": self.text}


@dataclass(frozen=True)
class Score:
    """A position on ordered levels, worst last, each described in words."""

    name: str
    text: str
    levels: tuple[tuple[str, str], ...]

    def as_question(self) -> dict:
        return {"name": self.name, "kind": "score", "text": self.text,
                "levels": [list(level) for level in self.levels]}

    @property
    def level_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.levels)


@dataclass(frozen=True)
class Choice:
    """One option out of several that are not ordered, and a probability on each.

    Unlike a `Score`, the options have no order: there is no sense in which
    `blocked` is more than `starved`. So nothing here has an expected value,
    and the answer is the option the model put most of its probability on,
    with the whole distribution kept beside it.

    Every option is described, including the one that means "none of these".
    An undescribed option is answered against whatever the words happen to
    suggest, which is the ambiguity typed questions exist to remove.
    """

    name: str
    text: str
    options: tuple[tuple[str, str], ...]

    def as_question(self) -> dict:
        return {"name": self.name, "kind": "choice", "text": self.text,
                "options": [list(option) for option in self.options]}

    @property
    def option_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.options)


@dataclass(frozen=True)
class QuestionSet:
    """One battery, asked in one request over one piece of state.

    `state_class` is decision 0032's vocabulary - `catalogue`,
    `configuration`, `observation`, `production` - and it describes what the
    state *is*, not where it came from. The build loop's state is a
    simulated plant's log, which is observation-shaped even though no plant
    made it; saying so is cheaper than arguing about it later.
    """

    name: str
    state_class: str
    nouls: tuple[Noul, ...] = ()
    choices: tuple[Choice, ...] = ()
    score: Score | None = None

    def as_questions(self) -> list[dict]:
        asked = [n.as_question() for n in self.nouls]
        asked.extend(c.as_question() for c in self.choices)
        if self.score is not None:
            asked.append(self.score.as_question())
        return asked

    def text_of(self, name: str) -> str:
        for question in (*self.nouls, *self.choices, self.score):
            if question is not None and question.name == name:
                return question.text
        raise KeyError(name)


@dataclass(frozen=True)
class Answer:
    """One answer, with everything needed to read it again in a month."""

    question: str
    question_sha256: str
    #: For a condition: the probability it holds. For a score or a choice:
    #: the probability on whichever level or option came out highest, with
    #: the whole distribution kept in `probabilities` beside it.
    probability: float | None
    #: The level a score fell on, or the option a choice selected. `None`
    #: for a condition, which has neither.
    level: str | None
    #: For a score: where the answer fell on the ordered levels, which can be
    #: between two of them. `None` for a condition and for a choice, whose
    #: options have no order for an answer to fall between.
    expected_score: float | None
    confidence: float | None
    probabilities: dict | None
    model: str
    request_id: str
    asked_at: str

    def as_record(self) -> dict:
        return {
            "question": self.question,
            "question_sha256": self.question_sha256,
            "probability": self.probability,
            "level": self.level,
            "expected_score": self.expected_score,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "model": self.model,
            "request_id": self.request_id,
            "asked_at": self.asked_at,
        }


def question_sha256(text: str) -> str:
    """The wording, as a fingerprint. Short: this identifies, it does not seal."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Judgment:
    """One battery, answered: the answers and what the call itself was.

    The answers are what a record reads; `model`, `request_id` and `usage`
    are facts about the request rather than about any one question, and they
    are kept here rather than copied onto each answer.
    """

    answers: tuple[Answer, ...]
    model: str
    request_id: str
    #: Tokens in and out as the service reported them, or `None` where it
    #: reported nothing. Unknown is not zero (house rule 2).
    usage: dict | None = None


class JevClient:
    """One transport, one pinned model version, no opinions.

    Holds nothing secret - the key lives behind the transport - so its
    `repr` is safe to print anywhere a transport's is.
    """

    def __init__(self, transport: JevTransport, *, model: str) -> None:
        self.transport = transport
        self.model = model

    def __repr__(self) -> str:
        return (f"JevClient(model={self.model!r}, "
                f"transport={type(self.transport).__name__})")

    def ask(self, questions: QuestionSet, state: str) -> Judgment:
        """The battery, asked once. Raises `JevUnavailable` if it was not answered.

        Whatever the transport does - an SDK that moved, a service that is
        down, a test's own object raising something nobody expected - leaves
        here as a `JevUnavailable` carrying the sentence a record prints.
        A judgment may not cost the thing it judges (decision 0031).
        """
        asked = questions.as_questions()
        try:
            reply = self.transport.ask(state=state, questions=asked, model=self.model)
        except Exception as exc:  # nothing here may reach a caller
            raise JevUnavailable(reason(exc)) from exc
        served = str(reply.get("model") or "")
        if not served:
            raise JevUnavailable("no model version came back with the answers")
        if served != self.model:
            # Not an error: a pinned version can be retired, and the answer
            # is still an answer. It is recorded as what it is, and said out
            # loud, because decision 0031 makes a version change a
            # re-validation rather than a footnote.
            log.warning("a different model version answered",
                        asked_for=self.model, served=served)
        request_id = str(reply.get("request_id") or "")
        now = datetime.now(UTC).isoformat()
        answers = reply.get("answers") or {}
        out = []
        for question in asked:
            got = answers.get(question["name"])
            if got is None:
                raise JevUnavailable(
                    f"nothing came back for {question['name']!r}")
            out.append(Answer(
                question=question["name"],
                question_sha256=question_sha256(question["text"]),
                probability=got.get("probability"),
                level=got.get("level"),
                expected_score=got.get("expected_score"),
                confidence=got.get("confidence"),
                probabilities=got.get("probabilities"),
                model=served,
                request_id=request_id,
                asked_at=now,
            ))
        return Judgment(answers=tuple(out), model=served, request_id=request_id,
                        usage=dict(reply.get("usage") or {}) or None)


def available(settings=None) -> tuple[bool, str]:
    """Whether a judgment can be asked at all, and the sentence for why not.

    Three ways to be unavailable, and every one of them is normal. The
    no-key answer is the one the test suite runs by default, because it is
    the one every installation of this package gets.
    """
    if settings is None:
        from fsmes.config import get_settings

        settings = get_settings()
    if shadow.enabled(settings):
        entry = shadow.path("llm.jev")
        return False, f"shadow mode: this MES may not reach {entry.reaches}"
    if not (settings.jev_api_key or "").strip():
        return False, f"no {_KEY_SETTING} in this environment"
    if not (settings.jev_model or "").strip():
        return False, "no MES_JEV_MODEL: a judgment stored against no version cannot be read again"
    if (settings.jev_model or "").strip().endswith("latest"):
        return False, ("MES_JEV_MODEL names a moving version; pin the one that "
                       "is served so an answer can be reproduced")
    return True, ""



def from_settings(settings=None, transport: JevTransport | None = None
                  ) -> tuple[JevClient | None, str]:
    """A client, or None and the sentence that says why there is none."""
    if settings is None:
        from fsmes.config import get_settings

        settings = get_settings()
    ok, why = available(settings)
    if not ok:
        return None, why
    if transport is None:
        # Building a client is where the first real call failed on
        # 2026-09-17: the SDK's class had a different name and the
        # `AttributeError` reached a run. A client that cannot be built is a
        # judgment that was not asked, which is a sentence, not a crash.
        try:
            transport = SdkTransport(settings.jev_api_key,
                                     base_url=settings.jev_base_url,
                                     timeout=settings.jev_timeout_seconds)
        except Exception as exc:  # nothing here may reach a caller
            return None, reason(exc, secret=settings.jev_api_key or "")
    return JevClient(transport, model=settings.jev_model.strip()), ""
