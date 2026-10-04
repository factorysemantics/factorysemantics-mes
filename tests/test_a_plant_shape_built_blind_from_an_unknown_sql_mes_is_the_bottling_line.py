"""The skill is pointed at a SQL MES it was not written for, and the plant it describes is bottling.

This is the only check that says whether `skills/plant-from-your-mes/` works.
Everything else about it is a document somebody could read and agree with.

`tests/unknown_mes.py` builds a deliberately foreign Manufacturing Execution
System — its own table names, its own three-letter state words, tag history in
one tall table keyed by a tag path, two tables for things FactorySemantics does
not model — and fills it from the bottling line's own generated hour. Then the
skill is run against it the way a customer's agent would run it, through its own
command line, with no hint about the schema: the mapping is *proposed from the
catalogue*, not written here.

What that proves, and what it does not:

- It proves the queries, the mapping proposal, the statistics and the leak check
  work end to end against a schema nobody here wrote them for.
- It does not prove they work against *every* schema. It is one invented shape.
  The mapping proposal is name-matching, and on a real plant it will get some
  lines wrong, which is why the skill tells the agent to read it back to a person
  before using it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path, PureWindowsPath

import pytest
import tests.unknown_mes as unknown_mes

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "plant-from-your-mes"
LINE_JSON = ROOT / "labs" / "kepsim" / "line.json"

#: The three analogs the bottling scenario deliberately pushes around: a drift
#: on the rinser's motor temperature, a scrap burst that offsets the washer's
#: temperature, and the effect that burst has on fill weight downstream. Their
#: *base* still comes back (a median is robust); their spread is wider than the
#: line's noise figure, because the line really did wobble more than that.
SCRIPTED = {("RD", "MotorTemp"), ("Washer", "WashTemp"), ("Refill", "FillWeight")}

#: The washer's scrap burst takes it to 10% for five minutes, so its measured
#: scrap share must come back *above* its nameplate rather than near it.
SCRAP_BURST_STATION = "Washer"

#: What the mapping proposal has to find, from table and column names alone.
EXPECTED_MAPPING = {
    "assets": "plant_asset",
    "tags": "tag_definition",
    "samples": "tag_sample",
    "states": "asset_state_log",
    "reasons": "stop_reason",
    "production": "wo_output",
    "quality": "qa_result",
    "orders": "work_order",
    "shifts": "shift_calendar",
    "documents": "doc_register",
    "erp": "erp_call_log",
}

#: Values that are in that database and must not be in the output. Taken from
#: the fixture by hand, so this list does not move when the fixture's sampling
#: does: an order number, a lot, a pallet, an operator, an inspector, a
#: non-conformance, a customer, an item description and a technician's note.
MUST_NOT_APPEAR = (
    "WO-2026-0004711",
    "LOT-26",
    "PAL-7",
    "OP-1182",
    "QC-204",
    "NCR-2026",
    "Northgate Provisions Ltd",
    "clear PET, 24-pack shrink",
    "Infeed star wheel jammed, cleared by hand and restarted",
    "Gripper pads worn through",
    "Worn past the gauge",
    "Red crew",
    "KC-LN3-M01",
    "TL-GRIP-02",
    "MEC-04",
    "Net content check",
)


def _run(*args: str) -> str:
    done = subprocess.run(
        [sys.executable, *args], check=False, capture_output=True, text=True, cwd=ROOT,
    )
    assert done.returncode == 0, f"{args}\nstdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    return done.stdout


@pytest.fixture(scope="module")
def blind_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build the unknown MES, propose a mapping from it, and profile it."""
    work = tmp_path_factory.mktemp("plant-from-your-mes")
    database = unknown_mes.build(work / "unknown.db", LINE_JSON)
    mapping = work / "mapping.toml"
    shape = work / "plant-shape.toml"
    profile = str(SKILL / "scripts" / "profile.py")

    proposal_output = _run(profile, "--sqlite", str(database), "--propose-mapping", str(mapping))
    profile_output = _run(profile, "--sqlite", str(database), "--mapping", str(mapping),
                          "--out", str(shape))
    explained = _run(str(SKILL / "scripts" / "explain.py"), str(shape), "--tags")
    return {
        "database": database,
        "mapping": tomllib.loads(mapping.read_text(encoding="utf-8")),
        "shape": tomllib.loads(shape.read_text(encoding="utf-8")),
        "text": shape.read_text(encoding="utf-8"),
        "proposal_output": proposal_output,
        "profile_output": profile_output,
        "explained": explained,
        "line": json.loads(LINE_JSON.read_text(encoding="utf-8")),
    }


