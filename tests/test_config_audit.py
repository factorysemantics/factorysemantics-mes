"""config_audit: the scan finds the judgments somebody hard-coded, and says
how much of the tree it read.

Two of these tests run against the real source tree rather than a fixture,
on purpose. The calibration examples - the Cpk bar at 1.33 and the
maintenance warning at 80% of an interval - are the two candidates Scott and
the design session worked through by hand before the tool existed, so they
are what "the scanner works" means. If somebody makes either of them a real
setting, the matching test fails, and the right fix is to delete the test and
say so in the design page: a candidate that became configuration is the whole
point of the exercise, not a regression.

The rest run against small written-out files, because a rule about what the
scan ignores is only checkable when the tree is small enough to state.
"""

import json

from fsmes.sim import config_audit


def _write(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _at(run, fragment: str, literal: str):
    return [c for c in run.candidates
            if fragment in c.path and c.literal == literal]


# ------------------------------------------------- the two calibration finds


def test_the_scan_finds_the_capability_bar_that_calls_a_process_capable():
    run = config_audit.scan()
    hits = _at(run, "services/spc.py", "1.33")
    assert hits, "the Cpk bar at 1.33 is the first thing this tool exists to find"
    assert hits[0].strength == "strong"
    assert "cpk" in hits[0].why


def test_the_scan_finds_the_maintenance_warning_at_eighty_percent_of_an_interval():
    run = config_audit.scan()
    hits = _at(run, "services/maintenance.py", "0.8")
    assert hits, "the due-soon warning at 80% is the second calibration example"
    found = hits[0]
    assert found.strength == "strong"
    assert found.comment and "a plant wants warning" in found.comment.lower()
    assert any("hedges" in reason for reason in found.why), (
        "the comment beside it already says it is a judgment call, and the "
        "scan is supposed to notice that")


def test_the_whole_tree_can_be_scanned_without_crashing():
    run = config_audit.scan()
    assert run.files_scanned > 100
    assert run.unreadable == []
    assert run.candidates


# --------------------------------------------------- the list states its total


def test_the_run_says_how_many_files_it_read_and_how_many_it_skipped():
    run = config_audit.scan()
    totals = run.totals()
    assert totals["files_scanned"] == run.files_scanned
    assert totals["files_skipped"] == len(run.files_skipped)
    assert totals["candidates"] == len(run.candidates)
    assert totals["strong"] + totals["possible"] == totals["candidates"]
    assert run.files_skipped, "a skip nobody can see is a skip nobody can argue with"
    assert all(why for _, why in run.files_skipped), "every skip states its reason"


def test_every_domain_is_listed_even_when_it_has_nothing_in_it():
    run = config_audit.scan()
    by_domain = run.by_domain()
    assert set(by_domain) == set(config_audit.DOMAIN_TITLES)
    assert sum(len(rows) for rows in by_domain.values()) == len(run.candidates)


def test_a_file_the_domain_table_does_not_place_is_reported_as_unassigned():
    assert config_audit.domain_of("src/fsmes/services/spc.py") == "quality"
    assert config_audit.domain_of("src/fsmes/nowhere_in_the_table.py") == "unassigned"
    assert "unassigned" in config_audit.DOMAIN_TITLES, (
        "unknown is a valid answer; quietly folding it into the biggest "
        "domain is how a list stops being true")


# ------------------------------------------------------- what it refuses to see


def test_a_unit_conversion_is_not_a_configuration_candidate(tmp_path):
    _write(tmp_path, "clock.py",
           "# the retention window, in days\n"
           "def age_limit(seconds):\n"
           "    return seconds / 86400\n")
    run = config_audit.scan(tmp_path)
    assert not _at(run, "clock.py", "86400"), (
        "no plant gets an opinion about how many seconds are in a day")


def test_an_index_is_not_a_configuration_candidate(tmp_path):
    _write(tmp_path, "rows.py",
           "def first_two(rows):\n"
           "    limit = 2\n"
           "    return rows[0], rows[1], limit\n")
    run = config_audit.scan(tmp_path)
    assert not run.candidates


def test_a_rounding_error_is_not_a_configuration_candidate(tmp_path):
    _write(tmp_path, "compare.py",
           "def close_enough(a, b, tolerance=1e-9):\n"
           "    return abs(a - b) < tolerance\n")
    run = config_audit.scan(tmp_path)
    assert not run.candidates


def test_a_number_with_no_judgment_word_near_it_is_left_alone(tmp_path):
    _write(tmp_path, "plain.py", "def area(side):\n    return side * 37\n")
    run = config_audit.scan(tmp_path)
    assert not run.candidates


def test_a_skipped_file_is_named_with_its_reason_rather_than_dropped(tmp_path):
    _write(tmp_path, "sim/scenario.py",
           "# the stale window the scripted plant uses\nSTALE_LIMIT = 45\n")
    run = config_audit.scan(tmp_path)
    assert run.candidates == []
    assert [p for p, _ in run.files_skipped] == ["sim/scenario.py"]
    assert "simulator" in run.files_skipped[0][1]


# ------------------------------------------------------------ what it does see


def test_a_comment_that_hedges_makes_a_candidate_strong(tmp_path):
    _write(tmp_path, "warn.py",
           "# A plant wants warning, not a surprise. Anything past this is\n"
           "# worth putting on a shift plan.\n"
           "WARN_AT = 0.8\n")
    run = config_audit.scan(tmp_path)
    hits = _at(run, "warn.py", "0.8")
    assert hits and hits[0].strength == "strong"
    assert hits[0].comment.startswith("A plant wants warning")


def test_a_fixed_mapping_is_reported_once_and_not_as_each_of_its_numbers(tmp_path):
    _write(tmp_path, "rules.py", "RULE_WINDOW = {1: 1, 2: 3, 3: 5, 4: 8}\n")
    run = config_audit.scan(tmp_path)
    assert len(run.candidates) == 1, (
        "a mapping of four windows is one decision, not four - listing it "
        "four times is how a report teaches people to skim it")
    assert run.candidates[0].literal == "{1: 1, 2: 3, 3: 5, 4: 8}"
    assert "RULE_WINDOW" in run.candidates[0].what


def test_a_refresh_cadence_in_the_browser_is_found_as_a_judgment(tmp_path):
    _write(tmp_path, "web/panel.js", "setInterval(refresh, 8000);\n")
    run = config_audit.scan(tmp_path)
    hits = _at(run, "web/panel.js", "8000")
    assert hits and hits[0].strength == "strong"


def test_the_same_literal_twice_on_one_line_is_reported_once(tmp_path):
    _write(tmp_path, "twice.py",
           "# the stale window either side\n"
           "def window(a, b):\n"
           "    return max(a, 45), min(b, 45)\n")
    run = config_audit.scan(tmp_path)
    assert len(_at(run, "twice.py", "45")) == 1


# ------------------------------------------------------------------ reporting


def test_the_json_report_carries_every_domain_and_its_own_totals():
    run = config_audit.scan()
    report = json.loads(config_audit.as_json(run))
    assert report["totals"] == run.totals()
    assert set(report["domains"]) == set(config_audit.DOMAIN_TITLES)
    counted = sum(d["total"] for d in report["domains"].values())
    assert counted == report["totals"]["candidates"]
    quality = report["domains"]["quality"]["candidates"]
    assert any(c["literal"] == "1.33" for c in quality)


def test_the_text_report_states_its_total_before_it_lists_anything():
    run = config_audit.scan()
    lines = config_audit.as_text(run)
    assert str(run.totals()["candidates"]) in lines[0]
    assert "file(s) scanned" in lines[0]
    for slug, title in config_audit.DOMAIN_TITLES.items():
        assert any(line.startswith(title) for line in lines), (
            f"{slug} is not named in the report")


def test_asking_for_one_domain_still_states_that_domains_whole_total():
    run = config_audit.scan()
    lines = config_audit.as_text(run, only="quality", strong_only=True)
    header = [line for line in lines if line.startswith("Quality engineering")]
    assert header, "the domain asked for is the one shown"
    assert f"{len(run.by_domain()['quality'])} candidate(s)" in header[0], (
        "filtering to the strong ones may not quietly change the total")
    assert not any(line.startswith("Controls engineering") for line in lines)


# ---------------------------------------------------------------- the command


def test_the_command_prints_the_calibration_examples_and_its_own_totals():
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["config-audit"])
    assert result.exit_code == 0, result.output
    assert "file(s) scanned" in result.output
    assert "services/spc.py" in result.output
    assert "services/maintenance.py" in result.output
    for title in config_audit.DOMAIN_TITLES.values():
        assert title in result.output


