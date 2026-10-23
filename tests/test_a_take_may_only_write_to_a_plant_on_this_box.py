"""A scene allowed to write may only write to a plant on this box.

`scripts/record-scene.py` normally films a plant and changes nothing: any
request the page makes that is not a read fails the take. One scene needs the
exception. A clip about quality work with no write in it is a tour — the
operator's part of the story is *taking the hold under review*, and that is a
write — so a scene may set `allow_writes = true`, and the take that scene
makes brings up its own scratch plant to do it on.

The danger is that `allow_writes` is one line in a text file and `--base` is
one word on a command line. The lab plants and the public demo are up on this
tailnet with somebody reading them, and a take pointed at one of those with
writes allowed would take a hold under review on a plant that is not ours,
leaving a real-looking decision in somebody else's record. Nothing inside the
scene file can tell you that happened.

So the plant's *address* decides, not the scene: writes are honoured at a
loopback address and refused everywhere else, and the refusal comes before the
browser opens rather than after the take is in the can.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

#: A plant on some other machine, at one of the ports a plant somebody else
#: started tends to answer on. The address is a documentation one (RFC 5737)
#: and not any real plant's: what the test needs is an address that is not
#: this box, and nothing is gained by writing down one that answers.
A_PLANT_SOMEBODY_ELSE_IS_WATCHING = "http://198.51.100.7:8110"


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "base",
    [
        "http://127.0.0.1:8010",
        "http://127.0.0.1:8010/",
        "http://localhost:8010",
        "http://127.5.9.3:8010",
        "http://[::1]:8010",
    ],
)
def test_a_plant_at_a_loopback_address_is_on_this_box(runner, base):
    assert runner.on_loopback(base) is True


@pytest.mark.parametrize(
    "base",
    [
        A_PLANT_SOMEBODY_ELSE_IS_WATCHING,
        "http://198.51.100.7:8090",
        "http://192.168.1.40:8010",
        "https://demo.factorysemantics.org",
        "http://0.0.0.0:8010",
    ],
)
def test_a_plant_anywhere_else_is_not_on_this_box(runner, base):
    assert runner.on_loopback(base) is False


def test_a_hostname_that_merely_resolves_to_loopback_is_not_on_this_box(runner):
    """The rule is one a person can check by reading the scene file.

    A name's answer is a question to a resolver, and the resolver can answer
    differently tomorrow than it did when the scene was reviewed. Only the
    literal addresses and `localhost` itself count.
    """
    assert runner.on_loopback("http://my-laptop.local:8010") is False
    assert runner.on_loopback("http://bottling.internal:8010") is False


def test_a_take_that_may_write_is_refused_against_a_plant_that_is_not_on_this_box(runner):
    with pytest.raises(SystemExit) as refused:
        runner.refuse_to_write_off_this_box(
            {"allow_writes": True, "base": A_PLANT_SOMEBODY_ELSE_IS_WATCHING}
        )

    complaint = str(refused.value)
    assert A_PLANT_SOMEBODY_ELSE_IS_WATCHING in complaint, "the refusal names the plant it would have written to"
    assert "loopback" in complaint
    assert "allow_writes" in complaint, "and names the line in the scene file to change"


def test_a_take_that_may_write_is_allowed_against_a_plant_on_this_box(runner):
    runner.refuse_to_write_off_this_box({"allow_writes": True, "base": "http://127.0.0.1:8010"})


def test_a_take_that_writes_nothing_may_film_any_plant(runner):
    """Reading the lab plant in a browser is allowed, and is where most takes belong."""
    runner.refuse_to_write_off_this_box(
        {"allow_writes": False, "base": A_PLANT_SOMEBODY_ELSE_IS_WATCHING}
    )


def test_the_refusal_comes_before_the_browser_opens(runner, tmp_path, monkeypatch):
    """A take refused after it is filmed has already written to the wrong plant.

    `record` asks the address question first, before it launches Chromium,
    signs in, or sends the plant a single request — so this refusal cannot
    depend on anything the plant said.
    """
    def no_plant_should_be_asked(base):  # pragma: no cover - the point is it is not called
        raise AssertionError(f"{base} was asked /health before the address was checked")

    monkeypatch.setattr(runner, "the_plant", no_plant_should_be_asked)
    scene = dict(runner.DEFAULTS)
    scene.update({"allow_writes": True, "base": A_PLANT_SOMEBODY_ELSE_IS_WATCHING, "step": []})

    with pytest.raises(SystemExit) as refused:
        runner.record(scene, tmp_path)

    assert "loopback" in str(refused.value)
