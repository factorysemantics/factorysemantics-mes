"""The ``fsmes`` command line — every part of the MES is operated from here.

The commands below came in with the engine and were reached as ``mes`` in
MES-TWIN. They keep their names; only the executable changed.
"""

import asyncio
import os
import textwrap
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path

import typer

from fsmes import __version__
from fsmes.config import Settings, get_settings
from fsmes.logging import setup_logging

app = typer.Typer(
    name="fsmes",
    help="FactorySemantics MES — modular, agent-native manufacturing execution.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def init_db() -> None:
    """Create or upgrade the database schema (runs Alembic migrations)."""
    ini = Path("alembic.ini")
    if ini.exists():
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(ini)), "head")
    else:  # running outside the repo — create the schema directly
        import fsmes.domain  # noqa: F401
        from fsmes.db import Base, get_engine

        Base.metadata.create_all(get_engine())
    typer.echo("Database schema is up to date.")


@app.command()
def seed() -> None:
    """Load the demo plant (site, line, machines, materials, routing, lots)."""
    from fsmes.db import session_scope
    from fsmes.seed import seed_demo_plant

    with session_scope() as session:
        created = seed_demo_plant(session)
    typer.echo("Demo plant seeded." if created else "Database already has data — nothing done.")


@app.command()
def seed_kepsim() -> None:
    """Load the KepSim simulated line (six stations, routing, materials, lots)."""
    from fsmes.db import session_scope
    from fsmes.seed_kepsim import seed_kepsim_line

    with session_scope() as session:
        created = seed_kepsim_line(session)
    typer.echo("KepSim line seeded." if created else "KepSim line is already present — nothing done.")


@app.command()
def make_tag_map(
    worksheet: Path = typer.Argument(..., help="The filled-in tag worksheet (CSV) from Engineering."),
    out: Path = typer.Option(Path("config/tag_map_plant.json"), help="Where to write the tag map."),
) -> None:
    """Build a tag map from Engineering's worksheet (docs/onboarding).

    The worksheet is the plant's answers — which tags exist, what the state
    values mean, the rated cycle times. This turns it into the config file the
    agent runs from, refusing anything the agent could not honestly interpret.
    """
    from fsmes.integrations.opc.worksheet import WorksheetError, write_tag_map

    try:
        warnings = write_tag_map(worksheet, out)
    except WorksheetError as exc:
        typer.echo(f"Worksheet problem: {exc}")
        raise typer.Exit(1) from None
    for warning in warnings:
        typer.echo(f"  note: {warning}")
    typer.echo(f"Wrote {out}. Point the MES at it with:  set MES_TAG_MAP_FILE={out}")
    typer.echo("Next: `fsmes opc-verify` against the live server, then `fsmes seed-line`.")


@app.command()
def seed_line(
    line: str = typer.Argument(..., help="Code for the line (work centre), e.g. LINE1."),
    line_name: str = typer.Option(None, help="Human name for the line."),
    tag_map: Path = typer.Option(None, help="Tag map to seed from (default: MES_TAG_MAP_FILE)."),
    enterprise: str = typer.Option("PLANT", help="Enterprise code (created if missing)."),
    enterprise_name: str = typer.Option("Plant", help="Enterprise name."),
    site: str = typer.Option("SITE1", help="Site code (created if missing)."),
    site_name: str = typer.Option("Main Site", help="Site name."),
    area: str = typer.Option("PROD", help="Area code (created if missing)."),
    area_name: str = typer.Option("Production", help="Area name."),
    no_routing: bool = typer.Option(False, help="Observe only: no routing, so production never books."),
) -> None:
    """Create the equipment a tag map describes, so the agent has rows to write to."""
    from fsmes.db import session_scope
    from fsmes.seed_line import seed_line_from_tag_map

    path = tag_map or get_settings().tag_map_file
    with session_scope() as session:
        created = seed_line_from_tag_map(
            session,
            path,
            line_code=line,
            line_name=line_name,
            enterprise_code=enterprise,
            enterprise_name=enterprise_name,
            site_code=site,
            site_name=site_name,
            area_code=area,
            area_name=area_name,
            with_routing=not no_routing,
        )
    if created:
        typer.echo(f"Line {line} seeded from {path}.")
        if no_routing:
            typer.echo("No routing: the MES observes (states, downtime, trends) but books no production.")
        else:
            typer.echo(f"Release a work order for FG-{line} and counter deltas will book against it.")
    else:
        typer.echo(f"Line {line} already exists — nothing done.")


@app.command()
def opc_browse(
    depth: int = typer.Option(3, help="How deep to walk the address space."),
    contains: str = typer.Option(None, help="Only show nodes whose name or id contains this."),
) -> None:
    """Walk the OPC UA server and print what is actually there.

    The ground truth Engineering's worksheet gets checked against. Uses the
    same endpoint/security settings as the agent (MES_OPC_*).
    """
    from fsmes.integrations.opc import inspect

    settings = get_settings()
    setup_logging("WARNING", settings.log_dir, "opc-browse")
    raise typer.Exit(asyncio.run(inspect.browse(settings, depth=depth, contains=contains)))


@app.command()
def opc_verify(
    seconds: int = typer.Option(10, help="How long between the two reads."),
) -> None:
    """Prove the tag map against the live server before trusting it.

    Resolves every mapped tag through the agent's own code, reads each twice,
    and reports per machine: readable, changing, and State values that map.
    Exit 0 = PASS. Run it while the line is running for the strongest answer.
    """
    from fsmes.integrations.opc import inspect

    settings = get_settings()
    setup_logging("WARNING", settings.log_dir, "opc-verify")
    raise typer.Exit(asyncio.run(inspect.verify(settings, seconds=seconds)))


@app.command()
def make_demo_feed(
    output: Path | None = typer.Option(None, help="Where to write it (default: the web folder's demo/)."),
    replay_dir: Path | None = typer.Option(None, help="Generated tables to record (default: labs/kepsim/out)."),
    tag_map: Path | None = typer.Option(None, help="Tag map describing that line (default: MES_TAG_MAP_FILE)."),
    line: str = typer.Option("SIMLINE", help="Which seeded line the recording is of."),
) -> None:
    """Record the KepSim hour for the 3D line view's standalone demo.

    The result is a plain static file, so /dashboard/line?demo=1 plays the whole
    hour with no database, no OPC server and no agent running. The database is
    needed here, when recording, only to describe the line's layout — the same
    way the live endpoint describes it, which is what stops the demo drifting
    into fiction.
    """
    import json

    from fsmes.db import session_scope
    from fsmes.demo_feed import DEFAULT_OUTPUT, build

    with session_scope() as session:
        payload = build(session, replay_dir=replay_dir, tag_map=tag_map, line_code=line)

    destination = Path(output or DEFAULT_OUTPUT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    typer.echo(
        f"Recorded {payload['ticks']} ticks of {payload['line']} "
        f"({len(payload['stations'])} stations) to {destination} "
        f"[{destination.stat().st_size / 1024:.0f} KB]"
    )


@app.command()
def add_user(code: str, name: str, password: str, role: str = "operator") -> None:
    """Create a sign-in account (roles: viewer, operator, supervisor, admin)."""
    from fsmes.db import session_scope
    from fsmes.services import auth

    with session_scope() as session:
        person = auth.create_user(session, code=code, name=name, password=password, role=role, actor="cli")
        typer.echo(f"Created {person.code} ({person.role}).")


@app.command()
def set_password(code: str, password: str) -> None:
    """Reset a user's password."""
    from fsmes.db import session_scope
    from fsmes.services import auth

    with session_scope() as session:
        auth.set_password(session, code=code, password=password, actor="cli")
        typer.echo(f"Password updated for {code.upper()}.")


@app.command()
def run_api(host: str | None = None, port: int | None = None) -> None:
    """Run the MES API server (interactive docs at /docs)."""
    import uvicorn

    from fsmes.api.app import create_app

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "api")
    uvicorn.run(create_app(), host=host or settings.api_host, port=port or settings.api_port, log_config=None)


