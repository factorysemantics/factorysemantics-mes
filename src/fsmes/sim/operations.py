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
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from fsmes import identity
from fsmes.config import Settings
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

    async def sign_in(self, code: str, password: str) -> None:
        r = await self.client.post("/auth/login", json={"code": code, "password": password})
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['token']}"

    async def get(self, path: str, **params):
        r = await self.client.get(path, params=params or None)
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
                response = await self.client.post(
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
                r = await self.client.post("/quality/checks", json=body)
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
            response = await self.client.post(
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
            r = await self.client.post("/execution/consume", json=body)
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
                    response = await self.client.post(
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
            response = await self.client.post(f"/workorders/{nxt['code']}/release")
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
            response = await self.client.post(path, json=json)
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
              speed: float = 1.0,
              seed: int = 0, user: str = "FLOOR-SIM", password: str = "operator",
              supervisor: str = "FLOOR-SUP", supervisor_password: str = "supervisor",
              inspect_all: bool = False, finish_orders: bool = True) -> None:
    """Generate shop-floor activity until stopped.

    Two identities, because the plant has two: the floor (an operator) records
    checks and issues material; the shift supervisor closes non-conformances,
    finishes an order the line has made the number for and releases the next
    one in the book. An operator may not close a non-conformance - segregation
    of duties the product is right to enforce, and which the simulator must
    respect rather than be granted around - and the order book is the
    supervisor's for the same reason: the audit trail has to name whoever
    finished an order, and it has to be true.

    `finish_orders` is False for a scripted over-run, where nobody stopping
    the line is the whole point.

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

    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client, \
            httpx.AsyncClient(base_url=base, timeout=20.0) as sup_client:
        floor = Floor(settings, client, rng, script, speed)
        shift = Floor(settings, sup_client, rng, script, speed)

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

        gauges_known = await floor.read_the_register()
        if shift is not None and script:
            await shift.read_the_register()

        log.info("shop floor online", endpoint=base, inspect_every=inspect_every,
                 issue_every=issue_every, supervise_every=supervise_every,
                 finishes_orders=finish_orders and shift is not None,
                 floor_script=str(settings.floor_script_file) if script else None,
                 gauges_on_the_register=gauges_known)

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

        async def do_supervise(summary, orders):
            # The supervisor's client, not the operator's: what this does -
            # finishing an order, releasing the next, calibrating a gauge
            # that has fallen due - is a supervisor's act, and the audit row
            # has to say so.
            if shift is not None:
                await shift.work_the_book(orders, finish=finish_orders)
                if await shift.calibrate_what_is_due():
                    # The operator measures with whatever the register now
                    # says. A bench still holding the old calibration date
                    # would go on adding a bias the plant has just removed,
                    # and the records would show a gauge that was calibrated
                    # and never came back.
                    await floor.read_the_register()

        await asyncio.gather(
            every(inspect_every, do_inspect),
            every(issue_every, do_issue),
            every(review_every, do_review),
            every(supervise_every, do_supervise),
            watch_the_stops(),
        )
