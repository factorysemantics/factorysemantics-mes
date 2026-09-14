"""`fsmes fleet` touches only the plants this installation created.

Decision 0023 moved the safety property from *read-only* to *cannot touch a
plant it does not own*, and said plainly what that costs: the review question
is no longer "is there a call here that writes" but "does every call that
writes check ownership first". That is a weaker property, so this file holds
it two ways.

**By behaviour.** Against fake plants - one answering with the wrong instance
id, one answering with none, one that is not there at all - every verb
refuses and says why.

**By ratchet.** The last three tests read `fsmes/fleet/commands.py` as source:
every public function is classified as a write or a read, nothing outside the
writes calls anything that acts, and every write calls the gate before it
does. A verb added next month that forgets the gate fails here rather than
shipping.

**Nothing here starts a plant.** Standing rule: the lab fleet is its
operator's to start. `start` and `stop` are proved by the gate they go
through and by the machinery they hand off to, with that machinery replaced
by a recorder - so what is tested is that an unowned plant never reaches it.
"""

import ast
import contextlib
import os
import shutil
from pathlib import Path

import pytest

from fsmes import identity
from fsmes import plant as plants
from fsmes.fleet import commands, observe
from fsmes.fleet import owned as ownership

ROOT = Path(__file__).resolve().parents[1]
LABS = ROOT / "labs" / "multiplant"
SOURCE = ROOT / "src" / "fsmes" / "fleet" / "commands.py"


# ------------------------------------------------------------------ fixtures


def silent(*_args, **_kwargs) -> observe.Answer:
    """A plant that is not there. The commonest state of a lab fleet."""
    return observe.Answer("http://fake", False, why="did not answer (nothing listening)")


def answering(**fields):
    """A plant that answers /health with whatever this test wants it to say."""

    def _ask(where, **_kwargs) -> observe.Answer:
        return observe.Answer(f"{where}/health", True, body=dict(fields), status=200)

    return _ask


@pytest.fixture()
def fleet(tmp_path, monkeypatch):
    """An empty fleet: a root of its own, a data directory of its own, and no
    plant anywhere. Every settings cache is put back afterwards."""
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    # `fsmes pack apply` deliberately becomes the plant it is applying to:
    # it puts the pack's MES_* values into this process's environment. That
    # is right for a one-shot command and wrong for the next test in the
    # file, so the whole environment goes back afterwards.
    before = dict(os.environ)
    monkeypatch.delenv(plants.REGISTRY_ENV, raising=False)
    plants.environment.cache_clear()
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'plant.db').as_posix()}")
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    # Two settings that are about this database being a **file**, which the
    # rest of the suite's is not. `MES_TAG_RETENTION_DAYS=0` switches off the
    # hourly pruner the API's lifespan starts: it opens its own session in a
    # worker thread through `asyncio.to_thread`, `task.cancel()` does not stop
    # a thread already inside a SQLite `BEGIN IMMEDIATE`, and against a file
    # it contends with whatever the test is actually doing. Nothing here tests
    # retention. And the engine is disposed rather than dropped, because on
    # Windows an open handle is a file `tmp_path` cannot delete.
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    monkeypatch.setattr(observe, "health", silent)
    yield tmp_path
    os.environ.clear()
    os.environ.update(before)
    plants.environment.cache_clear()
    if get_engine.cache_info().currsize:
        with contextlib.suppress(Exception):
            get_engine().dispose()
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


@pytest.fixture()
def a_pack(fleet):
    """The machining lab plant, copied so a test may change it."""
    directory = fleet / "machining"
    shutil.copytree(LABS / "machining", directory)
    return directory


def recorded(monkeypatch) -> list[str]:
    """Replace the process control with a recorder. Nothing is started."""
    done: list[str] = []
    monkeypatch.setattr(plants, "start", lambda name, cfg, root, speed=None, echo=print:
                        done.append(f"start {name}"))
    monkeypatch.setattr(plants, "stop", lambda name, cfg, root, echo=print:
                        done.append(f"stop {name}"))
    return done


# --------------------------------------------- a plant nobody created is safe


