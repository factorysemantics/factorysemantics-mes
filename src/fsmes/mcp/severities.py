"""Non-conformance severity tools: read the plant's words, draft one.

The same two tools the downtime vocabulary has, against `/quality/severities`
instead of `/equipment/downtime-reasons`, because a second vocabulary that
answered in a different shape would be a second thing to learn rather than
the same thing again.

The agent role holds `quality.define` and not `quality.approve`
(`services/capabilities.py`), so the same two absences apply: approving is a
human decision and never a tool, and retiring stays a person's screen action
while the count of non-conformances a code labels is what somebody is told
before they take a word off the list.

A severity the product itself writes (`/quality/severities/vocabulary` marks
them) cannot be retired at all - which is another reason drafting is the only
write here.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def nc_severities(plant: str) -> dict:
        """The plant's whole severity vocabulary in code order, with its total:
        one row per code, carrying the revision `in_force`, the `draft` waiting
        on somebody, `labels_records` - how many non-conformances carry the
        code - and `product_writes`, which says whether the product itself
        raises findings at this severity.

        Read this before drafting. Only an approved revision in force may be
        put on a quality record. Drafting a code that already exists drafts its
        next revision."""
        return {"plant": plant, **call(plant, "GET", "/quality/severities/vocabulary")}

    @mcp.tool()
    def draft_nc_severity(plant: str, code: str, name: str, description: str = "",
                          dry_run: bool = False, on_behalf_of: str | None = None,
                          client_ref: str | None = None) -> dict:
        """Draft one non-conformance severity for this plant. It grades nothing
        until somebody holding `quality.approve` puts it in force, and you do
        not hold that.

        `code` is what is stored on every non-conformance raised at this
        severity and what leaves the plant. `name` is what a person reads in
        the list; `description` is the sentence that says what a finding at
        this severity means - which is the part that decides whether two
        inspectors grade the same defect the same way.

        Call nc_severities first and say what you found. A severity is a
        judgment about how bad something is, so the words have to be the
        plant's own."""
        identify(on_behalf_of, client_ref)
        body = {"code": code, "name": name, "description": description}
        return write(plant, "/quality/severities", body, dry_run,
                     f"draft the non-conformance severity {code}")

    return {f.__name__: f for f in (nc_severities, draft_nc_severity)}
