"""One node does the job, and the database decides which one.

A plant that runs on two boxes has to answer one question before it may
write anything: am I the node that reads the machines? Scott, 2026-10-15,
after hearing of a site that could not make its server redundant because of
the MES it ran: *"I want the next pypi release to be a parallelizable
mes."* The thing underneath that is a lease row, and the failure it exists
to prevent is the one the code had until today - two readers, each holding
its own counter baseline in memory, each booking the whole delta, so a plant
that made 1,000 bottles reports 2,000.

Everything in this file runs on the default in-memory SQLite, because the
lease's logic is one UPDATE and a clock and does not need a server to be
read. The proof that two *processes* on one PostgreSQL hand the role over
correctly is `test_a_plant_on_two_nodes_survives_one_of_them_dying.py`,
which the `postgres` CI job runs.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from fsmes.config import Settings
from fsmes.domain import Equipment, EquipmentLevel, PlantLease, TagValue
from fsmes.integrations.opc import agent
from fsmes.services import counters, leases, masterdata

SOURCE = Path(__file__).resolve().parents[1] / "src" / "fsmes"


def a_redundant_plant(**over) -> Settings:
    """Settings for one node of a plant that runs on more than one node."""
    base = {"plant_name": "kc1", "plant_profile": "plant",
            "database_url": "postgresql+psycopg://fsmes:fsmes@db/fsmes",
            "redundancy": True, "secret_key": "a-secret-of-our-own",
            "node_id": "a", "api_port": 8111}
    base.update(over)
    return Settings(**base)


# --------------------------------------------------------- taking the role

def test_the_first_node_to_ask_holds_the_role_and_the_second_is_told_no(session):
    assert leases.claim(session, role=leases.ROLE_READER, holder="a", seconds=30)
    assert not leases.claim(session, role=leases.ROLE_READER, holder="b", seconds=30)
    assert leases.held_by(session, "a") == [leases.ROLE_READER]
    assert leases.held_by(session, "b") == []


def test_asking_again_renews_the_lease_without_changing_when_it_was_taken(session):
    assert leases.claim(session, role=leases.ROLE_READER, holder="a", seconds=30)
    row = session.get(PlantLease, leases.ROLE_READER)
    took, first_expiry = row.acquired_at, row.expires_at

    assert leases.claim(session, role=leases.ROLE_READER, holder="a", seconds=300)
    session.refresh(row)
    # "Since when" is a different fact from "last seen", and a dashboard that
    # said a node had held the reader for two seconds when it had held it all
    # morning would be answering a question nobody asked.
    assert row.acquired_at == took
    assert row.expires_at > first_expiry


def test_a_lease_nobody_renewed_is_taken_by_whoever_asks_next(session):
    assert leases.claim(session, role=leases.ROLE_READER, holder="a", seconds=30)
    row = session.get(PlantLease, leases.ROLE_READER)
    # The node died: it stopped renewing, and the lease ran out. Moved in the
    # table rather than slept for, because the lease is read from the
    # database's clock and a test that waits out ten seconds is a test nobody
    # runs twice.
    row.expires_at = leases.db_now(session) - timedelta(seconds=1)
    session.flush()

    assert leases.claim(session, role=leases.ROLE_READER, holder="b", seconds=30)
    session.refresh(row)
    assert row.holder == "b"
    # It is b's role now, from now - not from whenever a took it.
    assert row.acquired_at == row.renewed_at
    assert leases.held_by(session, "a") == []


def test_a_live_lease_cannot_be_stolen_by_a_node_whose_clock_is_wrong(session):
    """Two boxes whose clocks disagree still hand the role over correctly,
    because neither box's clock is ever consulted: `now()` is read back from
    the database inside the transaction that writes the row."""
    assert leases.claim(session, role=leases.ROLE_READER, holder="a", seconds=30)
    assert not leases.claim(session, role=leases.ROLE_READER, holder="b", seconds=30)

    # The guard against the regression: nothing in the lease service may reach
    # for the box's clock. One `datetime.now` here and a node with a fast
    # clock takes a role another node is still renewing.
    source = (SOURCE / "services" / "leases.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    code = re.sub(r'"""', "'", code)
    assert "datetime.now" not in code
    assert "utcnow" not in code


def test_giving_up_a_role_does_not_let_a_node_delete_the_holder_that_replaced_it(session):
    assert leases.claim(session, role=leases.ROLE_FLOOR, holder="a", seconds=30)
    # a lost the role to b, then shuts down tidily and gives up "its" lease.
    row = session.get(PlantLease, leases.ROLE_FLOOR)
    row.holder = "b"
    session.flush()

    leases.release(session, role=leases.ROLE_FLOOR, holder="a")
    session.flush()

    assert leases.held_by(session, "b") == [leases.ROLE_FLOOR]


