"""The plant pack format, and the refusals that make it worth having.

Decision 0022 says a pack is declarative data that carries no code, holds no
secret, and cannot change what a number means. The registry it replaces could
say none of that: seventeen keys read into a plain dict, so a typo was a
default nobody saw and the file drifted from its own documentation inside two
weeks without anything failing.

So most of this file is about refusals. Each one is a sentence a person
standing next to a plant has to be able to act on, and each is pinned here
because the value of a validator is exactly the list of things it will not
let past.
"""

import json
import shutil
from pathlib import Path

import pytest

from fsmes import __version__
from fsmes.pack import apply as applier
from fsmes.pack import check as checker
from fsmes.pack import format as fmt
from fsmes.pack import masterdata, migrate

REPO = Path(__file__).resolve().parents[1]
LABS = REPO / "labs"

#: The packs this repository ships. Every one of them is checked below, so a
#: pack that stops being valid is a failing test rather than a plant that
#: refuses to start on somebody's morning.
SHIPPED = ("labs/multiplant/bottling", "labs/multiplant/machining",
           "labs/multiplant/finewire", "labs/cutlery", "labs/megafactory/full")

MINIMAL = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "acceptance"
label = "A plant written for this test"
timezone = "Europe/Berlin"
profile = "laptop"
'''


@pytest.fixture()
def pack_dir(tmp_path):
    """A pack that checks clean, ready to be broken one key at a time."""
    (tmp_path / "plant.toml").write_text(MINIMAL, encoding="utf-8")
    return tmp_path


def written(directory: Path, toml: str) -> Path:
    (directory / "plant.toml").write_text(toml, encoding="utf-8")
    return directory


def problems(directory: Path, **kwargs) -> list[str]:
    return [str(p) for p in checker.check(directory, **kwargs).problems]


# ------------------------------------------------------------- the refusals


def test_a_key_this_format_does_not_have_is_an_error_not_a_default(pack_dir):
    """The one thing the registry could never do. `tomllib` into a plain dict
    cannot tell a key from a typo, so `inspec_every` was silently nothing."""
    written(pack_dir, MINIMAL + '\n[floor]\ninspec_every = 900\n')
    found = problems(pack_dir)
    assert len(found) == 1
    assert "[floor] inspec_every" in found[0] and "is not a key this format has" in found[0]
    assert "inspect_every" in found[0], "the sentence lists the keys that do exist"


def test_a_table_this_format_does_not_have_is_an_error_too(pack_dir):
    written(pack_dir, MINIMAL + '\n[scheduling]\nhorizon = 3\n')
    assert any("[scheduling]" in line and "not a table" in line for line in problems(pack_dir))


def test_a_time_zone_this_machine_does_not_know_is_refused(pack_dir):
    written(pack_dir, MINIMAL.replace("Europe/Berlin", "Europe/Kansas_City"))
    found = problems(pack_dir)
    assert any("Europe/Kansas_City" in line and "IANA" in line for line in found), found


def test_a_pack_with_no_time_zone_is_refused_rather_than_given_one(pack_dir):
    """Unknown is not zero, and a defaulted clock is a guess about a plant.
    The registry never had a zone at all, which is why every plant's shift
    boundaries were drawn wherever the machine happened to be set."""
    written(pack_dir, MINIMAL.replace('timezone = "Europe/Berlin"\n', ""))
    assert any("[plant] timezone" in line for line in problems(pack_dir))


def test_a_pack_may_not_rename_a_word_a_number_depends_on(pack_dir):
    """Decision 0022 clause 3. Two plants that use one word for two things is
    a fleet view comparing dialects, which is house rule 1 across a company."""
    written(pack_dir, MINIMAL + '\n[words]\nrunning = "making"\n')
    found = problems(pack_dir)
    assert len(found) == 1
    assert "renames an equipment state" in found[0]
    assert "dialects" in found[0]

    # And the same for a capability, a role and an event kind.
    for term, what in (("audit.read", "a capability"), ("supervisor", "a role"),
                       ("order_hold", "a domain event kind"), ("oee", "a KPI")):
        written(pack_dir, MINIMAL + f'\n[words]\n"{term}" = "something else"\n')
        assert any(what in line for line in problems(pack_dir)), (term, problems(pack_dir))


def test_a_pack_may_rename_a_label_on_a_screen(pack_dir):
    """The other half. A plant that says coil rather than lot gets to say
    coil - that is what the vocabulary is for."""
    written(pack_dir, MINIMAL + '\n[words]\nlot = "coil"\nmaterial = "alloy"\n')
    assert problems(pack_dir) == []
    assert fmt.words(fmt.read(pack_dir)) == {"lot": "coil", "material": "alloy"}


def test_a_display_term_the_product_does_not_have_is_refused(pack_dir):
    """A rename that silently renames nothing is the registry's typo again."""
    written(pack_dir, MINIMAL + '\n[words]\nwidget = "thingy"\n')
    assert any("not a display term" in line for line in problems(pack_dir))


def test_a_requires_this_release_does_not_satisfy_is_refused(pack_dir):
    written(pack_dir, MINIMAL.replace('requires = ">=0.1.2"', 'requires = ">=0.9"'))
    found = problems(pack_dir)
    assert any("not written for this release" in line for line in found), found
    # And the same pack is fine on the release it was written for.
    assert problems(pack_dir, version="0.9.1") == []


def test_a_requires_nobody_can_read_is_refused_as_the_requirement_it_is(pack_dir):
    written(pack_dir, MINIMAL.replace('requires = ">=0.1.2"', 'requires = "the latest one"'))
    assert any("cannot be read" in line for line in problems(pack_dir))


def test_a_pack_from_a_later_release_is_refused_rather_than_half_understood(pack_dir):
    written(pack_dir, MINIMAL.replace("format = 1", f"format = {fmt.FORMAT + 1}"))
    assert any("refused rather than half-understood" in line for line in problems(pack_dir))


def test_a_module_this_version_does_not_have_is_refused(pack_dir):
    written(pack_dir, MINIMAL + '\n[modules]\nforecasting = false\n')
    found = problems(pack_dir)
    assert any("forecasting" in line and "not a module in this version" in line for line in found)


def test_the_kernel_cannot_be_switched_off_by_a_pack(pack_dir):
    written(pack_dir, MINIMAL + '\n[modules]\nauth = false\n')
    assert any("part of the kernel" in line for line in problems(pack_dir))


def test_a_file_the_pack_names_and_does_not_have_is_refused(pack_dir):
    written(pack_dir, MINIMAL + '\n[files]\ntag_map = "tag_map.json"\n')
    assert any("[files] tag_map" in line and "not in this pack" in line
               for line in problems(pack_dir))


def test_a_file_its_own_reader_refuses_is_refused_here_too(pack_dir):
    """Its *own* reader, deliberately: a tag map this accepted and the OPC
    agent then refused would be worse than no check at all."""
    (pack_dir / "tag_map.json").write_text(
        json.dumps({"machines": [{"equipment": "X1"}]}), encoding="utf-8")
    written(pack_dir, MINIMAL + '\n[files]\ntag_map = "tag_map.json"\n')
    found = problems(pack_dir)
    assert any("tag_map.json is not usable" in line for line in found), found


def test_a_file_outside_the_pack_is_refused_because_a_pack_is_one_directory(pack_dir):
    written(pack_dir, MINIMAL + '\n[files]\ntag_map = "../elsewhere/tag_map.json"\n')
    assert any("points outside the pack" in line for line in problems(pack_dir))


def test_generated_line_data_is_the_one_path_that_may_point_outward(pack_dir):
    """And a replay directory that has not been generated yet is unknown, not
    a failure - otherwise every fresh checkout has an invalid pack."""
    written(pack_dir, MINIMAL + '\n[files]\nreplay_dir = "../kepsim/out"\n')
    report = checker.check(pack_dir)
    assert report.ok
    assert any("generated" in str(u) for u in report.unknowns)


def test_a_secret_is_refused_by_name_rather_than_as_an_unknown_key(pack_dir):
    """"secret_key is not a key this format has" is a worse answer than "a
    pack never holds a secret; name the variable it lives in"."""
    written(pack_dir, MINIMAL + '\n[serve]\nsecret_key = "hunter2"\n')
    found = problems(pack_dir)
    assert len(found) == 1 and "never holds a secret" in found[0]
    assert "secret_key_env" in found[0]


def test_code_is_refused_by_name_too(pack_dir):
    """The `init` key: a Python file the registry named and ran as a
    subprocess with the plant's environment. Decision 0022 rule 1."""
    written(pack_dir, MINIMAL + '\n[files]\ninit = "seed.py"\n')
    found = problems(pack_dir)
    assert len(found) == 1 and "carries no code" in found[0]


def test_every_problem_is_listed_and_not_only_the_first(pack_dir):
    """A person fixing a pack beside a line should need one round trip."""
    written(pack_dir, '''
