"""The four analyses as read tools: the payload a screen gets, unchanged.

`oee_breakdown`, `state_timeline`, `downtime_pareto` and `tag_trend` are the
questions an ERP cannot answer, and until now they were HTTP-only - so an agent
asked about the plant's own analytics had to rebuild them out of `machines` and
the state history. A recomputed OEE is the thing
[0031](../../../docs/decisions/0031-a-judgment-is-a-proposal.md) and
[0033](../../../docs/decisions/0033-availability-is-a-share-of-what-was-watched.md)
exist to prevent, and a second arithmetic reachable only through an agent would
disagree with the screens sooner or later.

So these four are pass-throughs and nothing else. Each sends one `GET` to
`/analysis/...` and hands back what came back, whole: **an agent that reads them
inherits the honesty rather than being told about it** - a station whose figures
the coverage floor withholds arrives withheld, with the ledger that explains it,
and there is nothing here that could average it away.

## Which of the four carries a coverage figure, and which does not

Read off the payloads rather than promised for all four, because they differ and
a tool that implied otherwise would be the honesty going wrong in the one place
this file exists to keep it:

* **`oee_breakdown` carries all of it.** `coverage` and `coverage_floor` on the
  line and on every station, the `coverage_note` that withholds a figure, and
  the whole `ledger` behind it, beside `observed_seconds` and `unknown_seconds`.
  This is the one whose numbers are rates, and rates are what coverage governs.
* **`downtime_pareto` carries how blind the window was, not a coverage ratio.**
  `unknown_seconds` and `unknown_share` - machine-seconds nobody watched over
  machine-seconds in the window - and no ledger. So "how much of this pareto's
  window was watched" is answerable; "what share of it did each machine
  contribute" is not, from this payload.
* **`state_timeline` and `tag_trend` carry no coverage figure at all.** A Gantt
  and a trend are what the MES *recorded*, drawn on a window clamped to when it
  started watching (`window.clamped`, and for a trend the tag's own first
  reading). Neither says what share of the window anybody was watching, and
  neither may be read as saying the window was watched. Ask `oee_breakdown` for
  the same window when that is the question.

Nothing here supplies a coverage figure a route does not serve. PR #128's chart
kit draws `data-coverage=absent` for the two that have none, which is the same
fact said on a screen.

## Two of these existed, and both said a shift was eight hours

`downtime(hours=8.0)` and `tag_trend(hours=1.0)` were declared in
`mcp_server.py` with a default window of their own. The routes deliberately
have none - left out, `hours` is *this plant's* `[process]
default_report_hours`, read at the moment of the request, which is what makes
it editable on Engineering's Configuration page with no restart. A tool that
defaults to eight was telling a twelve-hour plant that its own default was
somebody else's, in the one place nobody would look. These four replace them:
`hours` left out reaches the route left out, and every answer states the
`requested_hours` it was given.

## No write tool, and no capability of its own

Reads are free: `plant.read` is the whole gate, as it is for every read in this
product. Nothing here takes `dry_run`, and
`tests/test_the_analyses_answer_an_agent_the_way_they_answer_a_screen.py`
asserts that of the module's whole catalogue rather than trusting the reading.

## A window wider than one tool result

`RESULT_LIMIT` shows a model the first 6,000 characters of a tool result, and a
twelve-station Gantt or an eight-hour trend is several times that. The loop
would drop whole trailing items and say how many - honest, but blunt. So each
tool bounds *itself*, the way `plant_settings` has since #108, and never with a
silent cut:

* **A list is paged.** `oee_breakdown` and `downtime_pareto` return as many
  whole items as fit, say how many of how many (`stations_showing`,
  `reasons_showing` beside the payload's own totals), and name the call that
  reaches the rest. Every line-level figure beside the list - the rollup, the
  coverage, `total_seconds`, the two labelling totals - is the *line's* and
  stays true of the whole line however few items are shown.
* **A Gantt asks the plant for fewer machines.** `state_timeline`'s route takes
  `limit`, so rather than dropping machines out of the answer and leaving the
  payload's own `machines_shown` describing a list that is no longer there, the
  tool measures what came back and asks again for the number that fits. What
  comes back is then a payload a screen could have got, with its own counts
  true of it.
* **A trend asks the plant for fewer buckets.** Taking every nth point would
  drop the excursions that `min` and `max` per bucket exist to keep - an
  excursion inside a discarded bucket would vanish from the answer. Asking for
  fewer, wider buckets is the plant's own arithmetic over the whole window,
  which is what the screen does when it has fewer pixels, and the answer says
  the bucket count it was given and the one that was asked for.

Nothing here resamples, reorders or rounds anything the plant did not.
"""

