"""The settings a plant owns: read any domain's, write one of them.

Two tools for every domain there will ever be. Not one pair per domain, and
not one tool file per domain either: a live setting is a row in one table,
listed by the `ConfigSection` that owns it and written through one endpoint
that reads the owning section per key
(`PATCH /dashboard/config/{domain}/settings/{key}`, #100). So the domain is an
argument here rather than a name in the source, and the day
`<domain>-live-settings` marks another section `edit_here` its numbers are
reachable through these two tools with nothing added.

That is why this file belongs to the `dashboard` module rather than to
Quality: the table, the page and the endpoint are the Configuration page's
own, and the module registry already says so
(`modules.py`, `tables=("plant_settings",)`).

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


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def plant_settings(plant: str, domain: str) -> dict:
        """Every setting this plant owns in one Configuration workspace, with
        its total: what it is set to now, whether that is the product's default
        or this plant's own choice, what kind of value it is, and the capability
        a write is gated on.

        `domain` is a Configuration workspace - the last segment of
        `/dashboard/config/<domain>`. Naming one this version does not have
        comes back refused, listing the ones it does have, so the way to find
        out is to ask rather than to guess.

        Read this before writing anything. `value` and `default` are text
        because a setting is text by the time a plant reads one, and `kind`
        says what that text has to be: `float` is `1.33`, `ints` is a
        comma-separated list like `1,2,3,4`, `bool` is `true` or `false`.

        `total` counts keys and `sections` counts the rows a person sees on the
        Configuration page; they differ because a judgment can be two numbers.

        `not_written_here` is the honest other half of the list: keys this
        workspace shows a person but nothing can change while the plant is
        running. Reading is free - no capability is needed for this call.
        """
        out = call(plant, "GET", f"/dashboard/config/{domain}/sections")
        if isinstance(out, dict) and "error" in out:
            return out
        settings = []
        read_only: list[str] = []
        for section in out["items"]:
            for key in section["pack_keys"]:
                if not section["edit_here"]:
                    read_only.append(key["key"])
                    continue
                settings.append({
                    **key,
                    "section": section["key"],
                    "section_label": section["label"],
                    # The capability a write is gated on, per key rather than
                    # per tool: it is the owning section's, which is why one
                    # tool covers every domain and no single capability gates
                    # it. `None` is a section nobody has to hold anything for.
                    "needs": section["define"],
                    # Whether the AGENT account itself may write it - the
                    # person asking is gated separately, where they are asked.
                    "agent_may_write": section["may_define"],
                })
        return {"plant": plant, "domain": out["domain"], "workspace": out["title"],
                "settings": settings, "total": len(settings),
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
                "switched_off": out["switched_off"]}

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

    return {f.__name__: f for f in (plant_settings, write_plant_setting)}