def test_the_mapping_is_proposed_from_the_catalogue_with_no_hint_about_the_schema(blind_run: dict) -> None:
    mapping = blind_run["mapping"]
    for section, table in EXPECTED_MAPPING.items():
        assert section in mapping, f"the proposal found no [{section}] at all"
        assert mapping[section]["table"] == table, (
            f"[{section}] was mapped to {mapping[section]['table']!r}, not {table!r}")
    assert "2 table(s) of 13 are not mapped" in blind_run["proposal_output"]


def test_the_line_comes_back_as_six_stations_in_line_order(blind_run: dict) -> None:
    stations = blind_run["shape"]["stations"]
    line = blind_run["line"]["stations"]
    assert blind_run["shape"]["plant"]["stations_total"] == len(line) == 6
    assert len(stations) == 6
    assert [s["name"] for s in stations] == ["ST-01", "ST-02", "ST-03", "ST-04", "ST-05", "ST-06"]
    assert [s["sequence"] for s in stations] == [1, 2, 3, 4, 5, 6]


def test_every_stations_rate_comes_back_within_a_tenth_of_what_the_line_was_told_to_run_at(
        blind_run: dict) -> None:
    """Measured below nameplate, never above: buffers, changeovers and the two
    scripted breakdowns mean a station cannot average its own ceiling."""
    for station, described in zip(blind_run["shape"]["stations"], blind_run["line"]["stations"],
                                  strict=True):
        nameplate = float(described["rate_per_min"])
        measured = station["rate_per_min"]
        assert isinstance(measured, float), f"{described['name']}: no rate was measured"
        assert 0.88 * nameplate <= measured <= 1.02 * nameplate, (
            f"{described['name']} is told to run at {nameplate}/min; the shape says {measured}/min")


def test_every_stations_scrap_share_comes_back_near_what_the_line_was_told_to_scrap(
        blind_run: dict) -> None:
    for station, described in zip(blind_run["shape"]["stations"], blind_run["line"]["stations"],
                                  strict=True):
        told = float(described["scrap_pct"])
        measured = station["scrap_pct"]
        assert isinstance(measured, float), f"{described['name']}: no scrap share was measured"
        if described["name"] == SCRAP_BURST_STATION:
            assert measured > told, (
                f"{described['name']} has a scripted scrap burst to 10%, so its measured share "
                f"({measured}%) must be above its nameplate {told}%")
            continue
        assert abs(measured - told) <= 0.35, (
            f"{described['name']} is told to scrap {told}%; the shape says {measured}%")


def test_every_analog_tag_comes_back_with_its_base_and_its_spread(blind_run: dict) -> None:
    """The one measurement the simulation is actually built from.

    Each of the line's twenty analogs is matched to the recovered tag on the same
    station with the nearest base, and that base has to be the right one. The
    spread is checked too, except on the three signals the scenario deliberately
    drifts or offsets, which genuinely moved more than their noise figure.
    """
    checked = 0
    for station, described in zip(blind_run["shape"]["stations"], blind_run["line"]["stations"],
                                  strict=True):
        recovered = [t for t in station["analogs"] if t["kind"] == "analog"]
        assert recovered, f"{described['name']}: no analog tags came back at all"
        for analog in described.get("analogs", []):
            base = float(analog["base"])
            noise = float(analog["noise"])
            nearest = min(recovered, key=lambda tag: abs(tag["base"] - base))
            assert abs(nearest["base"] - base) <= max(abs(base) * 0.01, 0.15), (
                f"{described['name']}.{analog['name']} sits at {base}; the nearest tag the shape "
                f"found on {station['name']} sits at {nearest['base']}")
            if (described["name"], analog["name"]) in SCRIPTED:
                assert nearest["noise"] > noise, (
                    f"{described['name']}.{analog['name']} is deliberately pushed around by the "
                    f"scenario, so its measured spread should exceed its noise figure {noise}")
            else:
                assert 0.85 * noise <= nearest["noise"] <= 1.15 * noise, (
                    f"{described['name']}.{analog['name']} wobbles by {noise}; the shape says "
                    f"{nearest['noise']}")
            assert nearest["sample_interval_s"] == pytest.approx(unknown_mes.SAMPLE_EVERY_S, abs=0.5)
            checked += 1
    assert checked == 20, f"the bottling line has twenty analogs; {checked} were checked"


def test_the_counters_and_the_ready_bit_are_told_apart_from_the_measurements(blind_run: dict) -> None:
    """Three counters and one ready bit per machine, by shape alone — the skill is
    never told which column is which kind."""
    for station in blind_run["shape"]["stations"]:
        kinds = [tag["kind"] for tag in station["analogs"]]
        assert kinds.count("counter") == 4, (
            f"{station['name']}: expected three count tags plus runtime minutes, got "
            f"{kinds.count('counter')} counters out of {kinds}")
        assert kinds.count("discrete") == 1, (
            f"{station['name']}: expected one ready bit, got {kinds.count('discrete')}")


