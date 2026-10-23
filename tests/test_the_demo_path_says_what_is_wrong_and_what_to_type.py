"""The three answers `fsmes demo <plant>` did not have on 2026-10-13.

A first-time Windows user installed 0.3.0, started the bottling plant, and by
07:56 had a plant that was alive and silent. What the product told him was
"Says 2 PID are alive, 7964 and 3940. But it can't talk to them", and the
recovery it named - `--force` - is a flag on a different command. He was
reading that on a phone.

These tests are about the words, because the words were the product gap. They
assert what a person sees: which process is gone, the log that will say why,
the command to type next, and that the easy stop needs no flag.
"""

from __future__ import annotations

import socket
from pathlib import Path

from fsmes import firstrun, standalone
from fsmes import plant as plants


def a_pack(where: Path, name: str = "bottling", port: int = 8010,
           opc: int | None = 4841) -> Path:
    pack = standalone.packs_dir(where) / name
    (pack / "masterdata").mkdir(parents=True)
    endpoint = f'opc_endpoint = "opc.tcp://127.0.0.1:{opc}/fsmes/{name}"\n' if opc else ""
    (pack / "plant.toml").write_text(
        f'[pack]\nformat = 1\n\n[plant]\nname = "{name}"\n\n'
        f'[serve]\napi_host = "127.0.0.1"\napi_port = {port}\n{endpoint}',
        encoding="utf-8")
    return pack


def a_half_dead_plant(name: str = "bottling") -> firstrun.Look:
    """Two of five alive, the API among the gone: the state he was in."""
    return firstrun.Look(
        name=name, owned=True, local=True, answering=False,
        url="http://127.0.0.1:8010",
        processes=(
            plants.Recorded(7964, True, "the OPC server the machines answer on",
                            r"C:\Users\scott\AppData\Local\fsmes\logs\bottling\opc-replay.jsonl"),
            plants.Recorded(1111, False, "the reader that writes the machines into the MES",
                            r"C:\Users\scott\AppData\Local\fsmes\logs\bottling\opc-agent.jsonl"),
            plants.Recorded(2222, False, "the dashboard and the API",
                            r"C:\Users\scott\AppData\Local\fsmes\logs\bottling\api.jsonl"),
            plants.Recorded(3333, False, "the simulated floor",
                            r"C:\Users\scott\AppData\Local\fsmes\logs\bottling\operations.jsonl"),
            plants.Recorded(3940, True, "the log sink",
                            r"C:\Users\scott\AppData\Local\fsmes\logs\bottling\sink.log"),
        ))


# ------------------------------------------------------------------ what status says


def test_status_on_a_half_dead_plant_names_the_process_that_is_gone():
    said = "\n".join(firstrun.what_status_should_say(a_half_dead_plant()))
    assert "2 of its 5 processes are still alive" in said
    assert "still alive: the OPC server the machines answer on (pid 7964)" in said
    assert "gone: the dashboard and the API (pid 2222)" in said


def test_status_on_a_half_dead_plant_sends_a_person_to_the_log_of_what_died():
    said = "\n".join(firstrun.what_status_should_say(a_half_dead_plant()))
    assert r"logs\bottling\api.jsonl" in said, (
        "the one thing his own machine could have told him was in a log, and nothing "
        "on screen said where it was")


def test_status_ends_with_the_command_to_type_next():
    lines = firstrun.what_status_should_say(a_half_dead_plant())
    assert lines[-2].strip() == "fsmes demo bottling stop      (stops what is left and clears the record)"
    assert lines[-1].strip() == "fsmes demo bottling           (starts it again)"
    assert "--force" not in "\n".join(lines), (
        "the easy path must not name a flag that lives on another command")


def test_status_on_a_healthy_plant_prints_the_address_to_open():
    up = firstrun.Look(name="bottling", owned=True, local=True, answering=True,
                       url="http://127.0.0.1:8010")
    assert firstrun.what_status_should_say(up) == [
        "  bottling is up: http://127.0.0.1:8010/dashboard"]


