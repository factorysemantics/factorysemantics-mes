"""Every door that records a check says what it set off, in the same words.

Structure report 2026-11-18, clean-up C3 ("quality-check entry points
agree"). `POST /quality/checks` answers with the result, the non-conformance
an out-of-spec reading opened, and `spc`: the control-chart signals the
reading tripped, each with the hold it raised. The station screen said all
three. The Floor's Quality check read only pass or fail, so a reading in
spec that fired a chart rule came back "Check passed." while a
non-conformance had just been raised on that material.

Now both screens ask `FS.checkSaid` in `web/common.js`, the one place that
turns the answer into words.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

# Just enough of a browser for common.js to load: no element is ever found,
# and no request ever answers.
_HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const nothing = () => null;
const doc = { getElementById: nothing, querySelector: nothing, querySelectorAll: () => [],
              addEventListener: () => {}, createElement: () => ({ classList: { add() {}, remove() {} },
              appendChild() {}, setAttribute() {}, style: {} }), body: { appendChild() {} } };
const ctx = { window: {}, document: doc, location: { pathname: "/" }, navigator: {},
              localStorage: { getItem: nothing, setItem() {} }, sessionStorage: { getItem: nothing, setItem() {} },
              fetch: () => new Promise(() => {}), setTimeout, clearTimeout, setInterval, clearInterval,
              console, Intl, Date, Math, Number, String, JSON, Promise, URLSearchParams };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const answers = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(answers.map((a) => ctx.window.FS.checkSaid(a))));
"""

NODE = shutil.which("node")


def _said(tmp_path, answers):
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    out = subprocess.run([NODE, str(harness), str(WEB / "common.js"), json.dumps(answers)],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node to run common.js")
def test_a_reading_that_fired_a_chart_rule_says_so_and_names_the_hold(tmp_path):
    spc, fail, ok = _said(tmp_path, [
        {"result": "pass", "value": 25.1, "non_conformance": None,
         "spc": [{"rule": 2, "nonconformance": "NC-00042", "what": "nine in a row above the centre line"}]},
        {"result": "fail", "value": 27.5, "non_conformance": "NC-00043", "spc": []},
        {"result": "pass", "value": 25.1, "non_conformance": None, "spc": []},
    ])
    assert spc["kind"] == "bad"
    assert "NC-00042" in spc["message"] and "rule 2" in spc["message"]
    assert "in spec" in spc["detail"] and "nine in a row" in spc["detail"]
    assert fail["kind"] == "bad" and "NC-00043" in fail["message"] and "out of spec" in fail["message"]
    # The words a filmed class waits for (05-01, the station's in-spec toast).
    assert ok == {"message": "Recorded 25.1 — in spec.", "kind": "good", "detail": None}


def test_both_screens_say_it_through_the_one_helper():
    floor = (WEB / "app.js").read_text(encoding="utf-8")
    station = (WEB / "station.js").read_text(encoding="utf-8")
    assert "FS.checkSaid(" in floor, "the Floor's Quality check words its own answer"
    assert "FS.checkSaid(" in station, "the station words its own answer"
    for name, source in (("app.js", floor), ("station.js", station)):
        assert "Check passed." not in source, f"{name} still says pass without reading `spc`"
        assert "control chart fired" not in source, f"{name} keeps its own copy of the words"
