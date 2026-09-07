"""The nightly rollup.

The rule this file exists to defend: every number in the note is computed
here, and the model is handed those numbers rather than asked to derive them.
A model doing arithmetic on your behalf is one that will get it wrong quietly,
and a report you cannot trust is worse than no report.
"""

from datetime import UTC, datetime, timedelta

import pytest

from fsmes.sim import store
from fsmes.sim.rollup import gather, write_report


@pytest.fixture
def db(tmp_path):
    return tmp_path / "runs.db"


def _record(db, *, plant="machining", recall=1.0, misclassified=0, lag=26.0,
            days_ago=0.0, variant=None):
    when = datetime.now(UTC) - timedelta(days=days_ago)
    card = {
        "plant": plant, "seed": 7, "duration_s": 3600, "speed": 60.0,
        "scored_at": when.isoformat(),
        "metrics": {"breakdown_recall": recall,
                    "planned_stop_misclassified": misclassified,
                    "faults_scored": 1, "faults_scripted": 1},
        "faults": [{"lag_sim_seconds": lag}],
    }
    return store.record(card, variant=variant, path=db)


def test_a_quiet_day_reports_that_nothing_moved(db):
    _record(db)
    facts = gather(days=1, path=db)
    assert facts["runs_today"] == 1
    assert facts["changes"] == ["nothing moved"] or "first runs" in facts["changes"][0]


def test_a_drop_in_recall_is_called_out_with_its_direction(db):
    _record(db, recall=1.0, days_ago=1.5)
    _record(db, recall=0.6)
    facts = gather(days=1, path=db)
    assert any("DOWN" in c for c in facts["changes"]), facts["changes"]


def test_a_planned_stop_booked_as_downtime_is_called_a_defect(db):
    _record(db, misclassified=0, days_ago=1.5)
    _record(db, misclassified=1)
    facts = gather(days=1, path=db)
    assert any("destroys availability" in c for c in facts["changes"])


def test_growing_detection_lag_is_a_regression_even_at_perfect_recall(db):
    _record(db, recall=1.0, lag=20.0, days_ago=1.5)
    _record(db, recall=1.0, lag=40.0)
    facts = gather(days=1, path=db)
    assert any("detection lag grew" in c for c in facts["changes"]), facts["changes"]


def test_unknown_scores_are_counted_separately_never_averaged_in(db):
    """A mean over three of twenty runs is not the same claim as a mean over
    twenty, and the note has to say which it is."""
    _record(db, recall=1.0)
    _record(db, recall=None)
    facts = gather(days=1, path=db)
    machining = facts["plants"]["machining"]
    assert machining["runs"] == 2
    assert machining["scored_recall_runs"] == 1
    assert machining["unknown_recall_runs"] == 1
    assert machining["mean_recall"] == 1.0


def test_bad_runs_are_listed_so_they_can_be_opened(db):
    good = _record(db, recall=1.0)
    bad = _record(db, recall=0.5)
    facts = gather(days=1, path=db)
    ids = [f["id"] for f in facts["failures"]]
    assert bad in ids and good not in ids


def test_a_note_is_written_even_when_the_model_says_nothing(db, tmp_path):
    """A missing narrator must not cost you the numbers."""
    _record(db, recall=0.5)
    facts = gather(days=1, path=db)
    path = write_report(facts, summary=None, out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "did not answer" in text
    assert "0.500" in text


def test_the_note_carries_the_numbers_the_model_was_given(db, tmp_path):
    _record(db, recall=0.75, misclassified=1, lag=33.0)
    facts = gather(days=1, path=db)
    path = write_report(facts, summary="Recall fell.", out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Recall fell." in text
    assert "0.750" in text and "33.0" in text
    # The defect is in the table, not only in the prose.
    assert "| machining | 1 | 0.750 | 0.750 | 1 | 33.0 |" in text


def _card_with_triage(findings):
    when = datetime.now(UTC).isoformat()
    return {
        "plant": "machining", "seed": 7, "duration_s": 3600, "speed": 60.0,
        "scored_at": when,
        "metrics": {"breakdown_recall": 1.0, "planned_stop_misclassified": 0,
                    "faults_scored": 1, "faults_scripted": 1},
        "faults": [{"lag_sim_seconds": 26.0}],
        "triage": {"findings": findings,
                   "worst": findings[0]["severity"] if findings else None},
    }


def test_the_rollup_surfaces_what_reading_the_logs_found(db, tmp_path):
    """A scorecard cannot show a problem nobody wrote a test for. If the model
    found one overnight, it belongs in the one note somebody reads."""
    store.record(_card_with_triage([
        {"severity": "high", "what": "counter went backwards on RD01",
         "evidence": "was 4102 now 17"},
    ]), path=db)

    facts = gather(days=1, path=db)
    assert facts["triage"]["runs_with_findings"] == 1
    assert facts["triage"]["high_severity_runs"] == 1

    text = write_report(facts, summary=None, out_dir=tmp_path).read_text(encoding="utf-8")
    assert "Found by reading the logs" in text
    assert "counter went backwards on RD01" in text


def test_a_clean_night_does_not_invent_a_findings_section(db, tmp_path):
    store.record(_card_with_triage([]), path=db)
    facts = gather(days=1, path=db)
    assert facts["triage"]["runs_with_findings"] == 0
    assert "Found by reading the logs" not in write_report(
        facts, summary=None, out_dir=tmp_path).read_text(encoding="utf-8")