def test_giving_up_a_role_lets_the_other_node_take_it_without_waiting(session):
    assert leases.claim(session, role=leases.ROLE_FLOOR, holder="a", seconds=300)
    leases.release(session, role=leases.ROLE_FLOOR, holder="a")
    session.flush()
    assert leases.claim(session, role=leases.ROLE_FLOOR, holder="b", seconds=300)


def test_a_role_nobody_holds_is_listed_as_expired_and_not_hidden(session):
    """An expired row is how a plant records that nobody is doing that job
    right now. Hiding it would make a reader that stopped at ten to one look
    like a plant that never had one - house rule 2, unknown is not zero."""
    assert leases.claim(session, role=leases.ROLE_READER, holder="a", seconds=30)
    row = session.get(PlantLease, leases.ROLE_READER)
    row.expires_at = leases.db_now(session) - timedelta(seconds=1)
    session.flush()

    listed = leases.holders(session)
    assert len(listed) == 1
    assert listed[0]["role"] == leases.ROLE_READER
    assert listed[0]["holder"] == "a"
    assert listed[0]["live"] is False


def test_there_are_three_roles_and_the_table_names_all_of_them():
    """The total, stated: every list in this product says how long it is."""
    assert leases.ROLES == (leases.ROLE_READER, leases.ROLE_FLOOR, leases.ROLE_HOUSEKEEPING)
    assert len(leases.ROLES) == 3


# ------------------------------------------------- a node that is not redundant

def test_a_plant_on_one_node_never_touches_the_lease_table():
    """The whole of the "redundancy off" story is one None: every caller runs
    its work directly and nothing reads or writes a lease."""
    one_box = Settings(plant_name="kc1", plant_profile="plant")
    assert one_box.redundancy is False
    for role in leases.ROLES:
        assert leases.for_role(role, one_box) is None


def test_a_node_that_was_not_named_calls_itself_by_its_box_and_its_port():
    """Unique among the nodes of one plant either way: two nodes on one box
    differ by port, two boxes by hostname. A guess, and `/health` says so."""
    named = leases.for_role(leases.ROLE_READER, a_redundant_plant(node_id="a"))
    assert named is not None and named.node == "a"

    guessed = leases.for_role(leases.ROLE_READER, a_redundant_plant(node_id=""))
    assert guessed is not None and guessed.node.endswith(":8111")


def test_a_node_that_cannot_reach_the_database_is_a_standby_and_not_a_crash(monkeypatch):
    """Losing the lease, or failing to ask for it, is not an error: a node
    that cannot prove it holds a role must not write, and must not exit -
    `plant.run` takes the whole plant down when any child exits, which would
    take that node's dashboard with it."""
    holder = leases.Holder(leases.ROLE_READER, "a", 10.0)

    def no_database():
        raise OSError("connection refused")

    monkeypatch.setattr(leases, "session_scope", no_database)
    assert holder.take() is False
    assert holder.held is False
    assert holder.holds() is False


def test_a_node_whose_plant_was_never_migrated_is_told_which_command_fixes_it(monkeypatch):
    """The first thing a person hits when they bring up two nodes by hand:
    `fsmes plant <name> run` serves the dashboard but creates no schema, so
    every role asks for a lease in a table that is not there. Said once, as
    an error, with the command - not as the same stack every three seconds
    for as long as the node stands by."""
    import structlog

    holder = leases.Holder(leases.ROLE_READER, "a", 10.0)

    def no_table():
        raise ProgrammingError(
            'UPDATE plant_leases SET holder=...', {},
            Exception('relation "plant_leases" does not exist'))

    monkeypatch.setattr(leases, "session_scope", no_table)
    with structlog.testing.capture_logs() as captured:
        for _ in range(3):
            assert holder.take() is False

    said = [line for line in captured if line["log_level"] == "error"]
    assert len(said) == 1, f"the same reason was printed {len(said)} times"
    assert "fsmes plant <name> init" in said[0]["event"]
    assert not [line for line in captured if line["log_level"] == "warning"], (
        "a missing table is one error with a command in it, not a warning a "
        "person has to read a stack out of")


# ------------------------------------------------------- baselines in the database

