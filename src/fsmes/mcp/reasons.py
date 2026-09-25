"""Downtime vocabulary tools: read the plant's words, draft one.

The agent role holds `process.define` (`services/capabilities.py`), so
drafting a downtime reason is open to it exactly as it is to a process
engineer, and lands in the same place: a row with `status = draft`, on
nobody's screen but Engineering > Downtime reasons and the *Waiting for you*
panel where somebody holding `process.approve` signs it.

Two things are deliberately absent.

Approving is not a tool. The agent role is built without `process.approve`,
so a tool for it would be a tool that always refuses; putting the plant's own
vocabulary in front of every operator is a human decision (decision 0035).

Retiring is not a tool either, and that is a phasing decision rather than a
capability one: the API takes `retires` on the same endpoint, but a person is
told how many recorded intervals a code labels *before* they retire it
(`/equipment/downtime-reasons/vocabulary` carries the count for exactly that
reason), and a tool that dropped a word off the list without that number in
front of somebody would be a worse version of the screen. Drafting only,
until the design says otherwise.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def downtime_reasons(plant: str) -> dict:
        """The plant's whole downtime vocabulary in code order, with its total:
        one row per code, carrying the revision `in_force` (approved or
        retired, or null if the plant never signed one), the `draft` waiting on
        somebody, `labels_intervals` - how many recorded stops carry the code -
        and how many revisions it has had.

        Read this before drafting. Only a revision `in_force` and approved is
        on an operator's screen; a draft is on nobody's. A code that already
        exists is not a collision - drafting it again drafts its next revision,
        and drafting one whose draft is still open edits that draft."""
        return {"plant": plant, **call(plant, "GET", "/equipment/downtime-reasons/vocabulary")}

    @mcp.tool()
    def draft_downtime_reason(plant: str, code: str, name: str, description: str = "",
                              dry_run: bool = False, on_behalf_of: str | None = None,
                              client_ref: str | None = None) -> dict:
        """Draft one downtime reason for this plant. It changes nothing on the
        floor: a draft is not on any operator's screen until somebody holding
        `process.approve` puts it in force, and you do not hold that.

        `code` is what the analysis groups on and what leaves this plant: two
        to forty characters, lowercase, starting with a letter (`jam_infeed`).
        `name` is what the operator reads on the button. `description` is the
        sentence shown beside it at the machine - write it for somebody
        standing there, not for a spreadsheet.

        Call downtime_reasons first and say what you found. Never invent a
        word the plant has not used: the point of the vocabulary is that it is
        the plant's own."""
        identify(on_behalf_of, client_ref)
        body = {"code": code, "name": name, "description": description}
        return write(plant, "/equipment/downtime-reasons", body, dry_run,
                     f"draft the downtime reason {code}")

    return {f.__name__: f for f in (downtime_reasons, draft_downtime_reason)}