@pytest.mark.parametrize("verb", ownership.WRITE_VERBS[1:])
def test_a_plant_this_installation_never_created_is_refused_by_every_verb(fleet, verb):
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate(verb, "somebody-elses-plant", data_dir=fleet)
    assert "no record of creating" in str(refusal.value)
    assert verb in str(refusal.value)


def test_the_gate_knows_only_the_verbs_that_write(fleet):
    """A verb the gate does not know cannot be gated, so asking is a mistake
    in the code rather than a refusal to report to a person."""
    with pytest.raises(ValueError):
        ownership.gate("book-production", "machining", data_dir=fleet)


def test_creating_a_plant_records_what_was_created_and_gives_it_an_instance_id(a_pack, fleet):
    entry = commands.create(a_pack, root=fleet, echo=lambda _: None)
    where = commands.data_dir(fleet)

    assert entry.name == "machining"
    assert entry.instance_id and len(entry.instance_id) >= 16
    assert ownership.read_instance(where, "machining") == entry.instance_id
    assert entry.created_on_host and entry.created_by_user

    again = ownership.load(where)
    assert [e.name for e in again.owned] == ["machining"]
    assert again.entry("machining").instance_id == entry.instance_id
    written = [line for line in ownership.path(where).read_text(encoding="utf-8").splitlines()
               if line and not line.startswith("#")]
    assert not [line for line in written if "password" in line.lower()], (
        "a secret reached the ownership file; it names variables, never values")


def test_creating_the_same_plant_twice_is_refused_rather_than_abandoning_the_first(a_pack, fleet):
    commands.create(a_pack, root=fleet, echo=lambda _: None)
    with pytest.raises(ownership.NotOwned) as refusal:
        commands.create(a_pack, root=fleet, echo=lambda _: None)
    assert "already recorded" in str(refusal.value)


def test_a_pack_that_does_not_check_never_reaches_a_database(a_pack, fleet):
    (a_pack / "plant.toml").write_text(
        (a_pack / "plant.toml").read_text(encoding="utf-8") + '\nfavourite_colour = "blue"\n',
        encoding="utf-8")
    with pytest.raises(commands.Refused) as refusal:
        commands.create(a_pack, root=fleet, echo=lambda _: None)
    assert "pack check" in str(refusal.value)
    assert not ownership.path(commands.data_dir(fleet)).exists()


# ------------------------------------- the plant has to corroborate the claim


@pytest.fixture()
def created(a_pack, fleet):
    """One owned plant, created from a real pack. Not running."""
    entry = commands.create(a_pack, root=fleet, echo=lambda _: None)
    return entry, fleet


@pytest.mark.parametrize("verb", ownership.WRITE_VERBS[1:])
def test_a_plant_answering_with_a_different_instance_id_is_not_owned(created, verb, monkeypatch):
    entry, fleet = created
    monkeypatch.setattr(observe, "health",
                        answering(plant="machining", instance_id="a-different-plant"))
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate(verb, entry.name, data_dir=commands.data_dir(fleet))
    assert "different instance id" in str(refusal.value)


@pytest.mark.parametrize("verb", ownership.WRITE_VERBS[1:])
def test_a_plant_answering_with_no_instance_id_is_not_owned(created, verb, monkeypatch):
    entry, fleet = created
    monkeypatch.setattr(observe, "health", answering(plant="machining"))
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate(verb, entry.name, data_dir=commands.data_dir(fleet))
    assert "no instance id" in str(refusal.value)
    assert "observed now, not owned" in str(refusal.value)


@pytest.mark.parametrize("verb", ownership.WRITE_VERBS[1:])
def test_something_else_on_the_port_is_not_this_plant(created, verb, monkeypatch):
    entry, fleet = created
    monkeypatch.setattr(observe, "health",
                        answering(plant="bottling", instance_id=entry.instance_id))
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate(verb, entry.name, data_dir=commands.data_dir(fleet))
    assert "Something else" in str(refusal.value)


