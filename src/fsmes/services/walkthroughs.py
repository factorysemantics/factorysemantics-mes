"""Recorded walkthroughs: a supervisor did the job once, on the real screens,
and said why at each step. Stored as a controlled document of kind
`walkthrough`; played by the assistant exactly like a built-in guide.

The one rule that makes a recording trustworthy: every step points at a
control that exists. A step is a page and a `data-assist` anchor, and the
plant's own HTML is the authority - the same check the built-in guides pass
in the test suite runs here, at save time, on user data. A recording that
cannot be played is refused with the step named, never stored to fail later
in front of a trainee.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from fsmes.services import Invalid
from fsmes.services import capabilities as caps

WEB = Path(__file__).resolve().parents[1] / "web"

# Dashboard route -> the file that serves it (see fsmes.api.app).
PAGE_FILES: dict[str, str] = {
    "/dashboard": "index.html",
    "/dashboard/station": "station.html",
    "/dashboard/orders": "orders.html",
    "/dashboard/quality": "quality.html",
    "/dashboard/ops": "ops.html",
    "/dashboard/instructions": "instructions.html",
    "/dashboard/admin": "admin.html",
    "/dashboard/line": "line.html",
    "/dashboard/machines": "machines.html",
    "/dashboard/analysis": "analysis.html",
    "/dashboard/maintenance": "maintenance.html",
    "/dashboard/schedule": "schedule.html",
    "/dashboard/gauges": "gauges.html",
    "/dashboard/spc": "spc.html",
    "/dashboard/trace": "trace.html",
    "/dashboard/masterdata": "masterdata.html",
    "/dashboard/triggers": "triggers.html",
    "/dashboard/adjustments": "adjustments.html",
    "/dashboard/tags": "tags.html",
    "/dashboard/coa": "coa.html",
}

STEP_FIELDS = {"page", "anchor", "title", "body", "fill", "tab", "open"}

#: How long a recorded walkthrough may be, and the four lengths at which the
#: plant's own words are shortened. The literals that were here, and the
#: defaults of `[admin] walkthrough_max_steps`, `walkthrough_title_chars`,
#: `walkthrough_body_chars`, `walkthrough_fill_chars` and
#: `walkthrough_tab_chars`. A plant with a ninety-step changeover procedure was
#: refused for a reason nothing outside this file cared about; now it says so
#: once, on Setup > Configuration.
MAX_STEPS = 60
TITLE_CHARS = 120
BODY_CHARS = 1000
FILL_CHARS = 200
TAB_CHARS = 60

#: What `validate_steps` uses when nobody hands it a plant's answer - the
#: shipped literals, named so the limits are one dictionary rather than five
#: positional arguments nobody can read at a call site.
SHIPPED_LIMITS = {"max_steps": MAX_STEPS, "title_chars": TITLE_CHARS,
                  "body_chars": BODY_CHARS, "fill_chars": FILL_CHARS,
                  "tab_chars": TAB_CHARS}


def limits(session) -> dict:
    """This plant's walkthrough limits, read through `plant_settings`.

    Takes the caller's session, like every other live setting: the five rows
    are one query per unit of work, and a limit somebody raises on the
    Configuration page applies to the next walkthrough recorded rather than to
    the next restart.
    """
    from fsmes.services import plant_settings

    return {name: int(plant_settings.setting(session, "admin", f"walkthrough_{name}"))
            for name in SHIPPED_LIMITS}


@lru_cache(maxsize=64)
def anchors_on(page: str) -> frozenset[str]:
    """Every data-assist anchor a page has. Cached for the life of the process:
    the HTML ships with the code and changes only on deploy."""
    name = PAGE_FILES.get(page)
    if name is None:
        return frozenset()
    try:
        html = (WEB / name).read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(re.findall(r'data-assist="([^"]+)"', html))


def validate_steps(steps: list[dict] | None, caps_: dict | None = None) -> list[dict]:
    """Return the steps, clean, or raise Invalid naming the first bad one.

    `caps_` is this plant's own limits, from `limits(session)`. `None` is the
    shipped ones, for the handful of callers with no session to read them
    through - and the sentence a person is refused with names the number they
    were refused by, whichever it was.
    """
    caps_ = SHIPPED_LIMITS if caps_ is None else caps_
    max_steps = int(caps_["max_steps"])
    if not steps:
        raise Invalid("a walkthrough needs at least one step")
    if len(steps) > max_steps:
        raise Invalid(f"a walkthrough may have at most {max_steps} steps")
    clean = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise Invalid(f"step {i} is not an object")
        unknown = sorted(set(step) - STEP_FIELDS)
        if unknown:
            raise Invalid(f"step {i} has unknown field(s): {', '.join(unknown)}")
        page, anchor = step.get("page"), step.get("anchor")
        if page not in PAGE_FILES:
            raise Invalid(f"step {i}: unknown page {page!r}")
        if not anchor or anchor not in anchors_on(page):
            raise Invalid(f"step {i}: {PAGE_FILES[page]} has no control data-assist={anchor!r}")
        if step.get("open") and step["open"] not in anchors_on(page):
            raise Invalid(f"step {i}: nothing to open called {step['open']!r} on {page}")
        title = str(step.get("title") or "").strip()
        body = str(step.get("body") or "").strip()
        if not title:
            raise Invalid(f"step {i} has no title")
        out = {"page": page, "anchor": anchor,
               "title": title[:int(caps_["title_chars"])],
               "body": body[:int(caps_["body_chars"])]}
        fill = step.get("fill")
        if fill is not None:
            if not isinstance(fill, dict) or "value" not in fill:
                raise Invalid(f"step {i}: fill must be {{\"value\": ...}}")
            out["fill"] = {"value": str(fill["value"])[:int(caps_["fill_chars"])]}
        for key in ("tab", "open"):
            if step.get(key):
                out[key] = str(step[key])[:int(caps_["tab_chars"])]
        clean.append(out)
    return clean


#: What a recorded walkthrough asks of a viewer when nobody says otherwise.
#: `[admin] walkthrough_default_capability`, and the literal that was here.
DEFAULT_NEEDS = "plant.read"


def default_needs(session) -> str:
    """The gate this plant puts on a recording nobody gates itself."""
    from fsmes.services import plant_settings

    return str(plant_settings.setting(session, "admin", "walkthrough_default_capability"))


def validate_needs(needs: str | None, fallback: str | None = None) -> str:
    """The capability a recording is gated on. `fallback` is this plant's own
    answer; `None` is the product's, for a caller with no session."""
    needs = (needs or fallback or DEFAULT_NEEDS).strip()
    if needs not in caps.CAPABILITIES:
        raise Invalid(f"unknown capability {needs!r}")
    return needs


def dumps(steps: list[dict]) -> str:
    return json.dumps(steps, ensure_ascii=False)


def as_guide(doc) -> dict:
    """A walkthrough document in the shape the assistant plays."""
    return {
        "id": f"doc:{doc.code}",
        "code": doc.code,
        "title": doc.title,
        "when": doc.body,
        "needs": doc.needs or DEFAULT_NEEDS,
        "steps": doc.steps_list(),
        "recorded_by": doc.created_by,
        "revision": doc.revision,
        "approved_by": doc.approved_by,
        "kind": "walkthrough",
    }