def test_status_on_a_plant_whose_processes_have_all_gone_says_how_to_start_it():
    down = firstrun.Look(
        name="bottling", owned=True, local=True, answering=False, url="http://127.0.0.1:8010",
        processes=(plants.Recorded(10, False, "the dashboard and the API", "api.jsonl"),
                   plants.Recorded(11, False, "the log sink", "sink.log")))
    said = firstrun.what_status_should_say(down)
    assert "none of the 2 processes it started last time is alive" in said[0]
    assert said[-1].strip() == "start it again with: fsmes demo bottling"


def test_status_adds_nothing_to_a_plant_this_installation_does_not_own():
    """`fsmes fleet status` has already said why in its own words, and a
    second voice guessing at a plant nobody here created is how a status
    command starts lying."""
    stranger = firstrun.Look(name="bottling", owned=False, local=False, answering=False, url="")
    assert firstrun.what_status_should_say(stranger) == []


# -------------------------------------------------------------------- what stop says


def test_stop_says_what_it_found_before_it_stops_it():
    assert firstrun.what_stop_found(a_half_dead_plant()) == [
        "  bottling was not answering; 2 of its 5 processes were still alive "
        "(7964, 3940); stopping them."]


def test_stop_on_a_plant_with_nothing_left_alive_says_it_is_clearing_the_record():
    down = firstrun.Look(
        name="bottling", owned=True, local=True, answering=False, url="http://127.0.0.1:8010",
        processes=(plants.Recorded(10, False, "the dashboard and the API", "api.jsonl"),))
    assert "clearing what is left of it" in firstrun.what_stop_found(down)[0]


def test_stop_says_nothing_extra_about_a_plant_it_may_not_touch():
    remote = firstrun.Look(name="bottling", owned=False, local=False, answering=False,
                           url="http://10.0.0.9:8010",
                           processes=(plants.Recorded(1, True, "the dashboard and the API", ""),))
    assert firstrun.what_stop_found(remote) == []
    assert firstrun.what_stop_leaves(remote) == []


def test_the_easy_stop_needs_no_flag_and_says_so_to_the_fleet(tmp_path, monkeypatch):
    """`fsmes demo <plant> stop` recovers the state above by itself.

    The flag it passes on to `fsmes fleet stop` gives up liveness, never
    ownership: a plant this installation did not create is refused with it
    exactly as without it, which is `fsmes.fleet.owned`'s own test. What this
    one holds is that the person on the demo path is not asked for it.
    """
    sent: list[tuple[str, ...]] = []

    def pretend(where, *args, echo=print):
        sent.append(args)
        return 0

    a_pack(tmp_path)
    standalone.write_fleet(tmp_path)
    monkeypatch.setattr(standalone, "_fsmes", pretend)
    monkeypatch.setattr(standalone.firstrun, "look", lambda *a, **k: a_half_dead_plant())
    said: list[str] = []

    assert standalone.stop("bottling", where=tmp_path, echo=said.append) == 0
    assert sent == [("fleet", "stop", "bottling", "--root", str(tmp_path), "--force")]
    assert said[0].startswith("  bottling was not answering; 2 of its 5 processes")
    assert said[-1] == "  `fsmes demo bottling` starts it again."


# ------------------------------------------------------- what a start looks at first


def test_a_start_is_not_blocked_when_nothing_holds_the_ports(tmp_path):
    pack = a_pack(tmp_path, port=_a_free_port(), opc=_a_free_port())
    assert firstrun.in_the_way("bottling", pack, data_dir=standalone.data_dir(tmp_path)) == []