from __future__ import annotations

import json
from urllib.parse import quote

#: How much of one tool result a bounded list may fill. The rest is headroom
#: for the model's own turn and for the loop's framing. The same share
#: `mcp/settings.py` fits its rows into, for the same reason and with the same
#: last resort: a plant that lowers `[admin] agent_result_limit` below the
#: product's default can still overrun this, and that case is caught by the
#: loop, which drops whole items and says how many.
LIST_SHARE = 0.6

#: What a trend says when the plant was asked for fewer buckets than the caller
#: wanted. A sentence, because the thing a reader has to understand is that the
#: numbers are still the plant's own over the whole window - wider slices of it,
#: not a sample of the narrow ones.
_TREND_MORE = (
    "{asked} buckets of this window do not fit one answer, so the plant was asked "
    "for {used} instead: every point is still its own mean with its own min and "
    "max, over a wider slice of the same window, and nothing was dropped. Narrow "
    "the window with hours= or shift= for finer buckets."
)

#: The fewest buckets a trend is ever asked for. Below this a "trend" is two
#: numbers and a wide window, which is not a picture of anything.
MIN_BUCKETS = 12


def _budget() -> int:
    """Characters one answer may take, read from the agent loop's own limit
    rather than written down a second time here."""
    from fsmes.services.agent import RESULT_LIMIT

    return int(LIST_SHARE * RESULT_LIMIT)


def _length(payload: dict) -> int:
    return len(json.dumps(payload, default=str))


def _query(**params) -> str:
    """The route's own query string, leaving out what nobody asked for.

    `hours=None` is left out rather than filled in, which is the whole point:
    the route with no `hours` reads this plant's `[process]
    default_report_hours` at the moment of the request. A named `shift` drops
    `hours` entirely - the route ignores it, and sending a number that is
    ignored would describe a request nobody made.
    """
    if params.get("shift"):
        params.pop("hours", None)
    parts = [f"{key}={quote(str(value), safe='/,')}"
             for key, value in params.items() if value is not None]
    return ("?" + "&".join(parts)) if parts else ""


