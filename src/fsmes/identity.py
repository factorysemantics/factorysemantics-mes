"""Which plant this is, and what clock it keeps.

Two facts, and until now neither of them had an owner. `MES_PLANT_NAME`
existed, defaulted to empty, and reached the unified namespace as
`plant: null` — an event that cannot say which plant produced it is an
event nobody can merge with another plant's. There was no time zone at
all: shifts were compared against the process's UTC clock, and every
screen drew its wall-clock boundaries in whatever zone the browser
happened to be in.

Three rules hold here.

* **A plant that is not a laptop must say its name.** The `laptop`
  profile is the evaluation profile and defaults to the demo plant's own
  name; `plant` and `fleet` refuse to start without one, in one sentence.
* **A name is a code.** It is validated against the characters a unified
  namespace topic segment allows, so a name never has to be sanitised
  downstream and then silently differ between two surfaces.
* **A defaulted zone is reported as defaulted.** Unknown is not zero: the
  process's own zone is a guess about the plant, and every reader is told
  it was a guess. If the machine's zone cannot even be named, that is
  said too rather than dressed up as an IANA zone.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAME_SETTING = "MES_PLANT_NAME"
ZONE_SETTING = "MES_PLANT_TIMEZONE"
PROFILE_SETTING = "MES_PLANT_PROFILE"

#: The deployment shapes, from docs/design/m8-packs-and-fleet.md §7. A plant
#: states which one it is so the product can refuse what does not fit.
PROFILES = ("laptop", "plant", "fleet")

#: What `fsmes demo` and a bare laptop call themselves. A default only in
#: the `laptop` profile: on a plant, guessing the plant's name is exactly
#: the mistake this module exists to stop.
LAPTOP_PLANT_NAME = "demo"

# The characters a unified-namespace topic segment keeps unchanged
# (`integrations.uns.topics`). A name outside this set would be published
# with underscores in place of the offending characters, so `/health` and
# the broker would disagree about what this plant is called. Refusing at
# start-up is the only way those two can never differ.
_CODE = re.compile(r"\A[A-Za-z0-9_.:@=-]+\Z")


class Misconfigured(RuntimeError):
    """A setting the product refuses to start on. Carries one plain sentence.

    One sentence, not a validation report: the person who set one
    environment variable wrongly is standing next to a plant.
    """


@dataclass(frozen=True)
class Clock:
    """The plant's wall clock, and whether anybody chose it.

    `name` is the IANA zone name, or None when the machine's own zone has
    no name this process can discover — which is a real state on a
    container with nothing but a UTC offset, and is reported rather than
    filled in.
    """

    tz: tzinfo
    name: str | None
    defaulted: bool

    def says(self) -> str:
        """One phrase a person can read, on a screen or in `fsmes info`."""
        if not self.defaulted:
            return self.name or "unknown"
        if self.name:
            return f"{self.name} (defaulted — this machine's own zone)"
        return f"this machine's own clock, which names no zone (set {ZONE_SETTING})"


def is_code(value: str) -> bool:
    """Is this a name that survives a topic segment unchanged?"""
    return bool(_CODE.match(value))


def check(*, plant_name: str, plant_profile: str, plant_timezone: str) -> str | None:
    """The one sentence wrong with this identity, or None if it holds.

    Called from the settings model, so every process that builds Settings
    gets the same refusal — a plant cannot start half-named because one
    worker happened not to look.
    """
    if plant_profile not in PROFILES:
        return (f"{PROFILE_SETTING} is {plant_profile!r}, which is not a deployment profile. "
                f"The profiles are {', '.join(PROFILES)}.")
    if plant_name and not is_code(plant_name):
        return (f"{NAME_SETTING} is {plant_name!r}, which is not a plant code. "
                "A plant name may hold letters, digits and _ . : @ = - and nothing "
                "else, because it is published as one segment of the namespace "
                "topic and a name that had to be cleaned up there would no longer "
                "match the one on this plant's own screens.")
    if not plant_name and plant_profile != "laptop":
        return (f"{NAME_SETTING} is not set, and the {plant_profile} profile needs it: "
                "a reader of this plant's health, metrics, backups or namespace "
                "events cannot tell which plant they are looking at. Set "
                f"{NAME_SETTING} to what this plant is called, or set "
                f"{PROFILE_SETTING}=laptop if this is an evaluation machine.")
    if plant_timezone:
        problem = _zone_problem(plant_timezone)
        if problem:
            return problem
    return None


def _zone_problem(name: str) -> str | None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ModuleNotFoundError, ValueError):
        return (f"{ZONE_SETTING} is {name!r}, which is not a time zone this machine "
                "knows. It wants an IANA name such as Europe/Berlin or "
                "America/Chicago. On Windows the IANA database comes from the "
                "`tzdata` package; if it is missing, `pip install tzdata`.")
    return None


def process_zone_name() -> str | None:
    """The IANA name of this machine's own zone, if it can be known.

    Python has no portable call for this. `TZ` is the explicit answer when
    somebody set it; `/etc/localtime` is the usual one on Linux and macOS.
    When neither answers, so be it: None means *this machine's clock has an
    offset and no name*, which is the truth, and inventing `UTC` here would
    tell a plant in Chicago that it works in London.
    """
    named = os.environ.get("TZ", "").strip()
    if named:
        try:
            ZoneInfo(named)
            return named
        except (ZoneInfoNotFoundError, ModuleNotFoundError, ValueError):
            pass
    link = Path("/etc/localtime")
    try:
        if link.is_symlink():
            target = str(link.readlink())
            if "zoneinfo/" in target:
                candidate = target.split("zoneinfo/", 1)[1]
                ZoneInfo(candidate)
                return candidate
    except (OSError, ZoneInfoNotFoundError, ModuleNotFoundError, ValueError):
        pass
    return None


def clock(settings=None) -> Clock:
    """The plant's wall clock, and whether it was chosen or defaulted."""
    settings = _settings(settings)
    name = (getattr(settings, "plant_timezone", "") or "").strip()
    if name:
        return Clock(tz=ZoneInfo(name), name=name, defaulted=False)
    # No zone set: the process's own. `astimezone()` with no argument binds
    # whatever this machine is doing, which is right even when nothing can
    # name it.
    local = datetime.now().astimezone().tzinfo
    return Clock(tz=local, name=process_zone_name(), defaulted=True)


