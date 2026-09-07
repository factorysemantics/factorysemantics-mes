"""The results store and sweeps.

A scorecard answers "is the MES honest right now". These answer "is it getting
better", which is the only question that guides a product.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fsmes.sim import store, sweep

LINE = Path("labs/multiplant/machining/line.json")


@pytest.fixture
def db(tmp_path):
    return tmp_path / "runs.db"


def _card(plant="machining", recall=1.0, misclassified=0, lag=27.0, variant=None):
    return {
        "plant": plant, "seed": 7, "duration_s": 3600, "speed": 60.0,
        "variant": variant,
        "scored_at": datetime.now(UTC).isoformat(),
        "metrics": {"breakdown_recall": recall,
                    "planned_stop_misclassified": misclassified,
                    "faults_scored": 1, "faults_scripted": 1},
        "faults": [{"lag_sim_seconds": lag}],
    }


# ------------------------------------------------------------------- store

def test_a_run_can_be_recorded_and_read_back(db):
    run_id = store.record(_card(), path=db)
    got = store.get(run_id, path=db)
    assert got["plant"] == "machining"
    assert got["breakdown_recall"] == 1.0
    # The whole card survives, not just the promoted columns.
    assert got["scorecard"]["metrics"]["faults_scripted"] == 1


def test_headline_metrics_are_columns_not_json_extracts(db):
    """They are what gets trended; digging them out of JSON on every query
    would be a slow lie about how the data is shaped."""
    store.record(_card(recall=0.5), path=db)
    with store.connect(db) as conn:
        row = conn.execute(
            "SELECT breakdown_recall FROM runs WHERE breakdown_recall < 1.0"
        ).fetchone()
    assert row["breakdown_recall"] == 0.5


def test_mean_lag_is_derived_from_the_faults(db):
    card = _card()
    card["faults"] = [{"lag_sim_seconds": 10.0}, {"lag_sim_seconds": 20.0},
                      {"lag_sim_seconds": None}]
    run_id = store.record(card, path=db)
    assert store.get(run_id, path=db)["mean_lag_sim_s"] == 15.0


def test_a_trend_reads_oldest_first(db):
    for recall in (0.4, 0.7, 1.0):
        store.record(_card(recall=recall), path=db)
    points = store.trend("machining", "breakdown_recall", path=db)
    assert [p["value"] for p in points] == [0.4, 0.7, 1.0]


def test_an_unknown_metric_is_refused_rather_than_interpolated(db):
    # The column name goes into SQL; anything not on the allow-list is an
    # error, not a query.
    with pytest.raises(ValueError, match="Unknown metric"):
        store.trend("machining", "'; DROP TABLE runs; --", path=db)


def test_pruning_removes_evidence_and_says_that_it_did(db, tmp_path):
    """The understanding is kept forever; the evidence is not. A pruned run
    must read as 'the logs were deleted', never as 'this run produced none'."""
    evidence = tmp_path / "run-evidence"
    evidence.mkdir()
    (evidence / "output.log").write_text("noisy megabytes", encoding="utf-8")

    run_id = store.record(_card(), evidence_dir=evidence, path=db)
    with store.connect(db) as conn:  # age it past the window
        conn.execute("UPDATE runs SET scored_at = ? WHERE id = ?",
                     ((datetime.now(UTC) - timedelta(days=60)).isoformat(), run_id))

    assert store.prune_evidence(older_than_days=30, path=db) == 1
    assert not evidence.exists()
    row = store.get(run_id, path=db)
    assert row["evidence_pruned_at"] is not None
    assert row["scorecard"]["metrics"]["breakdown_recall"] == 1.0


def test_recent_runs_are_newest_first_and_filterable(db):
    store.record(_card(plant="machining"), path=db)
    store.record(_card(plant="bottling"), path=db)
    assert store.recent(path=db)[0]["plant"] == "bottling"
    assert [r["plant"] for r in store.recent("machining", path=db)] == ["machining"]


# ------------------------------------------------------------------ sweeps

def test_a_grid_expands_to_the_product_of_its_knobs():
    variants = sweep.expand({"buffer_capacity": [2, 20], "seed": [1, 2]})
    assert len(variants) == 4
    assert {"buffer_capacity": 2, "seed": 1} in variants


def test_an_unknown_knob_is_an_error_not_a_silent_no_op():
    config = json.loads(LINE.read_text(encoding="utf-8"))
    with pytest.raises(KeyError, match="Unknown knob"):
        sweep.apply_variant(config, {"buffer_capasity": 6})


def test_a_variant_changes_the_line_and_leaves_the_original_alone():
    config = json.loads(LINE.read_text(encoding="utf-8"))
    before = config["buffers"]["capacity"]
    out = sweep.apply_variant(config, {"buffer_capacity": 99})
    assert out["buffers"]["capacity"] == 99
    assert config["buffers"]["capacity"] == before


def test_a_station_scoped_knob_touches_only_that_station():
    config = json.loads(LINE.read_text(encoding="utf-8"))
    out = sweep.apply_variant(config, {"rate_per_min": 5, "_station": "Mill"})
    rates = {s["name"]: s["rate_per_min"] for s in out["stations"]}
    assert rates["Mill"] == 5
    assert rates["Saw"] != 5


def test_down_seconds_moves_the_end_of_the_breakdown_not_its_start():
    config = json.loads(LINE.read_text(encoding="utf-8"))
    out = sweep.apply_variant(config, {"down_seconds": 600, "_station": "Mill"})
    down = next(e for e in out["events"] if e["type"] == "down")
    assert down["start"] == 2700
    assert down["end"] == 3300


def test_a_comparison_names_the_finding_rather_than_leaving_it_to_be_spotted():
    good = _card(misclassified=0, variant="buffer_capacity=20")
    bad = _card(misclassified=1, variant="buffer_capacity=2")
    result = sweep.compare([good, bad])
    assert "1 of 2" in result["verdict"]
    assert result["spread"]["planned_stop_misclassified"] == {"min": 0, "max": 1}


def test_comparing_identical_runs_says_nothing_went_wrong():
    result = sweep.compare([_card(), _card()])
    assert result["verdict"] == "no variant misclassified a planned stop"
