"""The ``fsmes`` command line — every part of the MES is operated from here.

The commands below came in with the engine and were reached as ``mes`` in
MES-TWIN. They keep their names; only the executable changed.
"""

import asyncio
import contextlib
import os
import textwrap
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path

import typer

from fsmes import __version__
from fsmes.config import Settings, get_settings

# The console's default port, from the module that serves it rather than
# typed again here: a Typer default is read at import time, and two spellings
# of one port is how the console ended up inside the simulator's range.
from fsmes.fleet.console import PORT as CONSOLE_PORT
from fsmes.logging import setup_logging

# The labelled set's own defaults, from the module that states them rather
# than typed a second time here: a Typer default is read at import time, and
# two spellings of one number is how the console ended up inside the
# simulator's port range.
from fsmes.sim import labelled

app = typer.Typer(
    name="fsmes",
    help="FactorySemantics MES — modular, agent-native manufacturing execution.",
    no_args_is_help=True,
    add_completion=False,
)


#: The two options every command that touches a database now takes. A status
#: command that cannot be told which database is a status command that will
#: eventually report on the wrong one - which it did, on 2026-09-14, and a
#: script rolled a healthy plant back on the strength of it.
PACK_OPTION = typer.Option(None, "--pack", help="A pack directory; use the database its "
                                                "`[storage]` names.")
PLANT_OPTION = typer.Option(None, "--plant", help="A plant in the fleet file; use the "
                                                  "database the fleet gives it.")


def _which_database(pack: Path | None, plant: str | None) -> tuple[str, str]:
    """Which database this invocation is about, or the sentence saying nothing
    here can tell - printed, and then a non-zero exit. Never a silent default."""
    from fsmes import storage
    from fsmes.pack import fleet as fleet_file
    from fsmes.pack import format as pack_format

    try:
        if pack is not None and plant is not None:
            raise storage.Unknown("--pack and --plant name two different databases; pass one.")
        if pack is not None:
            return pack_format.database_url(pack_format.read(pack))
        if plant is not None:
            return fleet_file.database_url(plant)
        return storage.of_process()
    except (storage.Unknown, pack_format.PackError) as exc:
        typer.echo(f"Which database: unknown. {exc}")
        raise typer.Exit(2) from None


@app.command()
def init_db(
    pack: Path | None = PACK_OPTION,
    plant: str | None = PLANT_OPTION,
) -> None:
    """Create or upgrade the database schema (runs the migrations the package ships).

    Says which database it is about on the first line, before it changes
    anything - without `--pack` or `--plant` that is this process's own
    default, which is almost never a plant's, and creating a stray SQLite
    file beside a real database is the mistake this line exists to stop.

    The migrations travel inside the wheel, so this does the same thing on a
    plant PC that installed from PyPI as it does in a source checkout. Before
    2026-09-10 it did not: with no `alembic.ini` in the working directory it
    created any missing tables and ran no `ALTER` at all, then said the schema
    was up to date. A database made that way is recognised here and stamped.
    """
    from fsmes.schema import SchemaError, upgrade_database

    url, source = _which_database(pack, plant)
    from fsmes import storage

    typer.echo(f"Looking at {storage.redacted(url)} ({source}).")
    try:
        upgrade_database(url, echo=typer.echo)
    except SchemaError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None


@app.command()
def db_status(
    pack: Path | None = PACK_OPTION,
    plant: str | None = PLANT_OPTION,
) -> None:
    """What revision a database is at, and whether that is the current one.

    **Which database is the first line, every time.** With `--pack` it is the
    one that pack's `[storage]` names, password read from the file the pack
    names; with `--plant` it is the one the fleet file gives that plant; with
    neither it is this process's own setting, and the line says that it is
    only the default. On 2026-09-14 this command answered "there is no
    database yet" about a process default while the plant's own PostgreSQL
    sat at head one line above, and the script reading it rolled a healthy
    plant back.

    Exits non-zero when the database is behind, was never stamped, or did not
    answer at all, so a deployment script - and the release gate, which
    upgrades a database made by the previous release and then asks this - can
    act on the answer rather than read it. A database nobody reached is never
    reported as one that is empty.
    """
    from fsmes import storage

    url, source = _which_database(pack, plant)
    reading = storage.look(url, source)
    typer.echo(reading.looking_at())
    typer.echo(f"Current: {reading.revision or ('unknown' if not reading.answered else 'not stamped')}")
    typer.echo(f"Head:    {reading.head or 'unknown'}")
    typer.echo(reading.sentence())
    if not reading.at_head:
        raise typer.Exit(1)


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
def add_user(code: str, name: str, password: str, role: str | None = None) -> None:
    """Create a sign-in account (roles: viewer, operator, supervisor, admin).

    Leave `--role` out and the account gets the role this plant starts people
    on - `[admin] default_new_account_role`, which ships `operator`.
    """
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


@app.command("run-log-sink", hidden=True)
def run_log_sink(path: str, max_bytes: int = 0) -> None:
    """Write a plant's console log, with a ceiling on it.

    Started by `fsmes fleet start`, which gives the plant's processes this
    one's stdin to write to. Hidden because nobody runs it by hand: it is
    the answer to "four processes share one log file, so who rolls it".
    """
    import sys

    from fsmes.fleet import logsink

    # A sink that dies must never take the plant with it: a broken pipe or
    # a full disk ends this process and the plant runs on, logging nowhere.
    with contextlib.suppress(OSError):
        logsink.pump(sys.stdin.buffer, Path(path), max_bytes or logsink.MAX_BYTES)


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


inbound_app = typer.Typer(
    help="Inbound events: what other systems tell this MES that it cannot observe.")
app.add_typer(inbound_app, name="inbound")


def _inbound_mapping():
    """The configured column mapping, or a sentence saying what is wrong."""
    from fsmes.integrations.inbound.folder import MappingError, load_mapping

    settings = get_settings()
    try:
        return settings, load_mapping(settings.inbound_mapping_file)
    except MappingError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc


@inbound_app.command("check")
def inbound_check() -> None:
    """Say whether the folders and the column mapping are usable, in words.

    What it cannot check is the plant's data, and it says so rather than
    counting an empty inbox as a working interface.
    """
    from fsmes.integrations.inbound.folder import READABLE, folders_for

    settings, mappings = _inbound_mapping()
    typer.echo(f"Mapping {settings.inbound_mapping_file}: "
               f"{len(mappings)} stream(s) — {', '.join(sorted(mappings))}.")
    waiting = 0
    for stream, mapping in sorted(mappings.items()):
        folders = folders_for(settings.inbound_dir, stream)
        exists = folders.inbox.is_dir()
        files = [p for p in folders.inbox.iterdir()
                 if p.is_file() and p.suffix.lower() in READABLE] if exists else []
        waiting += len(files)
        typer.echo(f"  {stream:<9} {folders.inbox}  "
                   + ("exists" if exists else "does not exist yet; `watch` will make it"))
        typer.echo(f"            supplied by {mapping.source!r} ({mapping.source_kind}), "
                   f"timestamps in {mapping.timezone or 'whatever zone each row states'}")
        typer.echo(f"            {len(files)} file(s) waiting")
    typer.echo(f"{waiting} file(s) waiting in total. Nothing has been read; run "
               "`fsmes inbound watch --once` to read them.")

    from fsmes.integrations.inbound.mqtt import (
        SubscriptionError,
        load_event_subscriptions,
        load_tag_subscriptions,
        streams_and_topics,
    )

    mode = (settings.inbound_mqtt_mode or "off").lower()
    if mode == "off":
        typer.echo("\nMQTT: MES_INBOUND_MQTT_MODE is 'off'; no broker is subscribed to.")
        return
    try:
        tags = load_tag_subscriptions(settings.tag_map_file)
        events = load_event_subscriptions(mappings)
    except SubscriptionError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc
    lines = streams_and_topics(tags, events)
    typer.echo(f"\nMQTT: {settings.inbound_mqtt_broker_url} as {settings.inbound_mqtt_client_id!r}, "
               f"QoS {settings.inbound_mqtt_qos}.")
    for line in lines:
        typer.echo(f"  {line}")
    typer.echo(f"{len(lines)} topic filter(s) in total: {len(tags)} carrying tag values, "
               f"{len(events)} carrying events. Nothing has been subscribed to; run "
               "`fsmes inbound subscribe` to listen.")


@inbound_app.command("watch")
def inbound_watch(once: bool = False) -> None:
    """Read whatever has been dropped in the inbound folders, forever.

    `--once` makes a single pass and reports it — what a cron-shaped
    deployment wants, and what a person testing a new export wants.
    Every run states its totals: rows read, recorded, already seen, and
    rejected with the first reason for each.
    """
    import time

    from fsmes.db import session_scope
    from fsmes.integrations.inbound.folder import run_once

    settings, mappings = _inbound_mapping()
    setup_logging(settings.log_level, settings.log_dir, "inbound-watch")
    root = Path(settings.inbound_dir)
    if once:
        for line in run_once(session_scope, root, mappings).render():
            typer.echo(line)
        return
    typer.echo(f"Watching {root} every {settings.inbound_poll_seconds}s. Ctrl-C to stop.")
    while True:
        report = run_once(session_scope, root, mappings)
        if report.files:
            for line in report.render():
                typer.echo(line)
        time.sleep(settings.inbound_poll_seconds)


