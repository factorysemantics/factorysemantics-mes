"""One Configuration entry per workspace, and what is inside it.

Scott, 2026-09-21, testing the pilot build: the downtime vocabulary had
arrived in the nav bar as a top-level chip of its own, and he does not want
one of those per configurable thing. He expects hundreds of configurable
sections eventually, and a chip each is exactly the cluttered bar this whole
effort exists to avoid. So each workspace gets **one** Configuration entry,
and the things it configures are a list on the page behind it.

The failure this file is here to catch is the retrofit: the day somebody adds
a second configurable thing and reaches for the nav bar because that is where
the last one went. Two tests below hold the registry and `FS.NAV` against
each other so that reaching for the nav bar fails out loud, and the rest pin
what a person actually sees: the list, its total, who may draft and who signs
each row, and the bookmark of the old address still opening the screen it
always did.
"""

import re
from pathlib import Path

import pytest

from fsmes import modules

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
NAV = (WEB / "common.js").read_text(encoding="utf-8")


def _nav_items() -> list[tuple[str, str]]:
    """Every (href, label) in the one nav manifest, in order."""
    return re.findall(r'\{ href: "(/dashboard[^"]*)", label: "([^"]+)"', NAV)


# ------------------------------------------- the nav bar stops growing


def test_the_downtime_vocabulary_is_no_longer_a_chip_of_its_own():
    """Scott's actual complaint, in one line."""
    labels = [label for _, label in _nav_items()]
    assert "Downtime reasons" not in labels
    assert "/dashboard/reasons" not in [href for href, _ in _nav_items()]


def test_every_workspace_with_something_to_configure_has_one_configuration_entry():
    """The seam, held from the registry's side: adding a section to a domain
    that has no Configuration entry yet must fail here rather than tempt the
    next author back into the nav bar."""
    entries = {href for href, label in _nav_items() if label == "Configuration"}
    expected = {f"/dashboard/config/{d.slug}"
                for d in modules.config_domains_with_sections()}
    assert entries == expected


def test_no_configuration_entry_opens_a_workspace_with_nothing_in_it():
    """The other side of the same seam. A Configuration chip that opens an
    empty page is a dead end, and a dead-end chip is worse than no chip."""
    for href, label in _nav_items():
        if label != "Configuration":
            continue
        slug = href.rsplit("/", 1)[-1]
        assert slug in modules.DOMAIN_BY_SLUG, f"{href} names no configuration workspace"
        assert modules.config_sections(slug), f"{href} opens onto nothing"


def test_a_screen_one_level_down_names_itself_in_full():
    """`data-nav` is the page's whole path under /dashboard, not its last
    segment. While every screen was one level deep the two were the same
    string; the day Quality gets a configuration page, /dashboard/config/quality
    would have lit the Quality screen's chip instead."""
    assert 'active === href.split("?")[0].replace(/^\\/dashboard\\//, "")' in NAV
    assert 'data-nav="config/engineering"' in (WEB / "reasons.html").read_text(
        encoding="utf-8")


# ------------------------------------------------------ what the page lists


def test_the_configuration_page_lists_the_workspaces_sections_with_its_total(client):
    """Every list states its total. Engineering's was one section for three
    days and is eighteen since 2026-09-25, and the page says which number it
    is rather than leaving a reader to count the rows."""
    page = client.get("/dashboard/config/engineering/sections").json()

    assert page["title"] == "Engineering"
    assert page["total"] == len(page["items"]) == 18
    # The vocabulary is still the first row, because `equipment` is still the
    # first module this plant mounts, and the order the page states is that.
    assert page["items"][0]["key"] == "downtime_reasons"
    assert page["items"][0]["href"] == "/dashboard/reasons"


