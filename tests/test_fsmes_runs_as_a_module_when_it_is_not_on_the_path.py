"""`py -m fsmes` is the second way in, for a PC where PATH says no.

On 2026-10-13 at 07:18 the first person to install 0.3.0 on Windows could not
run it: `py -m pip install` put `fsmes.exe` in a user Scripts folder that was
not on PATH - the same reason `pip` itself had to be reached as `py -m pip` -
and PowerShell answered *fsmes is not recognized as an internal or external
command*. There was no second way in, because the package had no
`__main__.py`, so `py -m fsmes` printed nothing either and gave him no way to
tell "not installed" from "installed, not on PATH".

These tests hold both halves of the fix: the module entry point itself, and
a plant started through it being able to start its own processes.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from fsmes import __version__
from fsmes import plant as plants


def test_running_the_package_as_a_module_prints_the_version() -> None:
    done = subprocess.run([sys.executable, "-m", "fsmes", "--version"],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"fsmes {__version__}"


def test_running_the_package_as_a_module_offers_the_same_commands() -> None:
    """The module route is the same CLI, not a smaller one: a person sent to
    `py -m fsmes` by the install page must find `demo` there."""
    done = subprocess.run([sys.executable, "-m", "fsmes", "--help"],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    assert "demo" in done.stdout


def test_a_plant_prefers_the_console_script_when_there_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plants, "fsmes_bin", lambda: "/venv/bin/fsmes")
    assert plants.fsmes_command() == ["/venv/bin/fsmes"]


def test_a_plant_started_without_a_console_script_runs_its_processes_as_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half of the fix that is easy to forget.

    On the Windows PC above there is no `fsmes` on PATH and none beside the
    interpreter either, so `fsmes_bin` raises - and every process a plant is
    made of is started by naming it. Without this fallback `py -m fsmes demo
    bottling` would get as far as the first child and then say it cannot find
    the fsmes executable, on the machine where the whole point was that it is
    not findable.
    """
    def nothing_on_the_path() -> str:
        raise FileNotFoundError("Cannot find the fsmes executable.")

    monkeypatch.setattr(plants, "fsmes_bin", nothing_on_the_path)
    assert plants.fsmes_command() == [sys.executable, "-m", "fsmes"]