[pack]
format = 1
requires = ">=99"

[plant]
name = "not a name!"
timezone = "Mars/Olympus"

[floor]
inspec_every = 900

[words]
running = "making"
''')
    found = problems(pack_dir)
    assert len(found) >= 4, found


def test_what_cannot_be_proved_without_a_plant_is_reported_as_unknown():
    """House rule 2 at the configuration boundary. A report that lists only
    what it proved reads as a report that proved everything."""
    report = checker.check(LABS / "multiplant" / "finewire")
    assert report.ok
    said = [str(u) for u in report.unknowns]
    assert any("OPC endpoint" in u for u in said)
    assert any("the database" in u for u in said)
    assert any("FSMES_FINEWIRE_OPERATOR_PASSWORD" in u for u in said)


# -------------------------------------------------------- the protected list


def test_the_protected_list_is_read_from_the_product_and_not_typed_here():
    """An enumerated list stops growing the day somebody adds a capability.
    This one is built by importing the product, so a state, a role or an
    event kind added next month is protected the day it lands."""
    from fsmes.domain.equipment import EquipmentStateName
    from fsmes.domain.workorders import OrderStatus
    from fsmes.services.capabilities import BUILTIN_ROLES, CAPABILITIES

    protected = checker.protected_terms()
    for value in EquipmentStateName:
        assert str(value) in protected
    for value in OrderStatus:
        assert str(value) in protected
    for name in CAPABILITIES:
        assert name in protected
    for name in BUILTIN_ROLES:
        assert name in protected
    assert len(protected) > 60, len(protected)


def test_nothing_a_plant_may_rename_is_also_protected():
    """The two lists must not overlap, or a term is both offered and refused
    and the second message a person sees contradicts the first."""
    overlap = set(checker.RENAMEABLE) & set(checker.protected_terms())
    assert not overlap, (
        f"{sorted(overlap)} is offered as renameable and refused as protected. Whichever "
        "message a person sees first, the second one contradicts it. `equipment` and "
        "`operator` were both in this state when the test was written - they are the "
        "name of an event field and of a built-in role - and they came out of "
        "RENAMEABLE, not out of the protected list.")


# ----------------------------------------------------------- what it compiles


def test_a_pack_compiles_to_the_settings_it_states_and_to_no_others(pack_dir):
    """A key the pack does not carry is absent, so the product's own default
    stands. A pack is never a second opinion about what a default is."""
    written(pack_dir, MINIMAL)
    values = fmt.settings(fmt.read(pack_dir))
    assert values["MES_PLANT_NAME"] == "acceptance"
    assert values["MES_PLANT_TIMEZONE"] == "Europe/Berlin"
    assert values["MES_MODULES"] == "all"
    assert "MES_DATABASE_URL" not in values and "MES_API_PORT" not in values


def test_modules_compile_to_the_setting_the_module_wall_already_reads():
    """`MES_MODULES` came first, in piece 2. `[modules]` compiles down to it
    rather than becoming a second mechanism that could disagree."""
    from fsmes import modules as registry

    pack = fmt.read(LABS / "multiplant" / "finewire")
    spec = fmt.module_spec(pack)
    on = registry.resolve(spec)
    assert "serialization" not in on and "coa" not in on
    assert "quality" in on and "maintenance" in on


def test_a_module_added_by_a_later_release_arrives_switched_on(pack_dir):
    """`all` is written first and the pack's own words after it, so a pack
    that has never heard of a module serves it rather than silently not."""
    written(pack_dir, MINIMAL + '\n[modules]\nquality = false\n')
    assert fmt.module_spec(fmt.read(pack_dir)) == "all,-quality"


# --------------------------------------------------------- applying and drift


@pytest.fixture()
def applied(tmp_path, monkeypatch):
    """A pack applied to a database of its own, with every settings cache
    put back afterwards so nothing else in the suite inherits it."""
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    source = LABS / "multiplant" / "machining"
    directory = tmp_path / "machining"
    shutil.copytree(source, directory)
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'plant.db').as_posix()}")
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    yield directory, tmp_path
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


def test_applying_a_pack_seeds_the_master_data_it_carries(applied):
    directory, into = applied
    said: list[str] = []
    receipt = applier.apply(directory, into=into, echo=said.append)
    assert receipt["seeded"]["equipment"] == {"made": 7, "present": 0}
    assert receipt["seeded"]["work_orders"] == {"made": 1, "present": 0}
    assert receipt["revision"], "the schema was brought to head before anything was written"


def test_applying_a_pack_twice_makes_nothing_twice(applied):
    directory, into = applied
    applier.apply(directory, into=into, echo=lambda _: None)
    again = applier.apply(directory, into=into, echo=lambda _: None)
    for kind, counts in again["seeded"].items():
        assert counts["made"] == 0, kind
        assert counts["present"] > 0, kind


def test_a_rated_cycle_time_is_read_from_the_tag_map_and_not_repeated(applied):
    """OEE performance is ideal cycle x count / runtime, so a rate the master
    data repeated and let drift would produce a number that means nothing."""
    from sqlalchemy import select

    from fsmes.db import session_scope
    from fsmes.domain import Equipment

    directory, into = applied
    applier.apply(directory, into=into, echo=lambda _: None)
    with session_scope() as session:
        saw = session.scalar(select(Equipment).where(Equipment.code == "SAW01"))
        assert saw.ideal_cycle_seconds == pytest.approx(2.727)


def test_status_says_a_plant_has_drifted_when_a_file_in_its_pack_changes(applied):
    directory, into = applied
    applier.apply(directory, into=into, echo=lambda _: None)
    assert applier.status(directory, into=into).drifted is False

    equipment = directory / "masterdata" / "equipment.json"
    rows = json.loads(equipment.read_text(encoding="utf-8"))
    rows.append({"code": "SAW02", "name": "Bar Saw 02", "level": "work_unit", "parent": "NGLINE"})
    equipment.write_text(json.dumps(rows), encoding="utf-8")

    state = applier.status(directory, into=into)
    assert state.drifted is True
    assert any("changed since it was applied" in line for line in state.render())


def test_status_never_calls_a_plant_that_was_never_applied_undrifted(applied):
    """Never applied and no drift are different facts, and a console that
    rendered the first as the second would be reporting silence as agreement."""
    directory, into = applied
    state = applier.status(directory, into=into)
    assert state.drifted is None
    assert any("never" in line for line in state.render())


def test_a_generated_replay_directory_is_not_part_of_the_fingerprint(applied):
    """A pack's fingerprint covers what a person wrote. Line data is what a
    generator produced, and it can be gigabytes."""
    directory, _ = applied
    before = fmt.fingerprint(fmt.read(directory))
    (directory / "out").mkdir(exist_ok=True)
    (directory / "out" / "SAW01.csv").write_text("t,State\n0,1\n", encoding="utf-8")
    assert fmt.fingerprint(fmt.read(directory)) == before


# ------------------------------------------------------------------ migrating


REGISTRY = '''
[plants.oldworld]
label      = "Old World Works — before there were packs"
api_host   = "127.0.0.1"
api_port   = 8060
opc_port   = 4850
tag_map    = "tag_map.json"
replay_dir = "out"
init       = "seed.py"
post_boot  = "drive.py"
secret_key = "a-real-secret"
agent      = "something nothing ever read"
inspect_every = 600
'''


def test_migrating_a_registry_entry_says_what_it_moved(tmp_path):
    (tmp_path / "plants.toml").write_text(REGISTRY, encoding="utf-8")
    (tmp_path / "tag_map.json").write_text(
        json.dumps({"machines": [{"equipment": "M1", "object": "M1"}]}), encoding="utf-8")
    receipt = migrate.from_registry(tmp_path / "plants.toml", "oldworld", tmp_path / "pack",
                                   root=tmp_path)
    moved = "\n".join(receipt.moved)
    assert "label -> [plant] label" in moved
    assert "api_port -> [serve] api_port" in moved
    assert "inspect_every -> [floor] inspect_every" in moved
    assert "opc_port -> [serve] opc_endpoint" in moved
    written_pack = fmt.read(tmp_path / "pack")
    assert written_pack.name == "oldworld"
    assert written_pack.table("serve")["opc_endpoint"].endswith(":4850/fsmes/oldworld")


def test_migrating_drops_code_and_secrets_and_says_why(tmp_path):
    (tmp_path / "plants.toml").write_text(REGISTRY, encoding="utf-8")
    receipt = migrate.from_registry(tmp_path / "plants.toml", "oldworld", tmp_path / "pack",
                                   root=tmp_path)
    dropped = "\n".join(receipt.dropped)
    assert "init:" in dropped and "carries no code" in dropped
    assert "post_boot:" in dropped
    assert "agent:" in dropped and "ever read it" in dropped
    text = (tmp_path / "pack" / "plant.toml").read_text(encoding="utf-8")
    assert "a-real-secret" not in text, "a migration never carries a secret forward"
    assert "secret_key_env" in text, "it names the variable instead"


def test_migrating_never_guesses_a_time_zone(tmp_path):
    """The one value a registry could not hold and a pack must. Filling it in
    from this machine's clock would tell a plant in Chicago it works in
    London - so the pack is written without one, and the receipt says so."""
    (tmp_path / "plants.toml").write_text(REGISTRY, encoding="utf-8")
    receipt = migrate.from_registry(tmp_path / "plants.toml", "oldworld", tmp_path / "pack",
                                   root=tmp_path)
    assert "timezone" not in (tmp_path / "pack" / "plant.toml").read_text(encoding="utf-8")
    assert any("timezone" in line for line in receipt.incomplete)
    # And the pack it wrote is refused until a person sets it.
    assert any("[plant] timezone" in line for line in problems(tmp_path / "pack"))


def test_a_pack_already_at_this_format_says_there_is_nothing_to_do(pack_dir):
    assert migrate.migrate(pack_dir).unchanged is True


# ------------------------------------------------------------- the master data


def test_master_data_that_names_something_it_does_not_declare_is_refused(tmp_path):
    (tmp_path / "materials.json").write_text('[{"code": "FG-A", "name": "A"}]', encoding="utf-8")
    (tmp_path / "work_orders.json").write_text(
        '[{"code": "WO-1", "material": "FG-B", "quantity": 5}]', encoding="utf-8")
    found = masterdata.problems(tmp_path)
    assert any("FG-B" in line and "does not declare" in line for line in found), found


def test_a_master_data_file_nobody_reads_is_refused(tmp_path):
    """Master data nobody reads is master data somebody thinks is loaded."""
    (tmp_path / "shifts.json").write_text("[]", encoding="utf-8")
    assert any("not a kind of master data" in line for line in masterdata.problems(tmp_path))


# ------------------------------------------------------------- what we ship


@pytest.mark.parametrize("where", SHIPPED)
def test_every_pack_this_repository_ships_checks_clean(where):
    report = checker.check(REPO / where, version=__version__)
    assert report.ok, [str(p) for p in report.problems]


def test_the_fleet_file_lists_every_multiplant_pack():
    import tomllib

    fleet = tomllib.loads((LABS / "multiplant" / "fleet.toml").read_text(encoding="utf-8"))
    assert sorted(fleet["packs"]) == ["bottling", "finewire", "machining"]
