"""A plant knows its own name, and what clock it keeps.

The console this is built for shows a fleet, and a console that cannot tell
two plants apart is worse than no console. Everything here is pinned against
settings objects rather than running plants: two Settings are two plants as
far as every surface in this file is concerned, which is the point - none of
them may read the address they were dialled on.
"""

from datetime import date, datetime, time
from typing import ClassVar

import pytest
from pydantic import ValidationError

from fsmes import identity
from fsmes.config import Settings
from fsmes.integrations.uns.envelope import envelope


def a_plant(**over) -> Settings:
    """One plant's settings. Defaults to a real, named, non-laptop plant."""
    base = {"plant_name": "kc1", "plant_profile": "plant",
            "plant_timezone": "America/Chicago"}
    base.update(over)
    return Settings(**base)


# ------------------------------------------------------------------- the name

def test_a_plant_that_is_not_a_laptop_must_say_its_name():
    with pytest.raises(ValidationError) as caught:
        Settings(plant_name="", plant_profile="plant")
    said = str(caught.value)
    assert "MES_PLANT_NAME" in said
    # One sentence naming the fix, not a validation report: the person who
    # set one variable wrongly is standing next to a plant.
    assert "MES_PLANT_PROFILE=laptop" in said


def test_a_laptop_with_no_name_set_is_the_demo_plant():
    """The evaluation profile has to run with nothing set - that is what it is
    for - and the plant it runs is the demo plant, which has a name."""
    assert Settings(plant_name="", plant_profile="laptop").plant_name == "demo"


def test_a_name_that_would_be_mangled_in_the_namespace_is_refused_at_startup():
    """A name is published as one topic segment. A name that had to be cleaned
    up there would stop matching the one on this plant's own screens, and the
    two would disagree for ever after."""
    with pytest.raises(ValidationError) as caught:
        a_plant(plant_name="Kansas City/1")
    assert "MES_PLANT_NAME" in str(caught.value)

    from fsmes.integrations.uns.topics import segment

    for good in ("kc1", "KC-1", "plant.north", "line_2"):
        assert segment(good) == good
        assert a_plant(plant_name=good).plant_name == good


def test_an_unknown_profile_is_refused_and_the_profiles_are_named():
    with pytest.raises(ValidationError) as caught:
        Settings(plant_name="kc1", plant_profile="kubernetes")
    said = str(caught.value)
    assert "laptop" in said and "plant" in said and "fleet" in said


# -------------------------------------------------------------- two plants

def test_two_plants_configured_differently_report_different_identities():
    """The whole reason this milestone piece exists. No running plants: two
    settings objects are two plants to every surface that reads them."""
    north = identity.summary(a_plant(plant_name="north", plant_timezone="Europe/Berlin"))
    south = identity.summary(a_plant(plant_name="south", plant_timezone="America/Chicago"))
    assert north["plant"] == "north" and south["plant"] == "south"
    assert north["timezone"] == "Europe/Berlin"
    assert south["timezone"] == "America/Chicago"
    assert north != south


def test_health_answers_with_the_plant_it_was_configured_as(anon):
    body = anon.get("/health").json()
    assert set(body) >= {"status", "shadow", "plant", "profile",
                         "timezone", "timezone_defaulted"}


def test_the_metrics_carry_the_plant_so_a_fleet_can_be_scraped_into_one(anon):
    """Without the label, two plants' series have the same name in one
    Prometheus and silently become their sum."""
    body = anon.get("/metrics").text
    for line in body.strip().splitlines():
        assert 'plant="' in line, f"a series with no plant label: {line}"


# -------------------------------------------------------------- the envelope

def test_a_namespace_event_always_says_which_plant_it_came_from():
    """`plant` is half the dedupe key for anyone merging two MES databases.
    It used to be null whenever nobody had set a name."""

    class FakeMessage:
        id = 7
        message_key = "WO-1:op10"
        kind = "operation.confirmed"
        created_at = datetime(2026, 3, 1, 12, 0)
        payload: ClassVar[dict] = {"equipment": "MIX01"}

        class direction:
            value = "outbound"

    for settings in (a_plant(), Settings(plant_name="", plant_profile="laptop")):
        body = envelope(FakeMessage(), settings, published_at=datetime(2026, 3, 1, 12, 1))
        assert body["plant"], "an event that cannot say which plant made it"


# ------------------------------------------------------------------ the clock

def test_a_zone_this_machine_does_not_know_is_refused_with_the_windows_fix():
    with pytest.raises(ValidationError) as caught:
        a_plant(plant_timezone="Mars/Olympus")
    said = str(caught.value)
    assert "MES_PLANT_TIMEZONE" in said
    # Windows has no IANA database of its own; the inbound work found this
    # the hard way and the sentence is the same one.
    assert "tzdata" in said


def test_a_zone_nobody_chose_is_reported_as_defaulted():
    """Unknown is not zero. The process's zone is a guess about the plant, and
    every reader is told it was a guess."""
    chosen = identity.clock(a_plant(plant_timezone="Europe/Berlin"))
    assert chosen.defaulted is False and chosen.says() == "Europe/Berlin"

    guessed = identity.clock(a_plant(plant_timezone=""))
    assert guessed.defaulted is True
    assert "defaulted" in guessed.says() or "names no zone" in guessed.says()