def test_every_row_names_the_module_that_put_it_there(client):
    """The page says it is grouped by the module each section belongs to, so
    each row has to carry the module it belongs to. An order a reader is told
    about and cannot see is an order they have to take on trust."""
    page = client.get("/dashboard/config/engineering/sections").json()
    titles = [row["module"] for row in page["items"]]

    assert all(titles), "a row with no module is a group heading nobody can draw"
    # Grouped, not merely labelled: each module's sections are contiguous, so
    # the same module never comes back after another one has been drawn.
    seen, groups = set(), []
    for title in titles:
        if not groups or groups[-1] != title:
            groups.append(title)
            assert title not in seen, f"{title} appears in two separate groups"
            seen.add(title)
    assert groups[0] == "Machines, their states and their tags"
    assert len(groups) == 6


def test_each_row_names_the_capability_that_drafts_it_and_the_one_that_signs_it(
        client, admin):
    """The page gates nothing - it says where the door is. What it adds is
    whether this caller's own role opens it, read live from the role rather
    than from the token."""
    for who, may_define, may_approve in ((client, False, False), (admin, True, True)):
        row = who.get("/dashboard/config/engineering/sections").json()["items"][0]
        assert row["define"] == "process.define"
        assert row["approve"] == "process.approve"
        assert row["may_define"] is may_define
        assert row["may_approve"] is may_approve


def test_a_workspace_this_version_does_not_have_is_refused_not_shown_empty(client):
    """A typed URL that renders a real-looking empty page is how somebody
    comes to believe a workspace has nothing in it."""
    answer = client.get("/dashboard/config/sorcery/sections")
    assert answer.status_code == 404
    assert "engineering" in answer.json()["detail"]


def test_a_section_whose_module_a_plant_does_not_serve_is_withheld_and_named(client):
    """A row that opens onto a screen this plant does not serve is a dead end,
    so it is not offered - and it is named rather than dropped, because a
    workspace with nothing in it and a workspace with one section switched off
    are different plants, and only one of them has a screen to go looking for.

    Held on the registry helper rather than through `MES_MODULES` so that the
    assertion is about the rule and not about which modules a test plant
    happens to serve. Since 2026-09-25 this is a real case rather than a
    hypothetical one: Engineering's eighteen sections come from six modules and
    five of those six are optional, so a plant that serves no scheduling has
    four fewer rows and is told so.
    """
    without = tuple(m for m in modules.REGISTRY if m.name != "scheduling")
    kept = {s.key for s in modules.config_sections("engineering", without)}
    everything = {s.key for s in modules.config_sections("engineering")}

    assert everything - kept == {"default_cycle_seconds", "schedule_default_horizon_hours",
                                "previous_shift_horizon_days", "working_week_mask"}
    # The vocabulary belongs to `equipment`, which is kernel, so no plant can
    # be without it.
    assert "downtime_reasons" in kept

    # This test plant serves everything, so nothing is withheld from it - and
    # the endpoint says so out loud rather than leaving the key off.
    assert client.get("/dashboard/config/engineering/sections").json()["switched_off"] == []


# ------------------------------------------------- nothing moved underneath


def test_the_screen_people_bookmarked_is_still_at_the_address_they_bookmarked(client):
    """Only the nav entry moved. An engineer who bookmarked /dashboard/reasons
    in the four days it was a chip opens the same screen."""
    answer = client.get("/dashboard/reasons")
    assert answer.status_code == 200
    assert "<html" in answer.text.lower()


def test_the_configuration_page_is_served_for_every_domain_that_has_one(client):
    for domain in modules.config_domains_with_sections():
        answer = client.get(f"/dashboard/config/{domain.slug}")
        assert answer.status_code == 200, domain.slug
        assert "config.js" in answer.text


@pytest.mark.parametrize("section", [s for m in modules.REGISTRY
                                     for s in m.config_sections])
def test_every_registered_section_points_at_a_screen_the_product_serves(
        client, section):
    """A row that opens onto a 404 is the one failure this registry can
    introduce that nothing else would notice."""
    assert section.domain in modules.DOMAIN_BY_SLUG
    assert client.get(section.href).status_code == 200
