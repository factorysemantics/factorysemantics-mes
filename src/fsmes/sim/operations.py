"""The people part of a plant: inspections and material issue.

The OPC agent gives the MES its machines. This gives it its operators - the
activity a line generates that no PLC reports, and without which the quality
screens, the non-conformance flow and genealogy are all empty pages sitting
on top of a working database.

It talks to the MES only through the HTTP API, exactly as an operator's
browser or an agent would. That is the dogfood rule doing its job: if a
scenario cannot be expressed here, the gap is in the product's own surface,
and a missing endpoint shows up as a thing this cannot do rather than as a
weakness nobody notices.

Inspections measure what the line actually made. A fill-weight check reads the
Refill station's recorded FillWeight rather than inventing a number, so a
quality excursion in the simulated line becomes a failing check and an open
non-conformance without either side being told about the other.

A reading is taken *by an instrument*, and which one is the floor's own fact
rather than the MES's: the plant's pack puts its gauges on the register, and
the pack's floor script says which gauge measures what, how often each is
picked up, and which one is drifting between calibrations. See
`fsmes.sim.measurement`. A plant with no floor script is unchanged - it
records what the tag said and names no gauge, which is *not recorded* and is
the honest answer.

It also works the order book, because a supervisor does. The MES does not
finish an order at its quantity and will not start (decision 0029): reaching a
number and being finished are different facts, and the MES only knows the
first. The person who knows the second is the shift supervisor, and here that
person is simulated - so when the line has made the number, `FLOOR-SUP` signs
in as themselves, completes the order through the API and releases the next
one in the book. The audit row carries their name. Nothing here invents an
order: when the book is empty the floor says so, once, and the line's counts
become unassigned production, which is the true answer (decision 0019).

The supervisor also raises the maintenance work that has come due and runs the
rules that hand it out, if the floor script asks. Who does it is the simulated
crew, if the script asks for one: each mechanic on this shift's roster walks to
his machine, starts the order the rules gave him, and finishes it with what he
found and how long the machine was down. He asks two questions first, and they
are the plant's own data rather than this module's opinion - is my window open,
and can I get at the machine - so the job that needs the line stopped waits for
the line to be stopped, all shift if that is how the shift went. An order still
at `assigned` at the end of it is a record and not a gap: see `work_the_list`
and the `maintenance` block of a pack's `floor.json`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import NamedTuple

import httpx
import structlog

from fsmes import identity
from fsmes.config import Settings
from fsmes.db import utcnow
from fsmes.sim import measurement
from fsmes.sim.measurement import Bench

log = structlog.get_logger("operations")

# Orders the floor can still work on. Taken from OrderStatus rather than
# guessed: an earlier version looked for "in_progress", which is not a status
# this MES has, so every running order read as idle and the floor released a
# fresh one every twenty seconds forever.
ACTIVE_STATUSES = ("released", "running")

#: Far enough away that an order with no due date sorts after every order that
#: has one, rather than ahead of all of them the way an empty string would.
_NO_DUE_DATE = "9999-12-31T23:59:59"

#: How long a mechanic takes to get to the machine with his tools, in line
#: seconds, when a pack's floor script does not say. Without it an order is
#: assigned and started in the same second, and the Maintenance page shows a
#: plant where nobody walks anywhere.
_WALK_S = 180.0

#: How long a machine must have been standing before somebody opens it for a
#: job that needs it stopped, in line seconds, when a pack does not say.
#: A mechanic does not take the filler apart because the conveyor starved it
#: for forty seconds: a job that needs the line stopped waits for the line to
#: be stopped, and ten minutes is the difference between a hiccup and a stop.
#: It is also what keeps this floor from writing a stop of its own across a
#: changeover the line was already having - two stops in the records for one
#: machine that stopped once, and a planned stop read as downtime.
_STANDING_S = 600.0

#: How long a job takes when no plan says so. A corrective order raised
#: against a machine has no plan behind it and therefore no expected time; the
#: product's dispatcher assumes one the same way, for the same reason.
_ASSUMED_JOB_MINUTES = 30.0

#: The windows this floor knows how to read. An order whose window is a word
#: that is not here waits, and says so once: guessing at it would be this
#: floor deciding when a plant may stop its line.
_WINDOWS = ("anytime", "between_orders", "end_of_shift")


class _Answer(NamedTuple):
    """Yes or no, the sentence why, and the name of the reason.

    The sentence is for a person reading the log and carries this moment's
    figures - how long the machine has been standing, how many minutes of the
    shift are left. The key is the reason itself with no figures in it, so
    that a job which has been waiting on the same reason for eight hours says
    so once instead of once every twenty seconds with a different number in
    it. Counting down in the log is not an event.
    """

    ok: bool
    because: str
    key: str


@dataclass
class _Job:
    """One maintenance order in one person's hands, as this floor sees it.

    Two of these facts are the floor's own and the MES has no column for
    either: the instant somebody set off for the machine, and the instant the
    machine actually went down for the job. Everything else here - the order,
    the person, the plan - is the plant's, and is read back from the roster
    every pass rather than remembered.
    """

    order: str
    person: str
    equipment: str
    plan: str | None
    needs_stop: bool
    #: How long this job takes, in line minutes: the plan's expected time give
    #: or take a tenth, from this run's own seeded stream.
    minutes: float
    #: This floor's own monotonic clock, in its own seconds - so a replay at
    #: 30x walks and works thirty times as fast as the plant it is replaying.
    at_the_machine: float
    #: When the job will be finished. None until it has been started: a job
    #: nobody has started has no end, which is not the same as one ending now.
    finished_at: float | None = None
    #: When the machine went down for this job, and when it came back up. Both
    #: None when the job needs no stop - which is nought minutes of downtime
    #: against the order and not an unknown number of them.
    stopped_at: float | None = None
    came_back_at: float | None = None
    #: What the machine was doing when the mechanic got there, so it can be
    #: handed back the way it was found.
    found_state: str | None = None
    #: Said once per job, not once a pass.
    said_the_line_took_it_back: bool = False


def _book_position(order: dict) -> tuple:
    """Where an order sits in the book: priority, then due date, then code.

    All three, so two orders of the same priority and the same due date still
    have one answer rather than whichever the database listed first.
    """
    return (order.get("priority", 50), order.get("due_date") or _NO_DUE_DATE,
            order.get("code") or "")


def _slug(text: str) -> str:
    """fill_weight and FillWeight are the same characteristic."""
    return re.sub(r"[^a-z0-9]", "", text.lower())

#: How many orders the planner reads to learn what this plant makes. The same
#: five hundred every other read on this floor takes a page of. The list comes
#: back newest first, so one page is the last five hundred orders this plant
#: made - which on any lab plant is the pack's own book and the planner's
#: copies of it, and always holds the highest code in use.
_BOOK_WINDOW = 500

#: A code and the number on the end of it: `WO-ACME-4711` is `WO-ACME-` and
#: 4711, four digits wide. A code with no number on the end cannot be
#: continued, and is left out of the pattern rather than guessed at.
_SEQUENCE = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)$")


@dataclass(frozen=True)
class Book:
    """The pattern of what one plant makes, read off its own order book.

    A simulated planner has to plan *this* plant's work, and what this plant
    makes is already written down - in the orders its pack put in its book. So
    the pattern is read back from the plant over the same API everything else
    here uses: the sequence its codes are in, the materials, quantities and
    priorities in the order they were planned, and how far apart their due
    dates are. Nothing is invented, and nothing is read off disk - this floor
    has never opened a pack file and is not about to start.

    A plant whose book has never held an order has no pattern to continue, and
    `read` answers None. That is the honest answer rather than a first order
    of some default size: what a plant makes is not something a simulator
    knows.
    """

    #: `WO-ACME-` and 4, so the order after 4,720 is `WO-ACME-4721`.
    prefix: str
    width: int
    #: The number the pattern starts at, so which row a new order copies is a
    #: fact about its own code rather than about how many passes have run.
    first: int
    #: The highest number in the book, and the one new codes count on from.
    last: int
    #: What this plant makes: material, quantity, priority, in the order the
    #: book first asked for each. One row per *distinct* combination, not per
    #: order - bottling's ten orders are seven sizes, three of them asked for
    #: twice. Distinct on purpose: the planner's own orders land in the same
    #: book it reads next time, and a pattern that grew by one every time it
    #: was copied would never come round.
    rows: tuple[tuple[str, float, int], ...]
    #: Hours between one order's due date and the next, averaged over the
    #: book. None when fewer than two orders carry a due date: a planner with
    #: no spacing to copy plans an order with no due date and says so, rather
    #: than inventing a date the pack never implied.
    due_spacing_hours: float | None
    #: The latest due date in the book, which is what the next one is spaced
    #: from. None when no order in the book has one.
    latest_due: datetime | None

    def code_for(self, number: int) -> str:
        return f"{self.prefix}{number:0{self.width}d}"

    def row_for(self, number: int) -> tuple[str, float, int]:
        """The material, quantity and priority an order of this number copies.

        In rotation, by the order's own number: the eighth order on a pattern
        of seven is the first one again. Taken from the number rather than
        from a counter, so a floor that restarts picks the pattern up where
        the codes say it is rather than at the top.
        """
        return self.rows[(number - self.first) % len(self.rows)]

    def due_for(self, number: int, *, now: datetime) -> datetime | None:
        """When an order of this number is due: the book's own spacing.

        Never behind now. A plant that has been up for a week has a book whose
        last due date is days past, and an order planned this morning as due
        last Tuesday is a date nobody can act on.
        """
        if self.due_spacing_hours is None or self.latest_due is None:
            return None
        from_here = max(self.latest_due, now)
        return from_here + timedelta(hours=self.due_spacing_hours * (number - self.last))

    @classmethod
    def read(cls, orders: list[dict]) -> Book | None:
        """The pattern in a plant's own order book, or None when there is none.

        Every status, because the pattern is what this plant makes and a
        completed order is still an example of it.
        """
        # Nothing here is a judgement about what the plant *should* make: an
        # order with no material, no quantity, or a code with no number on the
        # end of it is one this reader cannot continue, so it is left out of
        # the pattern rather than filled in.
        numbered: dict[str, list[tuple[int, int, dict]]] = {}
        for order in orders:
            found = _SEQUENCE.match(str(order.get("code") or ""))
            if not found or not order.get("material"):
                continue
            if float(order.get("quantity") or 0) <= 0:
                continue
            digits = found.group("number")
            numbered.setdefault(found.group("prefix"), []).append(
                (int(digits), len(digits), order))
        if not numbered:
            return None
        # The prefix most of this plant's orders share. A plant migrated from
        # somewhere else carries a few codes in another shape, and the
        # sequence to continue is the one it is actually running.
        prefix = max(sorted(numbered), key=lambda p: len(numbered[p]))
        run = sorted(numbered[prefix], key=lambda row: row[0])
        rows: list[tuple[str, float, int]] = []
        for _n, _w, order in run:
            row = (str(order["material"]), float(order["quantity"]),
                   int(order.get("priority") or 50))
            if row not in rows:
                rows.append(row)
        dues = sorted(moment for moment in
                      (_as_moment(order.get("due_date")) for _n, _w, order in run)
                      if moment is not None)
        gaps = [(later - earlier).total_seconds() / 3600.0
                for earlier, later in pairwise(dues)]
        return cls(
            prefix=prefix,
            width=max(width for _n, width, _order in run),
            first=run[0][0],
            last=run[-1][0],
            rows=tuple(rows),
            due_spacing_hours=(sum(gaps) / len(gaps)) if gaps else None,
            latest_due=dues[-1] if dues else None,
        )


def keep_planned(script: dict) -> int:
    """How many orders this plant's pack wants kept planned ahead of the line.

    **Zero by default, which is off**, and zero is what every pack that says
    nothing gets: a plant whose book is meant to run out - the scripted
    over-run experiment is one - behaves exactly as it did before a planner
    existed. The three lab packs name a number.
    """
    planning = script.get("planning") or {}
    try:
        return max(int(planning.get("keep_planned") or 0), 0)
    except (TypeError, ValueError):
        log.warning("`planning.keep_planned` is not a whole number of orders; "
                    "planning nothing", value=planning.get("keep_planned"))
        return 0


def _utcnow() -> datetime:
    """Now, naive UTC - the same instant the MES stores on every row."""
    return datetime.now(UTC).replace(tzinfo=None)


def _as_moment(value) -> datetime | None:
    """An instant out of a JSON response, naive UTC.

    The API answers in ISO 8601, sometimes with a zone and sometimes without
    (every stored instant is naive UTC). Both come back here as the naive UTC
    the next request has to send.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


