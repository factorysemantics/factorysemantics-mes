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