def test_an_instant_is_read_on_the_plants_wall_clock():
    """Noon UTC is seven in the morning in Chicago and one in the afternoon in
    Berlin. The MES stores one number; the plant reads its own."""
    noon_utc = datetime(2026, 7, 1, 12, 0)
    chicago = identity.to_plant(noon_utc, a_plant(plant_timezone="America/Chicago"))
    berlin = identity.to_plant(noon_utc, a_plant(plant_timezone="Europe/Berlin"))
    assert chicago.hour == 7
    assert berlin.hour == 14


def test_a_shift_starts_when_the_plant_says_it_does_not_when_greenwich_does(session):
    """The bug this closes: shifts were compared against the stored UTC clock,
    so a plant six hours from Greenwich had its morning shift start in the
    middle of the night."""
    from fsmes.services import calendar

    calendar.create_pattern(session, code="DAY", name="Day",
                            starts=time(6, 0), ends=time(14, 0), days="1111111")
    session.flush()

    from zoneinfo import ZoneInfo

    chicago = ZoneInfo("America/Chicago")
    # Both instants below are chosen because the two readings disagree about
    # them: a test where UTC and the plant happen to agree pins nothing.
    #
    # 15:00 UTC is ten in the morning in Chicago - mid-shift for the plant,
    # and after 14:00 for anybody reading the stored number as a wall clock.
    assert calendar.is_working(session, datetime(2026, 7, 1, 15, 0), zone=chicago) is True
    # 07:00 UTC is two in the morning in Chicago - the middle of the night for
    # the plant, and an hour into the day shift for a UTC reading.
    assert calendar.is_working(session, datetime(2026, 7, 1, 7, 0), zone=chicago) is False

    # And the contrast, which is what the old code did to this plant: read on
    # a UTC clock (the suite's default zone), both answers come out backwards.
    assert calendar.is_working(session, datetime(2026, 7, 1, 15, 0)) is False
    assert calendar.is_working(session, datetime(2026, 7, 1, 7, 0)) is True


def test_the_shift_calendar_says_which_clock_its_times_are_on(session):
    """"Day 06:00-14:00" with no zone beside it is a fact about nothing."""
    from fsmes.services import calendar

    calendar.create_pattern(session, code="DAY", name="Day",
                            starts=time(6, 0), ends=time(14, 0), days="1111100")
    session.flush()
    described = calendar.describe(session)
    assert "timezone" in described and "timezone_defaulted" in described


def test_a_gauge_falls_due_on_the_plants_date(monkeypatch):
    """"Overdue" is the word that stops a line, and a server in another zone
    would say it a few hours early or late."""
    from fsmes.config import get_settings

    try:
        monkeypatch.setenv("MES_PLANT_TIMEZONE", "Pacific/Kiritimati")  # UTC+14
        get_settings.cache_clear()
        east = identity.today()
        monkeypatch.setenv("MES_PLANT_TIMEZONE", "Pacific/Midway")  # UTC-11
        get_settings.cache_clear()
        west = identity.today()
    finally:
        get_settings.cache_clear()
    # Twenty-five hours apart, so these two plants are never on the same date
    # and the process's own date cannot be right for both of them.
    assert east > west
    assert isinstance(east, date)


# ------------------------------------------------------------------ fsmes info

def test_fsmes_info_says_the_plant_and_whether_the_zone_was_chosen(monkeypatch):
    from typer.testing import CliRunner

    from fsmes.cli import app
    from fsmes.config import get_settings

    monkeypatch.setenv("MES_PLANT_NAME", "kc1")
    monkeypatch.setenv("MES_PLANT_PROFILE", "plant")
    monkeypatch.delenv("MES_PLANT_TIMEZONE", raising=False)
    get_settings.cache_clear()
    try:
        out = CliRunner().invoke(app, ["info"]).output
        assert "kc1" in out and "plant profile" in out
        assert "defaulted" in out or "names no zone" in out

        monkeypatch.setenv("MES_PLANT_TIMEZONE", "Europe/Berlin")
        get_settings.cache_clear()
        out = CliRunner().invoke(app, ["info"]).output
        assert "Europe/Berlin" in out and "defaulted" not in out
    finally:
        get_settings.cache_clear()


def test_a_refused_setting_reaches_the_person_as_one_sentence(monkeypatch, capsys):
    """Not as a traceback with the sentence in the middle of it. The person
    reading this is standing next to a plant with a command that did not run,
    and the only thing they need is which variable to change."""
    from fsmes.cli import run
    from fsmes.config import get_settings

    monkeypatch.setenv("MES_PLANT_PROFILE", "plant")
    monkeypatch.delenv("MES_PLANT_NAME", raising=False)
    monkeypatch.setattr("sys.argv", ["fsmes", "info"])
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as caught:
            run()
    finally:
        get_settings.cache_clear()
    assert caught.value.code == 2
    said = capsys.readouterr().out
    assert "MES_PLANT_NAME" in said
    assert "Traceback" not in said


# ---------------------------------------------------------------- the backup

def test_a_backup_records_which_plant_it_is_of(tmp_path):
    """A folder of timestamped backups from three plants is otherwise three
    sets of identical-looking folders."""
    from fsmes.backup import back_up

    settings = a_plant(database_url=f"sqlite:///{tmp_path / 'p.db'}",
                       tag_map_file=tmp_path / "nothing.json",
                       line_layout_file=tmp_path / "nothing.json",
                       opc_cert_dir=tmp_path / "nocerts")
    manifest = back_up(settings, tmp_path / "backups")
    assert manifest["plant"] == "kc1"
    assert manifest["timezone"] == "America/Chicago"
    assert manifest["timezone_defaulted"] is False