def test_deleting_the_id_from_the_plants_data_directory_takes_ownership_back(created):
    entry, fleet = created
    where = commands.data_dir(fleet)
    ownership.instance_path(where, entry.name).unlink()
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("start", entry.name, data_dir=where)
    assert "revokes ownership" in str(refusal.value)


def test_a_plant_created_on_another_machine_is_not_this_installations_to_start(created):
    entry, fleet = created
    where = commands.data_dir(fleet)
    record = ownership.load(where)
    from dataclasses import replace
    moved = replace(record.owned[0], created_on_host="some-other-box")
    ownership.save(ownership.Record(where=record.where, owned=(moved,)))
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("start", entry.name, data_dir=where)
    assert "some-other-box" in str(refusal.value)


def test_a_silent_plant_may_be_started_but_not_stopped(created, monkeypatch):
    """Silence blocks every verb that reaches into a *running* plant. It
    cannot block `start`, or nothing could ever be started."""
    entry, fleet = created
    where = commands.data_dir(fleet)
    assert ownership.gate("start", entry.name, data_dir=where).owned is True
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("stop", entry.name, data_dir=where)
    assert "did not answer" in str(refusal.value)


def test_starting_an_unowned_plant_never_reaches_the_process_control(fleet, monkeypatch):
    done = recorded(monkeypatch)
    with pytest.raises(ownership.NotOwned):
        commands.start("machining", root=fleet, echo=lambda _: None)
    with pytest.raises(ownership.NotOwned):
        commands.stop("machining", root=fleet, echo=lambda _: None)
    assert done == [], "the gate let a verb through to a plant nobody owns"


def test_starting_an_owned_plant_hands_off_to_the_machinery_that_already_runs_plants(
        created, monkeypatch):
    entry, fleet = created
    done = recorded(monkeypatch)
    commands.start(entry.name, root=fleet, echo=lambda _: None)
    assert done == ["start machining"]


def test_a_pack_is_not_applied_to_a_plant_that_is_still_answering(created, monkeypatch):
    entry, fleet = created
    monkeypatch.setattr(observe, "health",
                        answering(plant="machining", instance_id=entry.instance_id))
    with pytest.raises(commands.Refused) as refusal:
        commands.apply(entry.name, root=fleet, echo=lambda _: None)
    assert "Stop it first" in str(refusal.value)


def test_one_plants_pack_is_never_applied_to_another_plant(created, fleet):
    entry, _ = created
    other = fleet / "bottling"
    shutil.copytree(LABS / "bottling", other)
    with pytest.raises(commands.Refused) as refusal:
        commands.apply(entry.name, root=fleet, directory=other, echo=lambda _: None)
    assert "describes bottling, not machining" in str(refusal.value)


def test_applying_again_records_the_new_fingerprint(created):
    entry, fleet = created
    updated = commands.apply(entry.name, root=fleet, echo=lambda _: None)
    assert updated.pack_fingerprint == entry.pack_fingerprint

    equipment = Path(entry.pack) / "masterdata" / "equipment.json"
    equipment.write_text(equipment.read_text(encoding="utf-8").replace("Bar Saw", "Bar Saw Mk2"),
                         encoding="utf-8")
    after = commands.apply(entry.name, root=fleet, echo=lambda _: None)
    assert after.pack_fingerprint != entry.pack_fingerprint


# ------------------------------------------------------------ the file itself


def test_two_plants_claiming_one_instance_id_is_an_error_and_not_a_coin_toss(fleet):
    """A plant directory copied to make a second plant carries the first
    one's id. A fleet tool that picked one of them would act on the wrong
    plant, so it picks neither."""
    where = commands.data_dir(fleet)
    common = dict(pack="/packs/x", data_dir=str(where), api_host="127.0.0.1",
                  control="local", created_at="", created_on_host="", created_by_user="",
                  product_version="0.1.2", pack_fingerprint="")
    record = ownership.Record(where=ownership.path(where), owned=(
        ownership.Entry(name="one", instance_id="same", api_port=8010, **common),
        ownership.Entry(name="two", instance_id="same", api_port=8011, **common)))
    ownership.save(record)
    with pytest.raises(ownership.OwnershipError) as refusal:
        ownership.load(where)
    assert "same instance id" in str(refusal.value)