def test_the_command_refuses_a_domain_it_does_not_have():
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["config-audit", "--domain", "canteen"])
    assert result.exit_code == 2
    assert "unknown domain" in result.output
    assert "quality" in result.output, "it says which domains there are"


def test_the_command_can_emit_a_report_a_later_run_can_be_diffed_against():
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["config-audit", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["totals"]["files_scanned"] > 100


# ------------------------------------------------ the scope of each judgment
#
# Scope is the fourth thing a candidate carries, after domain, size and the
# reason it qualified: whose answer is it? It decides who gets asked, so the
# tests that matter are that every curated candidate has one, that the totals
# add up to the whole list, and that the per-object ones - the counter-reset
# threshold above all - are reachable without reading the design page.


def test_every_curated_candidate_says_whose_answer_it_is():
    from fsmes.sim import config_audit_curated

    for row in config_audit_curated.CURATED:
        assert row.scope in config_audit_curated.SCOPE_TITLES, (
            f"{row.id} has no scope, so nobody knows who to ask")
        assert row.domain in config_audit.DOMAIN_TITLES
        assert row.why.strip(), f"{row.id} states a scope without arguing it"


def test_the_scopes_add_up_to_the_whole_curated_list():
    from fsmes.sim import config_audit_curated

    totals = config_audit_curated.totals_by_scope()
    assert sum(totals.values()) == len(config_audit_curated.CURATED)
    assert set(totals) == set(config_audit_curated.SCOPE_TITLES), (
        "a scope with nothing in it is still listed, or its total is a "
        "claim nobody can check")


def test_no_two_curated_candidates_share_an_id():
    from fsmes.sim import config_audit_curated

    ids = [row.id for row in config_audit_curated.CURATED]
    assert len(ids) == len(set(ids))


def test_the_counter_reset_threshold_is_a_property_of_one_tag():
    from fsmes.sim import config_audit_curated

    c14 = config_audit_curated.by_id("C14")
    assert c14 is not None
    assert c14.scope == "object", (
        "a counter that wraps at 65535 and one zeroed every shift are two "
        "tags, not two plants")
    assert c14.domain == "controls"
    assert "tag" in c14.why.lower()


def test_every_curated_candidate_still_points_at_real_code():
    """The list is anchored on a fragment of the line, not on a line number,
    so a refactor moves a row rather than breaking it. A row whose anchor has
    gone is reported as stale and has to be read again by a person - that is
    the one thing this test refuses to let pass quietly."""
    run = config_audit.scan()
    assert len(run.curated) == len(config_audit.CURATED)
    stale = [f.entry.id for f in run.curated if f.where == "stale"]
    assert not stale, f"these curated rows no longer anchor anywhere: {stale}"


def test_a_curated_line_carries_its_scope_on_the_scanned_candidate():
    """A row a person kept and the scan still finds carries both judgments.

    Named on `C5`, the order cache a station trusts, rather than on the
    maintenance warning it used to name: `P1` was built on 2026-09-25 and its
    anchor moved to `[process] maintenance_due_soon_fraction`'s own default in
    `config.py`, which the scan deliberately does not treat as a candidate - a
    setting is already configuration, and reporting one as a thing to make
    configurable would be the tool arguing with itself.

    So a built row is *expected* to stop meeting the scan here, and this test
    holds the seam on a row that is still a question. `test_every_built_row_
    anchors_on_its_key` below is the other half: a built row still has to point
    at real code.
    """
    run = config_audit.scan()
    tagged = [c for c in run.candidates if c.curated_id]
    assert tagged, "the scan and the curated list have to meet somewhere"
    assert all(c.scope for c in tagged)
    assert any(c.curated_id == "C5" for c in tagged), (
        "how long a station's current order is trusted is one a person kept "
        "and the scan finds, so it should carry both")


def test_a_row_that_became_a_pack_key_anchors_on_that_keys_own_default():
    """A row that became a `plant.toml` key points at the key's default.

    The other half of the seam above. Nineteen rows became keys on 2026-09-25 -
    seventeen Engineering's and the two Administration rows that were the same
    item as two of them - and each one's anchor moved from the code that held
    the literal to the `Settings` field its key compiles to. A `settled`
    sentence with an anchor still on the old line would be this list claiming a
    change it had not checked.

    Only the rows that became *keys*: a row that became a database-backed
    vocabulary or a column on an object - `Q3`'s severities, `Q7`'s per-gauge
    warning, `Q9`'s counted-in-pieces flag - has no `Settings` field to anchor
    on, and pretending otherwise would be this test having an opinion about
    where an answer has to live.
    """
    live = [c for c in config_audit.CURATED
            if c.settled and "2026-09-25" in c.settled]
    assert len(live) == 19, (
        "seventeen Engineering rows plus A8 and A25, which are P6 and P9 under "
        "another domain's numbering - said out loud so the count cannot drift"
    )
    off = [c.id for c in live if c.path != "config.py"]
    assert not off, (
        "a row that became a key anchors on that key's own default in "
        f"config.py, so moving the old code cannot stale it: {off}")


def test_a_candidate_nobody_curated_claims_no_scope():
    run = config_audit.scan()
    uncurated = [c for c in run.candidates if not c.curated_id]
    assert uncurated, "419 raw matches, 75 kept - most carry no judgment"
    assert all(c.scope is None for c in uncurated), (
        "scope is a judgment; the scan does not get to make one")


def test_the_scan_does_not_judge_a_tree_that_is_not_this_product(tmp_path):
    _write(tmp_path, "warn.py", "# the stale limit for now\nWARN_AT = 45\n")
    run = config_audit.scan(tmp_path)
    assert run.curated == [], (
        "a judgment about this codebase says nothing about somebody else's")


def test_the_report_states_the_scope_totals_beside_the_scan():
    run = config_audit.scan()
    totals = run.curated_totals()
    assert totals["curated"] == len(config_audit.CURATED)
    assert sum(totals[s] for s in config_audit.SCOPE_TITLES) == totals["curated"]
    lines = config_audit.as_text(run)
    assert any("curated list" in line for line in lines)
    for title in config_audit.SCOPE_TITLES.values():
        assert any(title in line for line in lines)


def test_the_json_carries_the_scope_and_the_rule_that_decided_it():
    run = config_audit.scan()
    report = json.loads(config_audit.as_json(run))
    assert set(report["curated"]["scopes"]) == set(config_audit.SCOPE_TITLES)
    assert "object" in report["curated"]["rule"]
    rows = report["curated"]["scopes"]["object"]["candidates"]
    c14 = [r for r in rows if r["id"] == "C14"]
    assert c14, "--json has to carry the judgment beside the literal"
    assert c14[0]["why_this_scope"]
    assert c14[0]["path"].endswith("integrations/opc/agent.py")


# ---------------------------------------------------------------- the command


def test_asking_for_the_object_scope_returns_the_counter_reset_threshold():
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["config-audit", "--scope", "object"])
    assert result.exit_code == 0, result.output
    assert "C14" in result.output
    assert "Counter-reset detection" in result.output
    assert "C1 " in result.output, "tag staleness is per-tag for the same reason"
    assert "A2" not in result.output, "a product-wide decision is not an object"
    assert str(len(config_audit.CURATED)) in result.output, (
        "every list states its total, including when it is filtered")


