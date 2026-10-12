"""Starting and stopping a plant, on the platform nobody here owns.

`docs/operate/compatibility.md` promises plant PCs are Windows and that CI
proves it. CI did prove the *suite* there, every pull request, on two Pythons
- and the suite never starts a plant, so three things in `fsmes.plant` had
been wrong on Windows for as long as they had existed:

* `start_new_session=True` is accepted there and silently ignored, so every
  child of `fsmes plant <name> start` stayed in the launching console's
  process group and died with the window;
* `signal.SIGKILL` does not exist there, so `stop` raised `AttributeError`
  the moment a process outlived its ten-second deadline, leaving the pid file
  behind and the plant half down;
* a venv there puts the console script in `Scripts\\fsmes.exe`, which
  `Path(sys.executable).parent / "fsmes"` does not name - so every plant fell
  through to `shutil.which` and found nothing unless the venv was on PATH.

These tests run on either platform by saying which one they mean, because a
test that only runs on Windows is a test this project would never see fail.
The `wheel-plant` CI job starts a real plant on a real Windows runner; this
is the part that can fail in a second instead of half an hour.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from fsmes import plant as plants


def pretend(monkeypatch, platform: str) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    # Windows-only constants are absent from the `subprocess` module on POSIX.
    # Setting one is how a POSIX machine can ask what the Windows branch does.
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)


def test_on_windows_a_plant_child_asks_for_its_own_process_group(monkeypatch):
    pretend(monkeypatch, "win32")
    asked = plants._detached()
    assert asked == {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    assert "start_new_session" not in asked, (
        "start_new_session is accepted and ignored on Windows; asking for it there "
        "leaves the plant in the console's process group")


def test_on_posix_a_plant_child_asks_for_its_own_session(monkeypatch):
    pretend(monkeypatch, "linux")
    assert plants._detached() == {"start_new_session": True}


def test_stopping_a_stubborn_process_uses_the_strongest_signal_there_is(monkeypatch):
    """SIGKILL where there is one; on Windows `os.kill` is already a terminate."""
    sent: list[int] = []
    monkeypatch.setattr(plants.os, "kill", lambda pid, sig: sent.append(sig))

    plants._force_stop(4321)
    assert sent == [signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM]

    sent.clear()
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    plants._force_stop(4321)
    assert sent == [signal.SIGTERM], "a platform with no SIGKILL must not raise here"


def test_asking_a_process_to_finish_sends_the_one_signal_both_platforms_have(monkeypatch):
    sent: list[int] = []
    monkeypatch.setattr(plants.os, "kill", lambda pid, sig: sent.append(sig))
    plants._ask_to_stop(99)
    assert sent == [signal.SIGTERM]


def test_a_windows_venv_keeps_its_console_script_in_scripts(tmp_path, monkeypatch):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "fsmes.exe").write_text("not really an executable")
    pretend(monkeypatch, "win32")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    # Beside the interpreter *is* Scripts here, which is the normal Windows
    # layout; the one below is the layout of an installation whose python
    # lives a level up.
    assert plants.fsmes_bin() == str(scripts / "fsmes.exe")

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(sys, "executable", str(other / "python.exe"))
    monkeypatch.setattr(plants.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        plants.fsmes_bin()


def test_a_posix_venv_keeps_its_console_script_in_bin(tmp_path, monkeypatch):
    binned = tmp_path / "bin"
    binned.mkdir()
    (binned / "fsmes").write_text("#!/bin/sh\n")
    pretend(monkeypatch, "linux")
    monkeypatch.setattr(sys, "executable", str(binned / "python"))
    assert plants.fsmes_bin() == str(binned / "fsmes")


def test_a_plant_found_only_on_the_path_is_still_found(tmp_path, monkeypatch):
    """A packaged installation whose venv was never activated."""
    pretend(monkeypatch, "linux")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(plants.shutil, "which", lambda name: "/usr/local/bin/fsmes")
    assert plants.fsmes_bin() == "/usr/local/bin/fsmes"


def test_every_child_of_a_plant_is_started_detached(monkeypatch, tmp_path):
    """Not one `Popen` in `start` may be left attached to the console.

    Written as a sweep rather than six assertions because the failure this
    guards against is a seventh process added later with the flag forgotten.
    """
    started: list[dict] = []

    class Fake:
        def __init__(self, *args, **kwargs):
            started.append(kwargs)
            self.pid = 1000 + len(started)
            self.stdin = _Pipe()

    class _Pipe:
        def close(self):
            pass

    monkeypatch.setattr(plants.subprocess, "Popen", Fake)
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "fsmes")
    monkeypatch.setattr(plants, "running_pids", lambda root, name: [])
    monkeypatch.setattr(plants, "plant_env", lambda *a, **k: {})
    monkeypatch.setattr(plants.time, "sleep", lambda seconds: None)
    (tmp_path / "data").mkdir()

    plants.start("bottling", {"simulate": True, "api_port": 8010}, tmp_path,
                 echo=lambda line: None)

    assert len(started) == 5, (
        "a simulating plant is the OPC server, the agent, the API, the floor and "
        f"the log sink: {started}")
    detached = plants._detached()
    for kwargs in started:
        assert all(kwargs.get(key) == value for key, value in detached.items()), (
            f"a plant process was started without {detached}: {kwargs}")


def test_the_log_sink_does_not_hold_the_console_of_the_command_that_started_it(
        monkeypatch, tmp_path):
    """The sink outlives `start`, so anything it inherits it holds for the life
    of the plant.

    Given the console, it holds that - and a caller reading this command's
    output (`subprocess.run(..., capture_output=True)`, a shell pipeline) then
    waits for a pipe that only closes when the plant stops. `fsmes demo
    bottling` hung for ten minutes on exactly that on 2026-10-12, with the
    plant up and answering the whole time.
    """
    started: list[dict] = []

    class Fake:
        def __init__(self, *args, **kwargs):
            started.append(kwargs)
            self.pid = 7000 + len(started)
            self.stdin = type("P", (), {"close": lambda self: None})()

    monkeypatch.setattr(plants.subprocess, "Popen", Fake)
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "fsmes")
    monkeypatch.setattr(plants, "running_pids", lambda root, name: [])
    monkeypatch.setattr(plants, "plant_env", lambda *a, **k: {})
    monkeypatch.setattr(plants.time, "sleep", lambda seconds: None)
    (tmp_path / "data").mkdir()

    plants.start("bottling", {"simulate": False, "api_port": 8010}, tmp_path,
                 echo=lambda line: None)

    sink = next(k for k in started if k.get("stdin") == plants.subprocess.PIPE)
    assert sink.get("stdout") is not None, (
        "the log sink inherited the caller's stdout and will hold it until the "
        "plant stops")
    assert Path(sink["stdout"].name) == tmp_path / "logs" / "bottling" / "sink.log"