def test_a_start_that_would_meet_a_taken_port_says_which_port_and_stops(tmp_path):
    """Five processes started into a port somebody else holds do not fail at
    the start; they die one by one, a second or two later, in a log file."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        pack = a_pack(tmp_path, port=taken, opc=_a_free_port())

        said = firstrun.in_the_way("bottling", pack, data_dir=standalone.data_dir(tmp_path))

    assert said, "a port that is taken has to be said before anything is launched"
    assert f"127.0.0.1:{taken} is already in use" in said[0]
    assert "the dashboard and the API needs" in said[0]
    assert "another program on this PC" in said[1], (
        "nothing this installation made is on it, and saying otherwise would be a guess")


def test_a_start_blocked_by_this_installations_own_plant_names_that_plant(tmp_path):
    from fsmes.fleet import owned

    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        pack = a_pack(tmp_path, name="my-line", port=taken, opc=_a_free_port())
        data = standalone.data_dir(tmp_path)
        owned.remember(data, owned.Entry(
            name="bottling", pack="packs/bottling", instance_id="abc", data_dir=str(data),
            api_host="127.0.0.1", api_port=taken, control=owned.LOCAL, created_at="",
            created_on_host="", created_by_user="", product_version="", pack_fingerprint=""))

        said = firstrun.in_the_way("my-line", pack, data_dir=data)

    assert "bottling, another plant this installation made" in said[1]
    assert "fsmes demo bottling stop" in said[1]


def test_a_start_that_meets_this_plants_own_closing_socket_says_to_wait_not_to_stop(tmp_path):
    """The two seconds after a stop on Windows, said as what they are.

    `fsmes.plant.stop` waits for its own ports before it returns, so this is
    only ever reached when that grace period was not enough. What must not
    happen then is the sentence of 2026-10-21: *another program on this PC -
    stop that program*, about the plant the person just stopped, whose
    processes are all gone.
    """
    from fsmes.fleet import owned

    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        taken = held.getsockname()[1]
        pack = a_pack(tmp_path, port=taken, opc=_a_free_port())
        data = standalone.data_dir(tmp_path)
        owned.remember(data, owned.Entry(
            name="bottling", pack=str(pack), instance_id="abc", data_dir=str(data),
            api_host="127.0.0.1", api_port=taken, control=owned.LOCAL, created_at="",
            created_on_host="", created_by_user="", product_version="", pack_fingerprint=""))

        said = firstrun.in_the_way("bottling", pack, data_dir=data)

    assert f"127.0.0.1:{taken} is already in use" in said[0]
    assert "bottling's own socket, still closing" in said[1]
    assert "wait a moment and start it again" in said[1]
    assert "Stop that program" not in said[1], (
        "there is nothing to stop: every process of this plant is gone and what is "
        "left is the socket")


def test_a_start_on_a_half_dead_plant_sends_a_person_to_the_stop_that_recovers_it(
        tmp_path, monkeypatch):
    pack = a_pack(tmp_path)
    monkeypatch.setattr(firstrun, "look", lambda *a, **k: a_half_dead_plant())
    said = firstrun.in_the_way("bottling", pack, data_dir=standalone.data_dir(tmp_path))
    assert said[0].startswith("  not starting bottling: it is not answering, but 2 of its 5")
    assert "fsmes demo bottling stop" in "\n".join(said)
    assert "die one by one" in "\n".join(said)


def test_the_ports_a_plant_will_want_are_read_from_its_pack(tmp_path):
    """Config, not code, at the plant boundary: a port is the pack's to say."""
    pack = a_pack(tmp_path, port=8123, opc=4999)
    assert firstrun.ports_of(pack) == [
        ("the dashboard and the API", "127.0.0.1", 8123),
        ("the OPC server", "127.0.0.1", 4999),
    ]


def test_a_plant_that_serves_without_an_opc_endpoint_has_one_port_to_check(tmp_path):
    pack = a_pack(tmp_path, port=8124, opc=None)
    assert [port for _, _, port in firstrun.ports_of(pack)] == [8124]