def test_asking_for_the_general_scope_returns_only_what_a_maintainer_is_asked():
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["config-audit", "--scope", "general"])
    assert result.exit_code == 0, result.output
    assert "C14" not in result.output
    general = [row.id for row in config_audit.CURATED if row.scope == "general"]
    for candidate_id in general:
        assert candidate_id in result.output


def test_the_command_refuses_a_scope_it_does_not_have():
    from typer.testing import CliRunner

    from fsmes.cli import app

    result = CliRunner().invoke(app, ["config-audit", "--scope", "regional"])
    assert result.exit_code == 2
    assert "unknown scope" in result.output
    assert "object" in result.output, "it says which scopes there are"


# ------------------------------------------------- the page and the tool agree
#
# The audit page carries the scope of each candidate in its own tables, so a
# person reading it never has to run anything. That is two copies of one
# judgment, which is the failure this whole audit is about - so a test holds
# them together. The page owns the prose; the tool owns the label; this
# refuses to let the label drift.


def _audit_page() -> str:
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1]
            / "docs" / "design" / "config-audit-2026-09-21.md")
    return page.read_text(encoding="utf-8")


def test_the_audit_page_gives_every_candidate_the_scope_the_tool_gives_it():
    import re

    rows = re.findall(r"^\| \*\*([QPCSAI]\d+)\*\* \|.*\*\*`(\w+)`\*\*",
                      _audit_page(), re.M)
    assert len(rows) == 75, (
        f"section 4 of the audit page lists {len(rows)} candidates with a "
        "scope; it listed 75 when the scope was added")
    for candidate_id, scope in rows:
        row = next((r for r in config_audit.CURATED
                    if r.id == candidate_id), None)
        assert row is not None, f"{candidate_id} is on the page and not in the tool"
        assert row.scope == scope, (
            f"{candidate_id} is {scope} on the page and {row.scope} in the "
            "tool; one of them is wrong and both are read")


def test_the_page_asks_a_person_only_about_the_general_candidates():
    page = _audit_page()
    general = [row.id for row in config_audit.CURATED if row.scope == "general"]
    section = page.split("## 8.")[1].split("## 9.")[0]
    for candidate_id in general:
        assert f"**{candidate_id}**" in section, (
            f"{candidate_id} is general and is not in the list of what "
            "anybody is asked")
    for row in config_audit.CURATED:
        if row.scope != "general":
            assert f"| **{row.id}** |" not in section, (
                f"{row.id} is {row.scope}: it is routed, not asked")
