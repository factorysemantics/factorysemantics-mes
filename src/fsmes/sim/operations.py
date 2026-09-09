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

    async def sign_in(self, code: str, password: str) -> None:
        r = await self.client.post("/auth/login", json={"code": code, "password": password})
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['token']}"

    async def get(self, path: str, **params):
        r = await self.client.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    # ---------------------------------------------------------- inspections

    async def _measured_value(self, spec: dict, machines: list[dict]) -> float | None:
        """What the line actually made, if any machine reports this characteristic."""
        want = _slug(spec["characteristic"])
        for machine in machines:
            analog = (machine.get("analog") or {}).get("name")
            if analog and _slug(analog) == want:
                try:
                    trend = await self.get(f"/analysis/tag/{machine['code']}", hours=0.2)
                except httpx.HTTPError:
                    return None
                points = [p for p in trend.get("points", []) if p.get("mean") is not None]
                if points:
                    return float(points[-1]["mean"])
        return None

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
            value = await self._measured_value(spec, machines)
            if value is None:
                value = self._plausible_value(spec)
            same = [o for o in active if o.get("material") == spec["material"]]
            order = self.rng.choice(same or active)["code"] if (same or active) else None
            body = {"material": spec["material"], "characteristic": spec["characteristic"],
                    "value": round(value, 3)}
            if order:
                body["order"] = order
            try:
                r = await self.client.post("/quality/checks", json=body)
                r.raise_for_status()
                out = r.json()
                log.info("inspected", characteristic=spec["characteristic"],
                         value=round(value, 3), result=out.get("result"),
                         order=order)
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

    # --------------------------------------------------------------- orders

    async def release_work(self, orders: list[dict]) -> None:
        """Keep work on the floor.

        A line that finishes its only order and then runs on nothing is a
        demo, not a plant: material issue has nothing to book against and
        genealogy stays empty. Real plants have a queue, so when none is
        active this releases the next one.
        """
        active = [o for o in orders if o.get("status") in ACTIVE_STATUSES]
        if active:
            return

        done = [o for o in orders if o.get("code")]
        material = done[0]["material"] if done else None
        if not material:
            return

        # Continue the seeded numbering rather than inventing a scheme.
        numbers = []
        for o in done:
            tail = o["code"].rsplit("-", 1)[-1]
            if tail.isdigit():
                numbers.append(int(tail))
        prefix = done[0]["code"].rsplit("-", 1)[0] if done else "WO"
        code = f"{prefix}-{(max(numbers) + 1) if numbers else 1001}"

        quantity = float(self.rng.choice([1500, 2000, 2500, 4000]))
        try:
            r = await self.client.post("/workorders", json={
                "code": code, "material": material, "quantity": quantity,
                "priority": self.rng.choice([10, 20, 50]),
            })
            r.raise_for_status()
            await self.client.post(f"/workorders/{code}/release")
            log.info("released order", order=code, material=material, quantity=quantity)
        except httpx.HTTPStatusError as exc:
            log.warning("release refused", status=exc.response.status_code,
                        detail=exc.response.text[:160])

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
              release_every: float = 20.0,
              seed: int = 0, user: str = "FLOOR-SIM", password: str = "operator",
              supervisor: str = "FLOOR-SUP", supervisor_password: str = "supervisor",
              inspect_all: bool = False) -> None:
    """Generate shop-floor activity until stopped.

    Two identities, because the plant has two: the floor (an operator) records
    checks, issues material and releases work; the shift supervisor closes
    non-conformances, which an operator may not - segregation of duties the
    product is right to enforce, and which the simulator must respect rather
    than be granted around.
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
            log.warning("no supervisor account; non-conformances will stay open",
                        user=supervisor, error=str(exc)[:120])
            shift = None

        log.info("shop floor online", endpoint=base, inspect_every=inspect_every,
                 issue_every=issue_every)

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
            specs = await floor.get("/quality/specs")
            await floor.inspect(specs, summary.get("machines", []), orders, every_spec=inspect_all)

        async def do_issue(summary, orders):
            lots = (await floor.get("/execution/lots"))["items"]
            await floor.issue_material(orders, lots)

        async def do_review(summary, orders):
            if shift is not None:
                await shift.review_nonconformances()

        async def do_release(summary, orders):
            await floor.release_work(orders)

        await asyncio.gather(
            every(inspect_every, do_inspect),
            every(issue_every, do_issue),
            every(review_every, do_review),
            every(release_every, do_release),
        )