def _a_free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_half_dead_state_is_made_and_recovered_on_both_runners():
    """The words above are asserted here from fakes; a real plant half dying
    is asserted in CI, on a real Windows runner.

    A plant PC is a Windows PC, nobody on this project owns one, and all of
    this is processes, pids and console output - which is where Windows
    differs. If this job goes, the unit tests above still pass and nothing
    ever starts a plant on Windows again.
    """
    repo = Path(__file__).resolve().parents[1]
    ci = (repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "\n  half-dead-plant:\n" in ci, (
        "ci.yml has no `half-dead-plant` job: nothing then kills a plant's API "
        "process and reads what the product says about it")
    job = ci.split("\n  half-dead-plant:\n", 1)[1].split("\n  wheel-plant-from-shape:")[0]
    assert "recover_a_half_dead_plant.py" in job
    assert "windows-latest" in job and "ubuntu-latest" in job, (
        "both runners, or the half that matters is the one that is missing")


# ------------------------------------------------- what `--force` is answered with


def test_the_sentence_the_force_flag_gets_on_a_stop_sends_it_to_the_right_command():
    said = firstrun.what_a_force_flag_is_for("bottling", "stop")[0]
    assert "stop needs no --force here" in said
    assert "`fsmes demo bottling stop` stops a plant that is not answering by itself" in said
    assert "--force is an option of `fsmes fleet stop`" in said


def test_the_sentence_on_a_start_says_the_flag_is_not_this_commands_at_all():
    said = firstrun.what_a_force_flag_is_for("bottling", "start")[0]
    assert "--force is not an option of `fsmes demo`" in said
    assert "`fsmes fleet stop`" in said


def _runner():
    from typer.testing import CliRunner

    return CliRunner()


def _as_read(output: str) -> str:
    """The output as a person reads it: rich's colour taken out, wrapping undone.

    `FORCE_COLOR` is set on GitHub's runners, so rich paints and wraps there
    and not on a laptop; an assertion that does not do this passes here and
    fails on all four CI legs (2026-10-12).
    """
    import re

    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).split())


def test_a_stop_typed_with_force_answers_in_a_sentence_and_stops_the_plant(
        tmp_path, monkeypatch):
    """`fsmes demo bottling stop --force`, exactly as he typed it on 2026-10-15.

    What it printed then was typer's `No such option: --force` and nothing
    was stopped. The flag is hidden rather than removed from the parser:
    taking a word a person has already guessed costs nothing, and refusing it
    reads as a broken install.
    """
    from fsmes import cli

    sent: list[tuple[str, ...]] = []
    a_pack(tmp_path)
    standalone.write_fleet(tmp_path)
    monkeypatch.setattr(standalone, "_fsmes",
                        lambda where, *args, echo=print: sent.append(args) or 0)
    monkeypatch.setattr(standalone.firstrun, "look", lambda *a, **k: a_half_dead_plant())

    result = _runner().invoke(
        cli.app, ["demo", "bottling", "stop", "--force", "--home", str(tmp_path)])

    said = _as_read(result.output)
    assert result.exit_code == 0, result.output
    assert "stop needs no --force here" in said
    assert "--force is an option of `fsmes fleet stop`" in said
    assert "No such option" not in said
    assert sent == [("fleet", "stop", "bottling", "--root", str(tmp_path), "--force")], (
        "the sentence is not the whole job: the plant he asked about has to go down")
    assert "bottling was not answering" in said, (
        "and the stop's own account of what it found still has to be printed")


def test_the_same_flag_on_a_start_is_answered_and_the_plant_still_starts(monkeypatch):
    """`fsmes demo bottling --force`: typer reads the flag the same way, so the
    answer has to be there too, and the start has to happen anyway."""
    from fsmes import cli

    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "_shipped_plant",
                        lambda name, action, **kwargs: asked.append((name, action)) or 0)

    result = _runner().invoke(cli.app, ["demo", "bottling", "--force"])

    assert result.exit_code == 0, result.output
    assert "--force is not an option of `fsmes demo`" in _as_read(result.output)
    assert asked == [("bottling", "start")]


def test_the_force_flag_is_taken_but_never_advertised():
    """Hidden on purpose. It is not a way to do anything — it is an answer to
    a word people arrive with — and a flag in the help is a flag people
    believe they need."""
    from fsmes import cli

    helped = _as_read(_runner().invoke(cli.app, ["demo", "--help"]).output)
    assert "--force" not in helped
