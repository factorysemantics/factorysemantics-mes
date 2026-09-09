"""The CLI's M0 contract — thin, but it is the installation's self-report."""

from __future__ import annotations

from typer.testing import CliRunner

from fsmes import __version__
from fsmes.cli import app

runner = CliRunner()


def test_version_reports_the_package_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_info_reports_the_version_and_the_modules_that_registered_themselves():
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert __version__ in result.output
    # Modules are read from the `fsmes.modules` entry-point group, never a
    # hardcoded list: the ERPNext connector registers there in pyproject.toml,
    # so an install that lost its metadata would say "kernel only" instead —
    # "none" and "we failed to look" must not read the same on a support call.
    assert "modules       erpnext" in result.output
