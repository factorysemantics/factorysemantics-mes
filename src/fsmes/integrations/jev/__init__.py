"""A hosted judgment model, asked fixed typed questions and nothing else.

Jev answers named, typed questions about state it is given - a probability
that a condition holds, a position on described levels - and writes no text.
That makes it a different kind of thing from the language models this
package already talks to: there is no reply to parse, so there is no reply
to misparse.

What lives here is the client and the transport behind it, and nothing that
decides anything. Every question in this package is asked somewhere else,
beside the deterministic answer it is a second opinion on, and a judgment is
a proposal: it may not be an input to any number the scoring harness grades,
and it never runs inside a gate. That is decision 0031.

What leaves the box is one hosted call over the internet, which is why
`fsmes.shadow.REGISTER` carries `llm.jev` as refused: a plant lending us its
data to watch did not agree to that, and this path has no business being
open while it watches. Today the only caller is the build loop's run-log
triage, whose state is a simulated plant's own log.
"""

from fsmes.integrations.jev.client import (
    Answer,
    JevClient,
    JevUnavailable,
    Noul,
    QuestionSet,
    Score,
    available,
    from_settings,
)
from fsmes.integrations.jev.transport import NO_SDK, JevTransport, SdkTransport

__all__ = [
    "NO_SDK",
    "Answer",
    "JevClient",
    "JevTransport",
    "JevUnavailable",
    "Noul",
    "QuestionSet",
    "Score",
    "SdkTransport",
    "available",
    "from_settings",
]
