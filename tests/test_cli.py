"""The CLI's M0 contract — thin, but it is the installation's self-report."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version

import pytest
from typer.testing import CliRunner

from fsmes import __version__
from fsmes.cli import app

runner = CliRunner()


def test_version_reports_the_package_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_the_version_the_cli_reports_is_the_version_that_was_installed():
    """The number the software states about itself has to be true.

    The 0.1.2 wheel answered `fsmes --version` with 0.1.0, because
    `__version__` was a second copy of the number that nobody bumped for
    either release. There is one copy now: `pyproject.toml` declares the
    version dynamic and hatchling reads `src/fsmes/__init__.py`, so the
    distribution's metadata is stamped from the same line the CLI prints.
    This fails if the two ever drift apart again — including against a stale
    editable install, which is the same drift seen from the other side.
    """
    try:
        distribution = installed_version("factorysemantics-mes")
    except PackageNotFoundError:
        # Running from a source tree with nothing installed. There is no
        # metadata to disagree with, so this pins nothing — say that rather
        # than pass and look like it checked.
        pytest.skip("factorysemantics-mes is not installed; there is no distribution metadata to compare")

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"fsmes {distribution}"


def test_info_reports_the_version_and_the_modules_that_registered_themselves():
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert __version__ in result.output
    # Modules are read from the `fsmes.modules` entry-point group, never a
    # hardcoded list: the ERPNext connector registers there in pyproject.toml,
    # so an install that lost its metadata would say "kernel only" instead —
    # "none" and "we failed to look" must not read the same on a support call.
    assert "modules       erpnext" in result.output


def test_erp_setup_refuses_plainly_when_erpnext_is_not_the_configured_mode(monkeypatch):
    """It would otherwise open a connection to whatever ERPNext defaults name,
    on behalf of somebody who is not using ERPNext at all."""
    monkeypatch.setenv("MES_ERP_MODE", "file")
    from fsmes.config import get_settings

    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["erp", "setup"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "not 'erpnext'" in result.output


def test_erp_check_exits_non_zero_when_the_site_cannot_be_reached(monkeypatch):
    """`fsmes erp check` is meant to gate a deployment, so the exit code has to
    carry the answer, and the message has to be a sentence rather than a
    traceback."""
    monkeypatch.setenv("MES_ERP_MODE", "erpnext")
    monkeypatch.setenv("MES_ERPNEXT_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MES_ERPNEXT_API_KEY", "key")
    monkeypatch.setenv("MES_ERPNEXT_API_SECRET", "secret")
    from fsmes.config import get_settings

    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["erp", "check"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "NOT OK" in result.output
    assert "Traceback" not in result.output