def test_a_remote_plant_names_the_variable_its_credential_lives_in_and_never_the_value(fleet):
    where = commands.data_dir(fleet)
    ownership.path(where).parent.mkdir(parents=True, exist_ok=True)
    ownership.path(where).write_text(
        '[[owned]]\nname = "far"\npack = "/packs/far"\ninstance_id = "abc"\n'
        f'data_dir = "{where.as_posix()}"\napi_port = 9000\ncontrol = "remote"\n',
        encoding="utf-8")
    with pytest.raises(ownership.OwnershipError) as refusal:
        ownership.load(where)
    assert "never defaulted" in str(refusal.value)


def test_a_remote_plant_whose_credential_is_not_set_here_is_not_owned_here(fleet, monkeypatch):
    where = commands.data_dir(fleet)
    ownership.path(where).parent.mkdir(parents=True, exist_ok=True)
    ownership.path(where).write_text(
        '[[owned]]\nname = "far"\npack = "/packs/far"\ninstance_id = "abc"\n'
        f'data_dir = "{where.as_posix()}"\napi_port = 9000\ncontrol = "remote"\n'
        'credential_env = "FSMES_FAR_CREDENTIAL"\n', encoding="utf-8")
    monkeypatch.delenv("FSMES_FAR_CREDENTIAL", raising=False)
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("apply", "far", data_dir=where)
    assert "not set on this machine" in str(refusal.value)


def test_there_is_no_remote_start_in_this_product(fleet, monkeypatch):
    where = commands.data_dir(fleet)
    ownership.path(where).parent.mkdir(parents=True, exist_ok=True)
    ownership.path(where).write_text(
        '[[owned]]\nname = "far"\npack = "/packs/far"\ninstance_id = "abc"\n'
        f'data_dir = "{where.as_posix()}"\napi_port = 9000\ncontrol = "remote"\n'
        'credential_env = "FSMES_FAR_CREDENTIAL"\n', encoding="utf-8")
    monkeypatch.setenv("FSMES_FAR_CREDENTIAL", "not-read-here")
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("start", "far", data_dir=where)
    assert "no remote start" in str(refusal.value)


def test_a_list_states_its_total_and_calls_silence_unknown(created):
    _entry, fleet = created
    said: list[str] = []
    result = commands.listing(root=fleet, echo=said.append)
    assert result["totals"] == {"plants": 1, "answered": 0, "empty": 0,
                                "unknown": 1, "owned": 1}
    assert said[0] == "1 plants, 0 answered, 0 answered but empty, 1 unknown; 1 owned."
    assert "unknown" in said[1] and "down" not in " ".join(said)


# ------------------------------------------------------- the plant's own half


def test_a_plant_told_where_its_data_lives_reports_the_instance_id_it_was_given(
        fleet, monkeypatch):
    where = commands.data_dir(fleet)
    ownership.instance_path(where, "machining").write_text("an-id\n", encoding="utf-8")
    monkeypatch.setenv("MES_PLANT_NAME", "machining")
    monkeypatch.setenv(identity.DATA_DIR_SETTING, str(where))
    from fsmes.config import get_settings

    get_settings.cache_clear()
    assert identity.instance_id() == "an-id"
    get_settings.cache_clear()


def test_a_plant_nothing_created_reports_no_instance_id_rather_than_omitting_it(client):
    """Unknown is not zero, and no id is not "probably fine": it is the
    answer that means no fleet tool owns this plant."""
    body = client.get("/health").json()
    assert "instance_id" in body
    assert body["instance_id"] is None


def test_a_plant_started_by_the_fleet_is_told_where_its_data_directory_is(fleet):
    env = plants.plant_env("machining", {"env": {}}, fleet)
    assert env[identity.DATA_DIR_SETTING] == str(plants.data_dir(fleet))


# ---------------------------------------------------------------- the ratchet