#: Never renew more often than this, however short the plant's token life is.
#: A plant configured with a two-second login would otherwise have this floor
#: spending its shift signing in.
_MIN_SECONDS_BETWEEN_RENEWALS = 5.0


def _seconds_this_token_has_left(token: str) -> float | None:
    """How long the plant says this login is good for, read from the token.

    The login reply does not carry an expiry, but the token does. The MES's
    token is a base64 payload and a signature (`fsmes.services.auth`), and
    `exp` in that payload is *the plant's* answer rather than this floor's
    guess about how the plant was configured - which matters on a floor
    talking to a plant whose settings it does not share.

    None when it cannot be read: a token in some other shape is not something
    to fail over, and the caller falls back to the configured TTL. Nothing is
    verified here - this reads an expiry, it does not trust a claim.

    `exp` is compared against `utcnow().timestamp()` and not against
    `time.time()`, because that is what the plant compares it against
    (`auth.read_token`). The MES's single timestamp convention is naive UTC,
    and the epoch seconds of a naive datetime are read in the machine's own
    zone - so on a plant five hours behind UTC the two differ by five hours,
    and a floor that used the wall clock here would think it had five extra
    hours of login and find out it did not.
    """
    try:
        body = token.split(".")[0]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return float(claims["exp"]) - utcnow().timestamp()
    except (ValueError, TypeError, KeyError):
        return None


