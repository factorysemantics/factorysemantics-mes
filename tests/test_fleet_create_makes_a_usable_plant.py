"""`fsmes fleet create` builds a plant a person can sign in to, or refuses.

On 2026-09-14 it built neither. `fleet create` on the bottling pack printed
"applied, recorded, owned"; the plant started; `/health` said ok and the
console counted it *answered*. Its database had never been created: `/pack`
said `schema.revision: null`, `fsmes db-status` said "there is no database
here yet", and **every sign-in returned 500**. On the machining pack, whose
pack carries master data, the rows went into an unstamped database with only
the tables the ORM had touched, and `fsmes plant machining init` afterwards
refused to have anything to do with it.

One cause underneath both. `fsmes pack apply` set `MES_DATABASE_URL` only
when the pack named a database of its own; a pack that named none was applied
to whatever this process already had, which outside a deployment is the
product's default `./fsmes.db`. `fleet create` calls `apply` in-process, so
it migrated and seeded a stray file beside the working directory and then
recorded ownership of a plant that did not exist.

So the tests here are end to end on purpose: create a plant in a temp
directory with **nothing** telling the process which database to use, then
ask the plant's own database what revision it is at and ask the real
application to sign somebody in.

Nothing here starts a plant on a port. The application is built in-process
against the database `create` actually wrote.
"""

import os
import shutil
from pathlib import Path

import pytest

from fsmes import plant as plants
from fsmes import storage
from fsmes.fleet import commands, observe
from fsmes.fleet import owned as ownership
from fsmes.pack import apply as applier
from fsmes.pack import format as fmt

ROOT = Path(__file__).resolve().parents[1]
LABS = ROOT / "labs" / "multiplant"


@pytest.fixture()
def nowhere(tmp_path, monkeypatch):
    """An empty directory, and a process that has not been told anything.

    Deliberately **without** `MES_DATABASE_URL`: that variable is what the
    old tests set, and setting it is what hid this bug for a fortnight. What
    is being proved is that a plant built by a person who exported nothing
    still gets its own database.
    """
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    before = dict(os.environ)
    for variable in ("MES_DATABASE_URL", plants.REGISTRY_ENV, applier.DATA_DIR_ENV):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    monkeypatch.setattr(observe, "health", lambda *a, **k: observe.Answer(
        "http://fake", False, why="did not answer (nothing listening)"))
    plants.environment.cache_clear()
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()
    yield tmp_path
    os.environ.clear()
    os.environ.update(before)
    plants.environment.cache_clear()
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


def a_pack(where: Path, name: str) -> Path:
    directory = where / name
    shutil.copytree(LABS / name, directory)
    return directory


def said(lines: list[str]):
    return lambda line: lines.append(line)


# ------------------------------------------------- the plant actually exists


@pytest.mark.parametrize("name", ["machining", "bottling"])
def test_creating_a_plant_leaves_its_own_database_at_head(nowhere, name):
    """The first half of the bug. The database the plant will be started
    against - not this process's - is the one that got migrated."""
    lines: list[str] = []
    commands.create(a_pack(nowhere, name), root=nowhere, echo=said(lines))

    where = commands.data_dir(nowhere)
    assert (where / f"{name}.db").is_file(), (
        f"{name}'s own database was never created; something else was migrated instead")
    assert not list(nowhere.glob("fsmes.db")), (
        "a database appeared that no plant is named after; that is the stray file "
        "`fleet create` used to migrate and seed instead of the plant's own")
    reading = storage.look(f"sqlite:///{(where / f'{name}.db').as_posix()}", "the plant's own")
    assert reading.answered and reading.at_head, reading.sentence()
    assert any("database sqlite" in line for line in lines), (
        "apply changed a database without saying which one on a line of its own")


@pytest.mark.parametrize("name", ["machining", "bottling"])
def test_a_plant_this_installation_just_created_can_be_signed_in_to(nowhere, name):
    """The half that a person notices. Every sign-in returned 500 because the
    users table was not there; ADMIN is the account the lab list creates and
    the one the handoff names."""
    from fastapi.testclient import TestClient

    from fsmes.api.app import create_app

    commands.create(a_pack(nowhere, name), root=nowhere, echo=lambda _: None)
    # `apply` has already made this process that plant, which is what lets the
    # application below be built against the database `create` just wrote.
    with TestClient(create_app()) as client:
        answer = client.post("/auth/login", json={"code": "ADMIN", "password": "admin"})
    assert answer.status_code == 200, answer.text
    assert answer.json().get("token")


@pytest.mark.parametrize("name", ["machining", "bottling"])
def test_a_plant_this_installation_just_created_has_a_line_on_it(nowhere, name):
    """Both lab packs carry their master data now. bottling's did not until
    2026-09-14, and a plant with no machines is what `fsmes score bottling`
    died on."""
    commands.create(a_pack(nowhere, name), root=nowhere, echo=lambda _: None)
    assert applier.line_here() == {"equipment": 6 if name == "bottling" else 3,
                                   "answered": True}


def test_what_a_created_plant_says_about_itself_is_at_head_and_not_empty(nowhere):
    """`/pack`'s payload, from inside the plant, which is what the console
    and `fsmes fleet list` read."""
    commands.create(a_pack(nowhere, "machining"), root=nowhere, echo=lambda _: None)
    os.environ[applier.DATA_DIR_ENV] = str(commands.data_dir(nowhere))
    say = applier.what_this_plant_runs()
    assert say["schema"]["at_head"] is True
    assert say["line"] == {"equipment": 3, "answered": True}
    assert observe.is_empty(say) is False


# ------------------------------------------------ or it refuses, and says so


def test_a_database_the_migrator_disowns_refuses_before_any_ownership_is_recorded(nowhere):
    """The second half of the lab's machining failure: a file with some of
    this product's tables and no Alembic stamp. `create` must not build on
    it, and must not claim it either."""
    from sqlalchemy import create_engine, text

    pack = a_pack(nowhere, "machining")
    where = plants.data_dir(nowhere)
    where.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{(where / 'machining.db').as_posix()}")
    with engine.begin() as connection:
        # One real table of this product's, and nothing else - the shape the
        # ORM leaves behind when it writes into a database nobody migrated.
        connection.execute(text("CREATE TABLE equipment (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    from fsmes.schema import SchemaError

    with pytest.raises((SchemaError, applier.Refused, commands.Refused)):
        commands.create(pack, root=nowhere, echo=lambda _: None)
    assert not ownership.path(commands.data_dir(nowhere)).exists(), (
        "a plant that was never built is recorded as owned")


def test_master_data_is_never_seeded_into_a_database_that_is_behind_head(nowhere):
    """The guard that makes the half-made file impossible rather than
    unlikely. A database really taken to an older revision, not a mock."""
    from alembic import command as alembic

    from fsmes.schema import alembic_config, head_revision

    pack = a_pack(nowhere, "machining")
    where = plants.data_dir(nowhere)
    where.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{(where / 'machining.db').as_posix()}"
    first = _oldest_revision()
    alembic.upgrade(alembic_config(url), first)
    assert first != head_revision()

    os.environ["MES_DATABASE_URL"] = url
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()
    with pytest.raises(applier.Refused) as refusal:
        applier.refuse_unless_at_head(fmt.read(pack))
    problems = "\n".join(refusal.value.report.render())
    assert "Nothing was seeded" in problems and first in problems


def _oldest_revision() -> str:
    from alembic.script import ScriptDirectory

    from fsmes.schema import alembic_config

    return [s.revision for s in ScriptDirectory.from_config(alembic_config()).walk_revisions()][-1]
