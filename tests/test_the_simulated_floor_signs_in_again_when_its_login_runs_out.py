"""The simulated floor outlives its own login, and has to notice.

On 2026-10-06 both lab plants' simulated floors stopped at 00:52 and stayed
stopped until somebody restarted them nine hours later. Nothing had crashed.
A login is good for one shift - `[auth] token_ttl_seconds`, twelve hours - and
`fleet start` had been run twelve hours to the minute earlier: from then on
every request the floor made answered **401**, the step loop caught it as an
HTTP error, logged *shop floor step failed* and tried again on the next tick,
forever. 9,485 of those lines on one plant and 7,381 on the other by the time
it was found, and `crew smoke` passed throughout, because a login and a work
order read answer fine while the floor is dead.

So: a simulation is only proof while it runs, and this file pins the part that
keeps it running.

**Nothing here listens on a port, and nothing here is the real plant either.**
What is being tested is the floor's own behaviour when a plant refuses it, and
the cheapest honest way to ask that is a plant that refuses on cue - a fake
whose token stops working after a stated number of requests. The tokens it
issues are made by the MES's own `issue_token`, so the floor is reading the
expiry out of the real thing rather than out of a shape this test invented.
"""

from __future__ import annotations

import asyncio
import random
import types

import httpx
import pytest
import structlog

from fsmes.config import Settings
from fsmes.services import auth
from fsmes.sim.operations import Floor

SECRET = "a-secret-this-test-signs-with"


class FakePlant:
    """A plant that answers, and whose logins run out when told to.

    `good_for_requests` is how many requests a freshly issued token answers
    before the plant starts refusing it - the test's stand-in for twelve hours
    passing. `token_ttl_seconds` is what the plant *says* in the token it
    issues, which is a separate dial on purpose: the floor renewing early
    because the token says so, and the floor recovering from a refusal, are
    two different behaviours and a test that moved both at once would not know
    which one it had proved.
    """

    def __init__(self, *, good_for_requests: int = 3, token_ttl_seconds: int = 12 * 3600,
                 renew: bool = True) -> None:
        self.good_for_requests = good_for_requests
        self.token_ttl_seconds = token_ttl_seconds
        self.renew = renew
        self.logins = 0
        self.refusals = 0
        self.steps_answered = 0
        self._used = 0
        self._token: str | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            self.logins += 1
            if self.logins > 1 and not self.renew:
                # A password that changed under a running floor, or an account
                # that was disabled: the plant will not issue another token.
                return httpx.Response(401, json={"detail": "no"})
            person = types.SimpleNamespace(code="FLOOR-SIM", role="operator")
            self._token = auth.issue_token(person, SECRET, self.token_ttl_seconds)
            self._used = 0
            return httpx.Response(200, json={"token": self._token, "code": "FLOOR-SIM",
                                             "name": "The floor", "role": "operator"})
        held = (request.headers.get("Authorization") or "").removeprefix("Bearer ")
        if held != self._token or self._used >= self.good_for_requests:
            self.refusals += 1
            return httpx.Response(401, json={"detail": "the login has run out"})
        self._used += 1
        self.steps_answered += 1
        return httpx.Response(200, json={"items": [], "has_more": False, "machines": []})


def run_the_floor(plant: FakePlant, work, *, token_ttl_seconds: int = 12 * 3600):
    """Sign a floor in to `plant` and run one coroutine of its steps."""
    settings = Settings(token_ttl_seconds=token_ttl_seconds, secret_key=SECRET)

    async def _go():
        async with httpx.AsyncClient(transport=plant.transport(),
                                     base_url="http://plant") as client:
            floor = Floor(settings, client, random.Random(7))
            await floor.sign_in("FLOOR-SIM", "operator")
            return await work(floor)

    return asyncio.run(_go())


async def take_steps(floor: Floor, how_many: int) -> int:
    """Read the dashboard `how_many` times, the way the step loop does.

    Returns how many of those reads answered. A read that is refused raises,
    exactly as it does in `run()`, so a floor that stopped recovering shows up
    here as a count short of what was asked for rather than as a silence.
    """
    answered = 0
    for _ in range(how_many):
        await floor.get("/dashboard/summary")
        answered += 1
    return answered


# -------------------------------------------- the login running out mid-shift


def test_the_floor_keeps_stepping_when_its_login_runs_out():
    # Twelve hours in the token, so nothing renews early: the only thing that
    # can keep this floor working is noticing the refusal.
    plant = FakePlant(good_for_requests=3)

    with structlog.testing.capture_logs() as captured:
        answered = run_the_floor(plant, lambda floor: take_steps(floor, 10))

    assert answered == 10, "the floor stopped stepping when its login ran out"
    assert plant.logins == 1 + 3, (
        f"the floor signed in {plant.logins} times for ten steps against a login good "
        "for three; expected one at the start and one per expiry")
    said = [row for row in captured if row["event"] == "the floor signed in again"]
    assert len(said) == 3, f"the re-sign-in was logged {len(said)} times, not once per event"
    assert {row["why"] for row in said} == {"the plant refused the floor's login"}


def test_a_refused_step_is_retried_rather_than_lost():
    """The step that met the expired login is the step that has to succeed.

    Recovering on the *next* tick would be a quieter version of the same bug:
    the inspection that was due at the moment the token died would never be
    recorded, and on a floor that inspects every eight seconds nobody would
    ever see the gap.
    """
    plant = FakePlant(good_for_requests=2)

    answered = run_the_floor(plant, lambda floor: take_steps(floor, 6))

    assert answered == 6
    assert plant.steps_answered == 6, (
        f"{6 - plant.steps_answered} steps were lost to the expiry rather than retried")


