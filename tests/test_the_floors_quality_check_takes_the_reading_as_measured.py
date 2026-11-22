"""The Floor's quality check takes a reading as precise as the gauge gave it.

Class 4.1 (2026-11-21) measured 26.35 and the Floor screen's Measured box
refused it: the box had `step="0.1"`, so the browser would only take tenths
and pushed the operator to round before saving - a recorded value nobody
measured, sitting next to a specification limit. The station screen's box
for the same reading has always said `step="any"`. Both doors now take the
number as measured.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _step_of(page: str, marker: str) -> str:
    html = (WEB / page).read_text(encoding="utf-8")
    tag = re.search(r"<input\b[^>]*" + re.escape(marker) + r"[^>]*>", html, re.S).group(0)
    return re.search(r'step="([^"]+)"', tag).group(1)


def test_the_floors_measured_box_takes_any_precision():
    assert _step_of("index.html", 'data-assist="quality-value"') == "any"


def test_the_floor_and_the_station_take_the_reading_the_same_way():
    assert _step_of("index.html", 'data-assist="quality-value"') == _step_of("station.html", 'id="q-value"')
