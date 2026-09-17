"""Asking a fixed battery of typed questions, and keeping what came back.

A question here is a value, not a string built at the call site. It has a
name, the exact words it is asked in, and - because decision 0032 says so -
the class of state it sends, so what would leave the box is a property of
the question and can be read off the code rather than guessed from it.

Two kinds, which is all this model has that this package uses:

* `Noul` - the probability that one named condition holds.
* `Score` - a position on ordered, described levels.

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
from fsmes.integrations.jev.transport import JevTransport, SdkTransport

log = structlog.get_logger("jev.client")

#: The one name the key is read from. Said here so every sentence about it
#: and the documentation cannot drift apart.
_KEY_SETTING = "MES_JEV_API_KEY"


class JevUnavailable(RuntimeError):
    """The judgment was not asked, and this is the sentence that says why."""


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
    nouls: tuple[Noul, ...]
    score: Score | None = None

    def as_questions(self) -> list[dict]:
        asked = [n.as_question() for n in self.nouls]
        if self.score is not None:
            asked.append(self.score.as_question())
        return asked

    def text_of(self, name: str) -> str:
        for question in (*self.nouls, self.score):
            if question is not None and question.name == name:
                return question.text
        raise KeyError(name)


@dataclass(frozen=True)
class Answer:
    """One answer, with everything needed to read it again in a month."""

    question: str
    question_sha256: str
    probability: float | None
    level: str | None
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
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "model": self.model,
            "request_id": self.request_id,
            "asked_at": self.asked_at,
        }


def question_sha256(text: str) -> str:
    """The wording, as a fingerprint. Short: this identifies, it does not seal."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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

    def ask(self, questions: QuestionSet, state: str) -> list[Answer]:
        """The battery, asked once. Raises if it was not answered."""
        asked = questions.as_questions()
        reply = self.transport.ask(state=state, questions=asked, model=self.model)
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
                confidence=got.get("confidence"),
                probabilities=got.get("probabilities"),
                model=served,
                request_id=request_id,
                asked_at=now,
            ))
        return out


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
        transport = SdkTransport(settings.jev_api_key,
                                 base_url=settings.jev_base_url,
                                 timeout=settings.jev_timeout_seconds)
    return JevClient(transport, model=settings.jev_model.strip()), ""