def register(mcp, call) -> dict:

    def _paged(plant: str, payload, *, listname: str, noun: str, advice: str,
               offset: int) -> dict:
        """The route's payload, whole where it fits and honestly paged where it
        does not.

        One item at a time against the real serialised length, rather than an
        estimate: a station with a long ledger and a station with none differ
        by a factor of three, and an estimate would either waste the budget or
        overrun it.
        """
        if isinstance(payload, dict) and "error" in payload:
            return payload
        items = list(payload.get(listname) or [])
        total = len(items)
        offset = min(max(0, int(offset)), max(0, total))
        envelope = {"plant": plant,
                    **{k: v for k, v in payload.items() if k != listname}}
        budget = _budget()
        kept: list = []
        for item in items[offset:]:
            if kept and _length({**envelope, listname: [*kept, item]}) > budget:
                break
            kept.append(item)
        out = {**envelope, listname: kept}
        if offset or offset + len(kept) < total:
            out[f"{listname}_showing"] = len(kept)
            out["more"] = advice.format(showing=len(kept), total=total, noun=noun,
                                        next_offset=offset + len(kept))
        return out

    # --------------------------------------------------------------- the losses

    @mcp.tool()
    def oee_breakdown(plant: str, line: str | None = None, hours: float | None = None,
                      shift: str | None = None, offset: int = 0) -> dict:
        """OEE per station with every loss named, plus the line's rollup - the
        payload the Shift analysis screen draws, unchanged.

        The losses are the point: "OEE 62%" says nothing, and "you lost 19
        minutes to downtime and 300 units to slow running" says where to stand.
        Each loss is in the unit its fix is measured in - seconds for
        availability, units for performance and quality.

        **Coverage governs every figure here.** `coverage` is how much of the
        window that was *asked for* anybody watched, and `ledger` is the
        account behind it. Below this plant's `coverage_floor` the figures are
        withheld and `coverage_note` is the sentence the screen puts beside
        them: `availability`, `performance`, `quality` and `oee` come back
        `null`, the evidence does not. Never read a `null` as a zero - an OEE
        of 0% and an OEE of "we were not watching" are different facts and only
        one is actionable. `unknown_seconds` is time nobody watched: not a
        state, not downtime, reported beside the states rather than inside them.

        `performance` is absent where the counted work will not fit inside the
        run time; the arithmetic stays on `performance_ratio` with
        `counts_outrun_run_time` naming why, because two of this MES's own
        records disagreeing is a finding and not a performance.

        `hours` left out is this plant's own default reporting window, and
        `window.requested_hours` says what that turned out to be;
        `window.hours` is what was realised and `window.clamped` is true when
        this MES simply had not been watching that long. `shift` windows it on
        the plant's own clock instead - `current`, `previous`, or a day and a
        code such as `2026-09-14/NIGHT` - and overrides `hours`. `line` left
        out is the line with the most machines.

        `machines_total` is how many stations the line has. A long line holds
        the tail back: `stations_showing` and `more` say so and name the call
        that reaches the rest. Every figure beside the list - `line_oee`,
        `constraint`, `coverage`, `stations_rated`, `stations_withheld` - is the
        whole line's and stays true however few stations are shown.

        Reading is free - no capability is needed for this call.
        """
        payload = call(plant, "GET",
                       "/analysis/oee" + _query(line=line, hours=hours, shift=shift))
        return _paged(plant, payload, listname="stations", noun="stations", offset=offset,
                      advice="showing {showing} of {total} {noun}; call again with "
                             "offset={next_offset} for the next of them, or with a "
                             "narrower window (hours= or shift=)")

    # -------------------------------------------------------------- the Gantt

    @mcp.tool()
    def state_timeline(plant: str, line: str | None = None, hours: float | None = None,
                       shift: str | None = None, equipment: str | None = None,
                       limit: int | None = None) -> dict:
        """Every state interval per machine - the shift as a Gantt, as the
        screen draws it.

        This is the view that makes a line legible: starvation walking
        downstream from a breakdown is obvious as a picture and nearly
        invisible as a table. Intervals shorter than one pixel of the chart are
        merged by the plant, which is why a machine's intervals are fewer than
        its state rows.

        `equipment` is a comma-separated list of machine codes; left out, the
        plant draws its own screenful (`[process] gantt_screenful`) and the
        answer says which machines exist so nothing reads as the whole line:
        `machines_shown` of `machines_total`, with every code in
        `machines_available`. A window too wide for one answer is asked of the
        plant again for the machines that fit - so `machines_shown` is always
        true of the list beside it - and `more` says what was left out and how
        to name it.

        `hours` left out is this plant's own default reporting window;
        `window.requested_hours` says what that was, and `window.clamped` is
        true where this MES had not been watching that long. `shift` overrides
        it - `current`, `previous`, or `2026-09-14/NIGHT`.

        **There is no coverage figure in this payload, and none is invented
        here.** A Gantt is what the MES recorded: the intervals it holds, on a
        window clamped to when it started watching. It does not say what share
        of that window anybody was watching, so a gap in a row is a gap in the
        record and must not be read as the machine being idle. When that is the
        question, ask `oee_breakdown` for the same window - it carries
        `coverage`, the ledger and `unknown_seconds` per machine.

        Reading is free - no capability is needed for this call.
        """
        path = "/analysis/timeline"
        query = dict(line=line, hours=hours, shift=shift, equipment=equipment)
        payload = call(plant, "GET", path + _query(**query, limit=limit))
        if isinstance(payload, dict) and "error" in payload:
            return payload
        machines = list(payload.get("machines") or [])
        out = {"plant": plant, **payload}
        if len(machines) < 2 or _length(out) <= _budget():
            return out

        # How many whole machines fit, measured on the answer rather than
        # guessed, and then asked of the plant so that its own `machines_shown`
        # describes the list it is beside.
        empty = {k: v for k, v in out.items() if k != "machines"}
        room = _budget() - _length({**empty, "machines": []})
        fits = 0
        taken: list = []
        for machine in machines:
            taken.append(machine)
            if fits and len(json.dumps(taken, default=str)) > room:
                break
            fits += 1
        narrowed = call(plant, "GET", path + _query(**query, limit=max(1, fits)))
        if isinstance(narrowed, dict) and "error" in narrowed:
            return narrowed
        shown = narrowed.get("machines_shown")
        total = narrowed.get("machines_total")
        return {"plant": plant, **narrowed,
                "more": f"{shown} of {total} machines drawn - a whole shift of "
                        f"intervals does not fit one answer. Name the machines you "
                        f"want with equipment=<codes from machines_available>, or "
                        f"narrow the window with hours= or shift=."}

    # ------------------------------------------------------------- the pareto

    @mcp.tool()
    def downtime_pareto(plant: str, line: str | None = None, hours: float | None = None,
                        shift: str | None = None, offset: int = 0) -> dict:
        """Downtime grouped by reason, worst first, with a running cumulative
        share - the payload the screen draws, unchanged.

        **Unlabelled downtime is reported under its own name**, never folded
        into an "other" bucket: on a line fed by OPC alone nothing labels a
        stop, and a pareto that quietly says 100% "other" is how a plant
        convinces itself it has no data problem. `unlabelled_share` states it
        outright.

        Every bucket says who named it. `labelled_by` is seconds per source:
        `here` is this MES's own, and the rest are named by supplier. A label
        from another system is real evidence and counts the same, and the two
        are not the same claim.

        **Two totals, stated.** A stop labelled from the plant's approved
        vocabulary groups on its code; one labelled with typed text groups on
        the text. `from_the_list_seconds` and `typed_seconds` say how much of
        the window came from each - the number that says whether a vocabulary
        is being used - and with `unlabelled_seconds` they account for every
        second in `total_seconds`. `vocabulary_total` is how many reasons are on
        the list right now.

        `unknown_seconds` is how much of this window nobody was watching. It is
        deliberately *not* a bucket - a disconnection is not downtime and must
        never be sorted beside a reason - and `unknown_share` prices it in
        machine-seconds, so one machine of a hundred going quiet reads as 1%
        rather than as a blind window. Those two are the whole of what this
        payload says about coverage: there is **no `coverage` ratio and no
        ledger** here, so how blind the window was is answerable and what each
        machine's watched share was is not. `oee_breakdown` over the same window
        carries both.

        `hours` left out is this plant's own default reporting window, stated
        back as `window.requested_hours`; `shift` overrides it. A pareto too
        long for one answer holds the smallest reasons back, worst first:
        `reasons_showing`, `reasons_total` and `more` say so, and the last
        bucket's `cumulative` says how much of the window is accounted for.

        Reading is free - no capability is needed for this call.
        """
        payload = call(plant, "GET",
                       "/analysis/downtime" + _query(line=line, hours=hours, shift=shift))
        out = _paged(plant, payload, listname="reasons", noun="reasons", offset=offset,
                     advice="showing {showing} of {total} {noun}, worst first; the last "
                            "bucket's cumulative share says how much of the window "
                            "these account for. Call again with offset={next_offset} "
                            "for the smaller ones")
        if isinstance(out, dict) and "reasons_showing" in out:
            out["reasons_total"] = len(payload.get("reasons") or [])
        return out

    # -------------------------------------------------------------- the trend

    @mcp.tool()
    def tag_trend(plant: str, machine: str, tag: str | None = None,
                  hours: float | None = None, shift: str | None = None,
                  buckets: int = 240) -> dict:
        """One machine's process value over the window, averaged into buckets
        with the min and the max of each - the trend the screen draws.

        Bucketed by the plant, not raw: an hour of 1 Hz data is 3,600 points per
        tag and no chart shows them. `min` and `max` come along with `mean`, so
        an excursion a mean would smooth away still appears, and `n` is how many
        readings the bucket holds. `t` is the middle of the bucket.

        `tag` left out is the machine's process value: the signal the tag map
        says this machine was wired up for, believed only where the machine has
        actually published it, and otherwise whatever it publishes that is not
        one of the tags every machine carries. `tag: null` with no points is a
        machine that has published no analog at all - not a flat line.

        The window is clamped to when this tag was first recorded, the same way
        OEE clamps to when the machine was first observed: an axis reaching back
        before the first reading draws "idle all day" over "we just started
        watching". `window.requested_hours` says what was asked for and
        `window.hours` what was realised.

        `hours` left out is this plant's own default reporting window; `shift`
        overrides it - `current`, `previous`, or `2026-09-14/NIGHT`.

        **There is no coverage figure in this payload, and none is invented
        here.** A trend is the readings this MES holds. A stretch of window with
        no point in it is a stretch nothing was recorded for, which is not the
        same claim as the value having held steady, and nothing here says what
        share of the window the machine was being watched over. `oee_breakdown`
        over the same window is what carries that.

        A window whose buckets do not fit one answer is asked of the plant again
        for fewer, wider ones - its own arithmetic over the whole window, which
        is what the screen does with fewer pixels, and never every nth point,
        which would drop the excursions `min` and `max` exist to keep.
        `buckets` then says what the plant was asked for, `buckets_requested`
        what you asked for, and `more` says it happened.

        Reading is free - no capability is needed for this call.
        """
        path = f"/analysis/tag/{quote(machine)}"
        query = dict(tag=tag, hours=hours, shift=shift)
        payload = call(plant, "GET", path + _query(**query, buckets=buckets))
        if isinstance(payload, dict) and "error" in payload:
            return payload
        points = list(payload.get("points") or [])
        out = {"plant": plant, **payload}
        if len(points) < 2 or _length(out) <= _budget():
            return out

        # Per point off the answer itself, so the bucket count asked for next is
        # measured rather than guessed.
        empty = {k: v for k, v in out.items() if k != "points"}
        room = _budget() - _length({**empty, "points": [], "buckets": buckets,
                                    "buckets_requested": buckets, "points_showing": 0,
                                    "more": _TREND_MORE.format(asked=buckets, used=buckets)})
        per_point = max(1, len(json.dumps(points, default=str)) // len(points))
        fewer = max(MIN_BUCKETS, min(buckets, room // per_point))
        narrowed = call(plant, "GET", path + _query(**query, buckets=fewer))
        if isinstance(narrowed, dict) and "error" in narrowed:
            return narrowed
        return {"plant": plant, **narrowed, "buckets": fewer,
                "buckets_requested": buckets,
                "points_showing": len(narrowed.get("points") or []),
                "more": _TREND_MORE.format(asked=buckets, used=fewer)}

    return {f.__name__: f for f in (oee_breakdown, state_timeline,
                                    downtime_pareto, tag_trend)}