def test_the_states_and_the_stops_come_back_with_the_changeover_the_scenario_scripted(
        blind_run: dict) -> None:
    stops = blind_run["shape"]["stops"]
    assert stops["found"] is True
    by_state = {row["state"]: row for row in stops["states"]}
    assert by_state["running"]["share"] > 0.80, "the line ran for most of the hour"
    # One scripted changeover, 2400s to 2640s, on all six stations at once.
    assert by_state["changeover"]["intervals"] == 6
    assert by_state["changeover"]["seconds"] == pytest.approx(6 * 240, abs=6)
    # Two scripted breakdowns: twelve seconds on the depalletiser, two minutes
    # on the rinser.
    assert by_state["down"]["intervals"] == 2
    assert by_state["down"]["seconds"] == pytest.approx(12 + 120, abs=2)
    assert {"starved", "blocked"} <= set(by_state)


def test_a_stop_with_no_reason_against_it_is_reported_as_unlabelled_and_not_as_other(
        blind_run: dict) -> None:
    """The fixture labels about four stops in five, the way a real MES does.

    What must not happen is the share coming back as zero, or the unlabelled
    stops being folded into `other` — that is how a plant convinces itself it has
    data it does not have.
    """
    stops = blind_run["shape"]["stops"]
    assert 0.05 < stops["unlabelled_share"] < 0.40, stops["unlabelled_share"]
    categories = {row["category"]: row for row in stops["reasons"]}
    assert "unlabelled" in categories
    assert categories["unlabelled"]["stops"] == stops["unlabelled_stops"]
    # Their six fault categories map onto ours, and none of their words survive.
    assert {"mechanical", "electrical", "material", "quality", "operator"} <= set(categories)
    assert stops["waiting_intervals"] > 0, "starved and blocked are reported, not dropped"


def test_the_quality_loop_and_the_orders_and_the_shifts_come_back(blind_run: dict) -> None:
    shape = blind_run["shape"]
    assert shape["quality"]["found"] is True
    assert shape["quality"]["checks_total"] == 30
    assert shape["quality"]["nonconformances_total"] >= 1
    assert shape["orders"]["found"] is True
    assert shape["orders"]["orders_seen"] == len(blind_run["line"]["orders"])
    assert shape["shifts"]["found"] is True
    assert shape["shifts"]["distinct_codes"] == 3
    assert [row["hours"] for row in shape["shifts"]["pattern"]] == [8.0, 8.0, 8.0]


def test_their_numbering_grammar_comes_back_as_a_shape_and_never_as_an_example(blind_run: dict) -> None:
    patterns = {row["what"]: row["pattern"] for row in blind_run["shape"]["plant"]["naming_grammar"]}
    assert patterns["order number"] == "AA-####-#######"
    assert patterns["lot number"] == "AAA-#######-#"
    assert patterns["pallet or container id"] == "AAA-######"
    documents = {row["pattern"]: row for row in blind_run["shape"]["documents"]["grammar"]}
    assert "AAA-AA-###" in documents, documents
    for row in blind_run["shape"]["documents"]["grammar"]:
        assert row["of_total"] == blind_run["shape"]["documents"]["documents_seen"]


def test_the_two_tables_it_could_not_use_are_reported_as_things_to_build(blind_run: dict) -> None:
    """Scott's own example: a plant that tracks tool replacement and its cost.

    The shape file has to say "there is something here with a tool, a cycle count
    and a cost, and FactorySemantics does not model it" — without carrying one of
    their column names.
    """
    also = blind_run["shape"]["also_tracked"]
    assert len(also) == 2
    assert blind_run["shape"]["also_tracked_note"]["tables_total"] == 2
    words = " ".join(row["words_recognised"] for row in also)
    assert "tool" in words and "cost" in words and "kwh" in words
    assert [row["name"] for row in also] == ["UNUSED-01", "UNUSED-02"]
    for row in also:
        assert isinstance(row["rows_seen"], int) and row["rows_seen"] > 0


def test_not_one_order_lot_pallet_person_code_or_note_from_that_database_is_in_the_file(
        blind_run: dict) -> None:
    """The promise, checked on identity rather than on counts."""
    text = blind_run["text"].lower()
    for value in MUST_NOT_APPEAR:
        assert value.lower() not in text, f"{value!r} leaked into plant-shape.toml"


def test_the_leak_check_itself_clears_the_file_against_the_whole_database(blind_run: dict) -> None:
    done = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "leakcheck.py"),
         "--sqlite", str(blind_run["database"]),
         "--shape", str(blind_run["database"].parent / "plant-shape.toml")],
        check=False, capture_output=True, text=True, cwd=ROOT,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("passed: 0 of ")
    considered = int(done.stdout.split("passed: 0 of ")[1].split()[0])
    assert considered > 500, f"only {considered} values were even considered — the check is theatre"
    assert blind_run["shape"]["meta"]["leak_check"].startswith("passed: 0 of ")