def test_a_write_the_login_ran_out_under_is_sent_again_not_dropped():
    """A refused write is retried too - a check is a POST, not a GET."""
    plant = FakePlant(good_for_requests=1)

    async def record_two_checks(floor: Floor) -> list[int]:
        first = await floor.post("/quality/checks", json={"value": 1})
        second = await floor.post("/quality/checks", json={"value": 2})
        return [first.status_code, second.status_code]

    assert run_the_floor(plant, record_two_checks) == [200, 200]
    assert plant.steps_answered == 2


# ------------------------------------------- signing in before it has run out


def test_the_floor_signs_in_again_before_the_login_runs_out():
    """So the first failed step never happens at all.

    The plant here never refuses anything - `good_for_requests` is larger than
    the run - so the only reason to sign in again is the floor reading the
    token's own life and replacing it halfway through. A floor that waited for
    a 401 would sign in exactly once here.
    """
    # Ten seconds of login: the renewal falls due after five, which the sleeps
    # below walk past without the test taking ten seconds to run.
    plant = FakePlant(good_for_requests=1000, token_ttl_seconds=10)

    async def step_either_side_of_the_halfway_mark(floor: Floor) -> None:
        await take_steps(floor, 1)
        # The clock the renewal is on is `time.monotonic`, so this moves it.
        floor._renew_at = -1.0
        await take_steps(floor, 1)

    with structlog.testing.capture_logs() as captured:
        run_the_floor(plant, step_either_side_of_the_halfway_mark, token_ttl_seconds=10)

    assert plant.refusals == 0, "a step was refused, so this was recovery and not renewal"
    assert plant.logins == 2, f"the floor signed in {plant.logins} times, not twice"
    said = [row for row in captured if row["event"] == "the floor signed in again"]
    assert [row["why"] for row in said] == ["the login was halfway through its life"]


def test_when_to_renew_comes_from_the_token_the_plant_issued():
    """Not from the floor's own copy of the configuration.

    A floor talking to a plant it does not share settings with would otherwise
    renew against a TTL that is nobody's - and the token is the only party to
    this that actually knows.
    """
    plant = FakePlant(good_for_requests=1000, token_ttl_seconds=60)
    settings = Settings(token_ttl_seconds=12 * 3600, secret_key=SECRET)
    import time as clock

    async def _go() -> float:
        async with httpx.AsyncClient(transport=plant.transport(),
                                     base_url="http://plant") as client:
            floor = Floor(settings, client, random.Random(7))
            await floor.sign_in("FLOOR-SIM", "operator")
            return floor._renew_at - clock.monotonic()

    seconds_until_renewal = asyncio.run(_go())
    assert 20.0 < seconds_until_renewal < 40.0, (
        f"the floor will renew in {seconds_until_renewal:.0f}s; the token the plant issued "
        "is good for 60s, so halfway is about 30s and twelve hours is the config it should "
        "have ignored")


def test_a_token_whose_life_cannot_be_read_falls_back_to_the_configured_ttl():
    """A token in some other shape is not a thing to fail over."""
    settings = Settings(token_ttl_seconds=100, secret_key=SECRET)
    import time as clock

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"token": "not-a-token-this-mes-issued"})
        return httpx.Response(200, json={})

    async def _go() -> float:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),
                                     base_url="http://plant") as client:
            floor = Floor(settings, client, random.Random(7))
            await floor.sign_in("FLOOR-SIM", "operator")
            return floor._renew_at - clock.monotonic()

    seconds_until_renewal = asyncio.run(_go())
    assert 40.0 < seconds_until_renewal < 60.0, (
        f"will renew in {seconds_until_renewal:.0f}s; half of the configured 100s expected")


# --------------------------------------------- a login that will not come back


def test_a_login_the_plant_will_not_renew_is_said_once_not_once_a_step():
    """The 2026-10-06 failure mode, with the recovery taken away.

    A password changed under a running floor cannot be fixed from here, and
    the floor is right to keep trying - but ten thousand identical lines is how
    a thing that needs a person stays unfound.
    """
    plant = FakePlant(good_for_requests=1, renew=False)

    async def step_until_refused(floor: Floor) -> int:
        refused = 0
        for _ in range(8):
            try:
                await floor.get("/dashboard/summary")
            except httpx.HTTPStatusError as exc:
                assert exc.response.status_code == 401
                refused += 1
        return refused

    with structlog.testing.capture_logs() as captured:
        refused = run_the_floor(plant, step_until_refused)

    assert refused == 7, "the floor gave up rather than keep trying"
    could_not = [row for row in captured
                 if row["event"].startswith("the floor could not sign in again")]
    assert all(row["log_level"] == "error" for row in could_not), (
        "a floor nobody can sign in is a warning nobody reads")
    assert len(could_not) == 1, (
        f"the plant's refusal to re-issue a login was logged {len(could_not)} times")


def test_a_floor_that_was_never_signed_in_is_not_quietly_signed_in():
    """Nothing here guesses at credentials it was not given.

    A `Floor` built and used without `sign_in` - which is how the supervisor's
    half behaves on a plant that has no supervisor account - gets the plant's
    refusal, not a login attempt invented from nothing.
    """
    plant = FakePlant(good_for_requests=0)

    async def _go() -> int:
        async with httpx.AsyncClient(transport=plant.transport(),
                                     base_url="http://plant") as client:
            floor = Floor(Settings(secret_key=SECRET), client, random.Random(7))
            with pytest.raises(httpx.HTTPStatusError):
                await floor.get("/dashboard/summary")
            return plant.logins

    assert asyncio.run(_go()) == 0
