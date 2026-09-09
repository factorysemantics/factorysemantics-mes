"""An install from PyPI has no checkout, so the defaults must still resolve.

The demo reads config/tag_map.json and config/line_layout.json relative to
the working directory. In a checkout they exist; from a wheel they do not,
and the same files ship as package data. These tests pin the rule: an
existing or deliberately set path wins, a missing default falls back to the
packaged copy, and a missing path someone set stays missing.
"""

from pathlib import Path

import pytest

from fsmes import config


def test_an_existing_relative_path_is_returned_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tag_map.json").write_text("{}")
    assert config.packaged_default(Path("config/tag_map.json")) == Path("config/tag_map.json")


def test_an_absolute_path_is_never_replaced(tmp_path):
    missing = tmp_path / "nowhere" / "tag_map.json"
    assert config.packaged_default(missing) == missing


def test_a_missing_default_falls_back_to_the_packaged_copy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    packaged = config.resources.files("fsmes") / "data" / "config" / "tag_map.json"
    if not packaged.is_file():
        pytest.skip("editable checkout: the package data only exists in a built wheel")
    resolved = config.packaged_default(Path("config/tag_map.json"))
    assert resolved.is_absolute() and resolved.name == "tag_map.json"


def test_a_missing_path_with_no_packaged_copy_stays_as_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.packaged_default(Path("config/no_such_map.json")) == Path("config/no_such_map.json")


def test_settings_fall_back_only_when_the_field_was_not_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MES_TAG_MAP_FILE", "config/mine.json")
    # get_settings is cached for the life of a process; this test wants a
    # fresh read of the environment, and must not leak its own to the next.
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
    finally:
        config.get_settings.cache_clear()
    assert settings.tag_map_file == Path("config/mine.json")
