"""The SQL Server queries fill in from a mapping, and hold nothing SQLite-only.

There is no SQL Server on this machine and there is none on the runner, so
nothing here parses these queries the way SQL Server would. Say that plainly:
**this is a structural check, not a parse.** A green run here means the
placeholders all resolve, the shape is a bounded single `SELECT`, the quoting is
SQL Server's, and none of the SQLite-only spellings that it would be very easy
to copy across has been copied across. It does not mean SQL Server will accept
them; the first person to run them against a real instance is the one who finds
out, and `docs/operate/plant-from-your-mes.md` says so.

The mistakes this does catch are the ones actually made while writing the pair:
`LIMIT` instead of `TOP`, `substr` instead of `CONVERT(date, …)`,
`GROUP BY 1, 2` (which SQLite allows and SQL Server refuses), and a placeholder
in one dialect that was never added to the other.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "plant-from-your-mes"

sys.path.insert(0, str(SKILL / "scripts"))
import profile as profiler  # noqa: E402  (shipped as a folder, not as a package)

#: A complete mapping, so every placeholder in every query has something to
#: resolve to. These are the invented names from `tests/unknown_mes.py`.
MAPPING = {
    "dialect": "mssql",
    "assets": {"table": "plant_asset", "id": "asset_id", "code": "asset_code",
               "line": "line_code", "sequence": "seq_no"},
    "tags": {"table": "tag_definition", "id": "tag_id", "asset": "asset_id",
             "path": "tag_path", "unit": "eng_unit"},
    "samples": {"table": "tag_sample", "tag": "tag_id", "at": "sample_ts", "value": "num_value"},
    "states": {"table": "asset_state_log", "asset": "asset_id", "state": "state_word",
               "at": "started_at", "until": "ended_at", "seconds": "seconds",
               "reason": "reason_id", "note": "reason_note"},
    "reasons": {"table": "stop_reason", "id": "reason_id", "category": "category"},
    "production": {"table": "wo_output", "asset": "asset_id", "at": "booked_at",
                   "good": "good_qty", "scrap": "scrap_qty"},
    "quality": {"table": "qa_result", "at": "checked_at", "verdict": "verdict",
                "nonconformance": "nc_number", "disposition": "nc_status"},
    "orders": {"table": "work_order", "quantity": "qty_ordered", "opened": "released_at",
               "closed": "closed_at"},
    "shifts": {"table": "shift_calendar", "code": "shift_code", "starts": "starts_at",
               "ends": "ends_at"},
    "documents": {"table": "doc_register", "number": "doc_number", "revision": "revision"},
    "erp": {"table": "erp_call_log", "at": "called_at", "verb": "verb", "endpoint": "endpoint",
            "bytes": "body_bytes", "status": "http_status"},
}

#: What the per-table and per-column templates are given.
PER_TABLE = {"table": {"table": "asset_state_log", "at": "started_at", "column": "state_word"}}

#: Spellings that work in SQLite and do not work in SQL Server.
SQLITE_ONLY = ("limit ", "substr(", "pragma_", "sqlite_master", "ifnull(", "length(",
               '"', "group_concat(", "|| ")


def _mssql_queries() -> list[Path]:
    return sorted((SKILL / "queries" / "mssql").glob("*.sql"))


def _expand(path: Path) -> str:
    return profiler.expand(path.read_text(encoding="utf-8"), MAPPING, "mssql", 5000, PER_TABLE)


def test_the_two_dialects_ship_the_same_numbered_queries() -> None:
    """A query that exists for the test database and not for the customer's is a
    hole in the thing Scott is taking to a customer who runs SQL Server."""
    sqlite_names = sorted(p.name for p in (SKILL / "queries" / "sqlite").glob("*.sql"))
    mssql_names = sorted(p.name for p in (SKILL / "queries" / "mssql").glob("*.sql"))
    assert sqlite_names == mssql_names
    assert len(mssql_names) == 16


def test_every_placeholder_in_every_sql_server_query_resolves_from_a_mapping() -> None:
    for path in _mssql_queries():
        filled = _expand(path)
        assert "{{" not in filled, f"{path.name} still has an unresolved placeholder"
        assert "}}" not in filled


def test_every_sql_server_query_is_one_bounded_select_with_sql_server_quoting() -> None:
    for path in _mssql_queries():
        filled = _expand(path)
        body = "\n".join(line for line in filled.splitlines() if not line.strip().startswith("--"))
        stripped = body.strip()
        assert stripped.lower().startswith("select"), f"{path.name} does not start with SELECT"
        assert stripped.endswith(";"), f"{path.name} is not one statement ending in a semicolon"
        assert body.count(";") == 1, f"{path.name} has more than one statement"
        assert stripped.count("(") == stripped.count(")"), f"{path.name} has unbalanced brackets"
        assert stripped.count("'") % 2 == 0, f"{path.name} has an unbalanced quote"
        if "01_inventory" not in path.name and "02_columns" not in path.name:
            assert "[" in stripped, f"{path.name} names no identifier in SQL Server quoting"


def test_no_sql_server_query_holds_a_spelling_that_only_sqlite_understands() -> None:
    for path in _mssql_queries():
        filled = _expand(path)
        body = " ".join(line for line in filled.splitlines()
                        if not line.strip().startswith("--")).lower()
        for spelling in SQLITE_ONLY:
            assert spelling not in body, (
                f"{path.name} contains {spelling!r}, which SQLite understands and SQL Server does not")


def test_no_sql_server_query_groups_or_orders_by_a_column_number() -> None:
    """`GROUP BY 1, 2` is a SQLite convenience. SQL Server refuses it, and it is
    the single easiest thing to carry across while mirroring a query."""
    for path in _mssql_queries():
        body = " ".join(line for line in _expand(path).splitlines()
                        if not line.strip().startswith("--")).lower()
        after_group = body.split("group by", 1)
        if len(after_group) == 2:
            first = after_group[1].strip().split(",")[0].strip().split()[0]
            assert not first.rstrip(";").isdigit(), f"{path.name} groups by a column number"


def test_every_bounded_sql_server_query_bounds_itself_with_top() -> None:
    """A query without a bound is a query somebody runs once on a historian and
    then explains to their manager."""
    unbounded_by_design = {"01_inventory.sql", "01b_row_count.sql", "02_columns.sql",
                           "03_window.sql", "04_column_profile.sql"}
    for path in _mssql_queries():
        if path.name in unbounded_by_design:
            continue
        filled = _expand(path)
        assert "top (5000)" in filled.lower(), f"{path.name} carries no TOP bound"


def test_the_filled_in_sql_can_be_written_out_for_somebody_to_run_by_hand(tmp_path: Path) -> None:
    """The SQL Server path: there is no driver here, so the agent hands the person
    the exact text to paste into their own client."""
    written = profiler.emit_sql(tmp_path, MAPPING, "mssql", SKILL / "queries", 5000)
    assert written == 0
    files = sorted(p.name for p in tmp_path.glob("*.sql"))
    assert len(files) == 16, files
    # Twelve are ready to paste; the four per-table and per-column templates come
    # out as templates with a line saying what to substitute, rather than being
    # dropped so a step goes quietly missing.
    ready = [f for f in files if not f.endswith(".template.sql")]
    assert len(ready) == 12, ready
    assert sorted(f for f in files if f.endswith(".template.sql")) == [
        "01b_row_count.template.sql", "03_window.template.sql",
        "04_column_profile.template.sql", "04b_column_sample.template.sql",
    ]
    assert "{{" not in (tmp_path / "06_states.sql").read_text(encoding="utf-8")
    template = (tmp_path / "03_window.template.sql").read_text(encoding="utf-8")
    assert template.startswith("-- TEMPLATE, not ready to run")
    assert "{{table.at}}" in template


def test_a_mapping_that_names_something_other_than_an_identifier_is_refused() -> None:
    """The one place a mapping file could become an injection: it is checked
    against a plain-identifier pattern before it is ever quoted into SQL."""
    bad = {**MAPPING, "assets": {**MAPPING["assets"], "table": "plant_asset; DROP TABLE x"}}
    with pytest.raises(profiler.Unmapped) as refused:
        profiler.expand("SELECT 1 FROM {{assets.table}};", bad, "mssql", 10)
    assert "not a plain identifier" in str(refused.value)


def test_a_query_whose_mapping_is_incomplete_is_skipped_and_not_silently_wrong() -> None:
    """Unknown is not zero: a missing mapping has to raise, so the profiler can
    record the section as not measured rather than report an empty one."""
    thin = {"erp": {"table": "erp_call_log"}}
    with pytest.raises(profiler.Unmapped) as refused:
        profiler.expand("SELECT {{erp.endpoint}} FROM {{erp.table}};", thin, "mssql", 10)
    assert "erp.endpoint" in str(refused.value)
    # An optional placeholder, by contrast, becomes NULL rather than refusing.
    assert "NULL" in profiler.expand("SELECT {{erp.verb?@e}} FROM {{erp.table}} e;", thin, "mssql", 10)