def _inbound_sql_streams():
    """The configured queries, or a sentence saying what is wrong with them."""
    from fsmes.integrations.inbound.sql import SqlConfigError, load_streams

    settings = get_settings()
    try:
        return settings, load_streams(settings.inbound_sql_file)
    except SqlConfigError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc


def _one_stream(streams: dict, stream: str | None):
    """Every configured query, or the one named — or a sentence saying it is not there."""
    if stream is None:
        return streams
    if stream not in streams:
        typer.echo(f"NOT OK  no {stream!r} query is configured; there are "
                   f"{len(streams)}: {', '.join(sorted(streams)) or 'none'}")
        raise typer.Exit(1)
    return {stream: streams[stream]}


@inbound_app.command("sql-check")
def inbound_sql_check(stream: str | None = None) -> None:
    """Say whether each configured query is usable, and read nothing into the MES.

    It does connect and it does run the query, because a query that only
    looks right is what puts a plant three weeks behind. Every row it gets is
    mapped and reported as what *would* be recorded. Nothing is written here
    and no cursor moves.
    """
    from fsmes.db import session_scope
    from fsmes.integrations.inbound.folder import to_event
    from fsmes.integrations.inbound.sql import read_batch, watermark_of

    settings, streams = _inbound_sql_streams()
    streams = _one_stream(streams, stream)
    typer.echo(f"Configuration {settings.inbound_sql_file}: "
               f"{len(streams)} quer{'y' if len(streams) == 1 else 'ies'} — "
               f"{', '.join(sorted(streams))}.")
    problems = 0
    for name, one in sorted(streams.items()):
        typer.echo(f"  {name}")
        if one.description:
            typer.echo(f"    {one.description}")
        typer.echo(f"    supplied by {one.source!r} ({one.mapping.source_kind}), timestamps in "
                   f"{one.mapping.timezone or 'whatever zone each row states'}")
        typer.echo(f"    cursor on {one.watermark_column!r} ({one.watermark_type}), "
                   f"every {one.poll_seconds}s, at most {one.max_rows} rows a pass")
        with session_scope() as session:
            watermark = watermark_of(session, one)
        typer.echo(f"    reading from after {watermark!r}"
                   + ("" if watermark != one.start_from else " (its configured start; it has not run yet)"))
        try:
            rows, connection = read_batch(one, watermark)
        except Exception as exc:  # a plant wants the sentence, not the traceback
            problems += 1
            typer.echo(f"    NOT OK  the query could not be run: {exc}")
            continue
        for done in connection.done:
            typer.echo(f"    read-only: {done}")
        for could_not in connection.could_not:
            typer.echo(f"    NOT read-only: {could_not}")
        would_record, bad = 0, []
        for number, row in enumerate(rows, start=1):
            try:
                to_event({k: (None if v is None else str(v)) for k, v in row.items()}, one.mapping)
            except ValueError as exc:
                bad.append(f"      row {number}: {exc}")
                continue
            would_record += 1
        typer.echo(f"    {len(rows)} row(s) waiting; {would_record} map onto the contract, "
                   f"{len(bad)} do not")
        for line in bad:
            typer.echo(line)
        if bad:
            problems += 1
    typer.echo("Nothing was recorded and no cursor moved. Run `fsmes inbound poll-sql --once` to "
               "read them for real — and remember that a row mapping cleanly is not the same as "
               "this MES being willing to record it: a label for a stop it never saw is still "
               "refused, and that refusal is a finding.")
    if problems:
        raise typer.Exit(1)


@inbound_app.command("poll-sql")
def inbound_poll_sql(once: bool = False, stream: str | None = None) -> None:
    """Read the plant's own read-only queries on a schedule, forever.

    `--once` makes a single pass over every configured query and reports it,
    which is what a cron-shaped deployment wants and what a person testing a
    new query wants. Each query has its own interval; without `--once` this
    sleeps until the next one is due.

    Every pass states its totals — rows read, recorded, already seen, and
    rejected with the reason for each — and where each cursor stood before
    and after. A cursor that is held by a row nothing could be done with says
    so, by name, on every pass until the row is fixed or stepped over.
    """
    import time

    from fsmes.db import session_scope
    from fsmes.integrations.inbound.sql import poll_once, poll_stream

    settings, streams = _inbound_sql_streams()
    streams = _one_stream(streams, stream)
    setup_logging(settings.log_level, settings.log_dir, "inbound-poll-sql")
    rejects_root = Path(settings.inbound_dir)
    if once:
        report = poll_once(session_scope, streams, rejects_root=rejects_root)
        for line in report.render():
            typer.echo(line)
        if report.failed:
            # A query that could not be run at all is a broken interface, and a
            # cron-shaped deployment learns that from the exit code. A held
            # cursor is not: it is a finding about one row, it is already on
            # the page above, and it would otherwise cry wolf every pass.
            raise typer.Exit(1)
        return
    intervals = ", ".join(f"{name} every {one.poll_seconds}s" for name, one in sorted(streams.items()))
    typer.echo(f"Polling {len(streams)} quer{'y' if len(streams) == 1 else 'ies'}: {intervals}. "
               "Ctrl-C to stop.")
    due = {name: 0.0 for name in streams}
    while True:
        now = time.monotonic()
        for name in sorted(streams):
            if now < due[name]:
                continue
            report = poll_stream(session_scope, streams[name], rejects_root=rejects_root)
            due[name] = time.monotonic() + streams[name].poll_seconds
            if report.rows or report.error:
                for line in report.render():
                    typer.echo(line)
        time.sleep(min(1.0, min(streams[n].poll_seconds for n in streams)))


@inbound_app.command("sql-watermark")
def inbound_sql_watermark(stream: str | None = None, set_to: str = typer.Option(None, "--set"),
                          force: bool = False) -> None:
    """Show where each query has read through, and move one by hand if asked.

    Showing is safe. `--set` is not: rows between where the cursor stands and
    where it is being put will never be read, because the query will not ask
    for them again. So it names what it is skipping and refuses without
    `--force`, which is a person saying they meant it.
    """
    from fsmes.db import session_scope
    from fsmes.integrations.inbound.sql import set_watermark, watermark_row

    _, streams = _inbound_sql_streams()
    streams = _one_stream(streams, stream)
    if set_to is None:
        with session_scope() as session:
            for name, one in sorted(streams.items()):
                row = watermark_row(session, one)
                if row is None:
                    typer.echo(f"{name}: has not run; it will start after {one.start_from!r} "
                               "(from the configuration)")
                    continue
                typer.echo(f"{name}: read through {row.position!r} ({row.position_type}), "
                           f"{row.rows_seen} row(s) taken in total, last moved {row.updated_at}")
                if row.held_reason:
                    typer.echo(f"  HELD by {row.held_key!r}: {row.held_reason}")
        return
    if len(streams) != 1:
        typer.echo("NOT OK  --set moves one cursor; name it with --stream")
        raise typer.Exit(1)
    name, one = next(iter(streams.items()))
    with session_scope() as session:
        row = watermark_row(session, one)
        was = row.position if row is not None else one.start_from
        typer.echo(f"{name} has read through {was!r}; --set would move it to {set_to!r}.")
        typer.echo(f"Every row of {one.watermark_column!r} between those two values will never be "
                   "read: the query asks only for rows after the cursor, and nothing here goes back "
                   "for them. They stay in the supplying system, unread, and this MES will have no "
                   "record that they existed.")
        if not force:
            typer.echo("Nothing was changed. Add --force if that is what you meant.")
            raise typer.Exit(1)
        set_watermark(session, one, set_to)
    typer.echo(f"{name}: cursor moved from {was!r} to {set_to!r} by hand. Whatever was between them "
               "was not read and is not recorded.")


oee_app = typer.Typer(help="OEE, and how much of the window it was measured over.")
app.add_typer(oee_app, name="oee")


def _oee_window(session, window: str):
    """Turn `8h`, `90m`, `current`, `previous` or `2026-09-17/NIGHT` into the
    two instants a ledger is built between, and the sentence naming it.

    A window a person cannot say out loud is a window nobody audits, so the
    spellings are the ones the analysis API already takes plus a plain span.
    """
    from datetime import timedelta

    from fsmes.db import utcnow
    from fsmes.services import calendar as calendar_service

    text = (window or "8h").strip()
    span = text.lower()
    if span.endswith(("h", "m")) or span.replace(".", "", 1).isdigit():
        number = span[:-1] if span[-1] in "hm" else span
        try:
            amount = float(number)
        except ValueError as exc:
            raise typer.BadParameter(
                f"{window!r} is not a window. Say a span (`8h`, `90m`) or a shift "
                "(`current`, `previous`, `2026-09-17/NIGHT`).") from exc
        if amount <= 0:
            raise typer.BadParameter("a window has to have some time in it.")
        hours = amount / 60 if span.endswith("m") else amount
        end = utcnow()
        return end - timedelta(hours=hours), end, f"the last {text}"
    shift = calendar_service.resolve_shift(session, text)
    now = utcnow()
    return (shift.starts_at, max(shift.starts_at, min(shift.ends_at, now)),
            f"shift {shift.key()}" + (" (still running)" if shift.ends_at > now else ""))