@app.command()
def run_opc_sim(
    speed: float | None = None,
    replay: bool = typer.Option(False, help="Serve the KepSim line's generated CSVs instead of the built-in machines."),
    replay_dir: Path | None = typer.Option(None, help="Where the generated tables live (default: labs/kepsim/out)."),
) -> None:
    """Run the simulated plant (OPC UA server).

    --replay swaps the toy machines for the six-station KepSim line, played back
    from labs/kepsim/out at one row per second. Pair it with the matching tag
    map, which is what tells the agent how to read it:

        set MES_TAG_MAP_FILE=config/tag_map_kepsim.json
    """
    from fsmes.integrations.opc import csv_replay, simulator

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "opc-replay" if replay else "opc-sim")
    if replay:
        asyncio.run(csv_replay.run(settings, directory=replay_dir, speed=speed))
    else:
        asyncio.run(simulator.run(settings, speed=speed))


@app.command()
def run_opc_agent() -> None:
    """Run the OPC agent (machine data -> MES, order codes -> machines)."""
    from fsmes.integrations.opc import agent

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "opc-agent")
    asyncio.run(agent.run_all(settings))


@app.command()
def run_erp_sync() -> None:
    """Run the ERP sync worker (orders in, confirmations out)."""
    from fsmes.integrations.erp import sync
    from fsmes.integrations.erp.base import make_adapter

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "erp-sync")
    adapter = make_adapter(settings)
    if adapter is None:
        typer.echo("ERP mode is 'off' — nothing to run.")
        raise typer.Exit(1)
    _warn_if_the_erp_side_is_not_ready(adapter)
    asyncio.run(sync.run(adapter, settings.erp_poll_seconds))


def _warn_if_the_erp_side_is_not_ready(adapter) -> None:
    """Say at start-up if the ERP side was never prepared.

    A warning, not a refusal: the outbox is built to survive an ERP that is
    down for an hour, and a worker that will not start because the ERP was
    unreachable at the wrong second is worse than one that retries. What
    must not happen is silence — so a missing requirement is on the console
    the moment the worker comes up, whatever connector is configured.
    """
    from fsmes.integrations.erp import base

    try:
        result = base.check(adapter)
    except Exception as exc:
        typer.echo(f"Could not check the ERP side ({type(exc).__name__}: {exc}). Starting anyway.")
        return
    if result.ok:
        return
    for line in result.render():
        typer.echo(line)
    typer.echo("Run `fsmes erp setup`, or `fsmes erp check` for the whole picture. "
               "Until then confirmations will fail rather than be quietly lost.")


erp_app = typer.Typer(help="The ERP connector: what it needs, preparing it, and checking it.")
app.add_typer(erp_app, name="erp")


def _configured_connector():
    """The connector `MES_ERP_MODE` names, built from settings.

    These commands are the port's `requirements`, `setup` and `check` and
    nothing else, so they work for a connector published on its own exactly
    as they do for the ones that ship. Building it is where a wrong URL, a
    wrong password or a site that is not there shows up, and a sentence is
    a better answer than a traceback.
    """
    from fsmes.integrations.erp.base import make_adapter

    settings = get_settings()
    if settings.erp_mode == "off":
        typer.echo("MES_ERP_MODE is 'off' — no connector is configured, so there is nothing to do.")
        raise typer.Exit(1)
    try:
        adapter = make_adapter(settings)
    except Exception as exc:
        typer.echo(f"NOT OK  cannot build the {settings.erp_mode!r} connector: {type(exc).__name__}: {exc}")
        typer.echo("        check MES_ERP_MODE and the settings that mode reads.")
        raise typer.Exit(1) from exc
    if adapter is None:
        typer.echo(f"MES_ERP_MODE is {settings.erp_mode!r} — no connector was built.")
        raise typer.Exit(1)
    return settings, adapter


def _close(adapter) -> None:
    client = getattr(adapter, "client", None)
    if hasattr(client, "close"):
        client.close()


@erp_app.command("requirements")
def erp_requirements() -> None:
    """What the configured connector needs to exist on the ERP side.

    Often the person who administers the ERP is not the person running the
    MES. This is the list to send them, and it says which lines the MES can
    create itself.
    """
    from fsmes.integrations.erp import base

    settings, adapter = _configured_connector()
    try:
        needed = base.requirements(adapter)
    finally:
        _close(adapter)
    typer.echo(f"The {settings.erp_mode!r} connector needs {len(needed)} things on the ERP side.")
    if not needed:
        typer.echo("Nothing. This transport needs no preparation beyond being reachable.")
        return
    for requirement in needed:
        typer.echo("")
        typer.echo(f"  {requirement.name}  ({requirement.where})")
        typer.echo(f"    what   {requirement.what}")
        typer.echo(f"    why    {requirement.why}")
        typer.echo("    who    " + ("`fsmes erp setup` creates it" if requirement.created_by_setup
                                    else "a person with access to the ERP; the MES cannot create it"))


@erp_app.command("setup")
def erp_setup() -> None:
    """Prepare the ERP side for the configured connector.

    Safe to run twice: anything already there is left exactly as it is.
    Anything the MES cannot create says so rather than reporting success.
    """
    from fsmes.integrations.erp import base

    settings, adapter = _configured_connector()
    typer.echo(f"The {settings.erp_mode!r} connector.")
    try:
        outcome = base.setup(adapter)
    finally:
        _close(adapter)
    for step in outcome:
        typer.echo(f"  {step.name:<30} {step.outcome}")
    created = sum(1 for step in outcome if step.outcome == "created")
    by_hand = sum(1 for step in outcome if step.outcome == "must be done by hand")
    typer.echo(f"{len(outcome)} requirements: {created} created, "
               f"{len(outcome) - created - by_hand} already there, {by_hand} for a person to do.")
    if by_hand:
        typer.echo("Run `fsmes erp requirements` for what to send whoever administers the ERP.")
    typer.echo("Run `fsmes erp check` to confirm the whole connection.")


@erp_app.command("check")
def erp_check() -> None:
    """Say whether this MES can actually talk to the configured ERP.

    Connectivity, credentials and every requirement, each answered
    separately, in words. Exits non-zero if anything would stop the
    connector working, so it can gate a deployment. What could not be
    checked is reported as unknown rather than counted as working.
    """
    from fsmes.integrations.erp import base

    settings, adapter = _configured_connector()
    typer.echo(f"The {settings.erp_mode!r} connector.")
    try:
        result = base.check(adapter)
    finally:
        _close(adapter)
    for line in result.render():
        typer.echo(line)
    if not result.ok:
        typer.echo("Not ready — the connector would not work as configured.")
        raise typer.Exit(1)
    typer.echo("Ready, as far as anything above was checked." if result.unverified else "Ready.")


