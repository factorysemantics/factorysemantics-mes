"""The settings a plant owns: read any domain's, find one by name, write one,
and read what has been written to them.

Three tools for every domain there will ever be. Not one set per domain, and
not one tool file per domain either: a live setting is a row in one table,
listed by the `ConfigSection` that owns it and written through one endpoint
that reads the owning section per key
(`PATCH /dashboard/config/{domain}/settings/{key}`, #100). So the domain is an
argument here rather than a name in the source, and the day
`<domain>-live-settings` marks another section `edit_here` its numbers are
reachable through these tools with nothing added.

That is why this file belongs to the `dashboard` module rather than to
Quality: the table, the page and the endpoint are the Configuration page's
own, and the module registry already says so
(`modules.py`, `tables=("plant_settings",)`).

## A list the model can actually read, 2026-09-26

Scott asked the assistant to change "the default reporting window" and was
told **no workspace holds such a setting**. It does: `[process]
default_report_hours`, listed under Engineering, section label *The default
reporting window* - item 17 of 22. The list this tool returned carried every
key's full `about` paragraph, came to about 11,100 characters, and the agent
loop shows a model the first 6,000 of a tool result. The model saw eleven of
twenty-two items, had no way to know the list went on, and reported the half
it saw as the whole.

Three things came out of that, and they are why this file looks the way it
does:

- **A row is compact.** `about` is a paragraph per key and is not in a list;
  it is one `key=` call away for the one setting the model cares about. What
  a row does carry now is the owning section's **label** - *The default
  reporting window* - which is what a person calls a setting and which the
  row did not carry at all.
- **The list bounds itself.** Even compact, Administration's thirty-nine rows
  are about 10,900 characters, which no honest row shape gets under a
  six-thousand-character result. So this tool pages: it returns as many whole
  rows as fit `LIST_SHARE` of that budget, states the plant's total, and says
  how to reach the rest. A list cut by the transport is a list that lies; a
  list that cuts itself and says so is a list.
- **A setting is findable by what a person calls it.** `find=` searches every
  workspace's names, labels and paragraphs at once and is the first move for
  a phrase like "reporting window". It is what the assistant should reach for
  before it reaches for a whole workspace.

## Why writing one is not drafting one

`draft_downtime_reason` and `draft_nc_severity` (#99) propose a *word*: it
lands as a draft, on nobody's screen, until somebody holding the domain's
`approve` capability signs it. A setting is not a word. Rule three of
decision 0035: *a number a plant administrator edits and which takes effect
when saved has no pending state* - nothing anywhere records the Cpk bar that
was in force when it judged something, so there is nothing for a revision to
protect. The value is in force the moment it is written, which is exactly why
it is a proposal a person clicks rather than a draft somebody signs later:
`dry_run=True` previews it, the card asks, and **there is no approve
capability anywhere near this tool**.

## Nothing is checked twice

The value is sent as the text a person would have typed. `fsmes.pack.format`
parses it and `fsmes.pack.check` judges it, once, on the way in - including
the pairs, where `cpk_marginal` is judged against the `cpk_capable` this
plant is already running on. A 422 or a 403 from the API comes back as this
tool's own answer, in the API's own sentence. A second opinion here would be
a second parser, reachable only through an agent, disagreeing with the one a
pack file goes through.
"""

from __future__ import annotations

import json
from urllib.parse import quote

#: How much of one tool result a settings list may fill. The rest is headroom
#: for the model's own turn and for the loop's own framing; 0.6 was set with
#: the measured packs in `tests/test_a_settings_list_is_never_cut_in_half.py`,
#: which fails if any shipped pack's workspace ever crosses it.
LIST_SHARE = 0.6

#: The fields a row in a list carries. `about` is deliberately not one of
#: them - it is a paragraph, and twenty-two paragraphs are what cut the list
#: in half on 2026-09-26. `plant_settings(domain, key=...)` returns it for the
#: one setting somebody is actually reading.
ROW = ("name", "label", "value", "default", "is_default", "set_by", "kind",
       "section", "needs", "agent_may_write")