#: What "acting" looks like in this module: starting or stopping processes,
#: applying a pack, writing the ownership file or anything else on disk.
#: Reading a pack is not acting, which is why `create` may learn a plant's
#: name before the gate can be asked about it.
ACTS = ("plants.start", "plants.stop", "plants.run", "applier.apply", "owned.remember",
        "owned.save", "write_text", "mkdir", "unlink", "subprocess",
        # Creating accounts is acting on a plant, and it is reached through a
        # private helper - which `_functions` does not walk into - so the name
        # of the call is listed here instead.
        "_lab_accounts", "auth.create_user")

#: Functions here that neither write to a plant nor read one: pure helpers.
#: Named so that a new function has to be classified by whoever adds it.
HELPERS = ("data_dir", "render")


def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}


def _calls(node: ast.FunctionDef) -> list[tuple[int, str]]:
    return [(child.lineno, ast.unparse(child.func))
            for child in ast.walk(node) if isinstance(child, ast.Call)]


def test_every_verb_in_the_fleet_command_is_classified_as_a_write_or_a_read():
    named = set(commands.WRITES) | set(commands.READS) | set(HELPERS)
    assert set(_functions()) == named, (
        "a public function in fsmes/fleet/commands.py is in neither WRITES nor READS. "
        "Classify it: a write path must call owned.gate first, and this test is what "
        "makes that checkable.")


def test_nothing_outside_the_write_verbs_acts_on_a_plant():
    for name, node in _functions().items():
        if name in commands.WRITES:
            continue
        acting = [call for _, call in _calls(node) if any(a in call for a in ACTS)]
        assert not acting, (
            f"{name} is not a write verb and calls {acting}. Either it is a write verb - "
            "in which case put it in WRITES and call owned.gate first - or it should not "
            "be acting at all.")


def test_every_write_verb_asks_the_gate_before_it_does_anything():
    for name in commands.WRITES:
        node = _functions()[name]
        calls = _calls(node)
        gates = [line for line, call in calls if call.endswith("owned.gate")]
        assert gates, (
            f"{name} changes something and never calls owned.gate. Every write path in "
            "fsmes fleet checks ownership first; decision 0023 is written to make that "
            "checkable, and this is the check.")
        acting = [(line, call) for line, call in calls if any(a in call for a in ACTS)]
        for line, call in acting:
            assert line > min(gates), (
                f"{name} calls {call} on line {line}, before the ownership gate on line "
                f"{min(gates)}. The gate is called first, always.")


# ------------------------------------------- a plant that is running and mute


def a_live_pid_file(fleet, name: str) -> int:
    """This test process's own pid, written where the plant's would be. It is
    alive by definition and nothing signals it: `plants.stop` is a recorder in
    every test that gets this far."""
    where = commands.data_dir(fleet)
    (where / f"{name}.pids").write_text(f"{os.getpid()}\n", encoding="utf-8")
    return os.getpid()


def test_a_silent_plant_whose_processes_are_alive_is_refused_with_the_reason_and_the_way_out(
        created):
    """The state the lab could only escape with `kill`: `/health` stopped
    answering while the plant was still running, so `stop` refused - and said
    nothing about the pid file it could see."""
    entry, fleet = created
    pid = a_live_pid_file(fleet, entry.name)
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("stop", entry.name, data_dir=commands.data_dir(fleet))
    why = str(refusal.value)
    assert "did not answer" in why
    assert str(pid) in why and "still alive" in why
    assert "--force" in why


def test_forcing_a_stop_reaches_the_process_control_and_says_why_it_did(created, monkeypatch):
    """`--force` gives up liveness, and only liveness."""
    entry, fleet = created
    a_live_pid_file(fleet, entry.name)
    done = recorded(monkeypatch)
    lines: list[str] = []
    commands.stop(entry.name, root=fleet, force=True, echo=lines.append)
    assert done == ["stop machining"]
    assert any("stopping it anyway" in line for line in lines)
    assert any("instance id" in line for line in lines)


