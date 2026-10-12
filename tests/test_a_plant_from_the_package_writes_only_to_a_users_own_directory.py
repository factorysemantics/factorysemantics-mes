"""`fsmes demo <plant>`: where it puts things, and what it refuses to touch.

The arranging layer for an installation with no checkout (`fsmes.standalone`)
has one hard rule and one soft one. The hard rule: **nothing under
site-packages is written to** - a wheel's files are read-only on a plant PC,
on a locked-down workstation and in any sensible container, and the generated
replay alone is 9.7 MB. Everything written goes to one per-user directory. The
soft rule: a copy of a pack somebody has edited is theirs, and a command that
silently put the shipped port back would be worse than one line longer.

These say so on all three platforms, by naming the platform rather than by
running on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fsmes import standalone


@pytest.fixture(autouse=True)
def no_inherited_home(monkeypatch):
    monkeypatch.delenv(standalone.HOME_ENV, raising=False)


def test_the_home_is_the_platforms_own_per_user_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert standalone.home() == tmp_path / "AppData" / "Local" / "fsmes"

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert standalone.home() == tmp_path / "share" / "fsmes"

    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert standalone.home() == tmp_path / ".local" / "share" / "fsmes"

    monkeypatch.setattr(sys, "platform", "darwin")
    assert standalone.home() == tmp_path / "Library" / "Application Support" / "fsmes"


def test_one_variable_moves_everything_a_packaged_plant_writes(monkeypatch, tmp_path):
    monkeypatch.setenv(standalone.HOME_ENV, str(tmp_path / "elsewhere"))
    where = standalone.home()
    assert where == tmp_path / "elsewhere"
    for under in (standalone.packs_dir(where), standalone.data_dir(where),
                  standalone.fleet_file(where)):
        assert where in under.parents


def test_the_packs_this_installation_carries_are_read_from_the_package():
    """From a checkout the package data is not built, and that is not a failure."""
    carried = standalone.shipped()
    assert isinstance(carried, list)
    assert carried == sorted(carried)
    if carried:
        assert set(carried) <= {"bottling", "machining"}


def a_pack(where: Path, name: str = "bottling", port: int = 8010) -> Path:
    pack = standalone.packs_dir(where) / name
    (pack / "masterdata").mkdir(parents=True)
    (pack / "plant.toml").write_text(
        f'[pack]\nformat = 1\n\n[plant]\nname = "{name}"\n\n'
        f'[serve]\napi_host = "127.0.0.1"\napi_port = {port}\n',
        encoding="utf-8")
    return pack


def test_the_fleet_file_lists_every_pack_under_the_home(tmp_path):
    a_pack(tmp_path, "bottling")
    a_pack(tmp_path, "machining", port=8020)
    file = standalone.write_fleet(tmp_path)
    written = file.read_text(encoding="utf-8")
    assert 'packs = ["packs/bottling", "packs/machining"]' in written
    assert f'data_dir = "{standalone.data_dir(tmp_path).as_posix()}"' in written


def test_a_pack_directory_somebody_removed_stops_being_listed(tmp_path):
    a_pack(tmp_path, "bottling")
    pack = a_pack(tmp_path, "machining", port=8020)
    standalone.write_fleet(tmp_path)
    (pack / "plant.toml").unlink()
    assert 'packs = ["packs/bottling"]' in standalone.write_fleet(tmp_path).read_text()


def test_the_fleet_file_says_which_command_wrote_it(tmp_path):
    a_pack(tmp_path)
    written = standalone.write_fleet(tmp_path).read_text(encoding="utf-8")
    assert "fsmes demo" in written.splitlines()[2]


def shipping(monkeypatch, tmp_path, *, port: int = 8010) -> Path:
    """Stand in for the package's own copy of a pack."""
    package = tmp_path / "site-packages"
    source = package / "bottling"
    (source / "masterdata").mkdir(parents=True)
    (source / "plant.toml").write_text(
        f'[pack]\nformat = 1\n\n[plant]\nname = "bottling"\n\n'
        f'[serve]\napi_port = {port}\n', encoding="utf-8")
    (source / "masterdata" / "equipment.csv").write_text("code\nFILL01\n", encoding="utf-8")

    class Files:
        def joinpath(self, *parts):
            return source

    monkeypatch.setattr(standalone, "shipped", lambda: ["bottling"])
    monkeypatch.setattr(standalone.resources, "files", lambda package_name: Files())
    monkeypatch.setattr(standalone.resources, "as_file",
                        lambda path: __import__("contextlib").nullcontext(path))
    return source


def test_a_pack_the_installation_has_never_copied_is_copied(monkeypatch, tmp_path):
    shipping(monkeypatch, tmp_path)
    home = tmp_path / "home"
    said: list[str] = []
    target = standalone.install_pack("bottling", home, echo=said.append)
    assert (target / "plant.toml").is_file()
    assert (target / "masterdata" / "equipment.csv").is_file()
    assert "copied" in " ".join(said)


def test_a_pack_the_person_has_edited_is_kept_and_the_difference_is_said(
        monkeypatch, tmp_path):
    shipping(monkeypatch, tmp_path)
    home = tmp_path / "home"
    target = standalone.install_pack("bottling", home, echo=lambda line: None)
    mine = (target / "plant.toml").read_text().replace("8010", "9100")
    (target / "plant.toml").write_text(mine, encoding="utf-8")

    said: list[str] = []
    standalone.install_pack("bottling", home, echo=said.append)
    assert "9100" in (target / "plant.toml").read_text(), "the edit was reverted"
    assert "keeping yours" in " ".join(said)


