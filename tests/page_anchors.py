"""Which file serves a screen, and which `data-assist` anchors it really has.

Two suites ask the same question - `test_assistant.py` of the authored guides
and `test_agent.py` of the surfaces behind "Show me" - and they used to answer
it twice, with two copies of the page map that could drift apart. This is the
one copy.

Nothing compiles these pages. An anchor renamed in the markup and left alone
in the walk highlights nothing, or highlights the wrong control and teaches
somebody the wrong habit, and only a check like this one notices.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

#: Every screen a walk may point at, and the file behind it.
PAGE_FILES = {
    "/dashboard": "index.html",
    "/dashboard/orders": "orders.html",
    "/dashboard/station": "station.html",
    "/dashboard/machines": "machines.html",
    "/dashboard/quality": "quality.html",
    "/dashboard/severities": "severities.html",
    "/dashboard/reasons": "reasons.html",
    "/dashboard/analysis": "analysis.html",
    "/dashboard/admin": "admin.html",
    "/dashboard/masterdata": "masterdata.html",
    "/dashboard/instructions": "instructions.html",
    "/dashboard/maintenance": "maintenance.html",
    "/dashboard/schedule": "schedule.html",
    "/dashboard/triggers": "triggers.html",
    "/dashboard/adjustments": "adjustments.html",
    "/dashboard/trace": "trace.html",
    # One file serves every workspace's Configuration page and reads the
    # workspace out of its own address, so the authored step names the
    # template and the proposal fills it in.
    "/dashboard/config/{domain}": "config.html",
}


def page_file(page: str) -> str:
    """The file behind a step's page. A step may carry a query string - which
    setting, which machine - and that is not part of which file serves it."""
    return PAGE_FILES[page.split("?")[0]]


def anchors_on(page: str) -> set[str]:
    """Every anchor a page actually has: the ones in its markup, and the ones
    its own scripts put on controls they build. A page whose rows are drawn
    from an API has no anchor in its HTML at all, and a check that only read
    the HTML would call every one of those steps broken."""
    html = (WEB / page_file(page)).read_text(encoding="utf-8")
    found = set(re.findall(r'data-assist="([^"]+)"', html))
    for script in re.findall(r'<script src="/static/([^"]+\.js)"', html):
        source = (WEB / script).read_text(encoding="utf-8")
        found |= set(re.findall(r'data-assist="([^"]+)"', source))
        found |= set(re.findall(r'dataset\.assist\s*=\s*"([^"]+)"', source))
    return found