@erp_app.command("validate")
def erp_validate(
    path: Path = typer.Argument(..., help="A confirmation file, or a folder of them."),
) -> None:
    """Check outbound confirmation files against the contract and the house rules.

    Reads files and nothing else: no ERP, no connector, no database. It is
    what a plant's ERP team runs on a shadow-mode outbox to answer the
    question they always ask first - would what this MES sends be correct
    if it were connected? Exits non-zero if anything in the folder would
    be right to reject.
    """
    from fsmes.integrations.erp import validate as validator

    if not path.exists():
        typer.echo(f"NOT OK  {path} does not exist.")
        raise typer.Exit(1)
    typer.echo(f"Checking {path} against the ERP confirmation contract.")
    report = validator.validate(path)
    for line in report.render():
        typer.echo(line)
    if not report.ok:
        typer.echo("Not correct - an ERP would be right to reject what is marked above.")
        raise typer.Exit(1)
    if not report.documents:
        typer.echo("Nothing was checked, so nothing is confirmed.")
        return
    typer.echo("Correct, as far as files can say: every document matches the contract the "
               "published JSON Schema is generated from and breaks none of the house rules. "
               "Whether the numbers describe what the line really did is the plant's "
               "question, not this command's.")


uns_app = typer.Typer(help="Unified namespace: relay MES events to an MQTT broker.")
app.add_typer(uns_app, name="uns")


@uns_app.command("publish")
def uns_publish(once: bool = False) -> None:
    """Publish the MES's events to the unified namespace (MQTT), forever.

    `--once` runs a single cycle and reports what it did, which is what a
    cron-shaped deployment or a person checking a new broker wants.
    """
    from fsmes.integrations.uns import publisher
    from fsmes.integrations.uns.transport import make_transport

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "uns-publish")
    transport = make_transport(settings)
    if transport is None:
        typer.echo("MES_UNS_MODE is 'off' — nothing to run. Set it to 'mqtt', "
                   "or to 'log' to see the topics without a broker.")
        raise typer.Exit(1)
    if not once:
        asyncio.run(publisher.run(transport, settings))
        return

    from fsmes.db import session_scope

    async def _one() -> dict:
        try:
            return await publisher.cycle(transport, session_scope, settings)
        finally:
            await transport.close()

    counted = asyncio.run(_one())
    typer.echo(f"enrolled {counted['enrolled']}, due {counted['due']}, "
               f"published {counted['published']}, failed {counted['failed']}")


@uns_app.command("topics")
def uns_topics() -> None:
    """Print the topic every machine in this plant publishes under.

    The tree as it would appear on the broker, before a single event has
    been produced — the check that the prefix, the enterprise and the site
    are what the plant's other systems expect.
    """
    from fsmes.db import session_scope
    from fsmes.integrations.uns.topics import equipment_topics

    settings = get_settings()
    with session_scope() as session:
        topics = equipment_topics(session, settings)
    for topic in topics:
        typer.echo(f"{topic}/<event kind>")
    typer.echo(f"{len(topics)} work units. Order-level events publish at the site, "
               f"without a machine segment.")


@uns_app.command("queue")
def uns_queue(limit: int = 30) -> None:
    """What the namespace has been told and what it still owes."""
    import json as _json

    from fsmes.db import session_scope
    from fsmes.services import uns as uns_service

    with session_scope() as session:
        typer.echo(_json.dumps(uns_service.queue_summary(session, limit=limit),
                               indent=2, default=str))


@app.command()
def run_mock_erp(host: str = "127.0.0.1", port: int = 8001) -> None:
    """Run the mock ERP (the business-system side of the twin)."""
    import uvicorn

    from fsmes.integrations.erp.mock_erp import app as erp_app

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "mock-erp")
    uvicorn.run(erp_app, host=host, port=port, log_config=None)


def _order_completion(confirmations: list[dict], order_code: str) -> dict | None:
    """The order completion for this order, out of everything the ERP holds.

    Not `confirmations[-1]`. Operation confirmations and the order completion
    are different messages with different fields — an operation does not make
    a finished-goods lot, so it has no `lot` — and they arrive in whatever
    order the outbox delivers them. Reading the last one that happened to
    land is how the release check crashed on `KeyError: 'lot'`.
    """
    for message in reversed(confirmations):
        if (message.get("kind", "production_confirmation") != "operation_confirmation"
                and str(message.get("order")) == order_code):
            return message
    return None


@app.command()
def demo(duration: int = 90) -> None:
    """Run the entire twin in one process and push one order through the full loop:
    ERP -> MES -> machines -> MES -> ERP."""
    from fsmes.db import session_scope
    from fsmes.seed import seed_demo_plant

    settings = get_settings()
    setup_logging("WARNING", settings.log_dir, "demo")  # keep the console for the story
    init_db()
    with session_scope() as session:
        seed_demo_plant(session)
    problem = asyncio.run(_demo(settings, duration))
    if problem:
        typer.echo(f"Demo result: the loop did not close - {problem}.")
        raise typer.Exit(1)
    typer.echo("Demo result: full loop closed - order booked, ERP confirmed, OEE reported.")


def _loop_verdict(status: str, completion: dict | None, genealogy: dict, oee: dict) -> str | None:
    """Why the demo's loop did not close, or None when it did.

    This exists because from the outside a demo that books nothing looked
    exactly like one that books everything. 0.1.0 shipped a wheel with no
    config files, so the simulator had no line to run, the order sat at
    `released` for the whole window, and `fsmes demo` still exited 0. The
    three facts the demo claims - the order completed, the ERP was told the
    order was done, a
    finished lot exists - are checked here so the exit code means something
    and a release can be gated on it.

    OEE is required to *answer*, not to be a number: a component that cannot
    be computed is reported as null on purpose, and failing on that would be
    asking the demo to guess.
    """
    if status != "completed":
        return f"the order was still {status} when the demo stopped watching"
    if not completion:
        # The order completion specifically, not any message: the operation
        # confirmations go out first, so "some confirmation arrived" would
        # have passed a run whose order was never closed to the ERP at all.
        return "the ERP never received the order completion"
    if not genealogy.get("produced"):
        return "no finished lot was booked"
    if "oee" not in oee:
        return "the OEE endpoint did not answer"
    return None


async def _wait_for(http, url: str, timeout: float = 30.0) -> None:
    import httpx

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            response = await http.get(url)
            if response.status_code < 500:
                return
        except httpx.TransportError:
            pass
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"{url} did not come up within {timeout}s")
        await asyncio.sleep(0.5)