class Floor:
    """One simulated shop floor, working through the API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient,
                 rng: random.Random, script: dict | None = None,
                 speed: float = 1.0) -> None:
        self.settings = settings
        self.client = client
        self.rng = rng
        # How many seconds of line time one second of this floor's time is
        # worth. 1.0 on a real plant and on every plant Scott runs; 30 or 60
        # on a scored replay, where an hour of line is played in a minute.
        # It is not a cosmetic factor: *how long a machine was stopped for*
        # is a fact about the line, and judging a two-minute breakdown by the
        # four seconds it took to replay would file it as a micro stop.
        self.speed = speed if speed and speed > 0 else 1.0
        # What this floor does that no PLC reports, as data. Empty for a
        # plant whose pack names no floor script, which is every real one.
        self.script = script or {}
        # The gauges this floor measures with. Built with no register until
        # `read_the_register` has been able to ask the plant for one, so a
        # floor whose first read fails records readings with no gauge rather
        # than refusing to inspect.
        self.bench = Bench(self.script)
        # The last value this floor recorded for each characteristic, so it
        # does not write the same reading down twice. See `inspect`.
        self._last_recorded: dict[str, float] = {}
        # The empty book is said once, not every twenty seconds for a shift.
        # It is reset the moment an order is released, so a book that is
        # refilled and empties again says so again.
        self._said_the_book_is_empty = False
        # Which shift is running, and when that answer stops being true. The
        # shift is read from the plant rather than from this script's own
        # idea of a clock: the patterns are the plant's master data, and a
        # floor that decided for itself when night began would stamp its
        # measurements against one calendar and the MES against another.
        self._shift: str | None = None
        self._shift_until: float = 0.0
        # What was stopped the last time this floor looked: machine -> the
        # state it was in and the instant it began. A stop is named when it
        # is no longer there, which is the first moment its length is known.
        self._stopped: dict[str, dict] = {}
        # That an idle machine cannot be told from a starved or a blocked one
        # is said once, not every twenty seconds.
        self._said_idle_cannot_be_told_apart = False
        # The sampling plans this floor works to: which characteristics it
        # inspects several pieces at a time, and where the pieces come from.
        # Empty for a plant whose script has no `sampling` section, which is
        # every pack written before one existed.
        self.plans = measurement.plans(self.script)
        # The newest stored reading each plan has already used, so the next
        # sample is five *different* pieces. A floor that re-measured the
        # same five would be writing one sample down twice, and on an X-bar
        # chart two identical points collapse the mean range the limits are
        # built from.
        self._sampled_through: dict[str, datetime] = {}
        # That there is not yet five pieces' worth of history is said once
        # per characteristic, not every fifteen minutes until there is.
        self._said_no_pieces: set[str] = set()
        # When the last changeover this floor watched came back, so the cause
        # the pack plants after one can be planted. None until it has seen
        # one end: a floor that assumed a changeover it never saw would be
        # putting the offset on readings nothing explains.
        self._changeover_ended: datetime | None = None
        # Who this floor is, kept so it can sign in again on its own. A login
        # is good for one shift (`[auth] token_ttl_seconds`, twelve hours) and
        # this floor runs for weeks: on 2026-10-06 both lab plants' floors
        # stopped dead twelve hours to the minute after they started, every
        # request from then on answered 401, and nothing on any screen said
        # so. A simulation is only proof while it runs.
        self._credentials: tuple[str, str] | None = None
        # When to sign in again without being asked, on the monotonic clock.
        # Halfway through the login's life, so the first failed step never
        # happens rather than being recovered from. Infinity until the first
        # sign-in, and on a plant whose token life cannot be read at all.
        self._renew_at: float = float("inf")
        # One re-sign-in at a time. Six loops share this client, and six
        # tasks noticing the same expiry would otherwise sign in six times.
        self._signing_in = asyncio.Lock()
        # The maintenance jobs this floor's crew has in hand: person code ->
        # the one job that person is on. One person, one job at a time - the
        # same rule the product's dispatcher keeps on its side of the API.
        self._jobs: dict[str, _Job] = {}
        # The NAME of the reason each job that has been given out and not
        # started is waiting on, as last said - not the sentence, which counts
        # down. A shift's worth of "still waiting" every twenty seconds is
        # noise; the moment the reason CHANGES is the record. On this pack the
        # reason never changes, which is chain link 2 saying itself.
        self._waiting: dict[str, str] = {}
        # Said once each, not once a pass: a plan nobody wrote findings for,
        # an order somebody else started, and a shift nobody is rostered on.
        self._said_no_findings: set[str] = set()
        self._said_somebody_else_started_it: set[str] = set()
        self._said_nobody_is_rostered = False
        # That this plant's book holds no pattern to continue is said once.
        # A planner on a plant whose book has never had an order in it has
        # nothing to plan from, and saying so every minute would bury it.
        self._said_there_is_no_pattern = False
        # That the login will not come back is said once, not once a step: a
        # password that has changed under a running floor is a thing to fix,
        # and ten thousand identical lines is how it stays unfound. Cleared by
        # the next request that works, so a plant that comes back says so.
        self._said_the_login_will_not_come_back = False

    async def sign_in(self, code: str, password: str) -> None:
        r = await self.client.post("/auth/login", json={"code": code, "password": password})
        r.raise_for_status()
        self._remember_the_login(code, password, r.json()["token"])

    def _remember_the_login(self, code: str, password: str, token: str) -> None:
        """Hold the token, and work out when to replace it."""
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._credentials = (code, password)
        left = _seconds_this_token_has_left(token)
        if left is None:
            left = float(getattr(self.settings, "token_ttl_seconds", 0) or 0)
        self._renew_at = (time.monotonic()
                          + max(left / 2.0, _MIN_SECONDS_BETWEEN_RENEWALS)
                          if left > 0 else float("inf"))

    async def _sign_in_again(self, why: str, held: str | None) -> bool:
        """Replace the login this floor is holding. One line in the log, once.

        `held` is the authorization this floor was using when the caller
        decided a new one was needed. Another task may have replaced it while
        this one waited for the lock, in which case there is nothing to do and
        nothing to say - that is the same event, not a second one.
        """
        if self._credentials is None:
            return False
        async with self._signing_in:
            if self.client.headers.get("Authorization") != held:
                return True
            code, password = self._credentials
            try:
                r = await self.client.post("/auth/login",
                                           json={"code": code, "password": password})
                r.raise_for_status()
                token = r.json()["token"]
            except (httpx.HTTPError, KeyError) as exc:
                if not self._said_the_login_will_not_come_back:
                    self._said_the_login_will_not_come_back = True
                    log.error("the floor could not sign in again; no step will succeed "
                              "until somebody looks", user=code, why=why,
                              error=str(exc)[:160])
                return False
            self._remember_the_login(code, password, token)
            log.info("the floor signed in again", user=code, why=why,
                     good_for_seconds=round(_seconds_this_token_has_left(token) or 0.0))
            return True

    async def _send(self, method: str, path: str, *, json: dict | None = None,
                    params: dict | None = None) -> httpx.Response:
        """One request, with the floor's login kept alive around it.

        Every read and write this floor makes goes through here, so there is
        one place that knows the login can run out - rather than each of the
        dozen callers having to.
        """
        if time.monotonic() >= self._renew_at:
            await self._sign_in_again("the login was halfway through its life",
                                      self.client.headers.get("Authorization"))
        held = self.client.headers.get("Authorization")
        response = await self.client.request(method, path, json=json, params=params)
        if response.status_code != 401:
            if response.status_code < 400:
                self._said_the_login_will_not_come_back = False
            return response
        # Refused. Either the login ran out early or it was never valid; both
        # are answered by signing in once and trying the step again, and the
        # answer to a second refusal is to say so rather than to loop.
        if not await self._sign_in_again("the plant refused the floor's login", held):
            return response
        again = await self.client.request(method, path, json=json, params=params)
        if again.status_code == 401 and not self._said_the_login_will_not_come_back:
            self._said_the_login_will_not_come_back = True
            log.error("the floor signed in again and the plant still refuses it; no step "
                      "will succeed until somebody looks",
                      user=self._credentials[0] if self._credentials else None, path=path)
        if again.status_code < 400:
            self._said_the_login_will_not_come_back = False
        return again

    async def post(self, path: str, *, json: dict | None = None) -> httpx.Response:
        """One write. The response is the caller's to read, refusals and all."""
        return await self._send("POST", path, json=json)

    async def get(self, path: str, **params):
        r = await self._send("GET", path, params=params or None)
        r.raise_for_status()
        return r.json()

    async def every(self, path: str, **params) -> list[dict]:
        """Every row of a paged list, page by page.

        The simulator is not a screen: it inspects against every
        specification the plant has, so it reads to the end of the envelope
        rather than taking the first page and calling it the plant.
        """
        out: list[dict] = []
        offset = 0
        while True:
            page = await self.get(path, limit=500, offset=offset, **params)
            out.extend(page["items"])
            if not page.get("has_more") or not page["items"]:
                return out
            offset += len(page["items"])

    # ------------------------------------------------------------- gauges

    async def read_the_register(self) -> int:
        """Ask the plant which gauges it has, and how each one stands.

        The register is the authority on resolution and on when a gauge was
        last calibrated, so it is read from the plant rather than repeated in
        the floor's script - which is what makes a calibration recorded on
        the screen take effect in the next reading this floor takes.

        Returns how many gauges came back. A read that fails leaves the
        bench as it was and says so: measuring with no gauge is worse than
        measuring with yesterday's register, but neither is worth refusing to
        inspect over.
        """
        if not self.script:
            return 0
        try:
            register = await self.get("/quality/gauges")
        except httpx.HTTPError as exc:
            log.warning("could not read the gauge register; readings will name "
                        "whatever gauge the last read knew about",
                        error=str(exc)[:160])
            return 0
        rows = register.get("gauges") or []
        self.bench = Bench(self.script, rows)
        missing = [code for code in self.bench.gauge_codes
                   if code not in {row.get("code") for row in rows}]
        if missing:
            # Said out loud, every read: a script that names a gauge the
            # plant has never registered is a story nobody can follow in the
            # records, and silently measuring without it is how that becomes
            # invisible.
            log.warning("the floor script names gauges this plant does not have",
                        gauges=missing, registered=len(rows))
        return len(rows)

    async def calibrate_what_is_due(self, by: str = "FLOOR-SUP") -> list[str]:
        """Calibrate every gauge the floor script uses that has fallen due.

        What a plant actually does, and the reason a drifting gauge is a
        *story* rather than a permanent offset: the gauge goes out slowly,
        the calibration finds it, and the readings after it are clean. The
        result is `adjusted` - found out and brought back - rather than
        `fail_as_found`, which takes the gauge off the floor and would quietly
        end the story instead of closing it.

        Only the gauges this floor measures with. Calibrating the rest of a
        plant's register from here would be inventing work nobody did.

        **When** is the script's, and the default is *overdue* rather than
        *due soon* on purpose. A floor that calibrated on the warning would
        calibrate the drifting scale in its first twenty seconds, every time
        a plant is built - and the drifting-gauge story would be over before
        anybody could open the screen. Overdue is also what a plant with a
        fortnight's warning window actually does: the warning is for planning
        the visit, and the visit happens when it falls due.
        """
        if not self.script:
            return []
        try:
            register = await self.get("/quality/gauges")
        except httpx.HTTPError as exc:
            log.warning("could not read the gauge register", error=str(exc)[:160])
            return []
        ours = set(self.bench.gauge_codes)
        when = str((self.script.get("calibration") or {}).get("when", "overdue"))
        done: list[str] = []
        for row in register.get("gauges") or []:
            code = row.get("code")
            if code not in ours or row.get("status") != "in_service":
                continue
            ready = row.get("overdue") or (when == "due_soon" and row.get("due_soon"))
            if not ready:
                continue
            try:
                response = await self.post(
                    f"/quality/gauges/{code}/calibrate",
                    json={"result": "adjusted", "performed_by": by,
                          "notes": "Found reading high against the reference weight "
                                   "and adjusted. Routine, on the calibration schedule."})
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                log.warning("calibration refused", gauge=code,
                            status=exc.response.status_code, detail=exc.response.text[:160])
                continue
            done.append(code)
            log.info("calibrated a gauge", gauge=code, result="adjusted", by=by,
                     was_overdue=bool(row.get("overdue")))
        if done:
            await self.read_the_register()
        return done

    async def raise_what_is_due(self, by: str = "FLOOR-SUP") -> list[str]:
        """Raise a preventive order for every plan this plant says is due.

        What a shift supervisor does on his first pass: looks at what has
        come due and puts work on the list. The product decides WHAT is due
        (`services/maintenance.raise_due`, idempotent, one order per plan);
        this only decides that somebody looked, which is the part that
        belongs to the floor and not to the MES.

        It does not start anything. Raising work is one act, handing it out
        is another and doing it is a third, and they belong to three different
        people on a real shift - `dispatch_the_backlog` and `work_the_list`
        are the other two, each with its own key in the script. A plant whose
        pack asks for this one alone gets its plans coming due as orders with
        nobody's name on them, which is a real plant with no rules written
        down.

        Off unless the script asks, like everything else here: a pack with no
        `maintenance` block gets a plant whose plans come due with no orders
        against them, which is a real plant with no planner.
        """
        wanted = self.script.get("maintenance") or {}
        if not wanted.get("raise_due"):
            return []
        try:
            response = await self.post("/maintenance/raise")
            response.raise_for_status()
            raised = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not raise the maintenance work that is due",
                        error=str(exc)[:160])
            return []
        codes = [str(row.get("code")) for row in raised.get("raised") or []]
        if codes:
            # Said out loud with the plans beside the orders, because the
            # chain's answer key names its link by PLAN code and a reader of
            # this log has to be able to join the two without the database.
            log.info("raised the maintenance work that was due", by=by,
                     orders=codes,
                     plans=[row.get("plan") for row in raised.get("raised") or []],
                     started="none - this shift runs to the end of the order")
        return codes

    async def dispatch_the_backlog(self, by: str = "FLOOR-SUP") -> dict | None:
        """Hand the orders that are due to the people who are on shift.

        The second half of the supervisor's pass, and a different act from
        raising: raising says work exists, dispatching says who has it. The
        product decides WHO (`services/dispatch.dispatch`, idempotent, one
        audit row per assignment naming the rule that assigned); this only
        decides that somebody ran the rules, which is the part that belongs to
        the floor.

        It still does not start anything: an order at `assigned` is an order
        somebody has been given and not yet walked over to. Whether anybody
        ever does is `work_the_list`, and a plant that asks for this key and
        not that one is a plant whose work is given out and waits - which is
        exactly the state this pack's chain reads at its second link: given to
        an electrician in the first minutes, still his eight hours later,
        because the job needs the line stopped and the line ran all shift.

        Off unless the script asks. A pack with no `dispatch` key gets a plant
        whose due work sits at `due` with nobody's name on it - which is a real
        plant that has not written its rules down, and is what every pack
        written before the crew existed means.
        """
        wanted = self.script.get("maintenance") or {}
        if not wanted.get("dispatch"):
            return None
        try:
            response = await self.post("/maintenance/dispatch")
            response.raise_for_status()
            out = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not dispatch the maintenance work that is due",
                        error=str(exc)[:160])
            return None
        if out.get("considered"):
            # The reasons named rather than totalled, because "four left" is a
            # number a supervisor can do nothing with and "four nobody on
            # shift with the skill" is a morning.
            log.info("dispatched the maintenance work that was due", by=by,
                     considered=out["considered"], assigned=out["assigned"],
                     unassigned=out["unassigned"],
                     because=out.get("unassigned_by_reason") or {},
                     started="none - this shift gives work out, it does not do it")
        return out

    # ---------------------------------------------------------------- the crew

    async def work_the_list(self) -> list[str]:
        """The crew does the work it was given: walks over, starts, finishes.

        What #157 left out. The rules hand an order to a named person and the
        Maintenance page shows their name against it; until something does the
        job, that page is a list rather than a plant.

        One pass, for each person on this shift's roster who has been given a
        job: ask the two questions a mechanic asks, and if both answer yes,
        set off. **Is my window open** - the order's own, copied from the plan
        when it was raised: `anytime` always, `between_orders` only while
        nothing is on the line, `end_of_shift` only inside the last of it.
        **Can I get at the machine** - either the job needs no stop, or the
        machine has been standing long enough that the stop is the line's and
        not a hiccup. Neither question is answered here: both are read off the
        order and the machine, and what counts as long enough is the pack's.

        Then the walk (`walk_s`), then `start` as that person, then the job's
        expected time give or take a tenth, then `complete` with what he found
        and how long the machine was down. A job that needs a stop books one -
        `down`, named with the plant's own word for planned maintenance and
        the order's code in the sentence - so the stop and the job are one
        event in the records rather than two. A job that needs none books
        nought minutes of downtime, because the machine never stopped.

        What it does NOT do is matter as much: it never stops a machine that
        is making something, it never finishes an order it did not watch
        start, and it never invents a reason for a job that waits. The orders
        that wait say why, once each, in the log - and on this pack the answer
        is the same all shift, which is what makes chain link 2 a record.

        Off unless the script asks. Every pack written before 2026-10-09
        behaves exactly as it did: the work is raised, handed out, and waits.
        """
        wanted = self.script.get("maintenance") or {}
        if not wanted.get("crew"):
            return []
        try:
            # Three reads, once a pass, and each of them answers a question
            # the others cannot: who is on shift and what they hold, what the
            # machines are doing, and whether the line is between orders.
            roster = await self.get("/maintenance/roster")
            states = {row.get("equipment"): row
                      for row in await self.get("/equipment/states")}
            book = await self.get("/workorders", status=["released", "running"], limit=1)
        except httpx.HTTPError as exc:
            log.warning("the crew could not read what it had been given",
                        error=str(exc)[:160])
            return []

        if not roster.get("shift"):
            # Nobody is rostered, which is a fact about the calendar and not
            # an empty crew. Said once: a plant with no shift patterns would
            # otherwise say it every twenty seconds for ever.
            if not self._said_nobody_is_rostered:
                self._said_nobody_is_rostered = True
                log.info("nobody is rostered at the moment, so nobody is doing the "
                         "maintenance work this plant has given out",
                         why=roster.get("why_empty"))
            return []
        self._said_nobody_is_rostered = False

        between_orders = not book.get("items")
        ends = _as_moment((roster.get("shift") or {}).get("ends"))
        now = time.monotonic()
        finished: list[str] = []
        # The jobs already in somebody's hands first, and from this floor's
        # own list rather than from the roster: a mechanic whose shift ended
        # while he was inside a machine still has to put the cover back on and
        # hand it over, and a job dropped here would leave an order in
        # progress and a machine down for the rest of the run.
        for job in list(self._jobs.values()):
            if await self._get_on_with_it(job, states, now):
                finished.append(job.order)
        for person in roster.get("people") or []:
            code = str(person.get("person") or "")
            if code in self._jobs:
                continue
            on_now = person.get("on_now")
            if not on_now or not person.get("available"):
                # Nothing in their hands, or they are not on the floor at all
                # - off sick, on a course. The roster row says which, and the
                # dispatcher has already read the same row.
                continue
            if on_now.get("status") != "assigned":
                self._leave_it_to_whoever_started_it(code, on_now)
                continue
            await self._set_off(code, on_now, states, between_orders, ends, now)
        return finished

    def _window_is_open(self, window, minutes: float, between_orders: bool,
                        ends: datetime | None) -> _Answer:
        """Whether a job may be done at this moment, and the sentence why.

        The reason comes back whichever way the answer goes, because a job
        that waits all shift has to be able to say why on a screen and in a
        log. `end_of_shift` is read against the plant's own calendar on the
        plant's own clock, not against line time: a shift is eight hours
        wherever it is replayed, so an eight-hour shift played in sixteen
        minutes never reaches its own last hour, and a job that waits for the
        end of the shift honestly waits.
        """
        if window in (None, "anytime"):
            return _Answer(True, "the job can be done at any time", "anytime")
        if window not in _WINDOWS:
            return _Answer(False, f"the order's window is {window}, which this floor "
                           "does not know how to read, so nobody goes",
                           f"window-not-understood:{window}")
        if window == "between_orders":
            if between_orders:
                return _Answer(True, "the line is between orders", "between-orders")
            return _Answer(False, "the job can only be done between orders and the "
                           "line is running one", "the-line-is-running-an-order")
        if ends is None:
            return _Answer(False, "the job can only be done at the end of the shift "
                           "and nothing says when this shift ends",
                           "no-shift-end-to-wait-for")
        left = (ends - _utcnow()).total_seconds() / 60.0
        if 0.0 <= left <= minutes:
            return _Answer(True, f"the shift ends in {left:.0f} minutes and the job "
                           f"takes {minutes:.0f}", "the-shift-is-ending")
        return _Answer(False, f"the job can only be done in the last {minutes:.0f} "
                       f"minutes of the shift, and there are {left:.0f} to go",
                       "the-shift-is-not-ending-yet")

    def _can_get_at_it(self, equipment: str, states: dict) -> _Answer:
        """Whether a job that needs the machine stopped can have it.

        Stopped is not the same as momentarily idle. A machine that has been
        standing for `standing_s` line seconds is a machine the line has
        stopped; one that went quiet thirty seconds ago is starved, and a
        mechanic who took it apart would be the reason the line could not
        restart. The figure is the pack's, and `_standing_s` beside it says
        what it is for.

        A machine this MES holds no state for answers no, not yes: nothing
        here can say that a machine nobody has ever reported on is stopped
        (unknown is not zero), and the order waits with that written down.
        """
        row = states.get(equipment) or {}
        state = str(row.get("state") or "")
        if not state:
            return _Answer(False, f"the job needs {equipment} stopped and this MES "
                           "holds no state for it at all, so nothing here can say "
                           "that it is", f"no-state-at-all:{equipment}")
        if state == "running":
            return _Answer(False, f"the job needs {equipment} stopped and it is "
                           "running", f"the-machine-is-running:{equipment}")
        since = _as_moment(row.get("since"))
        if since is None:
            return _Answer(False, f"{equipment} is {state} and nothing says since "
                           "when, so nothing here can say the line has stopped it",
                           f"no-since:{equipment}")
        wanted = float((self.script.get("maintenance") or {}).get("standing_s", _STANDING_S))
        # In line seconds: an interval that lasted ten line minutes is over in
        # twenty of this floor's seconds on a replay at 30x, and a floor
        # reading the wall clock here would call every hiccup a stop.
        standing = max((_utcnow() - since).total_seconds(), 0.0) * self.speed
        if standing < wanted:
            return _Answer(False, f"{equipment} has been {state} for {standing:.0f} "
                           f"line seconds and a job that needs it stopped waits for "
                           f"{wanted:.0f}, because a machine that has just gone quiet "
                           "is starved and not stopped",
                           f"not-standing-long-enough:{equipment}:{state}")
        return _Answer(True, f"{equipment} has been {state} for {standing / 60.0:.0f} "
                       "line minutes, so the line has stopped it",
                       f"the-line-has-stopped-it:{equipment}")

    def _say_once_why_it_waits(self, order: str, answer: _Answer) -> None:
        """Why a job that was given out has not been started, once per reason.

        The reason changing is the event, not the waiting: an order whose
        answer is the same at eight in the morning and at two in the afternoon
        has one line in the log, and an order that was waiting for the line
        and is now waiting for a person has two. What counts as the same
        reason is the answer's key and not its sentence - the sentence counts
        down ("there are 141 to go") and a log that repeats it every pass is
        the floor talking to itself.
        """
        if self._waiting.get(order) == answer.key:
            return
        self._waiting[order] = answer.key
        log.info("a job that was handed out is waiting", order=order,
                 because=answer.because, reason=answer.key)

    def _leave_it_to_whoever_started_it(self, person: str, on_now: dict) -> None:
        """An order in progress that nobody on this floor started.

        A restart of this floor mid-job, or a person on the screen. It is left
        alone: nobody here watched it begin, so nobody here knows when it is
        finished, and completing it with findings this floor made up would put
        fiction in the records under a real person's name.
        """
        order = str(on_now.get("order") or "")
        if not order or order in self._said_somebody_else_started_it:
            return
        self._said_somebody_else_started_it.add(order)
        log.info("an order is in progress that this floor did not start, so it is left "
                 "to whoever did", order=order, person=person,
                 since=on_now.get("since"), minutes=on_now.get("minutes"))

    async def _set_off(self, person: str, on_now: dict, states: dict,
                       between_orders: bool, ends: datetime | None, now: float) -> None:
        """Send one person to one machine, if the job can be done now."""
        order = str(on_now.get("order") or "")
        if not order:
            return
        minutes = float(on_now.get("expected_minutes") or _ASSUMED_JOB_MINUTES)
        window = self._window_is_open(
            on_now.get("window"), minutes, between_orders, ends)
        if not window.ok:
            self._say_once_why_it_waits(order, window)
            return
        equipment = str(on_now.get("equipment") or "")
        if on_now.get("needs_stop"):
            at_it = self._can_get_at_it(equipment, states)
            if not at_it.ok:
                self._say_once_why_it_waits(order, at_it)
                return
        wanted = self.script.get("maintenance") or {}
        walk = float(wanted.get("walk_s", _WALK_S))
        self._jobs[person] = _Job(
            order=order, person=person, equipment=equipment,
            plan=on_now.get("plan"), needs_stop=bool(on_now.get("needs_stop")),
            # Give or take a tenth, from this run's own stream: six identical
            # jobs taking exactly their planned minutes is a spreadsheet, and
            # the downtime figures off it would all be the same number.
            minutes=minutes * (1.0 + self.rng.uniform(-0.1, 0.1)),
            at_the_machine=now + walk / self.speed)
        self._waiting.pop(order, None)
        log.info("a mechanic is on his way", person=person, order=order,
                 equipment=equipment, because=window.because, walk_line_s=walk,
                 job_line_minutes=round(self._jobs[person].minutes, 1))

    async def _get_on_with_it(self, job: _Job, states: dict, now: float) -> bool:
        """Move one job in somebody's hands along. True when it is finished."""
        if now < job.at_the_machine:
            return False
        if job.finished_at is None:
            await self._start_it(job, states, now)
            return False
        if job.needs_stop and job.stopped_at is not None and job.came_back_at is None:
            row = states.get(job.equipment) or {}
            if str(row.get("state")) != "down" or job.order not in str(row.get("reason") or ""):
                # The line took the machine back while the job was still
                # going: the tags are the authority on what a machine is
                # doing, and this floor is not going to argue with them. The
                # downtime the order gets is what was measured, not what was
                # planned.
                job.came_back_at = now
                if not job.said_the_line_took_it_back:
                    job.said_the_line_took_it_back = True
                    log.info("the line took the machine back before the job was finished, "
                             "so the stop against the order is the part that was measured",
                             order=job.order, equipment=job.equipment,
                             state=row.get("state"))
        if now < job.finished_at:
            return False
        return await self._finish_it(job, now)

    async def _start_it(self, job: _Job, states: dict, now: float) -> None:
        """He is at the machine. Start the order, and stop the machine if the
        job needs it stopped.

        `performed_by` is how the audit trail names the mechanic without
        inventing a login for him. The simulated floor signs in as itself, as
        it always has, and the row says whose work it was - which is what a
        plant with seven trades and one terminal in the workshop actually
        records.
        """
        try:
            response = await self.post(f"/maintenance/orders/{job.order}/start",
                                       json={"performed_by": job.person})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Reassigned, started, or cancelled while he walked over. Dropped
            # rather than retried: the next pass reads the roster again and
            # finds whatever he actually has.
            log.warning("the order could not be started", order=job.order,
                        person=job.person, error=str(exc)[:160])
            self._jobs.pop(job.person, None)
            return
        job.finished_at = now + job.minutes * 60.0 / self.speed
        if job.needs_stop:
            job.found_state = str((states.get(job.equipment) or {}).get("state") or "") or None
            if await self._stop_the_machine(job):
                job.stopped_at = now
        log.info("a mechanic started a job", person=job.person, order=job.order,
                 equipment=job.equipment, needs_stop=job.needs_stop,
                 machine_was=job.found_state,
                 stopped_for_it=job.stopped_at is not None,
                 job_line_minutes=round(job.minutes, 1))

    async def _stop_the_machine(self, job: _Job) -> bool:
        """Book the machine down for the job, named with the plant's own word.

        The order's code goes in the sentence, so the stop and the job are one
        event in the records rather than two things a reader has to join by
        eye. The word is the plant's - `stop_reason` in the script, a code
        from its approved downtime vocabulary - because which of its words a
        stop gets named with is a site decision, and this floor never chooses
        one of its own.

        A refused write is said out loud and the job goes on: the mechanic is
        at the machine either way, and an order completed with no stop against
        it is a smaller lie than one that claims a stop the records do not
        have.
        """
        body = {"state": "down", "reason": f"planned maintenance {job.order}"}
        code = (self.script.get("maintenance") or {}).get("stop_reason")
        if code:
            body["reason_code"] = str(code)
        try:
            response = await self.post(f"/equipment/{job.equipment}/state", json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("the machine could not be booked down for the job, so the job "
                        "goes on the records with no stop against it",
                        order=job.order, equipment=job.equipment,
                        reason_code=code, error=str(exc)[:160])
            return False
        return True

    async def _hand_it_back(self, job: _Job) -> None:
        """Give the machine back the way it was found, if it is still his.

        The line's own tags are the authority on what a machine is doing. One
        that the replay or the OPC agent has already moved on is not written
        over from here: the mechanic standing in front of it is not who
        decided that, and two writers arguing over one machine is how a
        timeline stops being readable.
        """
        if job.stopped_at is None or job.came_back_at is not None:
            return
        try:
            rows = await self.get("/equipment/states")
        except httpx.HTTPError as exc:
            log.warning("could not read the machine back before handing it over",
                        equipment=job.equipment, error=str(exc)[:160])
            return
        row = next((r for r in rows if r.get("equipment") == job.equipment), None) or {}
        if str(row.get("state")) != "down" or job.order not in str(row.get("reason") or ""):
            job.came_back_at = time.monotonic()
            return
        if not job.found_state or job.found_state == "down":
            # It was already down when he got there - a breakdown somebody
            # else is working on, or a machine the plant had stopped. There is
            # nothing to give back and nothing honest to say it was.
            return
        try:
            response = await self.post(f"/equipment/{job.equipment}/state",
                                       json={"state": job.found_state})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("the machine could not be handed back after the job",
                        equipment=job.equipment, state=job.found_state,
                        error=str(exc)[:160])
            return
        job.came_back_at = time.monotonic()

    def _what_he_found(self, job: _Job) -> str | None:
        """What the mechanic writes in the box, from the plan's own list.

        One list per plan in the script, because what a greaser finds on a
        palletiser arm and what an electrician finds in a condenser are
        different sentences and neither of them belongs in Python. A plan with
        no list is completed with nothing written against it, which is honest:
        a job somebody did and wrote nothing about.
        """
        lists = (self.script.get("maintenance") or {}).get("findings") or {}
        said = lists.get(job.plan or "") or []
        if not said:
            if job.plan and job.plan not in self._said_no_findings:
                self._said_no_findings.add(job.plan)
                log.info("this plan has no findings written down, so its jobs are "
                         "completed with none", plan=job.plan)
            return None
        return str(self.rng.choice(list(said)))

    async def _finish_it(self, job: _Job, now: float) -> bool:
        """The job is done: hand the machine back and close the order.

        `downtime_minutes` is measured rather than planned - from the instant
        the machine went down for this job to the instant it came back - and
        a job that needed no stop books nought, because the machine never
        stopped. A plant whose preventive orders all booked their planned
        minutes as downtime would have a pareto made of the plan rather than
        of the shift.
        """
        await self._hand_it_back(job)
        minutes = 0.0
        if job.stopped_at is not None:
            held = max((job.came_back_at or now) - job.stopped_at, 0.0)
            minutes = round(held * self.speed / 60.0, 1)
        findings = self._what_he_found(job)
        try:
            response = await self.post(
                f"/maintenance/orders/{job.order}/complete",
                json={"findings": findings, "downtime_minutes": minutes,
                      "performed_by": job.person})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("the order could not be completed", order=job.order,
                        person=job.person, error=str(exc)[:160])
            self._jobs.pop(job.person, None)
            return False
        self._jobs.pop(job.person, None)
        log.info("a mechanic finished a job", person=job.person, order=job.order,
                 equipment=job.equipment, downtime_minutes=minutes,
                 needs_stop=job.needs_stop, findings=findings,
                 took_line_minutes=round(
                     max(now - job.at_the_machine, 0.0) * self.speed / 60.0, 1))
        return True

    async def current_shift(self) -> str | None:
        """Which shift the plant says is running, cached for a minute.

        `None` means the plant is not in a shift, which a plant that does not
        work nights genuinely is at three in the morning - and is not an
        error. The answer is the plant's own calendar, on the plant's own
        clock.
        """
        now = time.monotonic()
        if now < self._shift_until:
            return self._shift
        try:
            answer = await self.get("/analysis/shifts", days=1)
        except httpx.HTTPError:
            # Keep whatever was known; a missed shift read widens nothing
            # and narrows nothing, it just leaves the last answer standing.
            self._shift_until = now + 60.0
            return self._shift
        key = answer.get("current")
        self._shift = key.split("/", 1)[-1] if key else None
        self._shift_until = now + 60.0
        return self._shift

    # ---------------------------------------------------------- inspections

    async def _measured_value(self, spec: dict, machines: list[dict]) -> tuple[float | None, str | None]:
        """What the line actually made, and where - if any machine reports this
        characteristic.

        The station comes back with the number because the MES records where a
        reading was taken. An operator who walks to the filler with a scale has
        stood at the filler, and a check that does not say so leaves everything
        downstream - the control chart's signal, and any trigger on it - with
        no machine to name.
        """
        want = _slug(spec["characteristic"])
        for machine in machines:
            analog = machine.get("analog") or {}
            if not analog.get("name") or _slug(analog["name"]) != want:
                continue
            # What the machine is reporting now, not an average of the last
            # twelve minutes. The average was what this read until
            # 2026-09-14, and it produced a quality history in which the same
            # number was recorded over and over: two checks eight seconds
            # apart fell in the same bucket and got the same answer. That is
            # not a gauge reading twice, it is one reading written down
            # twice, and on a control chart it collapses the moving range and
            # makes ordinary noise look like a point beyond three sigma.
            if analog.get("value") is not None:
                stale = self._too_old(analog.get("at"))
                if stale is not None:
                    # The tag has stopped arriving. The number on the screen
                    # is the last one that did, and writing it down as a
                    # measurement would be recording a reading nobody took -
                    # once a minute, for as long as the tag stayed quiet,
                    # each one looking exactly like a bottle somebody
                    # weighed. Said out loud, and the check falls back to a
                    # plausible value with no gauge and no station on it,
                    # which is what the record should show.
                    log.warning("a tag has gone quiet; recording a plausible value "
                                "with no gauge and no station",
                                equipment=machine["code"], tag=analog.get("name"),
                                last_seen_s_ago=round(stale), characteristic=spec["characteristic"])
                    return None, None
                return float(analog["value"]), machine["code"]
            try:
                trend = await self.get(f"/analysis/tag/{machine['code']}", hours=0.2)
            except httpx.HTTPError:
                return None, None
            points = [p for p in trend.get("points", []) if p.get("mean") is not None]
            if points:
                return float(points[-1]["mean"]), machine["code"]
        return None, None

    def _too_old(self, observed) -> float | None:
        """How many seconds stale this reading is, or `None` if it is fresh.

        The window is the floor script's (`measurement.stale_after_s`); a
        plant with no script has none and takes every reading as it finds it,
        which is what this did before. A reading with no time on it is taken
        as fresh: not knowing when something was measured is a reason to say
        so, not a reason to call it old.
        """
        window = self.bench.stale_after_s
        if not window:
            return None
        at = _as_moment(observed)
        if at is None:
            return None
        age = (_utcnow() - at).total_seconds()
        return age if age > window else None

    def _plausible_value(self, spec: dict) -> float:
        """No matching tag: sample around the spec, mostly inside it.

        Deliberately not centred perfectly - a process that never drifts
        produces a quality history with nothing in it worth looking at.
        """
        low, high = spec.get("min_value"), spec.get("max_value")
        if low is None or high is None:
            return round(self.rng.gauss(100, 5), 2)
        mid, half = (low + high) / 2, (high - low) / 2
        return round(self.rng.gauss(mid + half * 0.15, half * 0.45), 2)

    async def inspect(self, specs: list[dict], machines: list[dict],
                      orders: list[dict], every_spec: bool = False) -> None:
        """Record a check: one specification chosen at random, or - a plant
        whose quality plan says "every characteristic, every fifteen
        minutes" - every specification once, against the order of the
        line that makes that material."""
        if not specs:
            return
        # A characteristic inspected several pieces at a time is not inspected
        # here. One check against it would be refused by the plant - and
        # rightly: one reading is not a point on an X-bar chart. `inspect_a_sample`
        # is what records those.
        specs = [s for s in specs if (s.get("sample_size") or 1) <= 1]
        if not specs:
            return
        chosen = specs if every_spec else [self.rng.choice(specs)]
        active = [o for o in orders if o.get("status") in ACTIVE_STATUSES]
        shift = await self.current_shift() if self.script else None
        for spec in chosen:
            value, station = await self._measured_value(spec, machines)
            measured = value is not None
            if value is None:
                value, station = self._plausible_value(spec), None
            elif self._last_recorded.get(spec["characteristic"]) == value:
                # The same stored reading as last time. The floor's cadence is
                # faster than the rate the MES samples a process value at, so
                # asking twice inside one sample gets one measurement back
                # twice - and writing it down twice is not two measurements.
                # It matters now that something judges the series: an
                # individuals chart estimates variation from the difference
                # between consecutive readings, and a run of identical ones
                # drags that estimate toward zero until ordinary noise reads
                # as a point beyond three sigma.
                continue
            same = [o for o in active if o.get("material") == spec["material"]]
            order = self.rng.choice(same or active)["code"] if (same or active) else None
            # The instrument, and what it says the value is - the gauge's own
            # bias, the spread of two readings of one thing, and the
            # resolution it can actually write down.
            #
            # Only for a value that came off the machine. A plausible number
            # was not measured by anything, and putting a gauge's name on it
            # would be the one lie that makes the whole record unusable: an
            # engineer reading the chart afterwards could no longer tell a
            # measurement from a stand-in.
            gauge = None
            # The number the machine reported, kept as it stood: the guard
            # above asks whether this floor has already written *this stored
            # reading* down, and a gauge's own error would make two readings
            # of one sample look like two samples.
            as_the_tag_had_it = value
            if measured:
                gauge, value = self.bench.measure(
                    spec["characteristic"], value, rng=self.rng,
                    today=identity.today(self.settings), shift_code=shift)
            body = {"material": spec["material"], "characteristic": spec["characteristic"],
                    "value": round(value, 3)}
            if order:
                body["order"] = order
            if station:
                body["equipment"] = station
            if gauge:
                body["gauge"] = gauge
            try:
                r = await self.post("/quality/checks", json=body)
                r.raise_for_status()
                out = r.json()
                self._last_recorded[spec["characteristic"]] = as_the_tag_had_it
                log.info("inspected", characteristic=spec["characteristic"],
                         value=round(value, 3), result=out.get("result"),
                         order=order, equipment=station, gauge=gauge,
                         measured=measured, shift=shift)
                # A control-chart rule fired on the write. Logged where a
                # person watching the run will see it, with the hold it raised.
                for signal in out.get("spc") or []:
                    log.warning("spc signal", rule=signal.get("rule"), what=signal.get("what"),
                                characteristic=spec["characteristic"],
                                nonconformance=signal.get("nonconformance"),
                                equipment=signal.get("equipment"))
            except httpx.HTTPStatusError as exc:
                log.warning("inspection refused", status=exc.response.status_code,
                            detail=exc.response.text[:160])

    # ------------------------------------------------------ sampled checks

    async def _pieces(self, plan, n: int) -> list[float] | None:
        """`n` different stored readings of the plan's tag, newest last.

        Different, and newer than the last sample's: five bottles weighed at
        five instants. The tag's history is read through `/analysis/tag`,
        whose buckets are narrow enough here that each stored sample lands in
        one of its own - so what comes back is the readings themselves and not
        an average of them, which on a sample of five would flatten the very
        spread the range chart is drawn from.

        `None` is *not yet*, and is said once per characteristic: a plant
        whose filler has published four readings since the last sample has not
        got five bottles, and taking four, or taking one twice, would be a
        sample this floor invented.
        """
        # A window wide enough to hold a sample's worth of history at any
        # replay speed, and buckets fine enough that two readings five
        # seconds apart cannot share one.
        try:
            trend = await self.get(f"/analysis/tag/{plan.equipment}",
                                   tag=plan.tag, hours=0.25, buckets=2000)
        except httpx.HTTPError as exc:
            log.warning("could not read the tag a sample is taken off",
                        equipment=plan.equipment, tag=plan.tag, error=str(exc)[:160])
            return None
        through = self._sampled_through.get(plan.characteristic)
        fresh: list[tuple[datetime, float]] = []
        for point in trend.get("points") or []:
            if point.get("mean") is None:
                continue
            at = _as_moment(point.get("t"))
            if at is None or (through is not None and at <= through):
                continue
            fresh.append((at, float(point["mean"])))
        if len(fresh) < n:
            if plan.characteristic not in self._said_no_pieces:
                self._said_no_pieces.add(plan.characteristic)
                log.info("not enough history for a sample yet; taking nothing",
                         characteristic=plan.characteristic, equipment=plan.equipment,
                         tag=plan.tag, wanted=n, found=len(fresh),
                         note="a sample is n different pieces; measuring fewer, or "
                              "measuring one of them twice, would be a sample this "
                              "floor invented")
            return None
        self._said_no_pieces.discard(plan.characteristic)
        taken = fresh[-n:]
        self._sampled_through[plan.characteristic] = taken[-1][0]
        return [value for _at, value in taken]

    def _after_a_changeover(self, plan) -> bool:
        """Whether the pack's post-changeover window is still open.

        In line minutes, like every other duration this floor judges: on a
        replay running an hour of line in a minute, twenty-five line minutes
        is twenty-five seconds of this floor's time.
        """
        if not plan.after_changeover_minutes or self._changeover_ended is None:
            return False
        since = (_utcnow() - self._changeover_ended).total_seconds() * self.speed
        return since <= plan.after_changeover_minutes * 60.0

    async def inspect_a_sample(self, plan, specs: list[dict], orders: list[dict]) -> bool:
        """Measure `sample_size` pieces and post them as one sample.

        The plant says how many: `sample_size` on the specification, which is
        the sampling plan and is master data. This floor does not choose it
        and does not post a sample of a different size - a specification whose
        plan says one piece at a time has no sample to record here.
        """
        spec = next((s for s in specs if s.get("characteristic") == plan.characteristic), None)
        if spec is None:
            return False
        size = int(spec.get("sample_size") or 1)
        if size < 2:
            return False
        pieces = await self._pieces(plan, size)
        if pieces is None:
            return False

        # The planted cause, if the pack plants one and this floor has watched
        # a changeover end inside the window. One offset on the whole sample,
        # because what the nozzle setting moved is the process and not one
        # bottle - which is why it shows on the means and not on the ranges.
        offset = plan.after_changeover_offset if self._after_a_changeover(plan) else 0.0
        shift = await self.current_shift() if self.script else None
        today = identity.today(self.settings)
        values: list[float] = []
        gauge: str | None = None
        for piece in pieces:
            true_value = plan.convert(piece) + offset
            if plan.piece_to_piece:
                # The glass itself: two bottles holding the same weight do not
                # stand at the same height. This is the piece-to-piece
                # variation the sampling exists to measure, and it is added
                # before the gauge rather than after, because it is the
                # bottle and not the instrument.
                true_value += self.rng.gauss(0.0, plan.piece_to_piece)
            code, reading = self.bench.measure(plan.characteristic, true_value,
                                               rng=self.rng, today=today, shift_code=shift)
            gauge = code or gauge
            values.append(round(reading, 3))

        active = [o for o in orders if o.get("status") in ACTIVE_STATUSES]
        same = [o for o in active if o.get("material") == spec.get("material")]
        order = self.rng.choice(same or active)["code"] if (same or active) else None
        body: dict = {"material": spec["material"], "characteristic": plan.characteristic,
                      "values": values}
        if order:
            body["order"] = order
        if gauge:
            # Where the sample was measured, which is the bench and not the
            # filler: the pieces came off the filler and were carried to the
            # gauge's own station, and the record says where the measuring
            # happened because that is what a reader can go and look at.
            body["gauge"] = gauge
            station = self.bench.station(gauge)
            if station:
                body["equipment"] = station
        try:
            response = await self.post("/quality/samples", json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.warning("sample refused", characteristic=plan.characteristic,
                        status=exc.response.status_code, detail=exc.response.text[:160])
            return False
        out = response.json()
        log.info("sampled", characteristic=plan.characteristic, n=len(values),
                 values=values, mean=out.get("mean"), range=out.get("range"),
                 result=out.get("results"), order=order, gauge=gauge,
                 equipment=body.get("equipment"), shift=shift,
                 after_a_changeover=bool(offset))
        for signal in out.get("spc") or []:
            log.warning("spc signal", rule=signal.get("rule"), what=signal.get("what"),
                        characteristic=plan.characteristic,
                        nonconformance=signal.get("nonconformance"),
                        equipment=signal.get("equipment"))
        return True

    # ---------------------------------------------------------------- stops

    async def watch_the_stops(self) -> list[str]:
        """Remember what is stopped, and name each stop once it has come back.

        A stop is named *afterwards*, because the moment a machine goes down
        is the moment nobody knows why yet - and on a plant whose states
        arrive from an OPC agent there is no moment at all when a person is
        asked. That is why every stop on this plant was unlabelled: 100% of
        seven days of downtime, measured 2026-10-04.

        What this floor can honestly say is what a person with a clipboard
        can: which machine stopped, when, and roughly for how long. A short
        stop somebody cleared where they stood is a micro stop; a long one is
        a breakdown; a stop still going when it is looked at is nobody's
        answer yet. Which of the plant's approved words each of those is, is
        the script's - this never chooses a word of its own.

        **It does not name an idle machine, on purpose.** This line's tag map
        maps *starved* and *blocked* both onto `idle`, so by the time the MES
        holds the interval the difference is gone - and the vocabulary's own
        `starved` and `blocked` cannot be chosen between from what was
        recorded. Guessing one would be putting a cause in the pareto that
        nothing observed. It is said once, in the log, as a finding.
        """
        stops = self.script.get("stops") or {}
        if not stops:
            return []
        try:
            states = await self.get("/equipment/states")
        except httpx.HTTPError as exc:
            log.warning("could not read the machine states", error=str(exc)[:160])
            return []

        watchable = {"down": "longer_than_a_micro_stop", "setup": "changeover"}
        stopped_now: dict[str, dict] = {}
        idle_unlabelled = 0
        for row in states:
            if row.get("reason") or row.get("reason_code"):
                continue
            state = str(row.get("state") or "")
            if state == "idle":
                idle_unlabelled += 1
                continue
            if state in watchable and row.get("since"):
                stopped_now[row["equipment"]] = {"state": state, "since": row["since"]}

        if idle_unlabelled and not self._said_idle_cannot_be_told_apart:
            self._said_idle_cannot_be_told_apart = True
            log.info(
                "an idle machine is left unlabelled, and that is the honest answer",
                machines=idle_unlabelled,
                note="this plant's tag map maps starved and blocked both onto idle, so "
                     "the difference is gone before the MES holds the interval; the "
                     "vocabulary has a word for each and nothing recorded says which")

        named: list[str] = []
        for code, seen in list(self._stopped.items()):
            now = stopped_now.get(code)
            if now and now["since"] == seen["since"]:
                continue              # the same stop, still going
            if await self._name_the_stop(code, seen):
                named.append(code)
            if seen["state"] == "setup":
                # The line has just changed over. The pack's planted cause
                # hangs off this instant, and it is this floor's own record of
                # it: the MES holds the labelled interval, and asking it back
                # every fifteen minutes to learn something this floor watched
                # happen would be two requests for a fact it already has.
                self._changeover_ended = _utcnow()
            self._stopped.pop(code, None)
        self._stopped.update(stopped_now)
        return named

    async def _name_the_stop(self, code: str, seen: dict) -> bool:
        """Put one of the plant's approved words on a stop that has ended.

        The window is the instant the stop began and one second of it, so
        exactly the interval that started then is named - never the idle or
        running stretch that followed it, which is what a window reaching
        up to now would have swept in as well.
        """
        stops = self.script.get("stops") or {}
        began = _as_moment(seen["since"])
        if began is None:
            return False
        # How long it was, as a person who looked twice would know it: from
        # when it began to when it was noticed back. That is up to one look
        # longer than the stop itself, which is near enough to tell a stop
        # somebody cleared where they stood from one that took a fitter, and
        # is not written down anywhere as the duration.
        #
        # In *line* seconds. On a replay running an hour of line in a minute,
        # a two-minute breakdown is over in four seconds of this floor's
        # time, and judging it by those four would file every breakdown on a
        # scored run as a micro stop.
        watched = max((_utcnow() - began).total_seconds(), 0.0)
        seconds = watched * self.speed
        if seen["state"] == "setup":
            reason_code = stops.get("changeover")
        elif seconds < float(stops.get("micro_stop_under_s") or 0.0):
            reason_code = stops.get("micro_stop")
        else:
            reason_code = stops.get("longer_than_a_micro_stop")
        if not reason_code:
            return False
        try:
            response = await self.post(
                f"/equipment/{code}/stops/label",
                json={"reason_code": reason_code, "start": began.isoformat(),
                      "end": (began + timedelta(seconds=1)).isoformat()})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.warning("could not name a stop", equipment=code, reason=reason_code,
                        status=exc.response.status_code, detail=exc.response.text[:160])
            return False
        out = response.json()
        if not out.get("labelled"):
            # The interval was already named, or nothing unlabelled was there
            # to name. Both are fine and neither is a label this floor gave.
            return False
        log.info("named a stop", equipment=code, reason=reason_code,
                 state=seen["state"], about_line_seconds=round(seconds),
                 watched_seconds=round(watched, 1), labelled=out["labelled"])
        return True

    # ------------------------------------------------------------- material

    async def issue_material(self, orders: list[dict], lots: list[dict]) -> None:
        """Stage a component at the station that consumes it.

        The bill of materials says where each component goes in - the preform
        at the loader, the cap and water at the filler, the carton at the
        palletiser. Issuing a random lot against the order was what made this
        look like a job shop: nothing entered anywhere in particular, so
        genealogy could never say where anything went.
        """
        active = [o for o in orders if o.get("status") in ACTIVE_STATUSES]
        if not active:
            return
        order = self.rng.choice(active)

        try:
            bom = await self.get(f"/execution/bom/{order['material']}")
        except httpx.HTTPError:
            return
        lines = bom.get("components") or []
        if not lines:
            return

        by_material: dict[str, list[dict]] = {}
        for lot in lots:
            if lot.get("status") == "available" and lot.get("quantity", 0) > 100:
                by_material.setdefault(lot["material"], []).append(lot)

        line = self.rng.choice(lines)
        candidates = by_material.get(line["component"])
        if not candidates:
            # A component with no stock is a shortage, not a bug. The staging
            # view is where that shows up; issuing something else instead
            # would hide it.
            return
        lot = self.rng.choice(candidates)

        # Roughly what the station would draw for a batch of production.
        batch = self.rng.randint(40, 240)
        quantity = round(max(1.0, line["per_unit"] * batch), 1)
        body = {"order": order["code"], "lot": lot["code"], "quantity": quantity}
        if line.get("seq") is not None:
            body["seq"] = line["seq"]

        try:
            r = await self.post("/execution/consume", json=body)
            r.raise_for_status()
            log.info("staged material", order=order["code"], lot=lot["code"],
                     component=line["component"], seq=line.get("seq"),
                     quantity=quantity)
        except httpx.HTTPStatusError as exc:
            log.warning("issue refused", status=exc.response.status_code,
                        detail=exc.response.text[:160])

    # ----------------------------------------------------------- order book

    async def finish_orders(self, orders: list[dict]) -> int:
        """Finish every order whose line has made the number. Returns how many.

        Decision 0029 stands and is not touched: the MES does not complete an
        order when its quantity is reached, because a quantity is what the
        plant was asked for and a line is routinely still running when it has
        made it. Completing is an act, and this is the act - performed by the
        simulated shift supervisor, over the same API a person uses, so the
        audit trail names `FLOOR-SUP` and nobody reading it later can mistake
        this for the MES closing an order by itself.

        An order whose steps are not all running is left open and said out
        loud. Starting a step no machine ever counted against, only so the
        order could be completed, would be putting a run in the record that
        never happened.
        """
        finished = 0
        for order in orders:
            quantity = float(order.get("quantity") or 0)
            if quantity <= 0 or order.get("status") not in ACTIVE_STATUSES:
                continue
            if float(order.get("good_qty") or 0) < quantity:
                continue
            code = order["code"]
            running = [op for op in order.get("operations") or []
                       if op.get("status") == "running"]
            if not running:
                continue
            refused = False
            for op in running:
                try:
                    response = await self.post(
                        f"/workorders/{code}/operations/{op['seq']}/complete")
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    refused = True
                    log.warning("could not complete an operation", order=code,
                                seq=op.get("seq"), status=exc.response.status_code,
                                detail=exc.response.text[:160])
            if refused:
                continue
            try:
                after = await self.get(f"/workorders/{code}")
            except httpx.HTTPError as exc:
                log.warning("could not read the order back", order=code, error=str(exc)[:160])
                continue
            if after.get("status") != "completed":
                waiting = [op["seq"] for op in after.get("operations") or []
                           if op.get("status") != "done"]
                log.warning("order not finished; some steps never ran", order=code,
                            waiting=waiting, status=after.get("status"))
                continue
            finished += 1
            log.info("finished order", order=code, quantity=quantity,
                     good=after.get("good_qty"), scrap=after.get("scrap_qty"),
                     over=after.get("over_qty"), by="the simulated shift supervisor")
        return finished

    async def release_next(self) -> dict | None:
        """Release the next order in the book, or say the book is empty - once.

        Never invents one. Until 2026-09-18 this made up an order code and a
        quantity when the floor ran out of work, and the line went on looking
        busy over a number nobody had asked for. A plant with nothing planned
        left has nothing planned left: what the line makes after that is
        unassigned production, listed with its total (decision 0019), and that
        is the answer a person needs to see rather than a fiction that hides it.
        """
        try:
            page = await self.get("/workorders", status=["planned"], limit=500)
        except httpx.HTTPError as exc:
            log.warning("could not read the order book", error=str(exc)[:160])
            return None
        book = page["items"]
        if not book:
            if not self._said_the_book_is_empty:
                self._said_the_book_is_empty = True
                log.warning(
                    "the order book is empty", planned=0,
                    note="nothing is planned on this plant; what the line counts from "
                         "here is unassigned production, not an order")
            return None
        nxt = sorted(book, key=_book_position)[0]
        try:
            response = await self.post(f"/workorders/{nxt['code']}/release")
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.warning("release refused", order=nxt["code"], status=exc.response.status_code,
                        detail=exc.response.text[:160])
            return None
        self._said_the_book_is_empty = False
        log.info("released the next order in the book", order=nxt["code"],
                 material=nxt.get("material"), quantity=nxt.get("quantity"),
                 due=nxt.get("due_date"), remaining_in_book=page["total"] - 1,
                 by="the simulated shift supervisor")
        return nxt

    async def plan_the_book(self, keep: int) -> list[str]:
        """Keep `keep` orders planned ahead of the line. Returns what it planned.

        **This is not the floor inventing production.** The floor's own rule
        is untouched one method up: `release_next` still never makes an order
        up when it runs dry, and decision 0019 still stands - nothing is
        booked that no machine counted. What this is, is the person a lab
        plant did not have. In a real plant orders come from a planner or from
        the ERP; the lab plants have no ERP, so after about forty hours both of
        them ran out of orders and every measurement from then on carried no
        order at all. This plants the next one, as `FLOOR-PLAN`, over the same
        `POST /workorders` a planner's browser uses, so the audit trail names
        who planned it.

        It plans and it does not release: an order goes into the book as
        *planned* and the simulated shift supervisor puts it on the line
        (`release_next`), which keeps the sequence planner -> supervisor ->
        floor that a real plant has.

        What it plans is the plant's own book read back in rotation - see
        `Book`. `keep` of zero plans nothing, which is every pack that has not
        asked for a planner.
        """
        if keep <= 0:
            return []
        try:
            waiting = await self.get("/workorders", status=["planned"], limit=1)
            short = keep - int(waiting.get("total") or 0)
            if short <= 0:
                return []
            page = await self.get("/workorders", limit=_BOOK_WINDOW)
        except httpx.HTTPError as exc:
            log.warning("could not read the order book to plan from", error=str(exc)[:160])
            return []
        book = Book.read(page["items"])
        if book is None:
            if not self._said_there_is_no_pattern:
                self._said_there_is_no_pattern = True
                log.warning(
                    "nothing in this plant's book to continue; planning nothing",
                    orders_read=len(page["items"]), of=page.get("total"),
                    note="a planner copies what this plant already makes, and this "
                         "plant has never had an order with a material and a "
                         "quantity in its book; what a plant makes is not something "
                         "a simulator knows")
            return []
        self._said_there_is_no_pattern = False
        now = _utcnow()
        planned: list[str] = []
        for step in range(short):
            number = book.last + 1 + step
            code = book.code_for(number)
            material, quantity, priority = book.row_for(number)
            due = book.due_for(number, now=now)
            body: dict = {"code": code, "material": material,
                          "quantity": quantity, "priority": priority}
            if due is not None:
                body["due_date"] = due.isoformat()
            try:
                response = await self.post("/workorders", json=body)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # A code already taken, a material with no routing, a refused
                # capability: all of them are things to say out loud rather
                # than retry in a loop.
                log.warning("could not plan an order", order=code,
                            status=exc.response.status_code,
                            detail=exc.response.text[:160])
                break
            except httpx.HTTPError as exc:
                log.warning("could not plan an order", order=code, error=str(exc)[:160])
                break
            planned.append(code)
            log.info("planned the next order", order=code, material=material,
                     quantity=quantity, priority=priority,
                     due=due.isoformat() if due else None,
                     copied_from_a_book_of=len(book.rows),
                     keeping_planned=keep, by="the simulated planner")
        return planned

    async def work_the_book(self, orders: list[dict], *, finish: bool = True) -> None:
        """One pass of what a supervisor does with the schedule.

        Finish what the line has made, then put the next order on it. A plant
        that is idle with orders still planned gets one too - that is the case
        a pack which releases nothing starts in.

        `finish` is off for a scripted over-run: an experiment whose whole
        subject is a line running past its order needs nobody stopping it, and
        it says so in its plan rather than being an accident of cadence.
        """
        finished = await self.finish_orders(orders) if finish else 0
        active = [o for o in orders if o.get("status") in ACTIVE_STATUSES]
        if finished or not active:
            await self.release_next()

    # ---------------------------------------------------------- supervision

    async def review_nonconformances(self, keep_open: int = 3) -> int:
        """Work through the open non-conformances the way a shift does.

        Three steps, each recorded against the person who took it: somebody
        picks it up, somebody decides what happens to the material, and only
        then is it closed. Decision 0024 refuses the shortcut, and this loop
        was taking it - it posted `/close` on an undispositioned record, the
        product refused, the refusal was logged and nothing else happened.
        What that produced on the bottling lab plant by 2026-10-04 was
        **887 open non-conformances, none of them ever reviewed**, so the
        quality screen and every "what is the biggest problem here" answer
        said the same thing for a week.

        Which disposition each severity gets is the script's, with its
        reason, because *use as is* on a batch that failed its specification
        is a concession somebody has to be able to defend a year later - and
        a simulated plant that invented the sentence would be writing the one
        field nobody can check. A plant with no script closes nothing, which
        is what this did before.

        The newest few are left alone on purpose: a plant where every finding
        is closed by the end of the shift is as unrealistic as one where
        nothing ever is. Returns how many were taken all the way to closed.
        """
        rules = self.script.get("nonconformances") or {}
        if not rules:
            return 0
        keep_open = int(rules.get("leave_open", keep_open))
        per_pass = int(rules.get("per_pass", 2))
        # Every plant retired on 2026-09-06 had every non-conformance it ever
        # raised still open - thousands - and this loop had said nothing,
        # because both failures below were swallowed. A loop that cannot
        # report its own failure is one nobody finds out is broken.
        try:
            page = await self.get("/quality/nonconformances", status=["open", "under_review"],
                                  limit=200)
        except httpx.HTTPError as exc:
            log.warning("could not read open non-conformances", error=str(exc)[:160])
            return 0
        rows = page["items"] if isinstance(page, dict) else page
        total = page.get("total") if isinstance(page, dict) else len(rows)
        # Newest first out of the API, so the newest `keep_open` are the ones
        # left alone and the oldest are the ones worked.
        waiting = [n for n in rows if n.get("status") in ("open", "under_review")]
        working = waiting[keep_open:][-per_pass:] if keep_open else waiting[-per_pass:]
        closed = 0
        for nc in working:
            if await self._work_one_nonconformance(nc, rules):
                closed += 1
        if closed:
            log.info("worked through non-conformances", closed=closed,
                     left_open=max(len(waiting) - closed, 0), open_in_total=total)
        return closed

    async def _work_one_nonconformance(self, nc: dict, rules: dict) -> bool:
        """Review, disposition, close - one record, in that order.

        Any step the product refuses stops this record and says so. A floor
        that pressed on would be writing a history in which a decision was
        taken twice or out of order.
        """
        code = nc["code"]
        by_severity = rules.get("by_severity") or {}
        verdict = by_severity.get(str(nc.get("severity"))) or rules.get("otherwise") or {}
        disposition, reason = verdict.get("disposition"), verdict.get("reason")
        if not disposition or not reason:
            # No rule for this severity and no fallback: left open, which is
            # the honest state for a finding nobody has decided about.
            return False

        if nc.get("status") == "open" and not await self._post(
                f"/quality/nonconformances/{code}/review",
                what="review a non-conformance", code=code):
            return False
        if not await self._post(f"/quality/nonconformances/{code}/disposition",
                                json={"disposition": disposition, "reason": reason},
                                what="disposition a non-conformance", code=code):
            return False
        log.info("dispositioned a non-conformance", code=code, disposition=disposition,
                 severity=nc.get("severity"))
        if not rules.get("close_after_disposition", True):
            return False
        if not await self._post(f"/quality/nonconformances/{code}/close",
                                what="close a non-conformance", code=code):
            return False
        log.info("closed a non-conformance", code=code, disposition=disposition)
        return True

    async def _post(self, path: str, *, json: dict | None = None, what: str,
                    code: str) -> bool:
        """One write, with its refusal reported rather than swallowed."""
        try:
            response = await self.post(path, json=json)
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            log.warning(f"could not {what}", code=code, status=exc.response.status_code,
                        detail=exc.response.text[:160])
        except httpx.HTTPError as exc:
            log.warning(f"could not {what}", code=code, error=str(exc)[:160])
        return False


async def run(settings: Settings, *, inspect_every: float = 8.0,
              issue_every: float = 25.0, review_every: float = 90.0,
              supervise_every: float = 20.0, watch_every: float = 15.0,
              plan_every: float = 60.0, crew_every: float = 20.0,
              speed: float = 1.0,
              seed: int = 0, user: str = "FLOOR-SIM", password: str = "operator",
              supervisor: str = "FLOOR-SUP", supervisor_password: str = "supervisor",
              planner: str = "FLOOR-PLAN", planner_password: str = "planner",
              crew_user: str = "FLOOR-CREW", crew_password: str = "operator",
              inspect_all: bool = False, finish_orders: bool = True,
              plan_orders: bool = True) -> None:
    """Generate shop-floor activity until stopped.

    Four identities, because the plant has four: the floor (an operator)
    records checks and issues material; the shift supervisor closes
    non-conformances, finishes an order the line has made the number for and
    releases the next one in the book; the production planner puts the next
    order *into* the book and may do nothing else. An operator may not close a
    non-conformance - segregation of duties the product is right to enforce,
    and which the simulator must respect rather than be granted around - and
    the order book is the supervisor's for the same reason: the audit trail
    has to name whoever finished an order, and it has to be true.

    The planner is the third because a lab plant has no ERP. In a real plant
    the orders arrive from a planner or from an ERP, never from the floor; the
    two lab plants had neither, so they ran out of orders about forty hours
    after they were built and measured into no order from then on. `plan_every`
    is how often the planner looks at the book; how deep it keeps it is the
    pack's `planning.keep_planned`, and **zero - no planner at all - is the
    default**, so a pack that says nothing behaves exactly as it did before
    this existed.

    The maintenance crew is the fourth, and it is one account rather than
    seven: a workshop has a terminal, and a mechanic's name reaches the audit
    trail as `performed_by` on the work he did rather than as a login nobody
    issued. `crew_every` is how often the crew looks at what it has been
    given; whether anybody looks at all is the pack's `maintenance.crew`, and
    **off is the default**, so a pack that says nothing has its work raised
    and handed out and waiting, exactly as before.

    `finish_orders` is False for a scripted over-run, where nobody stopping
    the line is the whole point. `plan_orders` is False for the same reason
    and in the same place: a plan whose subject is one order and what the line
    does past it needs the book it runs out of to stay the one the pack wrote,
    whatever depth that pack asks to keep. The pack is not edited for it - the
    plan says so in writing, as it already does for the supervisor.

    `watch_every` is how often the floor looks at the machines to see what
    has stopped and what has come back - the look that lets it name a stop
    afterwards. It is the floor's own cadence and not a sampling rate: the
    MES's record of the interval comes from the OPC agent either way, and a
    floor that looked less often names fewer stops rather than recording
    shorter ones.
    """
    base = f"http://{settings.api_host}:{settings.api_port}"
    rng = random.Random(seed)
    # What this floor does that no PLC reports, as data - or nothing, which
    # is every real plant and is the behaviour this had before scripts.
    script = measurement.load(settings.floor_script_file)

    keep = keep_planned(script)
    if keep > 0 and not plan_orders:
        # A plan has turned the planner off although this pack asks for a
        # depth - `labs/experiments/over-run.toml`, whose whole subject is a
        # book running out. Say which it was, so a reader of the log is not
        # left wondering why the pack's number did nothing.
        log.info("the planner is off for this run; the pack's book will not be "
                 "topped up", keep_planned=keep, plan_orders=False)
        keep = 0

    does_the_work = bool((script.get("maintenance") or {}).get("crew"))

    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client, \
            httpx.AsyncClient(base_url=base, timeout=20.0) as sup_client, \
            httpx.AsyncClient(base_url=base, timeout=20.0) as plan_client, \
            httpx.AsyncClient(base_url=base, timeout=20.0) as crew_client:
        floor = Floor(settings, client, rng, script, speed)
        shift = Floor(settings, sup_client, rng, script, speed)
        plans_the_book = Floor(settings, plan_client, rng, script, speed)
        crew = Floor(settings, crew_client, rng, script, speed)

        # The API comes up alongside us; keep trying rather than dying first.
        for _attempt in range(60):
            try:
                await floor.sign_in(user, password)
                break
            except (httpx.HTTPError, KeyError):
                await asyncio.sleep(2.0)
        else:
            log.error("could not sign in", base=base, user=user)
            return
        try:
            await shift.sign_in(supervisor, supervisor_password)
        except (httpx.HTTPError, KeyError) as exc:
            # A plant initialised before the supervisor account existed: say
            # so, and leave the non-conformances open rather than pretend.
            log.warning("no supervisor account; non-conformances will stay open and "
                        "no order will be finished or released",
                        user=supervisor, error=str(exc)[:120])
            shift = None

        if keep > 0:
            try:
                await plans_the_book.sign_in(planner, planner_password)
            except (httpx.HTTPError, KeyError) as exc:
                # A plant initialised before the planner account existed - every
                # lab plant built before 2026-10-07. Say so once, and run exactly
                # as this did before there was a planner: the book empties and
                # the floor says so. `fsmes plant <name> migrate` adds the
                # account.
                log.warning("no planner account; the order book will not be topped up "
                            "and will empty when the pack's orders are done",
                            user=planner, keep_planned=keep, error=str(exc)[:120])
                plans_the_book = None
        else:
            plans_the_book = None

        if does_the_work:
            try:
                await crew.sign_in(crew_user, crew_password)
            except (httpx.HTTPError, KeyError) as exc:
                # A plant initialised before the crew account existed - every
                # lab plant built before 2026-10-09. Say so once, and run
                # exactly as this did before there was a crew: the work is
                # raised, handed out, and waits. `fsmes plant <name> migrate`
                # adds the account.
                log.warning("no maintenance crew account; the work that is raised will "
                            "be handed out and nobody will start it",
                            user=crew_user, error=str(exc)[:120])
                crew = None
        else:
            crew = None

        gauges_known = await floor.read_the_register()
        if shift is not None and script:
            await shift.read_the_register()

        log.info("shop floor online", endpoint=base, inspect_every=inspect_every,
                 issue_every=issue_every, supervise_every=supervise_every,
                 finishes_orders=finish_orders and shift is not None,
                 # How many orders the planner keeps planned ahead of the line,
                 # and nothing when no pack asked for one: a plant that goes
                 # quiet after its book runs out should say on startup that
                 # nobody was ever going to plan another.
                 keeps_planned=keep if plans_the_book is not None else None,
                 # Whether anybody walks over to the machines this run. A
                 # Maintenance tab that fills up with assigned orders and
                 # never moves should say on startup that nobody was ever
                 # going to touch one.
                 crew_does_the_work=crew is not None,
                 floor_script=str(settings.floor_script_file) if script else None,
                 gauges_on_the_register=gauges_known,
                 # Which characteristics this floor inspects several pieces at
                 # a time, and how often. Said on startup because a plant whose
                 # pack names a plan the specifications do not carry would
                 # otherwise look like a floor that had quietly stopped
                 # sampling.
                 sampling=[f"{name} every {plan.every_line_s:g} line s"
                           for name, plan in floor.plans.items()])

        async def every(seconds: float, work) -> None:
            while True:
                await asyncio.sleep(seconds)
                try:
                    summary = await floor.get("/dashboard/summary")
                    # The open orders, all of them: a plant with sixty lines
                    # has sixty released orders, and the first page of fifty
                    # would leave a check attributed to whichever line came
                    # first alphabetically.
                    orders = (await floor.get("/workorders", status=["released", "running"], limit=500))["items"]
                    await work(summary, orders)
                except httpx.HTTPError as exc:
                    log.warning("shop floor step failed", error=str(exc)[:160])

        async def do_inspect(summary, orders):
            specs = await floor.every("/quality/specs")
            await floor.inspect(specs, summary.get("machines", []), orders, every_spec=inspect_all)

        async def sample_the_bench() -> None:
            """Its own loop, on each plan's own cadence.

            A sample is not an inspection with more numbers in it: the pieces
            come off a tag's history rather than off the machine's present
            value, the five readings are one record, and the cadence is the
            pack's own - fifteen line minutes on the bottling line, because
            that is how often somebody walks to the filler with a tray.
            """
            if not floor.plans:
                return
            due = {name: 0.0 for name in floor.plans}
            while True:
                wait = min(plan.every_line_s / floor.speed for plan in floor.plans.values())
                await asyncio.sleep(max(wait, 1.0))
                now = time.monotonic()
                try:
                    specs = await floor.every("/quality/specs")
                    orders = (await floor.get("/workorders", status=["released", "running"],
                                              limit=500))["items"]
                except httpx.HTTPError as exc:
                    log.warning("the sampling step failed", error=str(exc)[:160])
                    continue
                for name, plan in floor.plans.items():
                    if now < due[name]:
                        continue
                    due[name] = now + plan.every_line_s / floor.speed
                    try:
                        await floor.inspect_a_sample(plan, specs, orders)
                    except httpx.HTTPError as exc:
                        log.warning("the sampling step failed", characteristic=name,
                                    error=str(exc)[:160])

        async def do_issue(summary, orders):
            lots = (await floor.get("/execution/lots"))["items"]
            await floor.issue_material(orders, lots)

        async def do_review(summary, orders):
            # The register, every pass: a gauge somebody took out of service
            # on the screen is out of service for this floor too, within the
            # minute and without a restart.
            await floor.read_the_register()
            if shift is not None:
                await shift.review_nonconformances()

        async def watch_the_stops() -> None:
            # Its own loop, because it needs nothing but the machine states -
            # the dashboard and the order book that `every` reads for the
            # rest would be two requests a look for nothing.
            while True:
                await asyncio.sleep(watch_every)
                try:
                    await floor.watch_the_stops()
                except httpx.HTTPError as exc:
                    log.warning("the stop watch failed", error=str(exc)[:160])

        async def plan_ahead() -> None:
            """Its own loop, like the stop watch: it needs only the book.

            The planner plans and does not release. What it writes goes into
            the book as *planned*, and the supervisor's own loop puts it on
            the line a step later, so the sequence on a lab plant is the one a
            real plant has - planner, then supervisor, then floor - and each
            of the three audit rows names the right person.
            """
            if plans_the_book is None:
                return
            while True:
                try:
                    await plans_the_book.plan_the_book(keep)
                except httpx.HTTPError as exc:
                    log.warning("the planning step failed", error=str(exc)[:160])
                await asyncio.sleep(plan_every)

        async def do_supervise(summary, orders):
            # The supervisor's client, not the operator's: what this does -
            # finishing an order, releasing the next, raising the maintenance
            # work that has come due, calibrating a gauge that has fallen due
            # - is a supervisor's act, and the audit row has to say so.
            if shift is not None:
                await shift.work_the_book(orders, finish=finish_orders)
                await shift.raise_what_is_due()
                # Raised, then handed out, in that order and on the same pass:
                # work nobody has been given is the state an order spends its
                # first seconds in, not its shift.
                await shift.dispatch_the_backlog()
                if await shift.calibrate_what_is_due():
                    # The operator measures with whatever the register now
                    # says. A bench still holding the old calibration date
                    # would go on adding a bias the plant has just removed,
                    # and the records would show a gauge that was calibrated
                    # and never came back.
                    await floor.read_the_register()

        async def do_the_work() -> None:
            """Its own loop, because the crew reads its own three things.

            The supervisor's pass raises work and hands it out; this one is
            the people who were given it, and it runs on its own clock because
            a mechanic halfway through a job has to be looked in on whether or
            not anything new has come due.
            """
            if crew is None:
                return
            while True:
                await asyncio.sleep(crew_every)
                try:
                    await crew.work_the_list()
                except httpx.HTTPError as exc:
                    log.warning("the maintenance crew step failed", error=str(exc)[:160])

        await asyncio.gather(
            every(inspect_every, do_inspect),
            every(issue_every, do_issue),
            every(review_every, do_review),
            every(supervise_every, do_supervise),
            watch_the_stops(),
            sample_the_bench(),
            plan_ahead(),
            do_the_work(),
        )