def test_the_read_only_connection_is_a_uri_windows_can_read_too() -> None:
    """Plant PCs run Windows, and `file:C:\\plant\\mes.db` is not a URI.

    Read-only is the first promise this skill makes about somebody's production
    database, and it is made by opening the connection with `mode=ro` in a URI.
    A URI built by pasting a Windows path into it has backslashes where SQLite
    expects separators and a drive letter where it expects a path, so it either
    fails to open or opens something else. Spelled right it is
    `file:///C:/plant/mes.db?mode=ro`.
    """
    sys.path.insert(0, str(SKILL / "scripts"))
    import leakcheck  # shipped as a folder, not as a package

    for windows_path in (r"C:\plant\mes.db", r"D:\MES Data\history.db"):
        uri = leakcheck.read_only_uri(PureWindowsPath(windows_path))
        assert "\\" not in uri, uri
        assert uri.startswith("file:///"), uri
        assert uri.endswith("?mode=ro"), uri
        assert uri.count("?") == 1, uri
    # A `?` or `#` in the name would otherwise end the path early.
    odd = leakcheck.read_only_uri(PureWindowsPath(r"C:\plant\what#now?.db"))
    assert odd.count("?") == 1 and "%23" in odd and "%3f" in odd, odd


def test_the_leak_check_refuses_to_write_when_something_identifying_is_in_the_output(
        tmp_path: Path) -> None:
    """The check has to be able to fail, or none of the above means anything."""
    sys.path.insert(0, str(SKILL / "scripts"))
    import leakcheck

    clean = 'base = 110.2\nunit = "bpm"\nwindow_start = "2026-03-02T06:00:00Z"\n'
    values = ["WO-2026-0004711", "499.8", "2026-03-02T06:00:00Z", "bpm", "running"]
    assert leakcheck.check(clean, values).startswith("passed: 0 of ")

    with pytest.raises(leakcheck.Leak) as refused:
        leakcheck.check(clean + 'note = "WO-2026-0004711"\n', values)
    assert "WO-2026-0004711" in str(refused.value)
    assert "was NOT written" in str(refused.value)


def test_the_file_reads_back_in_plain_words_a_plant_engineer_would_recognise(blind_run: dict) -> None:
    explained = blind_run["explained"]
    assert "6 machines, in line order" in explained
    assert "ST-01: runs at" in explained
    assert "How the line stops" in explained
    assert "have no reason recorded at all" in explained
    assert "What their MES tracks that FactorySemantics does not" in explained
    assert "Leak check: passed: 0 of" in explained
    for value in MUST_NOT_APPEAR:
        assert value.lower() not in explained.lower(), f"{value!r} leaked into the spoken summary"


def test_the_file_states_what_it_did_not_measure_rather_than_implying_zero(blind_run: dict) -> None:
    meta = blind_run["shape"]["meta"]
    assert meta["tables_total"] == 13
    assert meta["tables_mapped"] == 11
    assert meta["tables_not_understood"] == 2
    assert meta["read_only"] is True
    assert meta["vocabulary_kept"] is False
    assert meta["window_hours"] == pytest.approx(1.0, abs=0.05)
    assert meta["rows_read"] > 20_000
    assert meta["queries_not_run"] == []


def test_every_query_the_skill_ships_is_a_bounded_select_and_nothing_else() -> None:
    """Read as text, both dialects, before anybody runs one.

    Not a style check: this is the first of the five promises SKILL.md makes to
    the person whose database it is.
    """
    forbidden = ("insert ", "update ", "delete ", "drop ", "create ", "alter ", "truncate ",
                 "exec ", "execute ", "merge ", "grant ", "revoke ", "into #", "sp_", "xp_")
    seen = 0
    for path in sorted(SKILL.glob("queries/*/*.sql")):
        sql = path.read_text(encoding="utf-8")
        body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        lowered = " " + body.lower().replace("\n", " ") + " "
        assert lowered.strip().startswith("select"), f"{path.name} does not start with SELECT"
        for word in forbidden:
            assert word not in lowered, f"{path.name} contains {word.strip()!r}"
        assert body.strip()[-1] == ";", f"{path.name} does not end in one statement"
        assert body.count(";") == 1, f"{path.name} has more than one statement"
        if "01_inventory" not in path.name and "02_columns" not in path.name:
            assert "{{row_cap}}" in sql or "count" in lowered or "group by" in lowered, (
                f"{path.name} is neither bounded nor an aggregate")
        seen += 1
    assert seen == 32, f"expected sixteen queries in each of two dialects, found {seen}"
