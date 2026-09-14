"""`fsmes fleet plan`: the fleet as a deployment script needs it.

A promote has to know four things about every plant before it touches one -
where the pack is, where the database is and what kind it is, whether there
is deterministic line data to regenerate, and where to ask the plant whether
it came back. This is the product answering all four, so `deploy/promote.sh`
does not answer them again in bash.
"""

import json

from typer.testing import CliRunner

from fsmes import plant
from fsmes.cli import app
from fsmes.pack import plan as planner

runner = CliRunner()

FILE_PLANT = '''
[pack]
format = 1

[plant]
name = "bottling"
label = "ACME Beverages / Kansas City"
timezone = "America/Chicago"

[serve]
api_host = "127.0.0.1"
api_port = 9010
simulate = true

[files]
replay_dir = "../replay/out"
'''

PG_PLANT = '''
[pack]
format = 1

[plant]
name = "cutlery"
label = "Cutlery, on a server database"
timezone = "America/Chicago"

[serve]
api_host = "0.0.0.0"
api_port = 9030
simulate = false

[storage]
database_url = "postgresql+psycopg://fsmes@127.0.0.1:5432/fsmes_prod_cutlery"
database_password_file = "PASSWORD_FILE"
'''

ODD_PLANT = '''
[pack]
format = 1

[plant]
name = "elsewhere"

[serve]
api_port = 9040

[storage]
database_url = "mysql+pymysql://root@127.0.0.1/elsewhere"
'''


def a_fleet(tmp_path, monkeypatch, packs: dict[str, str]) -> tuple:
    """A fleet file and its packs, outside any checkout, with a data
    directory of its own - the shape prod has."""
    where = tmp_path / "conf"
    where.mkdir()
    data = tmp_path / "data"
    secret = where / "pg-password"
    secret.write_text("not-in-the-pack\n", encoding="utf-8")
    (where / "fleet.toml").write_text(
        f'packs = {json.dumps(sorted(packs))}\n\n[environment]\ndata_dir = "{data.as_posix()}"\n',
        encoding="utf-8")
    for name, body in packs.items():
        (where / name).mkdir()
        (where / name / "plant.toml").write_text(
            body.replace("PASSWORD_FILE", secret.as_posix()), encoding="utf-8")
    monkeypatch.setenv(plant.REGISTRY_ENV, str(where / "fleet.toml"))
    plant.environment.cache_clear()
    root = tmp_path / "checkout"
    root.mkdir()
    return root, where, data


def test_the_plan_names_every_pack_the_fleet_lists_and_says_how_many(tmp_path, monkeypatch):
    root, where, data = a_fleet(tmp_path, monkeypatch,
                                {"bottling": FILE_PLANT, "cutlery": PG_PLANT})
    answer = planner.plan(root)
    assert answer["count"] == 2 and answer["can_back_up"] == 2
    assert [p["name"] for p in answer["plants"]] == ["bottling", "cutlery"]
    assert answer["data_dir"] == str(data)
    assert answer["fleet"] == str(where / "fleet.toml")
    plant.environment.cache_clear()


def test_a_file_plant_says_which_file_and_that_a_copy_backs_it_up(tmp_path, monkeypatch):
    root, _, data = a_fleet(tmp_path, monkeypatch, {"bottling": FILE_PLANT})
    storage = planner.plan(root)["plants"][0]["storage"]
    assert storage["kind"] == "sqlite" and storage["backup"] == "copy"
    assert storage["path"] == (data / "bottling.db").as_posix()
    plant.environment.cache_clear()


def test_a_simulated_plant_names_the_line_data_a_promote_regenerates(tmp_path, monkeypatch):
    root, where, _ = a_fleet(tmp_path, monkeypatch, {"bottling": FILE_PLANT})
    row = planner.plan(root)["plants"][0]
    assert row["simulate"] is True
    assert row["line_data"] == (where / "replay" / "line.json").as_posix()
    assert row["pack"] == str(where / "bottling")
    plant.environment.cache_clear()


def test_a_plant_that_does_not_simulate_has_no_line_data_to_regenerate(tmp_path, monkeypatch):
    root, _, _ = a_fleet(tmp_path, monkeypatch, {"cutlery": PG_PLANT})
    row = planner.plan(root)["plants"][0]
    assert row["simulate"] is False and row["line_data"] is None
    plant.environment.cache_clear()


def test_a_plant_that_serves_every_interface_is_asked_on_loopback(tmp_path, monkeypatch):
    """`0.0.0.0` is where a plant listens, not an address anything dials. The
    promote runs on the machine the plant runs on, so loopback is where it
    asks - guessing the host's own name would be inventing one."""
    root, _, _ = a_fleet(tmp_path, monkeypatch, {"cutlery": PG_PLANT})
    assert planner.plan(root)["plants"][0]["health"] == "http://127.0.0.1:9030"
    plant.environment.cache_clear()