def test_a_counter_baseline_outlives_the_reader_that_wrote_it(session):
    """Why the booking is exactly-once at all. The reader used to keep its
    baselines in memory, so a second reader - or the same one after a restart
    - booked the whole counter again as if it were new production."""
    counters.save(session, {("WASH01", "GoodCount"): 1040}, node="a")
    session.flush()
    assert counters.load(session) == {("WASH01", "GoodCount"): 1040}

    counters.save(session, {("WASH01", "GoodCount"): 1105}, node="b")
    session.flush()
    assert counters.load(session) == {("WASH01", "GoodCount"): 1105}


def test_a_counter_nobody_has_read_yet_is_absent_and_not_zero(session):
    """A baseline of zero would book the machine's whole lifetime counter as
    this shift's production the first time the reader sees it."""
    assert counters.load(session) == {}


# ------------------------------------------------------------------- refusals

def test_a_redundant_plant_on_sqlite_refuses_to_start_and_says_why():
    with pytest.raises(ValidationError) as caught:
        a_redundant_plant(database_url="sqlite:///./mes.db")
    said = str(caught.value)
    assert "MES_DATABASE_URL" in said and "MES_REDUNDANCY" in said
    # One sentence naming the fix, for a person standing next to a plant.
    assert "a file cannot be shared by two" in said


def test_a_redundant_plant_with_no_signing_key_set_refuses_to_start_and_says_why():
    """Two APIs share logins only if they sign tokens with the same key. With
    no key set every process invents its own, so a person signed in on one
    node is a stranger on the other - and until today the lab default made
    two nodes agree by luck rather than by design."""
    with pytest.raises(ValidationError) as caught:
        a_redundant_plant(secret_key="")
    said = str(caught.value)
    assert "MES_SECRET_KEY" in said and "secret_key_env" in said
    assert "a stranger on the other" in said


def test_a_redundant_plant_signing_with_the_lab_default_refuses_to_start():
    """The default is in the source of this package, so anybody can mint a
    session with it. Fine on a laptop demo; not on a plant on a network."""
    from fsmes import identity

    with pytest.raises(ValidationError) as caught:
        a_redundant_plant(secret_key=identity.lab_secret_key("bottling"))
    assert "MES_SECRET_KEY" in str(caught.value)


def test_a_lease_too_short_to_survive_a_slow_query_refuses_to_start():
    with pytest.raises(ValidationError) as caught:
        a_redundant_plant(lease_seconds=0.5)
    said = str(caught.value)
    assert "MES_LEASE_SECONDS" in said and "2 seconds or more" in said


def test_a_plant_on_one_node_is_not_asked_any_of_that():
    """None of the four refusals applies to the laptop demo: SQLite, no key
    set, the lab default and a one-second lease are all fine on one box."""
    from fsmes import identity

    one_box = Settings(plant_name="", plant_profile="laptop",
                       database_url="sqlite:///./mes.db",
                       secret_key=identity.lab_secret_key("demo"),
                       lease_seconds=0.5)
    assert one_box.redundancy is False


# ------------------------------------------- the history the other node has

def a_reader_on(session, scope, monkeypatch) -> agent._Handler:
    """A reader writing into the test's own session, with one machine."""
    if session.scalar(select(Equipment).where(Equipment.code == "WASH01")) is None:
        session.add(Equipment(code="WASH01", name="Washer",
                              level=EquipmentLevel.WORK_UNIT))
        session.flush()
    monkeypatch.setattr(agent, "session_scope", scope)
    monkeypatch.setattr(masterdata, "get_equipment",
                        lambda s, code: session.scalar(
                            select(Equipment).where(Equipment.code == code)))
    return agent._Handler({}, node="b")


def test_the_reader_that_takes_over_does_not_store_a_reading_the_dead_node_already_wrote(
        session, scope, monkeypatch):
    """An OPC subscription hands a new reader every tag's current value,
    stamped with the moment the machine last published it - which, on a
    plant whose other node has just died, is a moment already in
    `tag_values`. The table has no unique key, so without this the one
    table whose purpose is being able to argue with the PLC about what it
    sent would hold the same reading twice."""
    moments = [datetime(2026, 10, 15, 10, 0, second) for second in (1, 2, 3)]
    readings = [("WASH01", "WashTemp", 70.0 + i, ts) for i, ts in enumerate(moments)]

    dead_node = a_reader_on(session, scope, monkeypatch)
    dead_node._write_history(readings)
    session.flush()

    took_over = a_reader_on(session, scope, monkeypatch)
    took_over._write_history(readings)
    session.flush()

    stored = sorted(session.scalars(select(TagValue.ts)))
    assert stored == moments, (
        "the three readings the dead node stored are the three readings there are")