def test_forcing_a_stop_never_reaches_a_plant_this_installation_does_not_own(fleet, monkeypatch):
    """The flag is not a way past ownership, and there is no flag that is."""
    done = recorded(monkeypatch)
    with pytest.raises(ownership.NotOwned) as refusal:
        commands.stop("somebody-elses-plant", root=fleet, force=True, echo=lambda _: None)
    assert "no record of creating" in str(refusal.value)
    assert done == []


def test_forcing_a_stop_is_still_refused_when_the_plant_took_its_ownership_back(created):
    """Condition 2 is corroborated by the id in the plant's data directory
    while it is silent. Delete it and `--force` refuses like everything else."""
    entry, fleet = created
    where = commands.data_dir(fleet)
    a_live_pid_file(fleet, entry.name)
    ownership.instance_path(where, entry.name).unlink()
    with pytest.raises(ownership.NotOwned) as refusal:
        ownership.gate("stop", entry.name, data_dir=where, force=True)
    assert "revokes ownership" in str(refusal.value)


def test_only_stop_can_be_forced(fleet):
    """`--force` exists for one state. Nothing else in this product takes it,
    and a verb added to the forced list has to be argued for here."""
    assert ownership.WHILE_SILENT_IF_FORCED == ("stop",)
    assert not set(ownership.WHILE_SILENT) & set(ownership.WHILE_SILENT_IF_FORCED)


# -------------------------------------------------- answered, but empty


def test_a_plant_that_says_it_has_no_line_is_listed_as_empty_and_not_as_answered(created):
    """The third state, from what the plant said and from nothing else."""
    entry, fleet = created
    ask = answering(plant=entry.name, instance_id=entry.instance_id)
    empty = lambda where, **k: observe.Answer(  # noqa: E731
        f"{where}/pack", True, status=200,
        body={"schema": {"revision": "abc123", "head": "abc123", "at_head": True,
                         "answered": True},
              "line": {"equipment": 0, "answered": True}})
    said: list[str] = []
    result = commands.listing(root=fleet, echo=said.append, ask=ask, ask_pack=empty)
    assert result["totals"] == {"plants": 1, "answered": 0, "empty": 1,
                                "unknown": 0, "owned": 1}
    assert said[0] == "1 plants, 0 answered, 1 answered but empty, 0 unknown; 1 owned."
    assert "no line" in said[1]


def test_a_plant_that_did_not_answer_its_pack_is_not_called_empty(created):
    """Unasked is not empty. A plant too old to answer `/pack`, or one whose
    database did not answer, stays `answered`."""
    entry, fleet = created
    ask = answering(plant=entry.name, instance_id=entry.instance_id)
    quiet = lambda where, **k: observe.Answer(where, False, why="did not answer")  # noqa: E731
    result = commands.listing(root=fleet, echo=lambda _: None, ask=ask, ask_pack=quiet)
    assert result["totals"]["answered"] == 1 and result["totals"]["empty"] == 0


def test_a_plant_whose_database_did_not_answer_is_not_called_empty(created):
    entry, fleet = created
    ask = answering(plant=entry.name, instance_id=entry.instance_id)
    unreachable = lambda where, **k: observe.Answer(  # noqa: E731
        f"{where}/pack", True, status=200,
        body={"schema": {"revision": None, "head": None, "at_head": None,
                         "answered": False},
              "line": {"equipment": None, "answered": False}})
    result = commands.listing(root=fleet, echo=lambda _: None, ask=ask, ask_pack=unreachable)
    assert result["totals"]["answered"] == 1 and result["totals"]["empty"] == 0


def test_a_plant_that_has_never_been_migrated_is_empty_and_says_which_kind(created):
    entry, fleet = created
    ask = answering(plant=entry.name, instance_id=entry.instance_id)
    unmigrated = lambda where, **k: observe.Answer(  # noqa: E731
        f"{where}/pack", True, status=200,
        body={"schema": {"revision": None, "head": "abc123", "at_head": False,
                         "answered": True},
              "line": {"equipment": None, "answered": False}})
    said: list[str] = []
    commands.listing(root=fleet, echo=said.append, ask=ask, ask_pack=unmigrated)
    assert "no schema" in said[1]
