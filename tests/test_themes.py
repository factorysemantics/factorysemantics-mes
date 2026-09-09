"""Themes.

A control room at night, a shop floor under daylight and a screen read through
safety glasses are different problems, not different tastes. These check that
every screen can actually switch, and that no colour escaped the palette - a
hardcoded hex is invisible until somebody picks the theme it breaks under.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
PAGES = ["index.html", "orders.html", "quality.html", "analysis.html",
         "line.html", "line3d.html", "machine.html", "machines.html", "tags.html", "maintenance.html", "schedule.html",
         "spc.html", "gauges.html", "trace.html", "masterdata.html", "triggers.html", "adjustments.html", "coa.html",
         "admin.html", "instructions.html", "ops.html"]
THEMES = ["control-room", "daylight", "high-contrast", "night-shift"]
# Every colour token a component may use. A theme that omits one leaves a
# component drawing with whatever the previous theme left behind.
TOKENS = ["--bg", "--panel", "--panel-2", "--line", "--text", "--muted",
          "--accent", "--running", "--idle", "--down", "--setup", "--unknown"]


@pytest.fixture(scope="module")
def themes_css():
    return (WEB / "themes.css").read_text(encoding="utf-8")


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_defines_every_colour(themes_css, theme):
    selector = ':root,\n:root[data-theme="control-room"]' if theme == "control-room" \
        else f':root[data-theme="{theme}"]'
    block = themes_css.split(selector, 1)
    assert len(block) == 2, f"{theme} has no block"
    body = block[1].split("}", 1)[0]
    missing = [t for t in TOKENS if f"{t}:" not in body]
    assert not missing, f"{theme} leaves {missing} to whatever came before"


@pytest.mark.parametrize("page", PAGES)
def test_every_screen_can_be_themed(page):
    html = (WEB / page).read_text(encoding="utf-8")
    assert "themes.css" in html, f"{page} has no palette"
    assert "themes.js" in html, f"{page} has no picker"


@pytest.mark.parametrize("page", PAGES)
def test_the_choice_lands_before_first_paint(page):
    """Otherwise a daylight user gets a flash of the dark control-room
    palette on every navigation."""
    html = (WEB / page).read_text(encoding="utf-8")
    head = html.split("</head>", 1)[0]
    assert "fsmes-theme" in head, f"{page} applies its theme too late"
    assert "data-theme" in head


def test_no_stylesheet_hardcodes_a_palette_colour():
    """A literal colour is invisible until somebody picks the theme it breaks
    under, which is the worst way to find one."""
    offenders = []
    for sheet in WEB.glob("*.css"):
        if sheet.name == "themes.css":
            continue
        for number, line in enumerate(sheet.read_text(encoding="utf-8").split("\n"), 1):
            if line.strip().startswith(("/*", "*")) or "--" in line.split(":")[0]:
                continue
            # #06121f is deliberate: text on the accent, which is a light blue
            # or amber in every theme, so it must stay dark in all of them.
            for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b", line):
                if match.group(0).lower() in ("#06121f",):
                    continue
                offenders.append(f"{sheet.name}:{number} {match.group(0)}")
    assert not offenders, "hardcoded colours: " + ", ".join(offenders[:12])


def test_the_high_contrast_theme_is_actually_high_contrast(themes_css):
    """It exists for people the other palettes fail, so it has to earn the
    name rather than just be another dark theme."""
    body = themes_css.split(':root[data-theme="high-contrast"]', 1)[1].split("}", 1)[0]
    values = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", body))

    def luminance(hex_colour: str) -> float:
        r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
        def channel(c):
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = channel(r), channel(g), channel(b)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def ratio(a, b):
        la, lb = sorted((luminance(a), luminance(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    bg = values["--bg"]
    for token in ("--text", "--running", "--idle", "--down", "--accent"):
        got = ratio(values[token], bg)
        assert got >= 4.5, f"{token} is only {got:.1f}:1 against the background"
