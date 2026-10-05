import sqlite3
from copy import deepcopy

import pytest

from carbon_window.analysis import daily, latest
from carbon_window.pipeline import quality, save_snapshot, utc, validate


def rows():
    return [
        {
            "from": "2026-10-01T00:00Z",
            "to": "2026-10-01T00:30Z",
            "intensity": {"forecast": 100, "actual": None, "index": "moderate"},
        },
        {
            "from": "2026-10-01T00:30Z",
            "to": "2026-10-01T01:00Z",
            "intensity": {"forecast": 80, "actual": 90, "index": "low"},
        },
    ]


def snapshot():
    return {
        "run_id": "test",
        "start": "2026-10-01T00:00Z",
        "end": "2026-10-01T01:00Z",
        "fetched_at": "2026-10-02T00:00Z",
        "source": "test fixture",
        "data": rows(),
    }


def test_duplicates_normalised_and_missing_actual_remains_null():
    data = rows()
    data.append(deepcopy(data[0]))
    result = validate(data, utc(snapshot()["start"]), utc(snapshot()["end"]))
    assert len(result) == 2 and result[0]["intensity"]["actual"] is None


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "75", True])
def test_reject_bad_values(value):
    data = rows()
    data[0]["intensity"]["forecast"] = value
    with pytest.raises(ValueError):
        validate(data, utc(snapshot()["start"]), utc(snapshot()["end"]))


def test_conflicting_duplicates_fail():
    data = rows()
    extra = deepcopy(data[0])
    extra["intensity"]["forecast"] = 1
    data.append(extra)
    with pytest.raises(ValueError, match="Conflicting"):
        validate(data, utc(snapshot()["start"]), utc(snapshot()["end"]))


def test_refresh_keeps_old_run_on_failure(tmp_path):
    db = tmp_path / "data.sqlite"
    save_snapshot(snapshot(), db)
    bad = snapshot()
    bad["run_id"] = "bad"
    bad["data"][0]["to"] = "2026-10-01T01:00Z"
    with pytest.raises(ValueError):
        save_snapshot(bad, db)
    data, run = latest(db)
    assert run["run_id"] == "test" and len(data) == 2
    # Failure after the run insert also rolls the entire transaction back.
    duplicate = snapshot()
    with pytest.raises(sqlite3.IntegrityError):
        save_snapshot(duplicate, db)
    assert latest(db)[1]["run_id"] == "test"


def test_sql_denominator_ignores_missing_actual(tmp_path):
    db = tmp_path / "data.sqlite"
    save_snapshot(snapshot(), db)
    result = daily(db).iloc[0]
    assert result.actual_intervals == 1 and result.average_actual == 90
    assert result.archived_forecast_mae == 10


def test_quality_reports_gap_and_unreported_actual_separately():
    start = utc(snapshot()["start"])
    end = utc(snapshot()["end"])
    report = quality(rows()[:1], start, end, utc(snapshot()["fetched_at"]))
    assert report["missing_intervals"] == 1 and report["historical_actual_missing"] == 1


def test_naive_time_rejected():
    with pytest.raises(ValueError, match="timezone"):
        utc("2026-10-01T12:00")