def test_refresh_is_the_one_way_a_persons_copy_is_replaced(monkeypatch, tmp_path):
    shipping(monkeypatch, tmp_path)
    home = tmp_path / "home"
    target = standalone.install_pack("bottling", home, echo=lambda line: None)
    (target / "plant.toml").write_text("ruined", encoding="utf-8")
    (target / "mine.txt").write_text("also mine", encoding="utf-8")

    said: list[str] = []
    standalone.install_pack("bottling", home, refresh=True, echo=said.append)
    assert "8010" in (target / "plant.toml").read_text()
    assert not (target / "mine.txt").exists(), "a refresh leaves nothing of the old copy"
    assert "replaced" in " ".join(said)


def test_an_unchanged_copy_is_recognised_whatever_the_plant_generated_beside_it(
        monkeypatch, tmp_path):
    shipping(monkeypatch, tmp_path)
    home = tmp_path / "home"
    target = standalone.install_pack("bottling", home, echo=lambda line: None)
    (target / "out").mkdir()
    (target / "out" / "Line.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    said: list[str] = []
    standalone.install_pack("bottling", home, echo=said.append)
    assert "differs" not in " ".join(said), (
        "the generated replay is not part of what makes a copy the package's")


def test_a_plant_this_installation_does_not_carry_says_what_it_does(monkeypatch, tmp_path):
    monkeypatch.setattr(standalone, "shipped", lambda: ["bottling", "machining"])
    with pytest.raises(LookupError) as refused:
        standalone.install_pack("brewing", tmp_path, echo=lambda line: None)
    assert "bottling, machining" in str(refused.value)
    assert "brewing" in str(refused.value)


def test_an_installation_that_carries_no_packs_at_all_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(standalone, "shipped", lambda: [])
    with pytest.raises(LookupError) as refused:
        standalone.install_pack("bottling", tmp_path, echo=lambda line: None)
    assert "ships none" in str(refused.value)


def test_the_replay_is_generated_once_and_not_again(monkeypatch, tmp_path):
    pack = a_pack(tmp_path)
    (pack / "line.json").write_text("{}", encoding="utf-8")
    runs: list[list[str]] = []

    def fake(cmd, **kwargs):
        runs.append(cmd)
        (pack / "out").mkdir(exist_ok=True)
        (pack / "out" / "Line.csv").write_text("a\n1\n", encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(standalone, "_through", fake)
    monkeypatch.setattr(standalone.plants, "fsmes_bin", lambda: "fsmes")

    assert standalone.generate_replay(pack, echo=lambda line: None) is not None
    assert len(runs) == 1 and "sim-generate" in runs[0]
    assert standalone.generate_replay(pack, echo=lambda line: None) is None
    assert len(runs) == 1, "a plant that has been started once generates nothing again"


def test_a_pack_with_no_line_description_generates_nothing(tmp_path):
    """Not every plant simulates; one that does not is not a broken one."""
    pack = a_pack(tmp_path)
    assert standalone.generate_replay(pack, echo=lambda line: None) is None


def test_what_is_on_screen_says_what_is_off_without_a_key(monkeypatch, tmp_path):
    pack = a_pack(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without = standalone.how_to_sign_in("bottling", pack)
    assert any("http://127.0.0.1:8010/dashboard" in line for line in without)
    assert any("SCOTT / operator" in line for line in without)
    assert any("ANTHROPIC_API_KEY" in line for line in without)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    with_key = standalone.how_to_sign_in("bottling", pack)
    assert not any("ANTHROPIC_API_KEY" in line for line in with_key)


# ------------------------------------------------------- what the command does


def runner():
    from typer.testing import CliRunner

    return CliRunner()


def test_fsmes_demo_with_no_plant_still_runs_the_two_station_twin(monkeypatch):
    """`fsmes demo` is what it was. The shipped plants are new, not a swap."""
    from fsmes import cli

    ran: list[int] = []
    monkeypatch.setattr(cli, "_demo_twin", lambda duration: ran.append(duration))
    result = runner().invoke(cli.app, ["demo", "--duration", "7"])
    assert result.exit_code == 0, result.output
    assert ran == [7]


def test_fsmes_demo_with_a_plant_runs_that_plant(monkeypatch):
    from fsmes import cli

    asked: list[tuple] = []
    monkeypatch.setattr(cli, "_shipped_plant",
                        lambda name, action, **kwargs: asked.append((name, action, kwargs)) or 0)
    result = runner().invoke(cli.app, ["demo", "bottling", "--speed", "60"])
    assert result.exit_code == 0, result.output
    assert asked[0][0] == "bottling" and asked[0][1] == "start"
    assert asked[0][2]["speed"] == 60

    asked.clear()
    assert runner().invoke(cli.app, ["demo", "bottling", "stop"]).exit_code == 0
    assert asked[0][1] == "stop"


def test_a_verb_with_no_plant_is_read_as_a_verb_and_not_as_a_plant(monkeypatch):
    """`fsmes demo stop` means something plain; "there is no plant called
    'stop'" is a true answer that helps nobody."""
    from fsmes import cli

    result = runner().invoke(cli.app, ["demo", "stop"])
    assert result.exit_code == 2
    assert "fsmes demo bottling stop" in result.output
    assert "no packs" not in result.output


def test_an_unknown_action_on_a_shipped_plant_names_the_ones_there_are():
    from fsmes import cli

    assert cli._shipped_plant("bottling", "wobble", speed=None, refresh=False,
                              where=None) == 2


def test_the_help_says_what_the_one_command_is():
    from fsmes import cli

    result = runner().invoke(cli.app, ["demo", "--help"])
    assert result.exit_code == 0
    words = " ".join(result.output.split())
    assert "fsmes demo bottling" in words
    assert "no checkout" in words
