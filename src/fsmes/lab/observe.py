"""Watching the plant while the hour plays, instead of asking it afterwards.

Every measurement the lab took before this one was a question asked once, at
the end, of a plant that was about to be torn down. That is enough for *what
did the MES end up recording* and it is no use at all for *how long did it take
to say so*: by the time the run is over, the interval the answer lived in has
closed, and a stop that took four minutes to appear and one that appeared at
once leave the same trace.

So this polls. Three rules, and they are the whole file:

**It must not change what it is measuring.** These are HTTP requests against
the same API the run is scored through, on a machine that is also replaying a
line and running an agent. So the interval is a wall-clock second by default
rather than the agent's publishing interval - which can be fifty milliseconds -
and the routes are the cheap ones: the current state of each machine, and the
line view's own cursor-based feed, which returns what has happened since the
last look rather than the hour so far. A run whose harness was perturbed into
falling behind has its verdict withheld anyway, which is the safety net; the
interval is what keeps it from being needed.

**A look that failed is a fact, not an exception.** A route that did not answer
is written down as not having answered, with the reason, at the line second it
was asked. A measurement built on nine hundred looks and eleven failures should
say so rather than average over the gap.

**It records, it does not judge.** Nothing here knows what the script said or
what a lag means. It writes down what each surface said and the line second it
said it at; `fsmes.lab.measure.latency` puts that beside the truth. Keeping the
two apart is what lets the measurement be argued with in a test, and it is why
a run's samples are worth keeping in the results directory whether or not this
version of the measurement asked the right question of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: The routes a look asks, in order, and what each one is in a person's terms.
#: `/health` is here deliberately and answers nothing about the line - see
#: `CARRIES_NO_LINE_STATE`.
SURFACES = {
    "/equipment/states": "the operations screen's own feed: what each machine is doing now",
    "/line/events": "the line view's feed: station status, and the units booked since the "
                    "last look",
    "/health": "what the plant says about itself, including how much of itself it can see",
    "/equipment/connections": "whether the MES can still see each machine, and since when",
}

#: Surfaces that cannot answer *when did the MES notice* however often they are
#: asked, and why. Named rather than quietly left out of the results: a reader
#: who expected a number here is entitled to know that the route exists, that
#: it was asked, and that the question does not belong to it.
CARRIES_NO_LINE_STATE = {
    "/health": "health says this plant is alive, which plant it is and whether it may act - "
               "it carries nothing about what any machine is doing, so how long an event took "
               "to reach it is not a question it can answer",
}


@dataclass
class Look:
    """One pass over the surfaces, at one line second."""

    line_second: float
    at: str
    #: equipment code -> the state the operations feed was showing for it.
    states: dict[str, str] = field(default_factory=dict)
    #: The same question asked of the line view, which answers it from its own
    #: query. Two screens showing one machine two different states at one
    #: instant is a finding nothing else in the lab would catch.
    line_states: dict[str, str] = field(default_factory=dict)
    #: Units the line view had booked by this look, per machine, counted from
    #: its own feed rather than asked for as a total - which is what makes the
    #: feed cheap enough to poll. Per machine rather than summed, because the
    #: feed reports every machine on the line and a serial line counts most
    #: units once per station: adding them up and calling it the line's output
    #: would be six times the answer.
    booked_good: dict[str, float] = field(default_factory=dict)
    booked_scrap: dict[str, float] = field(default_factory=dict)
    #: The feed had more rows than it would hand over in one answer, so this
    #: look is catching up rather than current. Nothing is lost - the feed
    #: resumes from the last row it gave - but a reader is entitled to know.
    truncated: bool = False
    #: equipment code -> connected | disconnected | unknown, at this look.
    #: A machine the MES cannot see has no state at all, so it simply stops
    #: appearing in `states`; this is the surface that says why, and it is the
    #: only one that can tell "nobody is watching" from "nothing is happening".
    connections: dict[str, str] = field(default_factory=dict)
    #: What `/health` said the plant could see of itself: machines, connected,
    #: disconnected, and how many it has no connection fact for.
    watching: dict = field(default_factory=dict)
    #: Route -> why it did not answer.
    refused: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"line_second": round(self.line_second, 1), "at": self.at,
                "states": self.states, "line_states": self.line_states,
                "booked_good": self.booked_good, "booked_scrap": self.booked_scrap,
                "connections": self.connections, "watching": self.watching,
                "truncated": self.truncated, "refused": self.refused}


class Watch:
    """A during-run observer: hand it to `scored_run` as `observe`.

    Stateful on purpose. The line view's feed is a cursor - it answers with
    what has happened since the id you last saw - so the running total is the
    watcher's to keep, and keeping it is what makes each look cost one small
    reply instead of the hour so far.
    """

    def __init__(self, get=None, on_look=None) -> None:
        # Injected so the whole class can be exercised without a plant. The
        # default is the runner's own reader, which is the one the rest of the
        # lab already goes through.
        if get is None:
            from fsmes.sim.runner import _get as get
        self._get = get
        # Called with (base, token, line_second, how many looks so far) after
        # each look. The run uses it to tell the fleet console where this
        # plant is: an ephemeral plant claims its ports when it starts, so the
        # first look is the first moment anybody knows its address.
        self._on_look = on_look
        self.looks: list[Look] = []
        self._cursor = -1
        self._good: dict[str, float] = {}
        self._scrap: dict[str, float] = {}
        #: Whether the feed ever answered at all with a count. A watcher that
        #: never saw one is the difference between a screen that was behind and
        #: a screen that was never read - see the test that pins it.
        self.counted = False
        #: Routes that have failed, and how many times, so a measurement can
        #: say how much of the hour it did not see.
        self.failures: dict[str, int] = {}

    def __call__(self, base: str, token: str, line_second: float) -> None:
        look = Look(line_second=line_second,
                    at=datetime.now(UTC).replace(tzinfo=None).isoformat())

        said = self._ask(base, token, "/equipment/states", look)
        if isinstance(said, list):
            look.states = {str(row.get("equipment")): str(row.get("state"))
                           for row in said if row.get("equipment")}

        said = self._ask(base, token, f"/line/events?since={self._cursor}", look,
                         route="/line/events")
        if isinstance(said, dict):
            look.truncated = bool(said.get("truncated"))
            self._absorb(said)
            look.booked_good = dict(self._good)
            look.booked_scrap = dict(self._scrap)
            look.line_states = {str(row.get("code")): str(row.get("state"))
                                for row in said.get("stations") or [] if row.get("code")}

        said = self._ask(base, token, "/health", look)
        if isinstance(said, dict):
            look.watching = said.get("watching") or {}

        said = self._ask(base, token, "/equipment/connections", look)
        if isinstance(said, dict):
            look.connections = {str(row.get("equipment")): str(row.get("state"))
                                for row in said.get("machines") or [] if row.get("equipment")}

        self.looks.append(look)
        if self._on_look is not None:
            # Same rule as the hook that calls this: whatever the run wants to
            # do with a look must not be able to end the hour.
            try:
                self._on_look(base, token, look.line_second, len(self.looks))
            except Exception as exc:                 # deliberate
                self.failures["the run's own look"] = (
                    self.failures.get("the run's own look", 0) + 1)
                look.refused["the run's own look"] = f"{type(exc).__name__}: {exc}"

    def _absorb(self, said: dict) -> None:
        """Take the feed's cursor and add up what it booked since the last look.

        The first answer is deliberately a cursor with no backlog (`since=-1`),
        which is the feed's own contract: a watcher that arrives mid-hour gets
        the live tail rather than history. So the running total starts at zero
        at the first look, and every figure built on it is *since the watch
        began*, which the measurement states rather than assumes.
        """
        cursor = said.get("cursor")
        if cursor is not None:
            self._cursor = int(cursor)
            self.counted = True
        # `units`, which is what the feed calls them. The first version of this
        # watcher read a key the feed does not have, and every look came back
        # with a count of zero: a hundred and thirty-two looks agreeing that
        # the MES had booked nothing while the MES was demonstrably booking
        # thousands. A measurement that cannot be wrong about the plant can
        # still be wrong about the route, which is the argument for running
        # one against a real plant before believing it.
        for row in said.get("units") or []:
            code = str(row.get("equipment") or "")
            if not code:
                continue
            self._good[code] = self._good.get(code, 0.0) + float(row.get("good") or 0)
            self._scrap[code] = self._scrap.get(code, 0.0) + float(row.get("scrap") or 0)

    def _ask(self, base: str, token: str, path: str, look: Look, route: str | None = None):
        route = route or path
        try:
            return self._get(base, path, token)
        except Exception as exc:                 # deliberate - see the module docstring
            look.refused[route] = f"{type(exc).__name__}: {exc}"
            self.failures[route] = self.failures.get(route, 0) + 1
            return None

    def as_json(self) -> dict:
        return {
            "_what": "What each screen was showing, look by look, while the hour played. "
                     "Written by the run itself; `scores.json` holds the measurement built "
                     "from it.",
            "surfaces": SURFACES,
            "looks_total": len(self.looks),
            "the_feed_answered_with_a_cursor": self.counted,
            "failures": self.failures,
            "looks": [look.as_json() for look in self.looks],
        }

    def write(self, path) -> None:
        """Keep the raw looks beside the run, whatever the measurement made of
        them. A version of the measurement that asked the wrong question is
        worth re-running against a run somebody already paid for."""
        from pathlib import Path

        Path(path).write_text(json.dumps(self.as_json(), indent=2, default=str) + "\n",
                              encoding="utf-8")
