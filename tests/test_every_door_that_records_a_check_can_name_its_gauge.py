"""Every door that records a quality check can say which gauge took it.

The structure report of 2026-11-18 ranked this first: "One door got the
gauge, two did not." #252 gave the AI tab's `record_check` a `gauge`; the
Floor's Quality check form and the station screen posted the same reading
with none, so a plant whose people record at the machine could never answer
the question a control chart asks - did the process move, or did the gauge?
Both screens now offer the plant's gauge register and send what was chosen;
left empty, the check still says *not recorded*.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _check_body(script: str) -> str:
    """The object literal each screen posts to /quality/checks."""
    source = (WEB / script).read_text(encoding="utf-8")
    call = source.index('api("/quality/checks"')
    start = source.index("body:", call)
    open_at = source.index("{", start)
    depth = 0
    for i in range(open_at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[open_at:i + 1]
    raise AssertionError(f"{script}: no closing brace for the check's body")


def _input(page: str, marker: str) -> str:
    html = (WEB / page).read_text(encoding="utf-8")
    found = re.search(r"<input\b[^>]*" + re.escape(marker) + r"[^>]*>", html, re.S)
    assert found, f"{page} has no {marker} input"
    tag = found.group(0)
    listed = re.search(r'list="([^"]+)"', tag)
    assert listed and f'<datalist id="{listed.group(1)}"' in html, \
        f"{page}'s gauge box offers no list of the plant's gauges"
    return tag


def test_the_floors_check_sends_the_gauge_it_was_given():
    assert re.search(r"\bgauge\s*:", _check_body("app.js")), "the Floor posts a check with no gauge"
    _input("index.html", 'name="gauge"')


def test_the_station_sends_the_gauge_it_was_given():
    assert re.search(r"\bgauge\s*:", _check_body("station.js")), "the station posts a check with no gauge"
    _input("station.html", 'id="q-gauge"')


def test_both_screens_offer_the_gauge_register():
    for script in ("app.js", "station.js"):
        assert 'api("/quality/gauges")' in (WEB / script).read_text(encoding="utf-8"), script
