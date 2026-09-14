"""Environments: a fleet whose packs live outside the repository.

`test` is the checkout on main with the lab fleet; `prod` is a second
checkout pinned to a tag whose fleet, packs, data and secrets live under
~/.config/fsmes/prod. The one code path serves both: the fleet file is the
environment, and every difference between two plants is in a pack.

Before M8 piece 3 the fleet file *was* the plant - seventeen keys per entry,
read into a plain dict. These tests kept their shape when the plant moved
into a pack, which is the point: nothing about running a plant changed.
"""

import os
import subprocess
from pathlib import Path

import pytest

from fsmes import plant
from fsmes.pack import fleet

PROD_PACK = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "bottling"
label = "prod bottling"
timezone = "America/Chicago"
profile = "plant"

[serve]
api_host = "127.0.0.1"
api_port = 9010
opc_endpoint = "opc.tcp://127.0.0.1:4941/fsmes/bottling"
secret_key_env = "FSMES_BOTTLING_SECRET_KEY"

[files]
tag_map = "tag_map.json"

[[accounts]]
code = "DEMO"
name = "Demo visitor"
role = "viewer"
password_env = "FSMES_DEMO_PASSWORD"

[[accounts]]
code = "FLOOR-SIM"
name = "Simulated shop floor"
role = "operator"
password_env = "MES_OPERATOR_PASSWORD"
'''


@pytest.fixture()
def prod_fleet(tmp_path, monkeypatch):
    where = tmp_path / "prod"
    (where / "bottling").mkdir(parents=True)
    (where / "fleet.toml").write_text(
        'packs = ["bottling"]\n\n'
        '[environment]\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n',
        encoding="utf-8")
    (where / "bottling" / "plant.toml").write_text(PROD_PACK, encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "config" / "tag_map_kepsim.json"
    (where / "bottling" / "tag_map.json").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv(plant.REGISTRY_ENV, str(where / "fleet.toml"))
    monkeypatch.setenv("FSMES_BOTTLING_SECRET_KEY", "a-real-secret")
    monkeypatch.setenv("FSMES_DEMO_PASSWORD", "look-only")
    monkeypatch.setenv("MES_OPERATOR_PASSWORD", "floor-secret")
    plant.environment.cache_clear()
    yield where
    plant.environment.cache_clear()


def test_the_fleet_can_live_outside_the_checkout(prod_fleet, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    plants = fleet.load(root)
    assert list(plants) == ["bottling"] and plants["bottling"]["api_port"] == 9010
    assert plants["bottling"]["pack"] == (prod_fleet / "bottling").resolve()
    assert plant.data_dir(root) == tmp_path / "data"
    assert (tmp_path / "data").is_dir(), "the data directory is created where the environment says"


def test_the_lab_fleet_is_still_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv(plant.REGISTRY_ENV, raising=False)
    plant.environment.cache_clear()
    root = tmp_path / "repo"
    (root / "labs" / "multiplant" / "x").mkdir(parents=True)
    (root / plant.REGISTRY).write_text('packs = ["x"]\n', encoding="utf-8")
    (root / "labs" / "multiplant" / "x" / "plant.toml").write_text(
        '[pack]\nformat = 1\n[plant]\nname = "x"\n', encoding="utf-8")
    assert list(fleet.load(root)) == ["x"]
    assert plant.data_dir(root) == root / "labs" / "multiplant" / ".data"
    plant.environment.cache_clear()


def test_a_fleet_file_that_still_describes_plants_says_what_to_do(tmp_path, monkeypatch):
    """The refusal somebody upgrading will meet. A registry that was written
    before packs is not read as an empty fleet - it is named, with the command
    that converts it."""
    monkeypatch.setenv(plant.REGISTRY_ENV, str(tmp_path / "plants.toml"))
    plant.environment.cache_clear()
    (tmp_path / "plants.toml").write_text('[plants.bottling]\napi_port = 1\n', encoding="utf-8")
    with pytest.raises(fleet.FleetError, match="pack migrate"):
        fleet.load(tmp_path)
    plant.environment.cache_clear()


def test_a_prod_plant_has_its_own_secret_and_no_lab_key(prod_fleet, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    cfg = fleet.load(root)["bottling"]
    env = plant.plant_env("bottling", cfg, root)
    assert env["MES_SECRET_KEY"] == "a-real-secret"
    assert env["MES_API_HOST"] == "127.0.0.1" and env["MES_API_PORT"] == "9010"
    assert env["MES_DATABASE_URL"].endswith("/data/bottling.db")
    assert env["MES_PLANT_TIMEZONE"] == "America/Chicago"
    assert env["MES_PLANT_PROFILE"] == "plant"

    lab = dict(cfg, env={k: v for k, v in cfg["env"].items() if k != "MES_SECRET_KEY"})
    assert plant.plant_env("bottling", lab, root)["MES_SECRET_KEY"].startswith("lab-"), (
        "no key where this plant runs: the lab default, loudly named")


def test_accounts_come_from_the_pack_not_the_lab_list(prod_fleet, tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(plant.subprocess, "run", fake_run)
    monkeypatch.setattr(plant, "fsmes_bin", lambda: "fsmes")
    root = tmp_path / "checkout"
    root.mkdir()
    cfg = fleet.load(root)["bottling"]
    said: list[str] = []
    plant.ensure_accounts(root, plant.plant_env("bottling", cfg, root), cfg, echo=said.append)
    made = {c[2]: (c[3 + 1], c[-1]) for c in calls}  # code -> (password, role)
    assert made == {"DEMO": ("look-only", "viewer"), "FLOOR-SIM": ("floor-secret", "operator")}
    assert "ADMIN" not in made and "SCOTT" not in made, "lab accounts never reach a prod plant"


def test_an_account_whose_password_is_not_set_is_refused(prod_fleet, tmp_path, monkeypatch):
    monkeypatch.delenv("FSMES_DEMO_PASSWORD")
    root = tmp_path / "checkout"
    root.mkdir()
    cfg = fleet.load(root)["bottling"]
    with pytest.raises(SystemExit, match="FSMES_DEMO_PASSWORD"):
        plant.ensure_accounts(root, plant.plant_env("bottling", cfg, root), cfg, echo=lambda s: None)


def test_lab_plants_still_get_lab_accounts(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(plant.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    monkeypatch.setattr(plant, "fsmes_bin", lambda: "fsmes")
    plant.ensure_accounts(tmp_path, dict(os.environ), {"api_port": 1}, echo=lambda s: None)
    assert {c[2] for c in calls} >= {"ADMIN", "FLOOR-SIM", "AGENT"}


def test_two_packs_that_call_themselves_the_same_thing_are_refused(tmp_path, monkeypatch):
    """A fleet is a list of directories and a plant's name is in its pack, so
    two directories can claim one name. Two plants a console cannot tell apart
    is the failure M8 exists to prevent; it is refused here, by name."""
    monkeypatch.setenv(plant.REGISTRY_ENV, str(tmp_path / "fleet.toml"))
    plant.environment.cache_clear()
    (tmp_path / "fleet.toml").write_text('packs = ["a", "b"]\n', encoding="utf-8")
    for directory in ("a", "b"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "plant.toml").write_text(
            '[pack]\nformat = 1\n[plant]\nname = "twin"\n', encoding="utf-8")
    with pytest.raises(fleet.FleetError, match="both call themselves"):
        fleet.load(tmp_path)
    plant.environment.cache_clear()


def test_the_promote_script_and_prod_unit_ship_with_the_repo():
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    script = (deploy / "promote.sh").read_text(encoding="utf-8")
    unit = (deploy / "fsmes-prod-plant@.service").read_text(encoding="utf-8")
    assert "rollback" in script and "migrate" in script and "sim-generate" in script
    assert "FSMES_PLANT_REGISTRY" in unit and "MES_DESIGN_CHAT=0" in unit