@oee_app.command("explain")
def oee_explain(
    equipment: str = typer.Argument(..., help="The machine's code."),
    window: str = typer.Argument("8h", help="A span (`8h`, `90m`) or a shift "
                                            "(`current`, `previous`, `2026-09-17/NIGHT`)."),
    every_interval: bool = typer.Option(False, "--every-interval",
                                        help="Print every interval, however short. The "
                                             "default prints the longest fifty and says "
                                             "how many it did not print."),
) -> None:
    """The coverage ledger for one machine, as a table you can argue with.

    Every second of the window, in one disposition, with the rule that put it
    there and — where nobody was watching — the cause. The totals are at the
    bottom, and so is the check that they add up to the window: an OEE window
    whose seconds do not add up is a bug, not a rounding difference.

    Nothing here is recomputed for the table. It is the same ledger the
    availability figure on the screen is derived from, which is the point: a
    plant engineer who disagrees with the number can see which interval they
    disagree about.
    """
    from fsmes.db import session_scope
    from fsmes.services import NotFound, masterdata
    from fsmes.services import coverage as coverage_service

    with session_scope() as session:
        try:
            machine = masterdata.get_equipment(session, equipment)
        except NotFound as exc:
            typer.echo(f"NOT OK  {exc}")
            raise typer.Exit(1) from exc
        start, end, said = _oee_window(session, window)
        account = coverage_service.ledger(session, equipment_code=machine.code,
                                          equipment_id=machine.id, start=start, end=end)

        typer.echo(f"{machine.code} — {machine.name}")
        typer.echo(f"  {said}: {start:%Y-%m-%d %H:%M:%S} to {end:%Y-%m-%d %H:%M:%S} UTC "
                   f"({_hms(account.window_seconds)})")
        typer.echo("")

        shown = account.intervals
        hidden = 0
        if not every_interval and len(shown) > 50:
            longest = sorted(shown, key=lambda i: -i.seconds)[:50]
            keep = {id(i) for i in longest}
            hidden = len(shown) - len(longest)
            shown = tuple(i for i in shown if id(i) in keep)

        typer.echo(f"  {'from':>8}  {'to':>8}  {'seconds':>9}  disposition / cause")
        for interval in shown:
            where = interval.cause or interval.disposition
            typer.echo(f"  {interval.start:%H:%M:%S}  {interval.end:%H:%M:%S}  "
                       f"{interval.seconds:9.1f}  {where}")
            typer.echo(f"  {'':>8}  {'':>8}  {'':>9}    rule: {interval.rule}")
            if interval.detail:
                typer.echo(f"  {'':>8}  {'':>8}  {'':>9}    said: {interval.detail}")
        # Every list states its total, including this one.
        typer.echo(f"  {len(account.intervals)} interval(s)" +
                   (f", {hidden} shorter one(s) not printed — use --every-interval"
                    if hidden else ""))
        typer.echo("")

        for name, seconds in account.seconds_by_disposition.items():
            if name == coverage_service.NOT_OBSERVED:
                continue
            typer.echo(f"  {name:<30} {_hms(seconds)}")
        typer.echo(f"  {coverage_service.NOT_OBSERVED:<30} {_hms(account.not_observed_seconds)}")
        for cause, seconds in account.seconds_by_cause.items():
            typer.echo(f"    {cause:<28} {_hms(seconds)}")
        typer.echo("")

        total = sum(i.seconds for i in account.intervals)
        typer.echo(f"  observed                       {_hms(account.observed_seconds)}")
        typer.echo(f"  window                         {_hms(account.window_seconds)}")
        coverage_value = account.coverage
        typer.echo(f"  coverage                       "
                   f"{'unknown' if coverage_value is None else f'{coverage_value:.1%}'}")
        availability = account.availability
        typer.echo(f"  availability (run ÷ observed)  "
                   f"{'unknown' if availability is None else f'{availability:.1%}'}")
        if account.sample_interval_source:
            typer.echo(f"  observed at                    {account.sample_interval_source}")

        the_floor = coverage_service.floor()
        note = coverage_service.withhold(coverage_value, the_floor)
        typer.echo("")
        if note:
            typer.echo(f"  This plant's pack asks for {the_floor:.0%} coverage, so the screens "
                       "and the API")
            typer.echo("  report this window's figures as unknown and print this ledger instead.")
        elif the_floor:
            typer.echo(f"  This plant's pack asks for {the_floor:.0%} coverage and this window "
                       "clears it.")
        else:
            typer.echo(f"  {coverage_service.NO_FLOOR.capitalize()}.")

        if abs(total - account.window_seconds) > 0.001 or not account.tiles_exactly():
            typer.echo("")
            typer.echo(f"NOT OK  the intervals add up to {total:.3f} s and the window is "
                       f"{account.window_seconds:.3f} s. An OEE window whose seconds do not "
                       "add up is a bug; please report it.")
            raise typer.Exit(1)


def _hms(seconds: float) -> str:
    """Seconds as a plant reads them, with the raw number kept beside it."""
    whole = round(seconds)
    return f"{whole // 3600:d}h {whole % 3600 // 60:02d}m {whole % 60:02d}s  ({seconds:,.1f} s)"


shadow_app = typer.Typer(help="Running beside the MES in charge, and how well the two agreed.")
app.add_typer(shadow_app, name="shadow")


