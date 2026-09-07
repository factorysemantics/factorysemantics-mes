"""Environments: a plant registry that lives outside the repository.

`test` is the checkout on main with the lab registry; `prod` is a second
checkout pinned to a tag whose registry, data and secrets live under
~/.config/fsmes/prod. The one code path serves both: the registry file is
the environment, and every difference is a field in it.
"""

import os
import subprocess
from pathlib import Path

import pytest

from fsmes import plant


@pytest.fixture()
def prod_registry(tmp_path, monkeypatch):
    reg = tmp_path / "prod" / "plants.toml"
    reg.parent.mkdir()
    reg.write_text(
        '[environment]\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        '\n'
        '[plants.bottling]\n'
        'label = "prod bottling"\n'
        'api_host = "127.0.0.1"\n'
        'api_port = 9010\n'
        'opc_port = 4941\n'
        'tag_map = "config/tag_map_kepsim.json"\n'
        'replay_dir = "labs/kepsim/out"\n'
        'init = "labs/multiplant/bottling/init.py"\n'
        'secret_key = "a-real-secret"\n'
        'accounts = [\n'
        '  { code = "DEMO", name = "Demo visitor", role = "viewer", password_env = "FSMES_DEMO_PASSWORD" },\n'
        '  { code = "FLOOR-SIM", name = "Simulated shop floor", role = "operator",'
        '    password_env = "MES_OPERATOR_PASSWORD" },\n'
        ']\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(plant.REGISTRY_ENV, str(reg))
    monkeypatch.setenv("FSMES_DEMO_PASSWORD", "look-only")
    monkeypatch.setenv("MES_OPERATOR_PASSWORD", "floor-secret")
    plant.environment.cache_clear()
    yield reg
    plant.environment.cache_clear()


def test_the_registry_can_live_outside_the_checkout(prod_registry, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    plants = plant.load_registry(root)
    assert list(plants) == ["bottling"] and plants["bottling"]["api_port"] == 9010
    assert plant.data_dir(root) == tmp_path / "data"
    assert (tmp_path / "data").is_dir(), "the data directory is created where the environment says"


def test_the_lab_registry_is_still_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv(plant.REGISTRY_ENV, raising=False)
    plant.environment.cache_clear()
    root = tmp_path / "repo"
    (root / "labs" / "multiplant").mkdir(parents=True)
    (root / plant.REGISTRY).write_text('[plants.x]\napi_port = 1\n', encoding="utf-8")
    assert list(plant.load_registry(root)) == ["x"]
    assert plant.data_dir(root) == root / "labs" / "multiplant" / ".data"
    plant.environment.cache_clear()


def test_a_prod_plant_has_its_own_secret_and_no_lab_key(prod_registry, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    cfg = plant.load_registry(root)["bottling"]
    env = plant.plant_env("bottling", cfg, root)
    assert env["MES_SECRET_KEY"] == "a-real-secret"
    assert env["MES_API_HOST"] == "127.0.0.1" and env["MES_API_PORT"] == "9010"
    assert env["MES_DATABASE_URL"].endswith("/data/bottling.db")
    lab = plant.plant_env("bottling", {k: v for k, v in cfg.items() if k != "secret_key"}, root)
    assert lab["MES_SECRET_KEY"].startswith("lab-"), "no secret in the registry: the lab default, loudly named"


def test_accounts_come_from_the_registry_not_the_lab_list(prod_registry, tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(plant.subprocess, "run", fake_run)
    monkeypatch.setattr(plant, "fsmes_bin", lambda: "fsmes")
    root = tmp_path / "checkout"
    root.mkdir()
    cfg = plant.load_registry(root)["bottling"]
    said: list[str] = []
    plant.ensure_accounts(root, plant.plant_env("bottling", cfg, root), cfg, echo=said.append)
    made = {c[2]: (c[3 + 1], c[-1]) for c in calls}  # code -> (password, role)
    assert made == {"DEMO": ("look-only", "viewer"), "FLOOR-SIM": ("floor-secret", "operator")}
    assert "ADMIN" not in made and "SCOTT" not in made, "lab accounts never reach a prod plant"


def test_an_account_whose_password_is_not_set_is_refused(prod_registry, tmp_path, monkeypatch):
    monkeypatch.delenv("FSMES_DEMO_PASSWORD")
    root = tmp_path / "checkout"
    root.mkdir()
    cfg = plant.load_registry(root)["bottling"]
    with pytest.raises(SystemExit, match="FSMES_DEMO_PASSWORD"):
        plant.ensure_accounts(root, plant.plant_env("bottling", cfg, root), cfg, echo=lambda s: None)


def test_lab_plants_still_get_lab_accounts(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(plant.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    monkeypatch.setattr(plant, "fsmes_bin", lambda: "fsmes")
    plant.ensure_accounts(tmp_path, dict(os.environ), {"api_port": 1}, echo=lambda s: None)
    assert {c[2] for c in calls} >= {"ADMIN", "FLOOR-SIM", "AGENT"}


def test_the_promote_script_and_prod_unit_ship_with_the_repo():
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    script = (deploy / "promote.sh").read_text(encoding="utf-8")
    unit = (deploy / "fsmes-prod-plant@.service").read_text(encoding="utf-8")
    assert "rollback" in script and "migrate" in script and "sim-generate" in script
    assert "FSMES_PLANT_REGISTRY" in unit and "MES_DESIGN_CHAT=0" in unit