def _budget() -> int:
    """Characters a list may take, read from the agent loop's own limit rather
    than written down a second time here.

    A plant that lowers `[admin] agent_result_limit` below the product's
    default can still overrun this; that case is caught by the loop, which
    drops whole rows and says how many (`services/agent._tool_result`).
    """
    from fsmes.services.agent import RESULT_LIMIT

    return int(LIST_SHARE * RESULT_LIMIT)


def register(mcp, call, write, identify) -> dict:

    # ------------------------------------------------------------ reading

    def _workspaces(plant: str):
        """Every Configuration workspace this plant serves, asked rather than
        guessed: one server operates plants of different versions, so the list
        is the plant's own answer and never this process's registry."""
        out = call(plant, "GET", "/dashboard/config")
        if isinstance(out, dict) and "error" in out:
            return out
        return [w["domain"] for w in out["workspaces"]]

    def _page(plant: str, domain: str):
        return call(plant, "GET", f"/dashboard/config/{quote(domain)}/sections")

    def _rows(page: dict) -> tuple[list[dict], list[str]]:
        """One workspace's sections payload, as compact rows and the honest
        other half: keys the page shows and nothing can change while the plant
        is running."""
        settings: list[dict] = []
        read_only: list[str] = []
        for section in page["items"]:
            for key in section["pack_keys"]:
                if not section["edit_here"]:
                    read_only.append(key["key"])
                    continue
                settings.append({
                    "name": key["name"],
                    # What a person calls this setting. Scott asked for "the
                    # default reporting window", which is this label word for
                    # word, and the row used to carry only `default_report_hours`.
                    "label": section["label"],
                    "value": key["value"],
                    "default": key["default"],
                    "is_default": key["is_default"],
                    "set_by": key["set_by"],
                    "kind": key["kind"],
                    "section": section["key"],
                    # The capability a write is gated on, per key rather than
                    # per tool: it is the owning section's, which is why one
                    # tool covers every domain and no single capability gates
                    # it. `None` is a section nobody has to hold anything for.
                    "needs": section["define"],
                    # Whether the AGENT account itself may write it - the
                    # person asking is gated separately, where they are asked.
                    "agent_may_write": section["may_define"],
                    # Not part of a row (`ROW` says what is), carried here so
                    # `find` can search the paragraph without returning it.
                    "_about": key.get("about") or "",
                    "_domain": page["domain"],
                    "_key": key["key"],
                })
        return settings, read_only

    def _public(row: dict, with_domain: bool = False) -> dict:
        out = {field: row[field] for field in ROW}
        if with_domain:
            # Which workspace holds it, because a find crosses all of them and
            # a row nobody can locate is a row nobody can write.
            out["domain"] = row["_domain"]
        return out

    def _fit(envelope: dict, rows: list[dict], *, listname: str, total: int,
             offset: int, advice: str) -> dict:
        """As many whole rows as fit, the plant's total, and what to call to
        see the rest. Never a row cut in half and never a silent short list.

        One row at a time against the real serialised length, rather than an
        estimate of what a row costs: rows differ by a factor of two and an
        estimate would either waste the budget or overrun it.
        """
        budget = _budget()
        kept: list[dict] = []
        for row in rows:
            trial = {**envelope, listname: [*kept, row], "total": total,
                     "showing": len(kept) + 1, "more": advice}
            if kept and len(json.dumps(trial, default=str)) > budget:
                break
            kept.append(row)
        out = {**envelope, listname: kept, "total": total, "showing": len(kept)}
        if offset or offset + len(kept) < total:
            out["more"] = advice.format(showing=len(kept),
                                        next_offset=offset + len(kept))
        return out

    @mcp.tool()
    def plant_settings(plant: str, domain: str | None = None, find: str | None = None,
                       key: str | None = None, offset: int = 0) -> dict:
        """The settings a plant owns - found by what a person calls one, read
        one at a time, or listed a workspace at a time.

        **`find` first.** Somebody asking about "the reporting window", "how
        long a job takes by default" or "the Cpk bar" is naming a setting by
        its label, not by its key, and `find` searches every workspace's
        names, labels and descriptions in one call and says which workspace
        each answer is in. `plant_settings(plant, find="reporting window")`
        answers with `default_report_hours` in engineering. Reach for a whole
        workspace only when the person asked for one.

        `key` returns the one setting in full, with the paragraph that
        describes it - what to read before proposing a value for it.

        `domain` is a Configuration workspace - the last segment of
        `/dashboard/config/<domain>`. Naming one this version does not have
        comes back refused, listing the ones it does have. Calling this with
        nothing at all lists the workspaces, which is the cheapest way to find
        out what there is.

        **A list says its total and is never the whole list by default.**
        Administration alone has thirty-nine settings, which is more text than
        a tool result can carry, so a list answers with as many whole rows as
        fit, `total` for what the plant actually has, `showing` for what is
        here, and `more` naming the call that reaches the rest. If `more` is
        present, **say so** rather than reporting what you were shown as
        everything there is.

        `value` and `default` are text because a setting is text by the time a
        plant reads one, and `kind` says what that text has to be: `float` is
        `1.33`, `ints` is a comma-separated list like `1,2,3,4`, `bool` is
        `true` or `false`. `label` is what the Configuration page calls the
        setting; `needs` is the capability a write is gated on.

        Reading is free - no capability is needed for this call.
        """
        if key and find:
            return {"error": "ask for one setting with key=, or search with find= - not both"}
        offset = max(0, int(offset))

        if key:
            return _one(plant, domain, key)
        if find:
            return _search(plant, domain, find, offset)
        if domain is None:
            return _the_workspaces(plant)
        return _workspace(plant, domain, offset)

    def _the_workspaces(plant: str) -> dict:
        out = call(plant, "GET", "/dashboard/config")
        if isinstance(out, dict) and "error" in out:
            return out
        return {"plant": plant, "workspaces": out["workspaces"], "total": out["total"],
                "next": "name one as domain=, or search all of them at once with find="}

    def _workspace(plant: str, domain: str, offset: int) -> dict:
        page = _page(plant, domain)
        if isinstance(page, dict) and "error" in page:
            return page
        settings, read_only = _rows(page)
        envelope = {
            "plant": plant, "domain": page["domain"], "workspace": page["title"],
            # Two totals, because they are different numbers: some sections
            # are one judgment written as two keys (where a process stops
            # being capable and where it stops being marginal), and a list
            # of thirteen rows described as eleven settings is a list
            # nobody can reconcile with the screen.
            "sections": len({s["section"] for s in settings}),
            "not_written_here": read_only,
            "not_written_here_total": len(read_only),
            # What this plant does not serve at all, named rather than
            # counted away: a workspace with three sections switched off
            # is a different plant from one with none.
            "switched_off": page["switched_off"],
        }
        advice = (f"showing {{showing}} of {len(settings)} settings in {page['domain']}; "
                  f"call again with find=<a word the person used>, key=<name> for one in "
                  f"full, or offset={{next_offset}} for the next rows")
        return _fit(envelope, [_public(row) for row in settings[offset:]],
                    listname="settings", total=len(settings), offset=offset,
                    advice=advice)

    def _search(plant: str, domain: str | None, find: str, offset: int) -> dict:
        """Every setting whose name, label or description carries all the
        words somebody used, across one workspace or all of them."""
        wanted = [word for word in find.lower().split() if word]
        if not wanted:
            return {"error": "find= needs a word to look for"}
        slugs = [domain] if domain else _workspaces(plant)
        if isinstance(slugs, dict) and "error" in slugs:
            return slugs

        hits: list[tuple[int, dict]] = []
        looked: list[str] = []
        for slug in slugs:
            page = _page(plant, slug)
            if isinstance(page, dict) and "error" in page:
                return page
            looked.append(page["domain"])
            settings, _ = _rows(page)
            for row in settings:
                haystacks = (row["name"].lower(), row["label"].lower(),
                             row["_about"].lower(), row["section"].lower())
                whole = " ".join(haystacks)
                if not all(word in whole for word in wanted):
                    continue
                # Best match first: the name somebody typed, then the label
                # they would have read on the page, then the paragraph.
                rank = 3
                if all(w in haystacks[1] for w in wanted):
                    rank = 1
                if all(w in haystacks[0] for w in wanted):
                    rank = 0
                hits.append((rank, row))
        hits.sort(key=lambda pair: (pair[0], pair[1]["name"]))
        rows = [row for _, row in hits]
        envelope = {"plant": plant, "find": find, "searched": looked,
                    "searched_total": len(looked)}
        advice = (f"showing {{showing}} of {len(rows)} settings matching {find!r}; "
                  f"call again with offset={{next_offset}}, or with a narrower find=")
        out = _fit(envelope, [_public(row, True) for row in rows[offset:]],
                   listname="settings", total=len(rows), offset=offset, advice=advice)
        if not rows:
            out["nothing_matched"] = (
                f"no setting in {', '.join(looked)} has all of {wanted} in its name, "
                f"label or description. Try one word, or list a workspace.")
        return out

    def _one(plant: str, domain: str | None, key: str) -> dict:
        """One setting in full, paragraph and all - across every workspace when
        nobody said which one holds it."""
        slugs = [domain] if domain else _workspaces(plant)
        if isinstance(slugs, dict) and "error" in slugs:
            return slugs
        looked: list[str] = []
        for slug in slugs:
            page = _page(plant, slug)
            if isinstance(page, dict) and "error" in page:
                return page
            looked.append(page["domain"])
            settings, _ = _rows(page)
            for row in settings:
                if row["name"] != key:
                    continue
                return {"plant": plant, "domain": row["_domain"],
                        "workspace": page["title"], "setting": {
                            **_public(row), "key": row["_key"], "about": row["_about"]}}
        return {"error": f"no setting called {key!r} is editable in "
                         f"{', '.join(looked)}. Search for it with find=.",
                "plant": plant, "searched": looked}

    # ------------------------------------------------------------ history

    @mcp.tool()
    def setting_changes(plant: str, key: str | None = None, domain: str | None = None,
                        limit: int = 20) -> dict:
        """What has actually been written to this plant's settings, newest
        first: who set what, from what, to what, and when.

        **Read this before saying anything about what is or is not recorded.**
        When somebody says they have just changed a setting, this is the call
        that finds out - the Configuration page writes an audit row for every
        save, whoever made it, and answering from an earlier read in the
        conversation is answering from memory rather than from the plant.

        `key` narrows it to one setting by name (`default_report_hours`);
        `domain` narrows it to one Configuration workspace. With neither, it
        is every setting this plant has ever had written, newest first.

        `from` is `null` where no row existed yet, which means the product's
        default was standing and this was the first time the plant took
        ownership of that key - not that it was set to nothing.
        """
        wanted = min(max(int(limit), 1), 200)
        entity_ids: list[str] | None = None
        if key or domain:
            entity_ids = _entity_ids(plant, domain, key)
            if isinstance(entity_ids, dict):
                return entity_ids

        path = f"/audit?entity_type=plant_setting&limit={wanted}"
        if entity_ids is not None and len(entity_ids) == 1:
            # One setting: the endpoint filters it, so `limit` counts rows
            # about this key rather than rows this filter then throws away.
            path += f"&entity_id={quote(entity_ids[0])}"
            entity_ids = None
        elif entity_ids is not None:
            # A whole workspace has no server-side filter (`/audit` takes
            # `entity_type` and `entity_id`, not a set), so read a wider page
            # and say how wide: a filtered list that does not state what it
            # filtered from is a list that looks complete.
            path = f"/audit?entity_type=plant_setting&limit={min(1000, wanted * 10)}"

        trail = call(plant, "GET", path)
        if isinstance(trail, dict) and "error" in trail:
            return trail

        read = len(trail)
        if entity_ids is not None:
            keep = set(entity_ids)
            trail = [row for row in trail if row.get("entity_id") in keep]
        changes = [{
            "when": row.get("ts"),
            "who": row.get("on_behalf_of") or row.get("actor"),
            "as": row.get("actor"),
            "setting": row.get("entity_id"),
            "name": str(row.get("entity_id") or "").rpartition("] ")[2],
            "from": (row.get("before") or {}).get("value"),
            "to": (row.get("after") or {}).get("value"),
        } for row in trail][:wanted]
        envelope = {"plant": plant, "key": key, "domain": domain,
                    # How many rows of the trail were read to answer this, so
                    # a filtered list cannot read as the whole trail.
                    "audit_rows_read": read}
        advice = (f"showing {{showing}} of {len(changes)} changes; call again with a "
                  f"smaller limit=, or with key= for one setting's own history")
        out = _fit(envelope, changes, listname="changes", total=len(changes),
                   offset=0, advice=advice)
        if not changes:
            out["nothing_recorded"] = (
                "the audit trail holds no write to "
                + (f"{key!r}" if key else (f"the {domain} settings" if domain
                                           else "any setting"))
                + " on this plant. That is the record, not a guess.")
        return out

    def _entity_ids(plant: str, domain: str | None, key: str | None):
        """The `[section] key` the audit trail files a setting under, for one
        key or for every key in a workspace.

        Read off the Configuration page rather than assembled here: the pack
        table a key lives in is not the workspace it is listed under - a
        `[process]` key sits on an Engineering page - so guessing the bracket
        from the domain would query a row that does not exist.
        """
        slugs = [domain] if domain else _workspaces(plant)
        if isinstance(slugs, dict) and "error" in slugs:
            return slugs
        found: list[str] = []
        looked: list[str] = []
        for slug in slugs:
            page = _page(plant, slug)
            if isinstance(page, dict) and "error" in page:
                return page
            looked.append(page["domain"])
            settings, _ = _rows(page)
            found += [row["_key"] for row in settings
                      if key is None or row["name"] == key]
        if not found:
            return {"error": f"no setting called {key!r} is editable in "
                             f"{', '.join(looked)}. Search for it with "
                             f"plant_settings(find=...).", "plant": plant}
        return found

    # ------------------------------------------------------------- writing

    @mcp.tool()
    def write_plant_setting(plant: str, domain: str, key: str, value: str,
                            dry_run: bool = False, on_behalf_of: str | None = None,
                            client_ref: str | None = None) -> dict:
        """Put one of this plant's own settings in force, now.

        There is no draft and nothing to approve: the value is what the plant
        reads on its very next request, on every screen, with no restart. Undo
        is writing the old number back, and the audit trail says what it was -
        so say what it was before you change it, and say what you changed it
        to afterwards.

        `key` is the `name` from `plant_settings` (`cpk_capable`), not the
        bracketed form. `value` is text, in the shape that call's `kind` gave
        for this key. Call `plant_settings` first: a number proposed without
        the one it has to sit beside is a number nobody can judge.

        Refusals are the plant's, not this tool's. A value the pack checker
        will not accept - a marginal Cpk bar at or above the capable one - and
        a caller whose role does not grant the owning section's capability both
        come back as the sentence the API answered with. Nothing here decides
        either.
        """
        identify(on_behalf_of, client_ref)
        # "in the <domain> configuration", not "[<domain>] <key>": the pack
        # table a key lives in is not the workspace it is listed under (a
        # `[process]` key can be on an Engineering page), and printing the
        # workspace slug inside brackets would be inventing a table name.
        return write(plant, f"/dashboard/config/{domain}/settings/{key}",
                     {"value": value}, dry_run,
                     f"set {key} to {value} in the {domain} configuration",
                     method="PATCH")

    return {f.__name__: f for f in (plant_settings, setting_changes, write_plant_setting)}