@shadow_app.command("scorecard")
def shadow_scorecard(
    incumbent: Path = typer.Option(..., "--incumbent",
                                   help="The incumbent MES's bookings, exported as CSV or JSON."),
    ours: Path | None = typer.Option(None, "--ours",
                                     help="This MES's confirmations: a file or a folder of them. "
                                          "Omit to read this MES's own outbox instead."),
    mapping_file: Path | None = typer.Option(None, "--map",
                                             help="Mapping file: the export's column headings, "
                                                  "its order and equipment codes, tolerances."),
    json_out: Path | None = typer.Option(None, "--json", help="Write the scorecard JSON here."),
    html_out: Path | None = typer.Option(None, "--html",
                                         help="Write the self-contained HTML report here."),
    quantity_tolerance: float | None = typer.Option(
        None, "--quantity-tolerance", help="Units within which two quantities agree. "
                                           "Overrides the mapping file."),
    seconds_tolerance: float | None = typer.Option(
        None, "--seconds-tolerance", help="Seconds within which two times agree. "
                                          "Overrides the mapping file."),
) -> None:
    """Compare what the incumbent MES booked with what this MES would have sent.

    This is the instrument a shadow run is judged with. It reads two
    records of the same period - a file a person exported from the MES in
    charge, and this MES's own confirmations - and reports, per order and
    per operation, where they agree and where they do not.

    It never says which side is right. It names the differences, states
    what it could not compare, and leaves the judgement to the plant.
    """
    from fsmes.integrations.erp import scorecard as scoring
    from fsmes.integrations.erp import scorecard_html
    from fsmes.integrations.erp.incumbent import Mapping, MappingError
    from fsmes.integrations.erp.incumbent import read as read_export

    if not incumbent.exists():
        typer.echo(f"NOT OK  {incumbent} does not exist.")
        raise typer.Exit(1)
    try:
        mapping = Mapping.load(mapping_file)
    except MappingError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc
    try:
        export = read_export(incumbent, mapping)
    except Exception as exc:
        typer.echo(f"NOT OK  {incumbent.name} could not be read: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc

    if ours is not None:
        if not ours.exists():
            typer.echo(f"NOT OK  {ours} does not exist.")
            raise typer.Exit(1)
        record = scoring.read_confirmations(ours)
        our_side = str(ours)
    else:
        from fsmes.db import session_scope

        with session_scope() as session:
            record = scoring.read_outbox(session)
        our_side = "this MES's outbox"

    tolerances = scoring.Tolerances.from_mapping(mapping, quantity_tolerance, seconds_tolerance)
    card = scoring.compare(export, record, tolerances)

    typer.echo(f"Comparing {incumbent.name} with {our_side}.")
    for line in card.render():
        typer.echo(line)

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(card.to_json(), encoding="utf-8")
        typer.echo(f"  wrote {json_out}")
    if html_out:
        html_out.parent.mkdir(parents=True, exist_ok=True)
        html_out.write_text(scorecard_html.render(card), encoding="utf-8")
        typer.echo(f"  wrote {html_out}")

    if not card.orders_both and export.orders and record.orders:
        # Two records with orders in them and not one code in common is
        # almost always the mapping file, not the plant.
        typer.echo("")
        typer.echo("Not one order code appears in both records, so nothing was compared. The "
                   "export's codes and this MES's are different sets of strings; map them in "
                   "the mapping file's `orders` section.")
        raise typer.Exit(1)



@inbound_app.command("subscribe")
def inbound_subscribe() -> None:
    """Listen to the plant's MQTT broker, forever.

    The other half of the unified namespace: `fsmes uns publish` tells the
    broker what this MES recorded, and this hears what the plant's gateways
    publish — counters, state words and process values that never touch an
    OPC UA server, and downtime labels, quality results and counts where a
    broker already carries them.

    It publishes nothing. Every totals line it prints is over everything it
    has received since it started, refusals grouped with a count each.
    """
    from fsmes.db import session_scope
    from fsmes.integrations.inbound.mqtt import (
        Ingest,
        MqttSource,
        SubscriptionError,
        load_event_subscriptions,
        load_tag_subscriptions,
        run,
    )
    from fsmes.integrations.uns.transport import BrokerAddress

    settings, mappings = _inbound_mapping()
    mode = (settings.inbound_mqtt_mode or "off").lower()
    if mode == "off":
        typer.echo("MES_INBOUND_MQTT_MODE is 'off' — nothing to run. Set it to 'mqtt' and point "
                   "MES_INBOUND_MQTT_BROKER_URL at your broker.")
        raise typer.Exit(1)
    if mode != "mqtt":
        typer.echo(f"NOT OK  unknown inbound MQTT mode {settings.inbound_mqtt_mode!r} (known: off, mqtt)")
        raise typer.Exit(1)
    if settings.inbound_mqtt_client_id == settings.uns_client_id:
        # Not a style point: a broker evicts the older session when two
        # clients connect with one id, so this would have the subscriber and
        # the publisher disconnecting each other all shift.
        typer.echo(f"NOT OK  the subscriber and the publisher would both connect as "
                   f"{settings.uns_client_id!r}. A broker disconnects the older session when two "
                   "clients share an id. Set MES_INBOUND_MQTT_CLIENT_ID to something else.")
        raise typer.Exit(1)

    try:
        tags = load_tag_subscriptions(settings.tag_map_file)
        events = load_event_subscriptions(mappings)
    except SubscriptionError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc
    if not tags and not events:
        typer.echo(f"Nothing is mapped to a topic. Add an 'mqtt' section to {settings.tag_map_file} "
                   f"for tag values, or a 'topic' to a stream in {settings.inbound_mapping_file} "
                   "for events. This MES does not guess topics.")
        raise typer.Exit(1)

    setup_logging(settings.log_level, settings.log_dir, "inbound-subscribe")
    address = BrokerAddress(settings.inbound_mqtt_broker_url, settings.inbound_mqtt_username,
                            settings.inbound_mqtt_password)
    ingest = Ingest(tags, events, source=settings.inbound_mqtt_source or f"mqtt:{address.host}")
    source = MqttSource(address, client_id=settings.inbound_mqtt_client_id,
                        qos=settings.inbound_mqtt_qos)
    typer.echo(f"Listening to {settings.inbound_mqtt_broker_url} on {len(ingest.topics)} topic "
               f"filter(s). Nothing is published. Ctrl-C to stop.")

    def say(report) -> None:
        for line in report.render():
            typer.echo(line)

    try:
        asyncio.run(run(source, ingest, session_scope,
                        on_report=say, report_seconds=settings.inbound_mqtt_report_seconds))
    except KeyboardInterrupt:
        say(ingest.report)


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
    from fsmes import shadow
    from fsmes.db import session_scope
    from fsmes.seed import seed_demo_plant

    settings = get_settings()
    if shadow.enabled(settings):
        # The demo starts a simulated line, a mock ERP and a REST adapter of
        # its own, around make_adapter and every other gate. An installation
        # in shadow mode is pointed at a real plant; it must not also be
        # running a fake one, and it must not book the fake one's numbers.
        typer.echo(f"{shadow.BANNER}\n\n`fsmes demo` runs a simulated line and a mock ERP "
                   f"in this process, which is not what a plant in shadow mode is for. "
                   f"{shadow.HOW_TO_LEAVE}")
        raise typer.Exit(2)
    setup_logging("WARNING", settings.log_dir, "demo")  # keep the console for the story
    init_db(pack=None, plant=None)
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
                # The line has made the number and it has not stopped: the
                # counters keep coming and the MES keeps booking them to this
                # order, which is what makes an over-run the true one
                # (decision 0029). Finishing the order is SCOTT's act, and
                # here he does it once the last step has the ordered quantity
                # - every step, not only the ones that have reached it, since
                # what he is finishing is the order.
                last = order["operations"][-1] if order["operations"] else None
                running = [op for op in order["operations"] if op["status"] == "running"]
                if last and last["good_qty"] >= order["quantity"] and running:
                    typer.echo("      SCOTT finishes the order - the line made its number; "
                               "reaching a quantity is not the same fact as being finished")
                    for op in running:
                        (await http.post(
                            f"{api_url}/workorders/{code}/operations/{op['seq']}/complete",
                            headers=operator)).raise_for_status()

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
    """Report which plant this is, what the installation is, and which
    modules are present."""
    from fsmes import identity

    who = identity.summary(get_settings())
    typer.echo(f"fsmes {__version__}")
    typer.echo(f"installed at  {Path(__file__).parent}")
    # Which plant, first: it is the question a support call opens with and
    # the one a fleet of look-alike installations makes impossible to answer
    # from the prompt.
    typer.echo(f"plant         {who['plant']}  ({who['profile']} profile)")
    typer.echo(f"time zone     {who['timezone_says']}")
    # What this plant calls things, when it calls anything differently. Here
    # because a support call that opens "our jobs are stuck" is a support call
    # about work orders, and nobody on either end of it should have to guess
    # that. Display only: the pack that set these was refused if any of them
    # renamed something a number depends on.
    if who["words"]:
        said = ", ".join(f"{term} -> {word}" for term, word in sorted(who["words"].items()))
        typer.echo(f"words         {said}")

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
    typer.echo(f"  plant      {manifest['plant']}  ({manifest['profile']} profile, "
               f"{manifest['timezone_says']})")
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
    taken_from = receipt["from_plant"]
    if taken_from is None:
        typer.echo(f"  taken from a backup written before backups named their plant; "
                   f"this machine is {receipt['onto_plant']}.")
    elif taken_from != receipt["onto_plant"]:
        typer.echo(f"  ** this backup is plant {taken_from} and this machine is "
                   f"{receipt['onto_plant']}. **")
    else:
        typer.echo(f"  plant {taken_from}.")
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


pack_app = typer.Typer(
    help="Plant packs: the one directory that says which plant this is.")
app.add_typer(pack_app, name="pack")


def _pack_or_exit(directory: Path):
    from fsmes.pack import format as pack_format

    try:
        return pack_format.read(directory)
    except pack_format.PackError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(2) from None


@pack_app.command("check")
def pack_check(
    directory: Path = typer.Argument(..., help="The pack directory (the one with plant.toml)."),
) -> None:
    """Say everything wrong with a plant pack, without touching a plant.

    No database, no network, no plant: it reads the directory and refuses an
    unknown key, a bad time zone, a renamed protected term, a module this
    version does not have, a `requires` this release does not satisfy, and a
    file the pack names that is missing or that its own reader will not
    accept. Every problem, not the first - a person fixing a pack beside a
    line should need one round trip.

    What it cannot prove without a plant - that the OPC endpoint answers,
    that the database is reachable - it reports as unknown rather than
    counting it as passing.
    """
    from fsmes.pack import check as checker
    from fsmes.pack import format as pack_format

    _pack_or_exit(directory)
    try:
        report = checker.check(directory)
    except pack_format.PackError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(2) from None
    typer.echo(f"Checking {directory} against pack format {pack_format.FORMAT}.")
    for line in report.render():
        typer.echo(line)
    if not report.ok:
        typer.echo(f"Not a usable pack: {len(report.problems)} problem(s), "
                   f"{len(report.unknowns)} thing(s) this check cannot know.")
        raise typer.Exit(1)
    typer.echo(f"Usable, as far as a file can say: {report.checked} file(s) read, "
               f"nothing refused, {len(report.unknowns)} thing(s) only a running plant "
               "can answer.")


@pack_app.command("apply")
def pack_apply(
    directory: Path = typer.Argument(..., help="The pack directory."),
    data_dir: Path | None = typer.Option(None, help="Where to record what was applied."),
) -> None:
    """Make the plant this pack describes match this pack.

    Checks first and refuses on failure; adopts the pack's settings; brings
    the schema to head (pack before database, decision 0022); seeds the
    master data the pack carries, or says it carries none; and records what
    was applied, so `fsmes pack status` can answer whether the plant has
    drifted from it. Safe to run twice.
    """
    from fsmes.pack import apply as applier
    from fsmes.pack import format as pack_format

    _pack_or_exit(directory)
    typer.echo(f"Applying {directory}.")
    try:
        applier.apply(directory, into=data_dir, echo=typer.echo)
    except applier.Refused as exc:
        for line in exc.report.render():
            typer.echo(line)
        typer.echo("Refused - nothing was written. Fix the pack and run "
                   f"`fsmes pack check {directory}`.")
        raise typer.Exit(1) from None
    except pack_format.PackError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(2) from None


@pack_app.command("status")
def pack_status(
    directory: Path = typer.Argument(..., help="The pack directory."),
    data_dir: Path | None = typer.Option(None, help="Where the record was written."),
) -> None:
    """Which pack this plant runs, whether it has drifted, and its schema.

    The schema line is about **this pack's** database - the one its
    `[storage] database_url` names, with the password read from the file the
    pack names - and the line above it says which database that was. Before
    2026-09-14 it was about whatever database this process happened to be
    configured for, which is how it reported "never migrated" about a plant
    that `fsmes pack apply` had migrated a minute earlier.

    Exits non-zero when the files on disk no longer make the fingerprint that
    was applied, or when the database is behind the schema head or did not
    answer, so a deployment script can act on the answer rather than read it.
    A plant that has never been applied says so - never applied is not the
    same as no drift, and a database nobody reached is not one that is empty.
    """
    from fsmes.pack import apply as applier

    _pack_or_exit(directory)
    state = applier.status(directory, into=data_dir)
    for line in state.render():
        typer.echo(line)
    if state.drifted or not state.at_head:
        raise typer.Exit(1)


@pack_app.command("migrate")
def pack_migrate(
    source: Path = typer.Argument(..., help="A pack directory, or a plant registry file."),
    plant_name: str | None = typer.Option(None, "--plant", help="Which plant in the registry."),
    out: Path | None = typer.Option(None, "--out", help="Where to write the pack."),
    root: Path | None = typer.Option(None, help="What the registry's relative paths are from."),
) -> None:
    """Bring a pack forward, or write one from a plant registry entry.

    The one step that exists is from a registry entry - what a plant was
    before packs - to format 1. It says what it moved, what it dropped and
    why, and what it could not know: a registry never held a time zone, so
    the pack is written without one and the receipt says to set it. Guessing
    it from this machine's clock would be inventing a fact about a plant.
    """
    from fsmes.pack import format as pack_format
    from fsmes.pack import migrate as migrator

    try:
        if source.is_dir():
            receipt = migrator.migrate(source)
        else:
            if not plant_name or out is None:
                typer.echo("A registry holds several plants: say which with --plant, and "
                           "where the pack goes with --out.")
                raise typer.Exit(2)
            receipt = migrator.from_registry(source, plant_name, out, root=root)
    except pack_format.PackError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(2) from None
    for line in receipt.render():
        typer.echo(line)
    if receipt.written:
        typer.echo(f"Now check it: `fsmes pack check {receipt.written.parent}`.")


fleet_app = typer.Typer(
    help="The plants this installation created, and may therefore manage.")
app.add_typer(fleet_app, name="fleet")


def _fleet_root(root: Path | None) -> Path:
    from fsmes import plant as plants_mod

    return plants_mod.find_root(root)


def _refused(exc: Exception) -> None:
    typer.echo(str(exc))
    raise typer.Exit(1) from None


@fleet_app.command("create")
def fleet_create(
    directory: Path = typer.Argument(..., help="The pack directory (the one with plant.toml)."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Build a plant from a pack, and record that this installation made it.

    Checks the pack and refuses on failure; makes the fleet's data directory;
    writes the plant a random instance id, which the plant returns on
    /health; applies the pack; and records the ownership. Only what this
    command created can be started, stopped or re-packed by `fsmes fleet` -
    every other plant is observed and cannot be touched.

    It starts nothing. The plant exists after this; `fsmes fleet start` runs
    it, when a person says so.
    """
    from fsmes.fleet import commands
    from fsmes.fleet import owned as ownership
    from fsmes.pack import format as pack_format

    where = _fleet_root(root)
    typer.echo(f"Creating a plant from {directory}.")
    try:
        commands.create(directory, root=where, echo=typer.echo)
    except (ownership.NotOwned, commands.Refused, ownership.OwnershipError) as exc:
        _refused(exc)
    except pack_format.PackError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(2) from None


@fleet_app.command("start")
def fleet_start(
    name: str = typer.Argument(..., help="A plant this installation owns."),
    speed: float | None = typer.Option(None, help="Replay speed multiplier."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Start an owned plant. Refuses any plant this installation did not create."""
    from fsmes.fleet import commands
    from fsmes.fleet import owned as ownership

    try:
        commands.start(name, root=_fleet_root(root), speed=speed, echo=typer.echo)
    except (ownership.NotOwned, commands.Refused, ownership.OwnershipError) as exc:
        _refused(exc)


@fleet_app.command("stop")
def fleet_stop(
    name: str = typer.Argument(..., help="A plant this installation owns."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
    force: bool = typer.Option(False, "--force", help=(
        "Stop a plant this installation created even though it has stopped "
        "answering. Ownership still has to hold.")),
) -> None:
    """Stop an owned plant. Refuses any plant this installation did not create.

    A plant that has stopped answering `/health` is refused without
    `--force`, and the refusal says whether its pid file still names live
    processes. That is the state a plant gets into when it is running and
    can no longer be talked to, and before 2026-09-14 the only way out of it
    was `kill`. `--force` is the way out. It gives up **liveness**, never
    ownership: a plant this installation did not create is refused with the
    flag exactly as it is without it.
    """
    from fsmes.fleet import commands
    from fsmes.fleet import owned as ownership

    try:
        commands.stop(name, root=_fleet_root(root), force=force, echo=typer.echo)
    except (ownership.NotOwned, commands.Refused, ownership.OwnershipError) as exc:
        _refused(exc)


@fleet_app.command("apply")
def fleet_apply(
    name: str = typer.Argument(..., help="A plant this installation owns."),
    directory: Path | None = typer.Option(None, "--pack", help="A different pack to apply."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Apply a pack to an owned, stopped plant.

    `fsmes pack check` runs first and a failing check refuses the apply. A
    plant that is still answering is refused too: applying a pack upgrades a
    database, and doing that underneath a live process is how a plant ends
    up half-upgraded.
    """
    from fsmes.fleet import commands
    from fsmes.fleet import owned as ownership
    from fsmes.pack import format as pack_format

    try:
        commands.apply(name, root=_fleet_root(root), directory=directory, echo=typer.echo)
    except (ownership.NotOwned, commands.Refused, ownership.OwnershipError) as exc:
        _refused(exc)
    except pack_format.PackError as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(2) from None


@fleet_app.command("status")
def fleet_status(
    name: str = typer.Argument(..., help="Any plant in this fleet, owned or watched."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """What one plant says about itself, and whether this installation owns it.

    Reads only, and says why when the answer is no. A plant that did not
    answer is `unknown`: never healthy, never down.
    """
    from fsmes.fleet import commands
    from fsmes.fleet import owned as ownership

    try:
        commands.status(name, root=_fleet_root(root), echo=typer.echo)
    except ownership.OwnershipError as exc:
        _refused(exc)


@fleet_app.command("console")
def fleet_console(
    host: str = typer.Option("127.0.0.1", help="Interface to serve the page on."),
    port: int = typer.Option(CONSOLE_PORT, help="Port to serve the page on."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Serve the fleet console: one page that observes every plant in the list.

    It reads. There is no control on the page and no path from it to
    `fsmes fleet`: a console is a long-running process on a port, and
    whatever it can do, whoever can reach that port can do. It holds no
    credential either - everything it asks a plant is a GET of an endpoint
    the plant answers without one.

    Loopback by default. A plant that did not answer is shown as unknown,
    never as healthy and never as down.
    """
    import uvicorn

    from fsmes.fleet.console import create_app

    where = _fleet_root(root)
    typer.echo(f"Fleet console on http://{host}:{port} - reading only.")
    uvicorn.run(create_app(where), host=host, port=port, log_level="info")


@fleet_app.command("list")
def fleet_list(
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Every plant this installation owns or watches, with the totals.

    The number recorded is never the number seen, so the line at the top
    says both: "N plants, M answered, K unknown; J owned".
    """
    from fsmes.fleet import commands
    from fsmes.fleet import owned as ownership

    try:
        commands.listing(root=_fleet_root(root), echo=typer.echo)
    except ownership.OwnershipError as exc:
        _refused(exc)


@fleet_app.command("plan")
def fleet_plan(
    as_json: bool = typer.Option(False, "--json", help="Print the plan as JSON, for a script."),
    with_password: bool = typer.Option(
        False, "--with-password",
        help="Include each database password, for a promote that must run pg_dump. "
             "JSON only; send it to a variable and never to a file."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """What a deployment script needs to know about every plant in this fleet.

    Where each pack is, where each database is and what kind it is, whether
    the plant simulates a line worth regenerating, and where to ask it
    whether it came back. `deploy/promote.sh` reads this rather than parsing
    the fleet file a second time in bash - which is how the 0.2.0 script came
    to look for a table the format no longer has.

    Reads only, and touches no plant. The password is left out unless it is
    asked for, so the plain output is safe to paste into an issue.
    """
    import json as json_lib

    from fsmes.pack import fleet as packs
    from fsmes.pack import format as pack_format
    from fsmes.pack import plan as planner

    if with_password and not as_json:
        typer.echo("`--with-password` is for a script: pass `--json` with it.")
        raise typer.Exit(2)
    try:
        answer = planner.plan(_fleet_root(root), with_password=with_password)
    except (packs.FleetError, pack_format.PackError, FileNotFoundError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(2) from None

    if as_json:
        typer.echo(json_lib.dumps(answer, indent=2, sort_keys=True))
        return
    typer.echo(f"{answer['fleet']}: {answer['count']} plants, "
               f"{answer['can_back_up']} this tooling can back up before migrating.")
    for row in answer["plants"]:
        storage = row["storage"]
        typer.echo(f"  {row['name']:<14} {row['health']:<26} {storage['kind']:<11} "
                   f"backup: {storage['backup']}")
        if storage.get("why"):
            typer.echo(f"      {storage['why']}")


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
    dashboard. The fleet file is labs/multiplant/fleet.toml - adding a plant is a
    pack directory and a line there, never a code change.

        fsmes plant all init      create every schema and seed every plant
        fsmes plant all start     bring them all up
        fsmes plant all status    who is alive and answering
        fsmes plant all stop      shut them down
        fsmes plant bottling run  foreground supervisor (what systemd runs)
        fsmes plant all migrate   bring every stopped plant's database to the
                                  current schema (backup first, receipt after)
    """
    from fsmes import plant as plants_mod
    from fsmes.pack import fleet

    valid = {"init", "start", "stop", "status", "run", "migrate"}
    if action not in valid:
        typer.echo(f"Unknown action '{action}'. Expected one of: {', '.join(sorted(valid))}.")
        raise typer.Exit(2)

    where = plants_mod.find_root(root)
    registry = fleet.load(where)

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
    from fsmes.pack import fleet
    from fsmes.sim.runner import scored_run

    where = plants_mod.find_root(root)
    registry = fleet.load(where)
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
    from fsmes.pack import fleet, repoint
    from fsmes.pack import format as pack_format
    from fsmes.sim import store
    from fsmes.sim import sweep as sweep_mod
    from fsmes.sim.runner import scored_run

    where = plants_mod.find_root(root)
    registry = fleet.load(where)
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

        # The plant is rebuilt from a copy of its pack pointed at this
        # variant's data, not from the compiled dictionary with one key
        # swapped. `MES_REPLAY_DIR` is compiled out of plant.toml, so patching
        # `replay_dir` alone left every variant replaying the pack's original
        # hour: three runs of one line, printed as a sweep. Recompiling the
        # copy makes the two agree by construction - the same rule, and now
        # the same code, as `fsmes lab`.
        try:
            pack_copy, _ = repoint.pointed_at(
                pack_format.read(Path(cfg["pack"])), workdir / f"v{index}-pack", data)
        except repoint.RepointError as exc:
            typer.echo(str(exc))
            raise typer.Exit(2) from exc
        point = fleet.compile_pack(pack_format.read(pack_copy))
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
    # Which data each row is actually about. Named rather than assumed,
    # because the bug this column exists to make visible - every variant
    # replaying one hour - looked exactly like a sweep that found nothing.
    typer.echo("")
    typer.echo("  data replayed:")
    for row in result["runs"]:
        typer.echo(f"    {row['variant']:<28} {row['replay_dir'] or 'unknown'}")
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
        typer.echo("No scored runs recorded yet. Try: fsmes score <plant>")
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
    finish_orders: bool = typer.Option(
        os.environ.get("MES_OPS_FINISH_ORDERS", "true").lower() != "false",
        help="Finish an order once the line has made its quantity, and release the next one "
             "in the book (MES_OPS_FINISH_ORDERS). Off for a scripted over-run."),
    seed: int = typer.Option(0, help="Deterministic activity."),
) -> None:
    """Generate the shop-floor activity a PLC never reports.

    Inspections, material issue and the shift supervisor's own work - closing
    non-conformances, finishing an order the line has made the number for,
    releasing the next order in the book - performed through the public API
    exactly as an operator's browser or an agent would. Without it the quality
    screens, the non-conformance flow and genealogy are empty pages on top of
    a working database, and a line runs one order for as long as the plant is
    up.

    It never invents an order. Decision 0029 is untouched: the MES still does
    not finish an order at its quantity. What finishes one here is the
    simulated shift supervisor, and the audit trail says so.
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
                           finish_orders=finish_orders,
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
        typer.echo(f"{s['runs']} kept result(s); pass rate {rate} (the check)")
        for sid, rate in sorted(s["by_scenario"].items()):
            typer.echo(f"  {sid:<18} {rate:.0%}")
        for name, rate in sorted(s["by_agent"].items()):
            typer.echo(f"  agent {name:<12} {rate:.0%}")
        # Beside the pass rate, labelled, and reading nothing into it: the
        # trend above is the deterministic one and this decides nothing.
        j = s["judgment"]
        if not j["asked"]:
            typer.echo(f"  judgment           not asked on any of {j['of']} kept result(s)")
        else:
            mean = "—" if j["mean_probability"] is None else f"{j['mean_probability']:.2f}"
            typer.echo(f"  judgment           asked on {j['asked']} of {j['of']}; mean "
                       f"probability the answer was exactly right {mean}"
                       + (f" ({', '.join(j['models'])})" if j["models"] else ""))
            for label, key in (("where the check passed", "mean_probability_where_the_check_passed"),
                               ("where the check failed", "mean_probability_where_the_check_failed")):
                value = j[key]
                typer.echo(f"    {label:<24} {'—' if value is None else f'{value:.2f}'}")
        return

    with agent_eval.plant_client(plant) as client:
        api = agent_eval.Api(client)
        runner = agent_eval.claude_agent if agent == "claude" else agent_eval.no_agent
        chosen = [agent_eval.scenario(scenario)] if scenario else None
        typer.echo(f"agent eval on {plant} with {agent}:")
        rows = agent_eval.run(plant, api=api, agent=runner, scenarios=chosen, agent_name=agent, echo=typer.echo)
    passed = sum(1 for r in rows if r["pass"])
    typer.echo(f"{passed}/{len(rows)} passed")


@app.command("config-audit")
def config_audit_cmd(
    domain: str = typer.Option(
        None, help="Only this domain: administration, process, controls, "
                   "quality, supply-chain, it, unassigned."),
    strong: bool = typer.Option(
        False, "--strong",
        help="Only the candidates with a hedging comment beside them or a "
             "name that says what they are. Every domain still states its "
             "full total."),
    scope: str = typer.Option(
        None, "--scope",
        help="Only the curated candidates whose answer belongs to this "
             "scope: general (one default across every plant, a product "
             "decision), plant (the plant's own answer, under its domain's "
             "Configuration tab) or object (a property of one tag, machine, "
             "material, gauge or order). Combine with --domain."),
    as_json: bool = typer.Option(
        False, "--json", help="The whole run, for diffing against the next one."),
) -> None:
    """Find the business judgments still hard-coded in the source.

    A configuration candidate is a number, mapping or fixed list that encodes
    a judgment a reasonable plant could make differently - the Cpk bar at
    1.33, the maintenance warning at 80% through an interval - rather than a
    physical fact. Decision 0035's test: ask what breaks if two plants answer
    differently.

    Reports each with its file, line, the literal, and the comment beside it,
    organised by the six configuration domains. It decides nothing; a person
    reads the list and applies the test. Rerun it after any change and diff
    the --json against the last run: what is new is what somebody just added.

    Beside the scan it carries the curated list - the candidates a person
    kept - and each of those carries a scope, which is whose answer it is:
    general, plant or object. Only a general candidate is a question for a
    maintainer; plant and object are routed to the plant's Configuration tab
    or to the object's own row, and are answered by the engineer who knows
    them. `--scope object` lists that second kind.

    Deterministic - no model, no network, no database, no plant.
    """
    from fsmes.sim import config_audit

    run = config_audit.scan()
    if as_json:
        typer.echo(config_audit.as_json(run))
        return
    if domain and domain not in config_audit.DOMAIN_TITLES:
        typer.echo(f"unknown domain {domain!r}; "
                   f"one of {', '.join(config_audit.DOMAIN_TITLES)}")
        raise typer.Exit(2)
    if scope and scope not in config_audit.SCOPE_TITLES:
        typer.echo(f"unknown scope {scope!r}; "
                   f"one of {', '.join(config_audit.SCOPE_TITLES)}")
        raise typer.Exit(2)
    for line in config_audit.as_text(run, only=domain, strong_only=strong,
                                     scope=scope):
        typer.echo(line)


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


lab_app = typer.Typer(
    help="Experiments: a plan, a run, and a directory somebody else can read.",
)
app.add_typer(lab_app, name="lab")


def _results_root(results: Path | None, root: Path | None) -> Path:
    from fsmes import plant as plants_mod
    from fsmes.lab.run import DEFAULT_RESULTS

    if results:
        return Path(results).expanduser().resolve()
    return (plants_mod.find_root(root) / DEFAULT_RESULTS).resolve()


@lab_app.command("run")
def lab_run(
    plan: Path = typer.Argument(..., help="The experiment plan (TOML)."),
    results: Path | None = typer.Option(None, help="Where run directories go."),
    speed: float | None = typer.Option(None, help="Override the plan's replay speed."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
    keep_evidence: bool = typer.Option(
        False, "--keep-evidence",
        help="Keep each ephemeral plant's working directory and logs. Says where they are."),
) -> None:
    """Run a plan: build each plant from its pack, play the scenario, measure.

    Every plant is ephemeral - its own database, its own ports on loopback,
    torn down when the questions have been asked - so a run never disturbs a
    plant you already have going. What it leaves behind is one directory: the
    plan, the line each plant played, the data generated from it, what each
    plant recorded, the scores with the truth beside every number, an HTML
    report, and an empty notes.md for what a person saw that no number caught.

        fsmes lab run labs/experiments/one-line-bad-hour.toml
    """
    from fsmes.lab import run as lab

    try:
        lab.run(Path(plan), _results_root(results, root), root=root,
                speed=speed, keep_evidence=keep_evidence, echo=typer.echo)
    except (lab.PlanError, FileNotFoundError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(2) from exc


@lab_app.command("list")
def lab_list(
    results: Path | None = typer.Option(None, help="Where run directories are."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Every run in the results directory, newest first."""
    from fsmes.lab import run as lab

    where = _results_root(results, root)
    runs = lab.listing(where)
    typer.echo(f"{len(runs)} run(s) in {where}")
    for entry in runs:
        withheld = (f", {entry['withheld']} verdict(s) withheld" if entry["withheld"] else "")
        typer.echo(f"  {entry['run']:<40} {entry['started_at']}  "
                   f"{entry['plants_run']}/{entry['plants_total']} plant(s){withheld}")


def _run_dir(run_id: str, results: Path | None, root: Path | None) -> Path:
    """A run directory, named or pointed at."""
    directory = Path(run_id).expanduser()
    if not directory.is_dir():
        directory = _results_root(results, root) / run_id
    return directory


@lab_app.command("note")
def lab_note(
    run_id: str = typer.Argument(..., help="A run directory's name, or a path to one."),
    text: str = typer.Argument(..., help="What you saw. Recorded verbatim."),
    screen: str = typer.Option("", "--screen", help="Which screen it is about - a route "
                                                    "like /dashboard/orders, or its name."),
    plant: str = typer.Option("", "--plant", help="Which plant in the run."),
    results: Path | None = typer.Option(None, help="Where run directories are."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Leave a note on a run from the terminal, tagged like one left on screen.

    For a run watched without a browser. It goes into the same design store
    with the same tags, so the run's report renders it beside the screen it
    names and `fsmes lab review` reads it with the rest.

        fsmes lab note 2026-09-14-one-line-bad-hour \
            "the orders list does not say how many there are" --screen /dashboard/orders

    A note made while the run is going is picked up when the run exports its
    feedback at the end. One made afterwards is picked up by
    `fsmes lab open`, which re-exports before it re-renders.
    """
    import getpass
    import json

    from fsmes.lab import feedback as lab_feedback
    from fsmes.services import design

    directory = _run_dir(run_id, results, root)
    name = directory.name
    conversation = design.start(
        route=screen if screen.startswith("/") else "",
        plant=None, who=getpass.getuser(), title=text,
        lab_run=name, lab_plant=plant or None, screen=screen or "typed at the terminal")
    design.add_turn(conversation, "user", text,
                    context={"source": "fsmes lab note", "run": name,
                             "plant": plant or None, "screen": screen or None})
    typer.echo(f"noted against {name}"
               f"{f' · {plant}' if plant else ''}"
               f"{f' · {screen}' if screen else ''} (conversation {conversation})")
    if (directory / "scores.json").is_file():
        from fsmes.lab import report as lab_report
        from fsmes.lab import run as lab

        scores = json.loads((directory / "scores.json").read_text(encoding="utf-8"))
        said = lab.export_feedback(directory, scores, echo=typer.echo)
        lab_report.write(directory)
        notes = sum(1 for c in said for t in c["turns"] if t.get("role") == "user")
        typer.echo(f"  {notes} note(s) now in {directory / lab_feedback.FEEDBACK_DIR}")
    else:
        typer.echo("  the run has not finished; it will export this note when it does")


@lab_app.command("open")
def lab_open(
    run_id: str = typer.Argument(..., help="A run directory's name, or a path to one."),
    results: Path | None = typer.Option(None, help="Where run directories are."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
) -> None:
    """Re-render a run's report and say where it is.

    Re-rendered rather than just opened, because notes.md is written after the
    run and the report is where those notes belong. Nothing else is recomputed:
    the numbers come from the scores.json the run wrote.
    """
    import json

    from fsmes.lab import report as lab_report
    from fsmes.lab import run as lab

    directory = _run_dir(run_id, results, root)
    if not (directory / "scores.json").is_file():
        typer.echo(f"No run at {directory}: a run directory has a scores.json in it.")
        raise typer.Exit(2)
    # Notes are left after the numbers are in, so the export runs again here
    # for the same reason the report is re-rendered rather than just opened.
    scores = json.loads((directory / "scores.json").read_text(encoding="utf-8"))
    lab.export_feedback(directory, scores, echo=typer.echo)
    page = lab_report.write(directory)
    typer.echo(f"{page}")
    typer.echo(f"  notes {directory / 'notes.md'}")


@lab_app.command("review")
def lab_review(
    runs: list[str] = typer.Argument(None, help="Run directories, or their names. "
                                               "Default: every run in the results directory."),
    out: Path = typer.Option(Path("findings.md"), "--out",
                             help="Where to write the roll-up."),
    results: Path | None = typer.Option(None, help="Where run directories are."),
    root: Path | None = typer.Option(None, help="Repository root (default: found from cwd)."),
    model: bool = typer.Option(True, "--model/--no-model",
                               help="Let the on-device model title the clusters. "
                                    "--no-model is the deterministic roll-up."),
) -> None:
    """Read several runs together and write what recurs, cited.

    One run is an instrument reading; a finding is a thing that happened
    twice. This clusters every note left at a screen, every row where the MES
    and the script differed, and every question a run could not answer, by
    the screen and the measurement they belong to - each one citing its runs,
    its stations, its numbers and the notes verbatim.

    It never says which side is right, and it works with no model at all: the
    clustering is by screen and measurement, which are facts in the files.
    The local model, when it is running, is asked for one thing - a short
    heading for a cluster somebody has to skim.

        fsmes lab review --out findings.md
    """
    from fsmes.lab import review as lab_review

    where = _results_root(results, root)
    if runs:
        directories = [Path(r).expanduser() if Path(r).expanduser().is_dir() else where / r
                       for r in runs]
    else:
        directories = sorted(d for d in where.iterdir()
                             if (d / "scores.json").is_file()) if where.is_dir() else []
        if not directories:
            typer.echo(f"No runs in {where}. Run one with `fsmes lab run`.")
            raise typer.Exit(2)

    ask = lab_review.local_ask() if model else None
    if model and ask is None:
        typer.echo("  the on-device model did not answer; the roll-up is the deterministic one")
    try:
        path, collected = lab_review.write(directories, out, ask=ask)
    except lab_review.ReviewError as exc:
        typer.echo(str(exc))
        raise typer.Exit(2) from exc
    typer.echo(f"{path}")
    typer.echo(f"  {len(collected['runs'])} run(s), {len(collected['differences'])} difference(s), "
               f"{len(collected['unknowns'])} unknown(s), {len(collected['notes'])} note(s)")


jev_app = typer.Typer(
    help="The judgment model used in the build loop: which versions are served, "
         "which one to pin, and how well it does on this project's own runs.",
)
app.add_typer(jev_app, name="jev")


@jev_app.command("models")
def jev_models(
    resolve: bool = typer.Option(
        False, "--resolve",
        help="Also ask a moving alias one throwaway question, and print the "
             "concrete version that answered. Costs one call."),
    alias: str = typer.Option("jev-latest", "--alias",
                              help="The moving alias to ask when --resolve is given."),
) -> None:
    """What the judgment service serves, and what to pin `MES_JEV_MODEL` to.

    Two different facts, which is why `--resolve` exists. On 2026-09-17 the
    service's own list offered only moving aliases - and this MES refuses a
    moving alias as a pin, because a judgment stored against one cannot be
    read again - while a call made as `jev-latest` answered as `jev-1.13.0`,
    which is then accepted as a pin by name. So the way to learn the name of
    the version to pin is to ask a question, and asking one costs a call.
    That call is made only when you ask for it.

    Nothing else in this MES calls the service from a command line. The
    build loop asks its questions where it already runs, and shadow mode
    refuses all of it.
    """
    from fsmes.integrations import jev

    settings = get_settings()
    ok, why = jev.available(settings)
    if not ok:
        typer.echo(f"NOT OK  {why}")
        raise typer.Exit(1)

    typer.echo(f"Key: set ({jev.KEY_SETTING}).  Pinned: MES_JEV_MODEL={settings.jev_model}")
    try:
        listed = jev.list_models(settings.jev_api_key,
                                 base_url=settings.jev_base_url,
                                 timeout=settings.jev_timeout_seconds)
    except jev.JevUnavailable as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"{len(listed)} model(s) in total, as the service lists them:")
    for model in listed:
        moving = "  (moving: this MES refuses it as a pin)" if model["name"].endswith("latest") else ""
        typer.echo(f"  {model['name']:<16} {model['release_date'] or 'no release date'}"
                   f"  {model['description']}{moving}")
    if not any(m["name"] == settings.jev_model for m in listed):
        typer.echo(f"  The pinned {settings.jev_model} is not in that list. That is not by "
                   f"itself wrong: on 2026-09-17 the served version was pinnable by name "
                   f"and absent from the list. --resolve says what answers today.")

    if not resolve:
        typer.echo("Pass --resolve to spend one call learning which concrete version "
                   f"{alias} answers as.")
        return

    try:
        resolved = jev.resolve_version(settings.jev_api_key, alias=alias,
                                       base_url=settings.jev_base_url,
                                       timeout=settings.jev_timeout_seconds)
    except jev.JevUnavailable as exc:
        typer.echo(f"NOT OK  {exc}")
        raise typer.Exit(1) from exc

    usage = resolved["usage"]
    typer.echo(f"{alias} answered as {resolved['served']}"
               f" ({usage.get('input_tokens')} token(s) in, {usage.get('output_tokens')} out;"
               f" request {resolved['request_id'] or 'unknown'})")
    if resolved["served"] == settings.jev_model:
        typer.echo("That is what is pinned. Nothing to change.")
    else:
        typer.echo(f"Pinned is {settings.jev_model}. Moving the pin is a re-validation: "
                   f"answers either side of it are answers from different versions. "
                   f"Set MES_JEV_MODEL={resolved['served']} deliberately, and say so where "
                   f"the results are read.")


@jev_app.command("labelled-set")
def jev_labelled_set(
    results: list[Path] = typer.Option(
        ..., "--results", "-r", exists=True, file_okay=False,
        help="A lab results directory to read. Repeat for several."),
    out: Path = typer.Option(..., "--out", "-o",
                             help="Where to write the set, as JSON."),
    window_seconds: int = typer.Option(
        labelled.DEFAULT_WINDOW_SECONDS, "--window-seconds",
        help="How much line either side of the stop goes into the window."),
    minimum_seconds: int = typer.Option(
        labelled.DEFAULT_MINIMUM_SECONDS, "--minimum-seconds",
        help="Stops shorter than this are the line's buffers breathing, not a "
             "question anybody would ask. Below five seconds nothing in the "
             "truth can be named."),
    maximum_rows: int = typer.Option(
        labelled.DEFAULT_MAXIMUM_ROWS, "--maximum-rows",
        help="Above this many rows the window is thinned, keeping the first "
             "and the last, and says that it was."),
    controls: int = typer.Option(
        labelled.DEFAULT_CONTROLS_PER_MACHINE, "--controls",
        help="Windows per machine in which it never stopped, so the set can "
             "show a reason being invented. 0 for none."),
    state_view: str = typer.Option(
        labelled.STATE_VIEWS[0], "--state-view",
        help="`stopped-not-why` replaces the machine's state word with a plain "
             "running bit, which is what most machine layers publish. `full` "
             "keeps the state word, which names the reason outright."),
) -> None:
    """Turn recorded runs into a labelled set of stops. Asks nothing.

    The simulated plants script their own breakdowns, changeovers and
    micro-stops, so the true reason for every stop is already written down.
    This reads the scripted hour beside the tag history and emits one record
    per stop: the window, the machine, the reason it actually had, the state
    the MES could see around it, and what the run's own scorecard said.

    No key, no network and no plant: it reads files somebody else recorded.
    """
    import json

    built = labelled.build_many(
        list(results), window_seconds=window_seconds,
        minimum_seconds=minimum_seconds, maximum_rows=maximum_rows,
        controls=controls, state_view=state_view)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(built, indent=1) + "\n", encoding="utf-8")

    totals = built["totals"]
    typer.echo(f"{out}")
    typer.echo(f"  {totals['records']} window(s) in total from "
               f"{built['runs_total']} run(s), view {state_view}")
    for name, count in totals["by_label"].items():
        typer.echo(f"    {name:<14} {count}")
    typer.echo(f"  {totals['scripted']} named by a scripted event, "
               f"{totals['not_scripted']} produced by the line's own buffers")
    typer.echo(f"  {totals['observable']} the MES could see, "
               f"{totals['unobservable']} entirely inside a disconnect")
    sizes = totals["state_characters"]
    typer.echo(f"  state per window: {sizes['smallest']} to {sizes['largest']} "
               f"characters, median {sizes['median']}")
    for run in built["runs"]:
        for note in run["notes"]:
            typer.echo(f"  {run['results']}: {note}")


def _answers_already_stored(path: Path | None) -> dict | None:
    """An answers file from an earlier pass, or None if there is nothing to read.

    A missing file is the normal case the first time, and an unreadable one is
    not a reason to refuse to cost a pass - it is a reason to fall back to the
    stated figure and say so, which is what the caller then prints.
    """
    import json

    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@jev_app.command("ask")
def jev_ask(
    labelled_set: Path = typer.Option(
        ..., "--set", "-s", exists=True, dir_okay=False,
        help="The set written by `fsmes jev labelled-set`."),
    out: Path = typer.Option(..., "--out", "-o",
                             help="Where to write the answers, as JSON."),
    limit: int = typer.Option(
        0, "--limit", help="Ask about only the first N windows. 0 for all."),
    maximum_calls: int = typer.Option(
        None, "--maximum-calls",
        help="Refuse without --yes above this many calls."),
    include_unobservable: bool = typer.Option(
        False, "--include-unobservable",
        help="Also ask about windows that happened entirely while the MES had "
             "no connection. Off: there is nothing for a question to read."),
    yes: bool = typer.Option(False, "--yes",
                             help="Go ahead above the call cap."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Print what it would cost and stop."),
    measure_from: Path = typer.Option(
        None, "--measure-from", exists=True, dir_okay=False,
        help="An answers file from an earlier pass to cost this one from. "
             "Defaults to --out where that already exists. Only calls of the "
             "same state view are measured."),
) -> None:
    """Ask the judgment model why each stop happened, and store the answers.

    One typed choice over the reason vocabulary, one call per window. What it
    costs is printed before anything is asked, and above the cap it stops and
    waits to be told to go ahead.

    Needs a key (`MES_JEV_API_KEY`) and the `[jev]` extra. Without one it
    writes a file saying it was not asked and why, which is a record, not a
    failure.
    """
    import json

    from fsmes.sim import calibration

    built = json.loads(labelled_set.read_text(encoding="utf-8"))
    records = built["records"]
    total = len(records)
    if not include_unobservable:
        records = [r for r in records if r["observable"]]
    if limit > 0:
        records = records[:limit]

    view = calibration.state_view_of(records)
    stored_before = measure_from if measure_from is not None else out
    measured = calibration.measured_characters_per_token(
        _answers_already_stored(stored_before), view)
    cost = calibration.estimate(records, measured=measured)
    cap = calibration.DEFAULT_MAXIMUM_CALLS if maximum_calls is None else maximum_calls
    typer.echo(f"{total} window(s) in the set; {len(records)} to be asked about "
               f"({total - len(records)} left out by --limit and by being "
               f"unobservable).")
    typer.echo(f"  {cost['calls']} call(s), one question each, about "
               f"{cost['state_characters']} character(s) of state in total.")
    typer.echo(f"  Roughly {cost['estimated_input_tokens']} input token(s), about "
               f"{cost['estimated_input_tokens_per_call']} a call.")
    typer.echo(f"  {cost['how']}")
    if dry_run:
        typer.echo("--dry-run: nothing was asked.")
        return
    if cost["calls"] > cap and not yes:
        typer.echo(f"That is more than {cap} call(s). Pass --yes to go ahead, or "
                   f"--limit to ask about fewer.")
        raise typer.Exit(1)

    answers = calibration.ask(records)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(answers, indent=1) + "\n", encoding="utf-8")
    typer.echo(f"{out}")
    typer.echo(f"  {answers['note']}")
    usage = answers.get("usage") or {}
    if usage:
        typer.echo(f"  {usage.get('input_tokens')} input token(s) and "
                   f"{usage.get('output_tokens')} output reported for "
                   f"{usage.get('reported_for')} call(s); "
                   f"{usage.get('not_reported_for')} call(s) reported none.")


@jev_app.command("calibrate")
def jev_calibrate(
    results: list[Path] = typer.Option(
        [], "--results", "-r", exists=True, file_okay=False,
        help="Lab results directories, for D1's recorded answers against the "
             "scripted truth. Repeat for several."),
    labelled_set: Path = typer.Option(
        None, "--set", "-s", exists=True, dir_okay=False,
        help="The set the answers were asked of."),
    answers: Path = typer.Option(
        None, "--answers", "-a", exists=True, dir_okay=False,
        help="The answers written by `fsmes jev ask`."),
    out: Path = typer.Option(..., "--out", "-o", file_okay=False,
                             help="Directory for the report and the figures."),
) -> None:
    """Draw the confusion matrix and the calibration plot, and choose nothing.

    Reads answers somebody already stored; asks nothing itself. Writes a
    self-contained Markdown page whose every number can be checked against
    the two files it names, and one SVG per calibration plot. Empty bins are
    printed as empty and never interpolated.

    **No threshold is chosen.** Where a bin is right often enough over enough
    samples to be worth arguing about, it is named, with its count, for a
    person to decide in the open. Decision 0031 stands either way.
    """
    import json

    from fsmes.sim import calibration

    built = (json.loads(labelled_set.read_text(encoding="utf-8"))
             if labelled_set else {"totals": calibration.labelled.totals_of([]),
                                   "runs_total": 0, "records": []})
    stored = (json.loads(answers.read_text(encoding="utf-8"))
              if answers else {"answers": [], "answers_total": 0})
    d1 = calibration.pair_with_truth(list(results))

    samples = calibration.samples_from_p1(stored.get("answers") or [])
    out.mkdir(parents=True, exist_ok=True)
    figures = []
    for name, title, drawn in (
        ("p1-by-probability.svg",
         "P1: probability on the chosen reason against how often it was right",
         calibration.bins_of(samples["probability_on_the_chosen_option"])),
        ("p1-by-confidence.svg",
         "P1: stated confidence against how often the reason was right",
         calibration.bins_of(samples["stated_confidence"])),
    ):
        (out / name).write_text(calibration.reliability_svg(drawn, title=title),
                                encoding="utf-8")
        figures.append(name)
    for question, pairs in sorted(calibration.samples_from_d1(
            d1.get("pairs") or []).items()):
        name = f"d1-{question.replace('_', '-')}.svg"
        (out / name).write_text(
            calibration.reliability_svg(
                calibration.bins_of(pairs),
                title=f"D1: {question} against the scripted hours"),
            encoding="utf-8")
        figures.append(name)

    page = calibration.report(set_file=built, answers=stored, d1=d1, figures=figures)
    (out / "calibration.md").write_text(page, encoding="utf-8")
    (out / "d1-against-truth.json").write_text(json.dumps(d1, indent=1) + "\n",
                                               encoding="utf-8")
    typer.echo(f"{out / 'calibration.md'}")
    typer.echo(f"  {len(figures)} figure(s), {d1['pairs_total']} D1 condition(s) "
               f"({d1['labelled']} labelled, {d1['unlabelled']} unlabelled)")
    typer.echo("  No threshold was chosen. Decision 0031: a judgment is a proposal.")


def run() -> None:
    """The console script.

    Around the Typer app for one reason: a setting the product refuses to
    start on must reach the person who set it as one sentence, not as a
    traceback with the sentence somewhere in the middle of it. The person
    reading this is standing next to a plant with a command that did not
    run, and the only thing they need is which variable to change.
    """
    import sys

    from fsmes.identity import Misconfigured

    try:
        app()
    except Misconfigured as exc:
        # sys.exit rather than typer.Exit: nothing is inside Typer's command
        # runner here to turn that into an exit code, so it would print a
        # second traceback under the sentence.
        typer.echo(f"fsmes cannot start: {exc}")
        sys.exit(2)