def to_plant(moment: datetime, settings=None) -> datetime:
    """A naive-UTC instant as the plant's own wall clock.

    Timestamps are naive UTC throughout the MES (`fsmes.db.utcnow`). This
    is the one place that turns one into a local reading, so a shift that
    starts at six starts at six in the plant rather than at six in
    Greenwich.
    """
    from datetime import UTC

    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(clock(settings).tz)


def today(settings=None) -> date:
    """The plant's date now — what a person there would call "today"."""
    return datetime.now(clock(settings).tz).date()


def plant_name(settings=None) -> str:
    """What this plant is called. Never empty: see `Settings`."""
    return _settings(settings).plant_name


def summary(settings=None) -> dict:
    """What every surface reports: `/health`, `/shadow`, `fsmes info`, the
    backup manifest, the namespace envelope, the dashboard header.

    One function so those six can never disagree, which is the whole point
    of a fleet console being able to tell two plants apart.
    """
    settings = _settings(settings)
    the_clock = clock(settings)
    return {
        "plant": settings.plant_name,
        "profile": settings.plant_profile,
        "timezone": the_clock.name,
        "timezone_defaulted": the_clock.defaulted,
        "timezone_says": the_clock.says(),
    }


def _settings(settings=None):
    if settings is not None:
        return settings
    from fsmes.config import get_settings

    return get_settings()