async def _demo(settings: Settings, duration: int) -> str | None:
    """Run the loop and return why it did not close, or None when it did."""
    import httpx
    import uvicorn

    from fsmes.api.app import create_app
    from fsmes.integrations.erp import mock_erp, sync
    from fsmes.integrations.erp.rest_adapter import RestErpAdapter
    from fsmes.integrations.opc import agent, simulator

    api_url = f"http://127.0.0.1:{settings.api_port}"
    erp_url = "http://127.0.0.1:8001"
    quiet = {"log_config": None, "log_level": "warning"}
    api_server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=settings.api_port, **quiet))
    erp_server = uvicorn.Server(uvicorn.Config(mock_erp.app, host="127.0.0.1", port=8001, **quiet))
    tasks = [
        asyncio.create_task(simulator.run(settings, speed=max(settings.sim_speed, 4.0)), name="opc-sim"),
        asyncio.create_task(api_server.serve(), name="api"),
        asyncio.create_task(erp_server.serve(), name="mock-erp"),
        asyncio.create_task(agent.run(settings), name="opc-agent"),
        asyncio.create_task(sync.run(RestErpAdapter(erp_url), poll_seconds=2.0), name="erp-sync"),
    ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await _wait_for(http, f"{api_url}/health")
            await _wait_for(http, f"{erp_url}/")

            # Sign in the way any real client does; every action below is
            # attributed to SCOTT in the audit trail.
            login = await http.post(
                f"{api_url}/auth/login", json={"code": "SCOTT", "password": settings.operator_password}
            )
            login.raise_for_status()
            operator = {"Authorization": f"Bearer {login.json()['token']}"}
            code = f"WO-DEMO-{datetime.now():%H%M%S}"

            typer.echo(f"\n[1/5] A planner creates order {code} in the ERP: 15 x FG-COLA")
            (await http.post(f"{erp_url}/orders",
                             json={"code": code, "material": "FG-COLA", "quantity": 15})).raise_for_status()

            typer.echo("[2/5] ERP sync imports it into the MES...")
            deadline = asyncio.get_event_loop().time() + 30
            while (await http.get(f"{api_url}/workorders/{code}", headers=operator)).status_code != 200:
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError("order never arrived from the ERP")
                await asyncio.sleep(1)
            typer.echo("      ...imported and acknowledged back to the ERP.")

            typer.echo("[3/5] SCOTT releases the order and issues raw material lots")
            released_at = asyncio.get_event_loop().time()
            (await http.post(f"{api_url}/workorders/{code}/release", headers=operator)).raise_for_status()
            for lot, qty in (("LOT-SUGAR-001", 7.5), ("LOT-FLAVOR-001", 1.5)):  # per BOM for 15 units
                (
                    await http.post(
                        f"{api_url}/execution/consume",
                        json={"order": code, "lot": lot, "quantity": qty},
                        headers=operator,
                    )
                ).raise_for_status()

            typer.echo("[4/5] The agent loads the order into the machines; production is running...")
            deadline = asyncio.get_event_loop().time() + duration
            status = "released"
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(3)
                order = (await http.get(f"{api_url}/workorders/{code}", headers=operator)).json()
                status = order["status"]
                ops = " | ".join(
                    f"{op['name']} on {op['equipment']}: {op['good_qty']:.0f}/{order['quantity']:.0f} good, "
                    f"{op['scrap_qty']:.0f} scrap ({op['status']})"
                    for op in order["operations"]
                )
                typer.echo(f"      {ops}")
                if status == "completed":
                    break

            if status != "completed":
                typer.echo(f"      Order still {status} after {duration}s - leaving it running; "
                           "check /kpis/orders when you come back.")
                return _loop_verdict(status, None, {}, {})

            typer.echo("[5/5] Order completed - the MES books the finished lot and confirms to the ERP")
            deadline = asyncio.get_event_loop().time() + 15
            completion = None
            while completion is None and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(1)
                completion = _order_completion(
                    (await http.get(f"{erp_url}/confirmations")).json(), code)

            genealogy = (await http.get(f"{api_url}/execution/genealogy/{code}", headers=operator)).json()
            # OEE over the actual production window, so availability means something
            window_hours = max((asyncio.get_event_loop().time() - released_at) / 3600, 30 / 3600)
            oee = (await http.get(f"{api_url}/kpis/oee/MIX01", params={"hours": window_hours}, headers=operator)).json()

            typer.echo("\n=== MES-TWIN full-loop demo: complete ===")
            if completion:
                over = float(completion.get("over_qty") or 0)
                typer.echo(f"ERP received the confirmation: {completion['good_qty']:.0f} good / "
                           f"{completion['scrap_qty']:.0f} scrap"
                           + (f" ({over:.0f} over the ordered "
                              f"{completion['ordered_qty']:.0f})" if over else "")
                           + f", lot {completion.get('lot') or 'none'} "
                             f"(ref {completion.get('erp_reference')})")
            else:
                typer.echo(f"ERP has not received the completion for {code} yet "
                           "- check /erp/outbox; the operation confirmations may already be there.")

            unassigned = (await http.get(f"{api_url}/execution/unassigned", headers=operator)).json()
            if unassigned["total"]:
                typer.echo(f"Unassigned production: {unassigned['good_total']:.0f} good / "
                           f"{unassigned['scrap_total']:.0f} scrap counted with no order open "
                           f"to book them against, in {unassigned['total']} bookings.")
            consumed = ", ".join(f"{c['lot']} ({c['quantity']:g})" for c in genealogy["consumed"])
            produced = ", ".join(f"{p['lot']} ({p['quantity']:g})" for p in genealogy["produced"])
            typer.echo(f"Genealogy: consumed {consumed} -> produced {produced}")
            typer.echo(f"OEE MIX01 (last hour): availability {oee['availability']:.0%}, "
                       f"performance {oee['performance']:.0%}, quality {oee['quality']:.0%} "
                       f"-> OEE {oee['oee']:.0%}" if oee["oee"] is not None else f"OEE MIX01: {oee}")
            typer.echo("The audit trail, tag history, and ERP message log for all of this are in the database.")
            return _loop_verdict(status, completion, genealogy, oee)
    finally:
        # Teardown: clients first (their worker threads drain against live
        # servers), then a graceful server exit. Noise here is not signal.
        asyncio.get_running_loop().set_exception_handler(lambda _loop, _ctx: None)
        clients = [t for t in tasks if t.get_name() in ("erp-sync", "opc-agent")]
        servers = [t for t in tasks if t not in clients]
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        await asyncio.sleep(1.0)
        api_server.should_exit = erp_server.should_exit = True
        await asyncio.sleep(0.5)
        for task in servers:
            task.cancel()
        await asyncio.gather(*servers, return_exceptions=True)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"fsmes {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """FactorySemantics MES."""


@app.command()
def info() -> None:
    """Report what this installation is and which modules are present."""
    typer.echo(f"fsmes {__version__}")
    typer.echo(f"installed at  {Path(__file__).parent}")

    # Modules register through the `fsmes.modules` entry-point group, so this
    # reads what is actually installed rather than a hardcoded list — the same
    # question a support call opens with, answered without a code change when
    # the first module ships.
    found = sorted(ep.name for ep in entry_points(group="fsmes.modules"))
    typer.echo(f"modules       {', '.join(found) if found else 'none (kernel only)'}")

    # Whether this installation may act on its plant is the first thing a
    # support call needs to know, so it is on the first screen it asks for.
    from fsmes import shadow

    state = shadow.summary()
    if state["shadow"]:
        typer.echo(f"shadow mode   ON — {state['outbound_paths_closed']} of "
                   f"{state['outbound_paths_total']} outbound paths closed")
        for line in textwrap.wrap(shadow.BANNER, 62):
            typer.echo(f"              {line}")
        typer.echo(f"              {shadow.HOW_TO_LEAVE}")
    else:
        typer.echo(f"shadow mode   off — this MES may act on its plant "
                   f"({shadow.SETTING}=true to watch only)")


@app.command()
def backup(
    out: Path = typer.Option(Path("backups"), "--out", help="Where the timestamped backup folder is written."),
) -> None:
    """Copy the database, the tag map and the OPC certificate into one folder.

    Safe to run while the plant is running: on SQLite the copy goes through
    SQLite's own online backup. It does not copy `.env` — that holds the OPC
    password and the signing key, and belongs wherever this plant already
    keeps secrets. The manifest names everything it did not copy.
    """
    from fsmes.backup import BackupError, back_up

    settings = get_settings()
    try:
        manifest = back_up(settings, out)
    except BackupError as exc:
        typer.echo(f"Backup refused: {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"Backup written to {manifest['folder']}")
    database = manifest["database"]
    if database.get("copied"):
        rows = database.get("rows", {})
        typer.echo(f"  database   {database['file']}  revision {database.get('revision') or 'none recorded'}"
                   f"  ({len(rows)} tables, {sum(rows.values())} rows)")
    else:
        typer.echo(f"  database   NOT IN THIS BACKUP — {database['why_not']}")
    for entry in manifest["files"]:
        typer.echo(f"  file       {entry['file']}  ({entry['bytes']} bytes)")
    totals = manifest["totals"]
    typer.echo(f"  {totals['files']} files, {totals['bytes']} bytes in total.")
    typer.echo("Not copied, and why:")
    for entry in manifest["not_copied"]:
        typer.echo(f"  {entry['what']:<16}{entry['why']}")
    if not database.get("copied"):
        typer.echo("This backup does not contain your production record. Back the database up separately.")


@app.command()
def restore(
    folder: Path = typer.Argument(..., help="A folder written by `fsmes backup`."),
    force: bool = typer.Option(False, "--force", help="Write over a database that is already there."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Check the backup and say what it would write."),
) -> None:
    """Put a backup back, onto the paths this machine's settings name.

    Every file is checked against the hash the manifest recorded before
    anything is written. Restoring over a database that already exists needs
    `--force`, and the row counts printed afterwards are read from the
    restored file, not from the manifest.
    """
    from fsmes.backup import BackupError
    from fsmes.backup import restore as restore_backup

    settings = get_settings()
    try:
        receipt = restore_backup(settings, folder, force=force, dry_run=dry_run)
    except BackupError as exc:
        typer.echo(f"Restore refused: {exc}")
        raise typer.Exit(1) from exc

    verb = "would restore" if dry_run else "restored"
    typer.echo(f"{folder}: verified. {verb} {receipt['totals']['files']} files.")
    for entry in receipt["restored"]:
        typer.echo(f"  {entry['file']}  ->  {entry['to']}")
    database = receipt["database"]
    if database is None:
        typer.echo("  no database in this backup — restore it from your server's own dump.")
    else:
        rows = database["rows"]
        typer.echo(f"  database at {database['to']}: revision {database.get('revision') or 'none recorded'},"
                   f" {len(rows)} tables, {sum(rows.values())} rows")
    typer.echo("Still to be provided by hand:")
    for entry in receipt["not_copied"]:
        typer.echo(f"  {entry['what']:<16}{entry['why']}")
    if not dry_run:
        typer.echo("Then bring the schema to this version: `fsmes init-db`.")


@app.command()
def plant(
    names: list[str] = typer.Argument(..., help="Plant name(s), or 'all'."),
    action: str = typer.Argument(..., help="init | start | stop | status | run | migrate"),
    speed: float | None = typer.Option(
        None,
        help="Replay speed multiplier. 60 replays a scripted hour in a minute.",
    ),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Run several independent MES plants side by side.

    Each plant is a separate MES with its own database, OPC UA server and
    dashboard. The registry is labs/multiplant/plants.toml - adding a plant is
    an entry there plus a tag map and line data, never a code change.

        fsmes plant all init      create every schema and seed every plant
        fsmes plant all start     bring them all up
        fsmes plant all status    who is alive and answering
        fsmes plant all stop      shut them down
        fsmes plant bottling run  foreground supervisor (what systemd runs)
        fsmes plant all migrate   bring every stopped plant's database to the
                                  current schema (backup first, receipt after)
    """
    from fsmes import plant as plants_mod

    valid = {"init", "start", "stop", "status", "run", "migrate"}
    if action not in valid:
        typer.echo(f"Unknown action '{action}'. Expected one of: {', '.join(sorted(valid))}.")
        raise typer.Exit(2)

    where = plants_mod.find_root(root)
    registry = plants_mod.load_registry(where)

    try:
        targets = plants_mod.resolve(list(names), registry)
    except KeyError as exc:
        typer.echo(str(exc).strip('"'))
        raise typer.Exit(2) from exc

    if action == "run":
        if len(targets) != 1:
            typer.echo("`run` supervises exactly one plant - name it explicitly.")
            raise typer.Exit(2)
        name = targets[0]
        raise typer.Exit(plants_mod.run(name, registry[name], where, speed) or 0)

    for name in targets:
        cfg = registry[name]
        if action == "init":
            plants_mod.init(name, cfg, where, echo=typer.echo)
        elif action == "start":
            plants_mod.start(name, cfg, where, speed, echo=typer.echo)
        elif action == "stop":
            plants_mod.stop(name, cfg, where, echo=typer.echo)
        elif action == "status":
            plants_mod.status(name, cfg, where, echo=typer.echo)
        elif action == "migrate":
            plants_mod.migrate(name, cfg, where, echo=typer.echo)


@app.command()
def score(
    name: str = typer.Argument(..., help="Plant to run and score."),
    speed: float = typer.Option(60.0, help="Replay speed. 60 replays a scripted hour in a minute."),
    out: Path | None = typer.Option(None, help="Write the scorecard JSON here."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Run one plant through its scripted hour and score what it reported.

    The simulator knows what it did to the line, so this asks the question a
    test suite cannot: not "did the code raise" but "did the MES tell the
    truth about the plant?"

    The run is ephemeral - its own database and its own ports - so it never
    disturbs a plant you already have running.
    """
    import json as _json

    from fsmes import plant as plants_mod
    from fsmes.sim.runner import scored_run

    where = plants_mod.find_root(root)
    registry = plants_mod.load_registry(where)
    if name not in registry:
        typer.echo(f"Unknown plant '{name}'. Known: {', '.join(registry)}.")
        raise typer.Exit(2)

    try:
        card = scored_run(name, registry[name], where, speed, echo=typer.echo)
    except FileNotFoundError as exc:
        # A plant whose line is generated by code rather than described by data
        # has no ground truth to score against. Say that plainly instead of
        # showing a traceback.
        typer.echo(str(exc))
        typer.echo("")
        typer.echo("Only plants whose line is described by a line.json can be scored.")
        raise typer.Exit(2) from exc

    m = card["metrics"]
    typer.echo("")
    typer.echo(f"  scorecard for {name} (seed {card['seed']}, {card['duration_s']}s at {speed}x)")
    mis = m["planned_stop_misclassified"]
    typer.echo(f"    planned stops misclassified as downtime : "
               f"{'unknown - not observed' if mis is None else mis}")
    rec = m["breakdown_recall"]
    typer.echo(f"    scripted breakdowns detected            : "
               f"{'unknown - not observed' if rec is None else f'{rec:.0%}'}"
               f"  ({m['faults_scored']}/{m['faults_scripted']} scored)")

    from fsmes.sim import store

    run_id = store.record(card)
    typer.echo(f"    recorded as run {run_id}")

    if out:
        Path(out).write_text(_json.dumps(card, indent=2, default=str), encoding="utf-8")
        typer.echo(f"    written to {out}")


@app.command("sim-generate")
def sim_generate(
    config: Path = typer.Argument(..., help="Line description (JSON)."),
    out: Path | None = typer.Option(None, help="Output folder (default: out/ beside the config)."),
) -> None:
    """Generate a line's CSV tables from its description.

    The line is data: stations, rates, buffers and the script of things going
    wrong all live in the config, and the model never changes. Deterministic -
    the same seed gives byte-identical output.

        fsmes sim-generate labs/kepsim/line.json
    """
    from fsmes.sim.generate import generate

    target = Path(out) if out else Path(config).parent / "out"
    cfg = generate(Path(config), target)
    stations = len(cfg.get("stations", []))
    typer.echo(f"{Path(config).name}: {stations} stations x "
               f"{cfg.get('duration_s', 3600)}s -> {target}")


@app.command()
def sweep(
    name: str = typer.Argument(..., help="Plant to sweep."),
    knob: list[str] = typer.Option(
        ..., "--knob", "-k",
        help="key=v1,v2,v3 — e.g. -k buffer_capacity=6,12,20. Repeatable.",
    ),
    station: str | None = typer.Option(None, help="Scope station-level knobs to one machine."),
    speed: float = typer.Option(60.0, help="Replay speed."),
    out: Path | None = typer.Option(None, help="Write the comparison JSON here."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Vary a line, score every variant, and put the results side by side.

    One scored run says whether the MES is honest about one plant. A sweep says
    which conditions it stops being honest under, which is the question that
    tells you what to build next.

        fsmes sweep machining -k buffer_capacity=2,6,20
        fsmes sweep machining -k down_seconds=30,180,600 --station Mill
    """
    import json as _json
    import tempfile

    from fsmes import plant as plants_mod
    from fsmes.sim import store
    from fsmes.sim import sweep as sweep_mod
    from fsmes.sim.runner import scored_run

    where = plants_mod.find_root(root)
    registry = plants_mod.load_registry(where)
    if name not in registry:
        typer.echo(f"Unknown plant '{name}'. Known: {', '.join(registry)}.")
        raise typer.Exit(2)
    cfg = registry[name]

    grid: dict = {}
    for item in knob:
        if "=" not in item:
            typer.echo(f"Expected key=values, got {item!r}.")
            raise typer.Exit(2)
        key, raw = item.split("=", 1)
        values: list = []
        for token in raw.split(","):
            token = token.strip()
            values.append(int(token) if token.lstrip("-").isdigit()
                          else float(token) if token.replace(".", "", 1).lstrip("-").isdigit()
                          else token)
        grid[key.strip()] = values
    if station:
        grid["_station"] = station

    try:
        variants = sweep_mod.expand(grid)
    except KeyError as exc:
        typer.echo(str(exc).strip('"'))
        raise typer.Exit(2) from exc

    base_line = where / Path(cfg["replay_dir"]).parent / "line.json"
    if not base_line.is_file():
        typer.echo(f"No line description at {base_line}; this plant cannot be swept.")
        raise typer.Exit(2)

    typer.echo(f"sweeping {name}: {len(variants)} variants")
    cards = []
    workdir = Path(tempfile.mkdtemp(prefix=f"fsmes-sweep-{name}-"))
    for index, variant in enumerate(variants, 1):
        text = sweep_mod.label(variant)
        typer.echo(f"\n[{index}/{len(variants)}] {text}")
        line = sweep_mod.write_variant(base_line, variant, workdir / f"v{index}.json")
        # The variant's own generated data, so each point is a different line
        # rather than the same CSVs relabelled.
        data = workdir / f"v{index}-out"
        from fsmes.sim.generate import generate
        generate(line, data, write_docs=False)

        point = dict(cfg, replay_dir=str(data))
        card = scored_run(name, point, where, speed, line_json=line, echo=typer.echo)
        card["variant"] = text
        store.record(card, variant={k: v for k, v in variant.items()
                                    if not k.startswith("_")})
        cards.append(card)

    result = sweep_mod.compare(cards)
    typer.echo("")
    typer.echo(f"  {'variant':<28} {'misclassified':>13} {'recall':>8} {'lag':>8}")
    for row in result["runs"]:
        rec = "unknown" if row["breakdown_recall"] is None else f"{row['breakdown_recall']:.0%}"
        mis = "unknown" if row["planned_stop_misclassified"] is None else row["planned_stop_misclassified"]
        lag = "-" if row["mean_lag_sim_s"] is None else f"{row['mean_lag_sim_s']:.0f}s"
        typer.echo(f"  {row['variant']:<28} {mis:>13} {rec:>8} {lag:>8}")
    typer.echo("")
    typer.echo(f"  {result['verdict']}")

    if out:
        Path(out).write_text(_json.dumps(result, indent=2, default=str), encoding="utf-8")
        typer.echo(f"  written to {out}")


@app.command()
def runs(
    plant: str | None = typer.Option(None, help="Only this plant."),
    limit: int = typer.Option(15, help="How many."),
) -> None:
    """Recent scored runs, newest first."""
    from fsmes.sim import store

    rows = store.recent(plant, limit)
    if not rows:
        typer.echo("No scored runs recorded yet. Try: fsmes score machining")
        return
    typer.echo(f"  {'id':>4}  {'plant':<11} {'when':<20} {'misc':>5} {'recall':>7} {'lag':>7}  variant")
    for r in rows:
        rec = "unknown" if r["breakdown_recall"] is None else f"{r['breakdown_recall']:.0%}"
        mis = "?" if r["planned_stop_misclassified"] is None else r["planned_stop_misclassified"]
        lag = "-" if r["mean_lag_sim_s"] is None else f"{r['mean_lag_sim_s']:.0f}s"
        when = (r["scored_at"] or "")[:19].replace("T", " ")
        typer.echo(f"  {r['id']:>4}  {r['plant']:<11} {when:<20} {mis:>5} {rec:>7} {lag:>7}  "
                   f"{r['variant'] or ''}")


@app.command()
def rollup(
    days: int = typer.Option(1, help="How far back to sweep."),
    out: Path | None = typer.Option(None, help="Where to write the note."),
    quiet: bool = typer.Option(False, help="Write the note without printing it."),
) -> None:
    """Summarise recent scored runs into one note.

    Every number is computed from the results store; the local model is handed
    those numbers and asked only to say what changed. A model that does
    arithmetic on your behalf is one that will get it wrong quietly.
    """
    from fsmes.sim.rollup import gather, narrate, write_report

    facts = gather(days)
    if facts["runs_today"] == 0:
        typer.echo(f"No scored runs in the last {days} day(s). Nothing to report.")
        raise typer.Exit(0)

    summary = narrate(facts)
    path = write_report(facts, summary, out)
    typer.echo(f"wrote {path}")
    if not quiet:
        typer.echo("")
        typer.echo(path.read_text(encoding="utf-8"))


@app.command("prune-evidence")
def prune_evidence(
    days: int = typer.Option(30, help="Keep evidence newer than this."),
) -> None:
    """Delete raw run evidence past its keep-window.

    The understanding - scorecards, metrics, summaries - is kilobytes and kept
    forever. The evidence is megabytes and prunable: you can always run the
    scenario again, but you cannot re-derive last month's answer.
    """
    from fsmes.sim import store

    removed = store.prune_evidence(days)
    typer.echo(f"pruned evidence from {removed} run(s) older than {days} days")


@app.command("run-operations")
def run_operations(
    inspect_every: float = typer.Option(float(os.environ.get("MES_OPS_INSPECT_EVERY", "8")),
                                        help="Seconds between quality checks (MES_OPS_INSPECT_EVERY)."),
    issue_every: float = typer.Option(float(os.environ.get("MES_OPS_ISSUE_EVERY", "25")),
                                      help="Seconds between material issues (MES_OPS_ISSUE_EVERY)."),
    inspect_all: bool = typer.Option(os.environ.get("MES_OPS_INSPECT_ALL", "false").lower() == "true",
                                     help="Record every specification each pass, not one (MES_OPS_INSPECT_ALL)."),
    seed: int = typer.Option(0, help="Deterministic activity."),
) -> None:
    """Generate the shop-floor activity a PLC never reports.

    Inspections and material issue, performed through the public API exactly
    as an operator's browser or an agent would. Without it the quality
    screens, the non-conformance flow and genealogy are empty pages on top of
    a working database.
    """
    import asyncio as _asyncio

    from fsmes.sim.operations import run as run_floor

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, "operations")
    # The cadences are the floor's, in line time: a plant that inspects every
    # fifteen minutes still does when the simulation runs its hour in six.
    speed = float(os.environ.get("MES_SIM_SPEED") or 1.0)
    if speed > 0:
        inspect_every, issue_every = inspect_every / speed, issue_every / speed
    _asyncio.run(run_floor(settings, inspect_every=inspect_every,
                           issue_every=issue_every, seed=seed, inspect_all=inspect_all,
                           password=settings.operator_password))


@app.command("draft-instructions")
def draft_instructions(
    approve: bool = typer.Option(False, help="Approve each draft as it is written."),
) -> None:
    """Draft a work instruction for every specification and routing.

    The local model writes the prose from facts the plant already holds - the
    tolerance, the unit, the route. Everything arrives as an unapproved draft
    marked with the model that wrote it, because a procedure nobody read and
    approved is not a procedure.

    --approve is for seeding a demonstration plant. Do not use it on a real one.
    """
    from fsmes.db import session_scope
    from fsmes.services import documents, drafting

    with session_scope() as session:
        results = drafting.draft_all(session, actor="system")
        for r in results:
            if r.get("error"):
                typer.echo(f"  {r['code']}: {r['error']}")
            elif r.get("skipped"):
                typer.echo(f"  {r['code']}: {r['skipped']}")
            else:
                typer.echo(f"  {r['code']} rev {r['revision']} — {r['words']} words")
                if approve:
                    documents.approve(session, r["code"], r["revision"], actor="system")
                    typer.echo("      approved")
    typer.echo(f"{len(results)} instruction(s) considered.")


@app.command("design-log")
def design_log(
    conversation: int | None = typer.Option(None, help="Print one in full."),
    export: Path | None = typer.Option(None, help="Write every conversation as Markdown here."),
) -> None:
    """Design conversations held on the screens themselves.

    Every one is kept. A design decision argued out on a Tuesday and lost by
    Friday is worse than one never made - and a note read six months later is
    meaningless without the screen it was about, so the context travels with it.
    """
    from fsmes.services import design

    if conversation is not None:
        found = design.transcript(conversation)
        if not found:
            typer.echo(f"No conversation {conversation}.")
            raise typer.Exit(1)
        typer.echo(f"# {found['title']}\n")
        typer.echo(f"_{found['route']} · {found['who']} · {found['started_at'][:19]}_\n")
        for turn in found["turns"]:
            who = "Scott" if turn["role"] == "user" else (turn["model"] or "assistant")
            typer.echo(f"**{who}** — {turn['ts'][:19]}\n\n{turn['text']}\n")
        return

    rows = design.conversations()
    if not rows:
        typer.echo("No design conversations yet. Open a screen and press Design.")
        return

    if export:
        target = Path(export)
        target.mkdir(parents=True, exist_ok=True)
        for row in rows:
            found = design.transcript(row["id"])
            slug = found["route"].strip("/").replace("/", "-") or "dashboard"
            path = target / f"{found['started_at'][:10]}-{slug}-{found['id']}.md"
            lines = [
                "---",
                f"date: {found['started_at'][:10]}",
                f"screen: {found['route']}",
                "tags: [fsmes, design]",
                "---",
                "",
                f"# {found['title']}",
                "",
                f"_{found['route']} · {found['who']} · {found['started_at'][:19]}_",
                "",
            ]
            for turn in found["turns"]:
                who = "Scott" if turn["role"] == "user" else (turn["model"] or "assistant")
                lines += [f"### {who} — {turn['ts'][:19]}", "", turn["text"], ""]
            path.write_text("\n".join(lines), encoding="utf-8")
        typer.echo(f"exported {len(rows)} conversation(s) to {target}")
        return

    typer.echo(f"  {'id':>4}  {'when':<20} {'turns':>5}  screen")
    for row in rows:
        typer.echo(f"  {row['id']:>4}  {row['updated_at'][:19]:<20} {row['turns']:>5}  "
                   f"{row['route']}  {(row['title'] or '')[:48]}")


@app.command("design-pending")
def design_pending(
    everything: bool = typer.Option(
        False, "--all", help="Ignore the marks and re-read judged conversations too."),
    conversation: int | None = typer.Option(None, help="Just this one."),
) -> None:
    """Design-chat turns nobody has judged yet — the input to /design-triage.

    Prints them as Markdown because the reader is a Claude Code session, and
    what it needs is the conversation as it was actually held: who said what,
    on which screen, in order.
    """
    from fsmes.services import design_triage as triage

    waiting = triage.pending(everything=everything, conversation=conversation)
    if not waiting:
        typer.echo("Nothing pending. Every design conversation has been triaged.")
        return

    existing = {n.get("conversation"): n["slug"] for n in triage.notes()}
    for chat in waiting:
        typer.echo(f"## Conversation {chat['id']} — {chat['route']}")
        typer.echo(f"_{chat['plant']} · {chat['who']} · {chat['started_at'][:19]}_")
        if str(chat["id"]) in existing:
            typer.echo(f"_Already has a backlog note: {existing[str(chat['id'])]}_")
        typer.echo("")
        for turn in chat["turns"]:
            who = "Scott" if turn["role"] == "user" else (turn["model"] or "assistant")
            typer.echo(f"### turn {turn['id']} — {who}\n\n{turn['text']}\n")
        typer.echo(f"_last turn: {chat['last_turn']}_\n")


@app.command("design-verdict")
def design_verdict(
    conversation: int = typer.Option(..., help="The conversation this idea came from."),
    slug: str = typer.Option(..., help="Short kebab-case name; becomes the filename."),
    title: str = typer.Option(..., help="The idea in one line."),
    status: str = typer.Option(
        ..., help="approved | in-progress | built | needs-guidance | rejected | done"),
    summary: str = typer.Option("", help="One sentence of verdict."),
    body_file: Path | None = typer.Option(None, help="Markdown assessment to put in the note."),
    branch: str | None = typer.Option(None, help="The branch it was built on, if it was."),
    turns: str = typer.Option("", help="Comma-separated turn ids this idea came from."),
    say: Path | None = typer.Option(
        None, help="Markdown to append into the conversation, so Scott reads the "
                   "verdict where he raised the idea. Defaults to the summary."),
    chat: bool = typer.Option(True, "--chat/--no-chat"),
    advance: bool = typer.Option(True, "--advance/--no-advance"),
) -> None:
    """Record one triaged idea: a backlog note, and the verdict back in the chat."""
    from fsmes.services import design
    from fsmes.services import design_triage as triage

    ids = [int(t) for t in turns.replace(",", " ").split()] if turns else []
    found = design.transcript(conversation)
    if not found:
        typer.echo(f"No conversation {conversation}.")
        raise typer.Exit(1)

    try:
        path = triage.write_note(
            slug=slug, title=title, status=status, conversation=conversation,
            summary=summary, route=found["route"], plant=found["plant"],
            turns=ids, branch=branch,
            body=(body_file.read_text(encoding="utf-8") if body_file else ""))
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    typer.echo(f"note   {path}")

    if chat:
        text = say.read_text(encoding="utf-8") if say else f"**{status}** — {summary or title}"
        triage.record_verdict(conversation, text)
        typer.echo(f"chat   verdict appended to conversation {conversation}")

    if advance:
        latest = triage.latest_turn(conversation)
        triage.advance(conversation, latest)
        typer.echo(f"mark   conversation {conversation} judged to turn {latest}")


@app.command("design-backlog")
def design_backlog(
    status: str | None = typer.Option(None, help="Only this status."),
) -> None:
    """Every design idea and where it got to."""
    from fsmes.services import design_triage as triage

    rows = [n for n in triage.notes() if not status or n.get("status") == status]
    if not rows:
        typer.echo("No design ideas recorded yet. Run /design-triage after a chat.")
        return
    typer.echo(f"  {'status':<15} {'updated':<11} {'branch':<26} idea")
    for note in rows:
        typer.echo(f"  {note.get('status', '?'):<15} {note.get('updated', ''):<11} "
                   f"{(note.get('branch') or '-'):<26} {note.get('title', note['slug'])}")


@app.command("ai-status")
def ai_status_cmd() -> None:
    """What the local AI layer is doing, and whether anyone would notice it
    stop. The same facts the Ops screen's Local AI panel shows."""
    from fsmes.services import ai_status

    out = ai_status.status()
    if not out.get("enabled"):
        typer.echo("Local AI is disabled on this machine (MES_LOCAL_AI=0).")
        return

    o = out["ollama"]
    if o["reachable"]:
        loaded = ", ".join(f"{m['model']}" for m in o["loaded"]) or "nothing loaded"
        typer.echo(f"model server  up at {o['url']}  ({loaded})")
    else:
        typer.echo(f"model server  UNREACHABLE at {o['url']}")
    if out["gpu"]:
        g = out["gpu"]
        typer.echo(f"gpu           {g['vram_used_mb']}/{g['vram_total_mb']} MB, "
                   f"{g['utilization_pct']}% busy, {g['temperature_c']}\u00b0C")
    typer.echo("")
    typer.echo(f"  {'job':<22} {'last':<10} {'state':<8} output")
    for c in out["consumers"]:
        typer.echo(f"  {c['name']:<22} {c['last'] or '-':<10} {c['state']:<8} {c['output']}")
        if c.get("note"):
            typer.echo(f"  {'':<22} {'':<10} {'':<8} {c['note']}")
    typer.echo("")
    typer.echo("budget: " + " \u2192 ".join(out["budget"]))


@app.command("ui-check")
def ui_check_cmd(
    base: str = typer.Option(
        os.environ.get("MES_UI_CHECK_BASE", "http://127.0.0.1:8010"),
        help="The plant whose screens to crawl."),
    accept: bool = typer.Option(
        False, "--accept",
        help="Make the current look the accepted look. Run this in the "
             "branch that deliberately changed it, and commit the baselines "
             "with it."),
    theme: list[str] = typer.Option(None, help="Only these themes (repeatable)."),
    file_findings: bool = typer.Option(
        True, "--file/--no-file",
        help="Write new findings into docs/design/backlog as inbox notes."),
) -> None:
    """Crawl every screen in every theme and report what is broken or drifted.

    Deterministic - no model is involved, and the GPU is not touched. Broken
    links, console errors and failed requests are facts on any run; style
    drift is judged against the accepted baselines in tests/ui/baselines/.
    Exit code 1 when there are findings, so a script can gate on it.
    """
    from fsmes.sim import ui_check

    themes = tuple(theme) if theme else ui_check.THEMES
    typer.echo(f"crawling {base} ({', '.join(themes)})")
    run = ui_check.crawl(base, themes=themes, echo=typer.echo)

    if accept:
        for path in ui_check.accept(run):
            typer.echo(f"accepted {path}")
        typer.echo("The current look is now the baseline. Commit these files "
                   "in the branch that made it right.")
        return

    findings = ui_check.compare(run)
    if not findings:
        typer.echo("clean: every link answers, no console errors, no drift "
                   "from the accepted look.")
        ui_check.file_new([], echo=typer.echo)   # records the clean run
        return

    for f in findings:
        typer.echo(f"  {f['kind']:<15} {f['what']}")
    if file_findings:
        ui_check.file_new(findings, echo=typer.echo)
    typer.echo(f"{len(findings)} finding(s). Screenshots: {run['screenshots']}")
    raise typer.Exit(1)


@app.command("agent-eval")
def agent_eval_cmd(
    plant: str = typer.Argument(..., help="Plant to ask about (see labs/multiplant/plants.toml)."),
    agent: str = typer.Option("claude", help="claude (headless Claude Code with only the fsmes MCP server) "
                                              "or none (score the empty answer - proves the machinery)."),
    scenario: str | None = typer.Option(None, help="One scenario id; default is every scenario."),
    summary: bool = typer.Option(False, "--summary", help="Print the pass rate over kept results and exit."),
) -> None:
    """Can an agent, given only the tools, answer what the plant knows?

    The scorecard asks whether the MES told the truth about the plant; this
    asks whether an agent working through the product's agent surface alone
    can find it. Each scenario's right answer is computed from the API at
    the moment of asking; the agent gets the question and the MCP server,
    nothing else; the answer is scored and kept in
    ~/.local/share/fsmes/agent-evals.jsonl so the pass rate is a trend.
    """
    from fsmes.sim import agent_eval

    if summary:
        rows = agent_eval.recent(limit=500)
        s = agent_eval.summary(rows)
        rate = "—" if s["pass_rate"] is None else f"{s['pass_rate']:.0%}"
        typer.echo(f"{s['runs']} kept result(s); pass rate {rate}")
        for sid, rate in sorted(s["by_scenario"].items()):
            typer.echo(f"  {sid:<18} {rate:.0%}")
        for name, rate in sorted(s["by_agent"].items()):
            typer.echo(f"  agent {name:<12} {rate:.0%}")
        return

    with agent_eval.plant_client(plant) as client:
        api = agent_eval.Api(client)
        runner = agent_eval.claude_agent if agent == "claude" else agent_eval.no_agent
        chosen = [agent_eval.scenario(scenario)] if scenario else None
        typer.echo(f"agent eval on {plant} with {agent}:")
        rows = agent_eval.run(plant, api=api, agent=runner, scenarios=chosen, agent_name=agent, echo=typer.echo)
    passed = sum(1 for r in rows if r["pass"])
    typer.echo(f"{passed}/{len(rows)} passed")


@app.command("autoloop")
def autoloop_cmd(
    agent: bool = typer.Option(
        True, "--agent/--no-agent",
        help="--no-agent scores, crawls and writes the brief and note, but "
             "does not start the Claude Code session - for testing the "
             "machinery without spending a session."),
) -> None:
    """The night shift, on demand: score both plants, gather every finding,
    brief a headless Claude Code agent, write the morning note.

    Nightly this runs from fsmes-autoloop.timer. The agent's contract is
    .claude/skills/night-shift/SKILL.md: at most three builds, UI-scoped
    changes may merge themselves, everything hard queues for Scott.
    Kill switch: touch ~/.local/share/fsmes/autoloop.off
    """
    from fsmes.sim import autoloop

    note = autoloop.run(echo=typer.echo, with_agent=agent)
    typer.echo(f"done - read {note}")