def test_a_postgres_plant_says_its_host_and_database_and_never_its_password(tmp_path, monkeypatch):
    root, _, _ = a_fleet(tmp_path, monkeypatch, {"cutlery": PG_PLANT})
    storage = planner.plan(root)["plants"][0]["storage"]
    assert storage["kind"] == "postgresql" and storage["backup"] == "pg_dump"
    assert storage["host"] == "127.0.0.1" and storage["port"] == 5432
    assert storage["user"] == "fsmes" and storage["dbname"] == "fsmes_prod_cutlery"
    assert storage["password_known"] is True
    assert "not-in-the-pack" not in json.dumps(storage)
    assert "secret_url" not in storage and "secret_password" not in storage
    plant.environment.cache_clear()


def test_the_password_is_in_the_plan_only_when_it_is_asked_for(tmp_path, monkeypatch):
    """`pg_dump` needs it and a promote that cannot back a plant up is not one
    to run - so it can be had, under a name that makes any use of it visible."""
    root, _, _ = a_fleet(tmp_path, monkeypatch, {"cutlery": PG_PLANT})
    storage = planner.plan(root, with_password=True)["plants"][0]["storage"]
    assert storage["secret_password"] == "not-in-the-pack"
    assert "not-in-the-pack" in storage["secret_url"]
    assert "not-in-the-pack" not in storage["url"], "the plain URL stays safe to paste"
    plant.environment.cache_clear()


def test_a_database_this_tooling_cannot_back_up_says_so_rather_than_looking_fine(tmp_path, monkeypatch):
    """Unknown is not zero. A plant on a database the deployment tooling has
    no copy step for is reported as `none` with the sentence saying why, so a
    promote can refuse instead of migrating something it cannot put back."""
    root, _, _ = a_fleet(tmp_path, monkeypatch,
                         {"bottling": FILE_PLANT, "elsewhere": ODD_PLANT})
    answer = planner.plan(root)
    assert answer["count"] == 2 and answer["can_back_up"] == 1
    odd = next(p for p in answer["plants"] if p["name"] == "elsewhere")
    assert odd["storage"]["backup"] == "none"
    assert "mysql" in odd["storage"]["why"]
    plant.environment.cache_clear()


def test_a_password_file_that_is_not_there_is_a_fact_about_that_plant(tmp_path, monkeypatch):
    """One unreadable plant does not stop the plan being read. The promote
    refuses on it by name, which is more use than a traceback about a path."""
    root, where, _ = a_fleet(tmp_path, monkeypatch,
                             {"bottling": FILE_PLANT, "cutlery": PG_PLANT})
    (where / "pg-password").unlink()
    answer = planner.plan(root)
    assert answer["count"] == 2 and answer["can_back_up"] == 1
    odd = next(p for p in answer["plants"] if p["name"] == "cutlery")
    assert odd["storage"]["kind"] == "unreadable" and odd["storage"]["backup"] == "none"
    plant.environment.cache_clear()


def test_the_command_prints_json_a_script_can_read(tmp_path, monkeypatch):
    root, _, _ = a_fleet(tmp_path, monkeypatch, {"bottling": FILE_PLANT, "cutlery": PG_PLANT})
    result = runner.invoke(app, ["fleet", "plan", "--json", "--root", str(root)])
    assert result.exit_code == 0, result.output
    answer = json.loads(result.output)
    assert answer["count"] == 2 and len(answer["plants"]) == 2
    assert "not-in-the-pack" not in result.output
    plant.environment.cache_clear()


def test_the_password_is_never_printed_to_a_human(tmp_path, monkeypatch):
    """`--with-password` is for a script that pipes it straight into pg_dump.
    Without `--json` it is a password on somebody's terminal, so it refuses."""
    root, _, _ = a_fleet(tmp_path, monkeypatch, {"cutlery": PG_PLANT})
    result = runner.invoke(app, ["fleet", "plan", "--with-password", "--root", str(root)])
    assert result.exit_code == 2
    assert "not-in-the-pack" not in result.output
    plant.environment.cache_clear()


def test_the_human_listing_states_the_total_and_what_it_cannot_back_up(tmp_path, monkeypatch):
    root, _, _ = a_fleet(tmp_path, monkeypatch,
                         {"bottling": FILE_PLANT, "elsewhere": ODD_PLANT})
    result = runner.invoke(app, ["fleet", "plan", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "2 plants, 1 this tooling can back up" in result.output
    assert "elsewhere" in result.output and "mysql" in result.output
    plant.environment.cache_clear()


def test_a_fleet_file_that_is_not_one_is_refused_with_the_sentence_that_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv(plant.REGISTRY_ENV, str(tmp_path / "plants.toml"))
    plant.environment.cache_clear()
    (tmp_path / "plants.toml").write_text('[plants.bottling]\napi_port = 1\n', encoding="utf-8")
    result = runner.invoke(app, ["fleet", "plan", "--json", "--root", str(tmp_path)])
    assert result.exit_code == 2 and "pack migrate" in result.output
    plant.environment.cache_clear()
