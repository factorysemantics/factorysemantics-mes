"""Every settings list the assistant can ask for fits in one tool result.

Scott, 2026-09-26, as ADMIN on the bottling plant: *"I want to change the
default reporting window to 10 hrs"*. The assistant called `plant_settings`
five times and answered **"none of them hold a 'default reporting window'
setting."** It does. `[process] default_report_hours` is listed under
Engineering, its section is labelled *The default reporting window*, and it
is item **17 of 22**.

Nothing was broken except the size. The list carried every key's full `about`
paragraph and came to about 11,100 characters; the agent loop shows a model
the first `RESULT_LIMIT` characters of any tool result, which was 6,000, and
cut the JSON mid-string with a bare `…(truncated)`. The model saw eleven
items of twenty-two, had no way to know the list went on, and reported the
half it saw as the whole.

So this file is the ratchet, and the rule it states is: **a list of settings,
for any workspace of any pack this repository ships, is small enough that the
model sees all of what it was handed.** Two halves to that, both measured
here rather than argued:

1. A row is compact - the ten fields in `ROW`, and no paragraph.
2. A list bounds itself. Even compact, Administration's thirty-nine rows come
   to about 10,900 characters, which no honest row shape fits into six
   thousand. So `plant_settings` returns as many whole rows as fit, states
   the plant's `total`, and names the call that reaches the rest. A list that
   cuts itself and says so is a list; a list cut by the transport is a lie.

The margin is `LIST_SHARE` - six tenths - because a tool result is not the
only thing in a turn, and because a plant may lower `[admin]
agent_result_limit` below the product's default.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import delete

from fsmes import mcp_server
from fsmes.domain import PlantSetting
from fsmes.mcp.settings import LIST_SHARE, ROW
from fsmes.services import auth
from fsmes.services import plant_settings as settings_service
from fsmes.services.agent import RESULT_LIMIT

ROOT = Path(__file__).resolve().parents[1]

#: Every pack this repository ships, found rather than listed, so a pack
#: added tomorrow is measured without this file being edited.
PACKS = sorted(p.parent for p in ROOT.glob("labs/**/plant.toml"))


@pytest.fixture()
def tools(make_client, session, monkeypatch):
    """The product's own MCP tools, pointed at the in-process plant. Reads
    only - nothing here writes, so it needs none of the write plumbing."""
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    session.flush()
    monkeypatch.setattr(mcp_server, "_clients", {"testplant": make_client()})
    monkeypatch.setattr(mcp_server, "_registry",
                        lambda: {"testplant": {"api_port": 0, "label": "Test"}})


def _running_on(session, pack_dir: Path) -> None:
    """Put this plant on one pack's settings, as `fsmes pack apply` would.

    Seeded rather than defaulted because a row is the longer answer: it fills
    `set_by` with `pack-apply` where an unconfigured plant has `null`. The
    measurement should be of the plant that costs the most characters.
    """
    from fsmes.pack import format as fmt

    session.execute(delete(PlantSetting))
    settings_service.forget(session)
    settings_service.seed(session, fmt.read(pack_dir))
    session.flush()
    settings_service.forget(session)


def test_there_are_packs_to_measure():
    """The glob above is the whole list; an empty one would make every
    measurement below pass by measuring nothing."""
    assert len(PACKS) >= 4, [str(p) for p in PACKS]


@pytest.mark.parametrize("pack_dir", PACKS, ids=lambda p: p.name)
def test_no_workspace_of_any_shipped_pack_overruns_a_tool_result(tools, session, pack_dir):
    """The rule, measured: every workspace, every pack, under six tenths of
    what the model is shown."""
    _running_on(session, pack_dir)
    budget = LIST_SHARE * RESULT_LIMIT
    index = mcp_server.plant_settings("testplant")
    assert len(json.dumps(index, default=str)) < budget

    for workspace in index["workspaces"]:
        listed = mcp_server.plant_settings("testplant", workspace["domain"])
        size = len(json.dumps(listed, default=str))
        assert size < budget, (
            f"{pack_dir.name}/{workspace['domain']} answers {size} characters, over "
            f"{budget:.0f}; the tool has to hold back more rows or carry less per row")


@pytest.mark.parametrize("pack_dir", PACKS, ids=lambda p: p.name)
def test_a_list_states_the_plants_total_even_when_it_holds_rows_back(tools, session, pack_dir):
    """The half that stops a short list from reading as a whole one: `total`
    is what the plant has, `showing` is what is here, and `more` names the
    call that reaches the rest. The model is never left to infer any of it."""
    _running_on(session, pack_dir)
    for workspace in mcp_server.plant_settings("testplant")["workspaces"]:
        listed = mcp_server.plant_settings("testplant", workspace["domain"])
        assert listed["total"] == workspace["settings"]
        assert listed["showing"] == len(listed["settings"])
        assert listed["showing"] <= listed["total"]
        if listed["showing"] < listed["total"]:
            assert "more" in listed and str(listed["total"]) in listed["more"]
            # Reachable, not just admitted to: the rest come back.
            rest = mcp_server.plant_settings("testplant", workspace["domain"],
                                             offset=listed["showing"])
            assert rest["settings"] and rest["settings"][0] not in listed["settings"]
        else:
            assert "more" not in listed


def test_a_row_in_a_list_carries_no_paragraph(tools, session):
    """`about` is a paragraph per key, and twenty-two paragraphs are what cut
    the list in half. It is one `key=` call away for the one setting somebody
    is actually reading, and out of every list."""
    for workspace in mcp_server.plant_settings("testplant")["workspaces"]:
        listed = mcp_server.plant_settings("testplant", workspace["domain"])
        for row in listed["settings"]:
            assert set(row) == set(ROW), row
            assert "about" not in row

    full = mcp_server.plant_settings("testplant", key="default_report_hours")
    assert full["setting"]["about"]