def test_the_reader_that_takes_over_stores_everything_it_has_not_seen(
        session, scope, monkeypatch):
    """The check drops repeats, not readings. A batch that straddles the
    moment of the failover is half history and half new, and every unit in
    the new half has to be booked."""
    already = datetime(2026, 10, 15, 10, 0, 1)
    fresh = datetime(2026, 10, 15, 10, 0, 2)

    dead_node = a_reader_on(session, scope, monkeypatch)
    dead_node._write_history([("WASH01", "WashTemp", 70.0, already)])
    session.flush()

    took_over = a_reader_on(session, scope, monkeypatch)
    took_over._write_history([("WASH01", "WashTemp", 70.0, already),
                              ("WASH01", "WashTemp", 71.0, fresh)])
    session.flush()

    assert sorted(session.scalars(select(TagValue.ts))) == [already, fresh]


def test_the_repeat_check_asks_the_database_only_while_the_overlap_lasts(
        session, scope, monkeypatch):
    """One extra query per batch is cheap for the two batches a failover
    takes and wasteful for the next million, so the first batch that holds
    nothing already stored turns the check off."""
    took_over = a_reader_on(session, scope, monkeypatch)
    assert took_over._check_history_for_repeats, (
        "a reader starts by assuming another node may have been here")

    took_over._write_history(
        [("WASH01", "WashTemp", 70.0, datetime(2026, 10, 15, 10, 0, 1))])
    session.flush()
    assert not took_over._check_history_for_repeats, (
        "nothing was already stored, so the overlap is over")


def test_a_reading_with_no_moment_of_its_own_is_never_held_back(
        session, scope, monkeypatch):
    """A machine that publishes no source timestamp gets the instant the
    row is written, which cannot repeat. Treating those as possible
    repeats would drop readings a plant really sent."""
    took_over = a_reader_on(session, scope, monkeypatch)
    took_over._write_history([("WASH01", "State", "RUNNING", None),
                              ("WASH01", "State", "RUNNING", None)])
    session.flush()

    assert len(list(session.scalars(select(TagValue.id)))) == 2


# ------------------------------------------------------------- the migration
#
# Run on a real SQLite file, both ways. `[redundancy]` needs PostgreSQL, but
# the *migration* runs on every plant that upgrades, including the laptop
# that will never hold a lease, and a revision that cannot be rolled back is
# one nobody dares run.

REVISION = "b6f0c2a71d58"
PARENT = "d4e8a1c93b70"


def _shape(url: str, revision: str) -> dict[str, list[str]]:
    from alembic import command

    from fsmes import schema as schema_mod

    command.upgrade(schema_mod.alembic_config(url), revision)
    return schema_mod.database_shape(url)


def test_the_migration_adds_two_tables_and_takes_them_away_again(tmp_path):
    """Up: the lease table and the baseline table. Down: both gone and the
    parent's schema back, unchanged."""
    from alembic import command

    from fsmes import schema as schema_mod

    url = f"sqlite:///{tmp_path / 'leases.db'}"
    before = _shape(url, PARENT)
    assert "plant_leases" not in before
    assert "counter_baselines" not in before

    after = _shape(url, REVISION)
    assert sorted(after["plant_leases"]) == [
        "acquired_at", "expires_at", "holder", "renewed_at", "role"]
    assert sorted(after["counter_baselines"]) == [
        "equipment_code", "node", "tag", "updated_at", "value"]

    command.downgrade(schema_mod.alembic_config(url), PARENT)
    assert schema_mod.database_shape(url) == before


def test_the_migration_is_the_only_head(tmp_path):
    """One head, so `alembic upgrade head` on a plant that has been running
    since 0.3.1 is not a question about which branch it meant."""
    from alembic.script import ScriptDirectory

    from fsmes import schema as schema_mod

    heads = ScriptDirectory.from_config(
        schema_mod.alembic_config(f"sqlite:///{tmp_path / 'heads.db'}")).get_heads()
    assert list(heads) == [REVISION], heads


def test_the_migration_backfills_no_baseline_it_cannot_know(tmp_path):
    """A counter whose reader's memory is gone has no recoverable baseline:
    `production_logs` holds the deltas that were booked, never the absolute
    value the PLC showed. So the table starts empty on every upgrading
    plant, and the first reading per machine books nothing — unknown is not
    zero, and a guessed absolute counter would invent production."""
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'empty.db'}"
    _shape(url, REVISION)
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM counter_baselines")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM plant_leases")).scalar() == 0
    engine.dispose()
