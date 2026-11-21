"""An engineer looking for routings on Master data is told where they are.

A routing is master data to an engineer, so Engineering > Master data is where
the nightly walk looked for it, twice, and gave up: the routings editor is on
Setup > Admin, and nothing on Master data said so (card 2026-11-13
find-routing-form). The page now says it in a line, with a link that lands on
the list.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def test_master_data_links_to_the_routings_list_on_admin():
    page = (WEB / "masterdata.html").read_text(encoding="utf-8")
    link = re.search(r'<a [^>]*href="/dashboard/admin#routings"[^>]*>([^<]+)</a>', page)
    assert link, "Master data does not say where routings are kept"
    assert "Routings" in link.group(1)


def test_the_link_lands_on_the_routings_list():
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    assert 'id="routings"' in admin
