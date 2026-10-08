"""The SPC tab's chart is the kit's chart, in every theme, on the record.

Until this branch the SPC page drew its own SVG and `fsmes ui-check` found no
`svg.fs-chart` on `/dashboard/spc`: the four accepted baselines in
`tests/ui/baselines/` listed a header, a panel, a button and a table for that
route and no chart at all. Drawing the control chart through `FS.kit.draw` adds
one, so the conformance crawl would file *component 'chart' is new - accept if
deliberate* four times. It is deliberate, the baselines now say so, and this
file is why anybody can believe them: the four `chart` entries were measured in
Chromium on this fixture, not copied by hand, and they came back identical to
the chart entry the crawl already accepted on `/dashboard/analysis` - the same
frame, the same box, the same type, in all four themes.

So the check runs the crawl's own instrument on one route. `WATCHED` and
`PROPERTIES` are imported from `fsmes.sim.ui_check` rather than restated, so a
property added to the style contract is read here the day it is added. What
this file cannot do is crawl: it visits one page on a plant of its own, and the
whole-product sweep is still `fsmes ui-check`, which needs a seeded plant and a
browser. It pins the entry those baselines gained, and that is all it claims.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for.
"""

import json
from pathlib import Path

import pytest
from tests.test_clicking_a_sample_on_the_spc_chart_opens_the_five_bottles import (  # noqa: F401
    THEMES,
    _close,
    _open_spc,
    admin,
    chromium,
    plant,
)

from fsmes.sim.ui_check import BASELINES, PROPERTIES, WATCHED

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: The route as the crawler names it, which is the template's path and not a
#: particular spec's query string.
ROUTE = "/dashboard/spc"

#: The crawler's own probe, which reads one computed style off the first
#: element a watched selector matches.
PROBE = """([selector, properties]) => {
    const found = document.querySelector(selector);
    if (!found) return null;
    const computed = getComputedStyle(found);
    const out = {};
    for (const property of properties) out[property] = computed.getPropertyValue(property);
    return out;
}"""


def _accepted(theme: str) -> dict:
    path = Path(BASELINES) / f"{theme}.json"
    assert path.is_file(), f"{theme} has no accepted baseline"
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot(page) -> dict:
    return {
        name: got
        for name, selector in WATCHED.items()
        if (got := page.evaluate(PROBE, [selector, list(PROPERTIES)])) is not None
    }


@pytest.mark.parametrize("theme", THEMES)
def test_the_spc_page_wears_the_look_its_baseline_accepted(admin, plant, theme):  # noqa: F811
    """What the crawl would snapshot is what the baseline says, component for
    component and property for property - including the chart it just gained."""
    base, _sample = plant
    page = _open_spc(admin, base, theme=theme)
    try:
        snapshot = _snapshot(page)
    finally:
        _close(page)

    accepted = _accepted(theme)[ROUTE]
    assert sorted(snapshot) == sorted(accepted), (
        f"{theme}: the SPC page's components are {sorted(snapshot)}, the "
        f"baseline accepted {sorted(accepted)} - run fsmes ui-check --accept "
        f"in the branch that changed it")
    for component in sorted(accepted):
        assert snapshot[component] == accepted[component], f"{theme}: {component} drifted"


def test_the_chart_frame_is_the_same_frame_the_analysis_tab_draws(admin, plant):  # noqa: F811
    """One chart in the product, not two that look alike.

    The chart contract says the frame is the same wherever a chart is drawn.
    `/dashboard/analysis` has had a kit chart since the crawl's first run, so
    its accepted entry is the standing definition of that frame; the SPC page's
    new entry has to be it, in every theme, or the SPC tab has grown a chart of
    its own again.
    """
    base, _sample = plant
    for theme in THEMES:
        accepted = _accepted(theme)
        assert accepted[ROUTE]["chart"] == accepted["/dashboard/analysis"]["chart"], theme

    page = _open_spc(admin, base, theme="control-room")
    try:
        drawn = page.evaluate(PROBE, [WATCHED["chart"], list(PROPERTIES)])
    finally:
        _close(page)
    assert drawn == _accepted("control-room")["/dashboard/analysis"]["chart"]
