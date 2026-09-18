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

import httpx
import structlog

from fsmes.config import Settings

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


class Floor:
    """One simulated shop floor, working through the API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient,
                 rng: random.Random) -> None:
        self.settings = settings
        self.client = client
        self.rng = rng
        # The last value this floor recorded for each characteristic, so it
        # does not write the same reading down twice. See `inspect`.
        self._last_recorded: dict[str, float] = {}
        # The empty book is said once, not every twenty seconds for a shift.
        # It is reset the moment an order is released, so a book that is
        # refilled and empties again says so again.
        self._said_the_book_is_empty = False

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
                return float(analog["value"]), machine["code"]
            try:
                trend = await self.get(f"/analysis/tag/{machine['code']}", hours=0.2)
            except httpx.HTTPError:
                return None, None
            points = [p for p in trend.get("points", []) if p.get("mean") is not None]
            if points:
                return float(points[-1]["mean"]), machine["code"]
        return None, None

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
        for spec in chosen:
            value, station = await self._measured_value(spec, machines)
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
            body = {"material": spec["material"], "characteristic": spec["characteristic"],
                    "value": round(value, 3)}
            if order:
                body["order"] = order
            if station:
                body["equipment"] = station
            try:
                r = await self.client.post("/quality/checks", json=body)
                r.raise_for_status()
                out = r.json()
                self._last_recorded[spec["characteristic"]] = value
                log.info("inspected", characteristic=spec["characteristic"],
                         value=round(value, 3), result=out.get("result"),
                         order=order, equipment=station)
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

    async def review_nonconformances(self, keep_open: int = 3) -> None:
        """Close the older non-conformances, leaving a few genuinely open.

        A plant where nothing is ever closed is as unrealistic as one where
        nothing ever fails, and an NCR list that only grows tells an operator
        nothing about which problems are live.
        """
        # Every plant retired on 2026-09-06 had every non-conformance it ever
        # raised still open - thousands - and this loop had said nothing,
        # because both failures below were swallowed. A loop that cannot
        # report its own failure is one nobody finds out is broken.
        try:
            page = await self.get("/quality/nonconformances?status=open&limit=200")
        except httpx.HTTPError as exc:
            log.warning("could not read open non-conformances", error=str(exc)[:160])
            return
        ncs = page["items"] if isinstance(page, dict) else page
        stale = [n for n in ncs if n.get("status") == "open"][:-keep_open or None]
        for nc in stale[:2]:
            try:
                response = await self.client.post(f"/quality/nonconformances/{nc['code']}/close")
                if response.status_code >= 400:
                    log.warning("could not close non-conformance", code=nc["code"],
                                status=response.status_code, detail=response.text[:160])
                    continue
                log.info("closed non-conformance", code=nc["code"])
            except httpx.HTTPError as exc:
                log.warning("could not close non-conformance", code=nc["code"], error=str(exc)[:160])


async def run(settings: Settings, *, inspect_every: float = 8.0,
              issue_every: float = 25.0, review_every: float = 90.0,
              supervise_every: float = 20.0,
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
    """
    base = f"http://{settings.api_host}:{settings.api_port}"
    rng = random.Random(seed)

    async with httpx.AsyncClient(base_url=base, timeout=20.0) as client, \
            httpx.AsyncClient(base_url=base, timeout=20.0) as sup_client:
        floor = Floor(settings, client, rng)
        shift = Floor(settings, sup_client, rng)

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

        log.info("shop floor online", endpoint=base, inspect_every=inspect_every,
                 issue_every=issue_every, supervise_every=supervise_every,
                 finishes_orders=finish_orders and shift is not None)

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
            if shift is not None:
                await shift.review_nonconformances()

        async def do_supervise(summary, orders):
            # The supervisor's client, not the operator's: what this does -
            # finishing an order, releasing the next - is a supervisor's act,
            # and the audit row has to say so.
            if shift is not None:
                await shift.work_the_book(orders, finish=finish_orders)

        await asyncio.gather(
            every(inspect_every, do_inspect),
            every(issue_every, do_issue),
            every(review_every, do_review),
            every(supervise_every, do_supervise),
        )
